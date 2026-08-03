"""定时发布调度 + 通知告警 - SA-LogiFlow v3.0."""
import asyncio
import json as _json
import logging
import smtplib
import os
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
import publisher
import ratelimit
import truth_guard
import hotspot_fetcher
import hotspot_video_sources
import media_retention
import model_router

logger = logging.getLogger(__name__)

# 邮件配置
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.163.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")

# IM 机器人 webhook
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")

scheduler = AsyncIOScheduler()
_targeted_hotspot_refresh_task: asyncio.Task | None = None


def _intake_metadata_sample(candidates: list[dict], limit: int) -> list[dict]:
    """Build a recent, source-diverse metadata batch without deciding relevance.

    The intake model is the only component allowed to decide whether a story is
    useful for Buffalo.  This helper only prevents an old keyword score from
    monopolising the bounded metadata read with one publisher's unrelated
    archive.  It round-robins the newest records from each configured source,
    then fills any remaining slots by recency.
    """
    limit = max(1, int(limit))

    def freshness(item: dict) -> tuple[str, int]:
        return (str(item.get("published_at") or ""), int(item.get("id") or 0))

    ordered = sorted((dict(item) for item in candidates), key=freshness, reverse=True)
    groups: dict[str, list[dict]] = {}
    for item in ordered:
        source = str(item.get("publisher") or item.get("platform") or "unknown").strip() or "unknown"
        groups.setdefault(source, []).append(item)

    selected: list[dict] = []
    source_order = sorted(groups, key=lambda source: freshness(groups[source][0]), reverse=True)
    while len(selected) < limit:
        added = False
        for source in source_order:
            rows = groups[source]
            if not rows:
                continue
            selected.append(rows.pop(0))
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected


async def cleanup_media_retention():
    """按配置预演或执行素材生命周期清理。默认只预演。"""
    if os.environ.get("MEDIA_CLEANUP_ENABLED", "1") != "1":
        logger.info("媒体生命周期清理已关闭")
        return
    dry_run = os.environ.get("MEDIA_CLEANUP_DRY_RUN", "1") != "0"
    report = await asyncio.to_thread(
        media_retention.run_cleanup,
        Path(__file__).with_name("static"),
        dry_run,
    )
    logger.info(
        "媒体清理%s：候选=%s，预计/实际空间=%s bytes",
        "预演" if dry_run else "执行",
        report.get("candidate_count", 0) + report.get("output_candidate_count", 0),
        report.get("estimated_bytes", 0) if dry_run else report.get("released_bytes", 0),
    )


async def prewarm_authorized_hotspot_media(media_ids: list[int] | None = None):
    """每三天将全部已授权资讯视频交由内置模型分析为 Hook 素材库。

    不再由一个小型候选筛选器决定“只下载几条”。所有满足授权和状态
    门槛的资讯视频都会依次下载；项目内置模型负责镜头分析、事件切片、标题、
    事件说明及事实核验。``media_ids`` 仅供管理员受控验收时缩小范围。
    """
    if os.environ.get("HOTSPOT_HOOK_SYNC_ENABLED", os.environ.get("HOTSPOT_PREWARM_ENABLED", "1")) != "1":
        return {"status": "disabled", "reason": "HOTSPOT_HOOK_SYNC_ENABLED 未开启"}
    # 默认不以时长二次筛选：产品承诺的是“所有已获得且已授权的资讯视频”
    # 都由内置模型分析。若部署方有明确容量约束，才通过环境变量设置下限。
    min_duration = max(0, int(os.environ.get("HOTSPOT_PREWARM_MIN_DURATION_SECONDS", "0")))
    admin = db.get_user_by_username("admin")
    if not admin:
        logger.warning("热点预热跳过：缺少管理员用户")
        return {"status": "missing_admin", "reason": "缺少管理员用户"}
    # 规则只做硬性门禁（授权、时长、状态）。三天全量任务不会再用关键词或
    # 预下载模型筛选把大部分资讯视频留在库外；是否形成 Hook 由下载后的
    # 内置视觉/ASR/策展/事实核验模型决定。
    metadata_candidates = [
        item for item in db.list_active_authorized_hotspot_media_for_full_intake()
        if item.get("media_kind") in {"video_link", "video_file"}
        # A local service restart can interrupt a yt-dlp subprocess between
        # state updates. Treat that recoverable in-flight marker as pending on
        # the next three-day/full run instead of leaving an authorised source
        # permanently outside the model pipeline.
        # A terminal network failure is retried only by a later full run (never
        # within the same snapshot), so one dead source cannot starve the rest
        # of the authorised library forever.
        and (
            item.get("download_status") in {
                "metadata_ready", "failed", "download_failed", "pending", "downloading",
            }
            # If the service stopped after a source file was saved, resume the
            # built-in analysis from that authorised asset instead of requiring
            # another external download.
            or (
                item.get("download_status") == "downloaded"
                and item.get("processing_status") in {"not_started", "processing", "processing_failed"}
                and item.get("asset_id")
            )
        )
    ]
    requested_media_ids = {int(value) for value in (media_ids or []) if str(value).strip().isdigit()}
    if requested_media_ids:
        metadata_candidates = [item for item in metadata_candidates if int(item.get("id") or 0) in requested_media_ids]
    if not metadata_candidates:
        logger.info("热点预热：没有符合授权、时长和状态条件的长视频")
        return {
            "status": "no_candidates", "candidate_count": 0,
            "requested_media_ids": sorted(requested_media_ids),
        }
    # 每条 YouTube 视频都先读取单视频元数据，避免内置模型拿频道占位标题猜测
    # 事件，也避免频道列表中不准确的预估时长提前把可分析视频排除。本步骤
    # 不是选择器：所有授权视频先获得事实元数据，再用真实时长执行硬门禁。
    metadata_candidates = _intake_metadata_sample(metadata_candidates, len(metadata_candidates))
    decision_pool, metadata_report = await asyncio.to_thread(
        hotspot_video_sources.hydrate_youtube_intake_metadata,
        metadata_candidates,
        limit=len(metadata_candidates),
    )
    decision_pool = [
        item for item in decision_pool
        if item.get("platform") != "youtube" or item.get("intake_metadata_status") == "ready"
    ]
    decision_pool = [
        item for item in decision_pool
        # Directly embedded videos often disclose no duration until the project
        # downloads and probes them. They remain in the full chain. 默认阈值为
        # 0，因此所有已授权资讯视频都会进入内置模型分析；仅部署方显式配置时
        # 才跳过已知短于阈值的视频。
        if item.get("duration_seconds") in (None, "")
        or float(item.get("duration_seconds") or 0) >= min_duration
    ]
    if not decision_pool:
        logger.info("热点预热：授权视频元数据尚不可用，本轮不让模型猜测并下载（%s）", metadata_report)
        return {
            "status": "metadata_unavailable", "candidate_count": len(metadata_candidates),
            "metadata_candidate_ids": [int(item["id"]) for item in metadata_candidates],
            "metadata": metadata_report, "decision_pool_ids": [],
        }
    if not model_router.key_is_available("planner_text") or not model_router.key_is_available("critic"):
        return {
            "status": "model_unavailable", "candidate_count": len(metadata_candidates),
            "metadata_candidate_ids": [int(item["id"]) for item in metadata_candidates],
            "metadata": metadata_report,
            "decision_pool_ids": [int(item["id"]) for item in decision_pool],
            "reason": "内置 Hook 策展或事实核验模型未配置，本轮不下载以避免未分析的母片进入素材库。",
        }
    # Delayed import avoids scheduler ↔ app initialization cycle.  The shared
    # materializer downloads, runs ASR/OCR/vision, and lets the built-in
    # curator create event titles/descriptions and verified event clips.
    from app import _run_hotspot_media_materialization
    materialized: list[dict] = []
    for item in decision_pool:
        existing_asset = (
            db.get_asset(int(item["asset_id"]))
            if str(item.get("asset_id") or "").isdigit()
            else None
        )
        # yt-dlp 可能在服务停止前已写完并登记 asset，但晚到的进度事件仍把
        # 媒体行停留在 downloading。asset 可用时以本地事实为准，避免重复下载。
        reuse_downloaded_asset = bool(
            existing_asset and str(existing_asset.get("file_status") or "available") == "available"
        )
        intake_decision = {
            "admission_mode": "all_authorized_video_analysis",
            "why": "三天全量任务：已授权资讯视频必须由项目内置模型完成镜头与事件分析。",
            "source_title": str(item.get("intake_title") or "")[:300],
            "source_summary": str(item.get("intake_summary") or "")[:1_200],
        }
        db.update_hotspot_media_state(
            item["id"],
            # 服务重启后已有本地母片的任务必须直接续跑分析；若先重置成 pending，
            # materializer 会误以为没有 asset，再次走网络下载并拖慢整个全量队列。
            download_status="downloaded" if reuse_downloaded_asset else "pending",
            download_progress=65 if reuse_downloaded_asset else 5,
            progress_detail=(
                "热点 Hook 入库：复用已下载母片，继续内置模型分析和策展"
                if reuse_downloaded_asset
                else "热点 Hook 入库：已进入下载、分析和模型策展队列"
            ),
            processing_status="processing" if reuse_downloaded_asset else "not_started",
            error_message=None,
            intake_decision_json=_json.dumps(intake_decision, ensure_ascii=False),
        )
        await _run_hotspot_media_materialization(item["id"], admin["id"])
        refreshed = db.get_hotspot_media(int(item["id"])) or {}
        hook_count = 0
        if refreshed.get("asset_id"):
            hook_count = len(db.list_hotspot_event_clips(asset_id=int(refreshed["asset_id"])))
        request_ids = []
        if hook_count and request_ids:
            db.mark_hotspot_discovery_request_matched(request_ids, int(item["id"]))
        materialized.append({
            "media_id": int(item["id"]), "asset_id": refreshed.get("asset_id"),
            "download_status": refreshed.get("download_status"),
            "processing_status": refreshed.get("processing_status"),
            "hook_count": hook_count,
            "progress_detail": refreshed.get("progress_detail"),
        })
        db.add_audit_log(
            admin["id"], admin["username"], "prewarm_authorized_hotspot_media", target=str(item["id"]),
            detail=_json.dumps(intake_decision, ensure_ascii=False),
        )
    return {
        "status": "materialized", "candidate_count": len(metadata_candidates),
        "requested_media_ids": sorted(requested_media_ids),
        "metadata_candidate_ids": [int(item["id"]) for item in metadata_candidates],
        "metadata": metadata_report,
        "decision_pool_ids": [int(item["id"]) for item in decision_pool],
        "intake": {
            "mode": "all_authorized_video_analysis",
            "source_metadata": metadata_report,
            "curator": "planner_text + critic",
        },
        "selected_media_ids": [int(item["id"]) for item in decision_pool],
        "materialized": materialized,
    }


async def refresh_targeted_hotspot_hooks() -> dict:
    """Immediately rescan authorised sources after a chat video has no Hook pair.

    A user request must affect collection promptly.  The rescan remains bounded
    to configured, authorised feeds and channels; it never turns a free-form
    chat topic into an unlicensed web download.  The normal scheduler is still
    responsible for maintaining the library between requests.
    """
    admin = db.get_user_by_username("admin")
    try:
        fetched = await hotspot_fetcher.fetch_hotspots(
            static_dir=Path(__file__).with_name("static"),
            created_by=admin["id"] if admin else None,
            video_channels=hotspot_fetcher.configured_video_channels(),
            video_limit=hotspot_video_sources.MAX_CHANNEL_VIDEO_LIMIT,
        )
        intake = await prewarm_authorized_hotspot_media()
        report = {"status": "completed", "fetch": fetched, "intake": intake}
        logger.info(
            "聊天定向热点复扫完成：新增=%s，视频候选=%s，入库=%s",
            fetched.get("new", 0), fetched.get("video_media", 0), intake.get("status"),
        )
        return report
    except Exception as exc:
        logger.exception("聊天定向热点复扫失败")
        return {"status": "failed", "error": str(exc)[:300]}


def request_targeted_hotspot_refresh() -> bool:
    """Schedule one coalesced targeted refresh and report whether it just started."""
    global _targeted_hotspot_refresh_task
    if _targeted_hotspot_refresh_task and not _targeted_hotspot_refresh_task.done():
        return False
    _targeted_hotspot_refresh_task = asyncio.create_task(
        refresh_targeted_hotspot_hooks(), name="targeted-hotspot-hook-refresh"
    )
    return True


async def cleanup_hotspot_hook_library():
    """按十天滚动窗口清理 Hook 素材；最近三天与活动项目引用永远受保护。"""
    if os.environ.get("HOTSPOT_HOOK_CLEANUP_ENABLED", "1") != "1":
        return
    try:
        retention_days = max(3, int(os.environ.get("HOTSPOT_HOOK_RETENTION_DAYS", "10")))
        protect_days = max(0, int(os.environ.get("HOTSPOT_HOOK_PROTECT_DAYS", "3")))
    except ValueError:
        retention_days, protect_days = 10, 3
    report = await asyncio.to_thread(
        media_retention.cleanup_hotspot_hook_library,
        Path(__file__).with_name("static"),
        retention_days=retention_days,
        protect_days=protect_days,
    )
    logger.info(
        "热点 Hook 库滚动清理：删除=%s，候选=%s，跳过=%s，保护窗口=%s 天",
        report["deleted_count"], report["candidate_count"], report["skipped_count"], protect_days,
    )


async def send_feishu(text: str):
    """飞书自定义机器人文本消息。"""
    if not FEISHU_WEBHOOK:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": text}})
        logger.info("飞书通知已发送")
    except Exception as e:
        logger.error("飞书通知发送失败: %s", e)


async def send_wecom(text: str):
    """企业微信群机器人文本消息。"""
    if not WECOM_WEBHOOK:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(WECOM_WEBHOOK, json={"msgtype": "text", "text": {"content": text}})
        logger.info("企业微信通知已发送")
    except Exception as e:
        logger.error("企业微信通知发送失败: %s", e)


async def send_email(subject: str, body: str):
    """发送告警邮件。"""
    if not SMTP_USER or not ALERT_EMAIL:
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [ALERT_EMAIL], msg.as_string())
        logger.info("告警邮件已发送: %s", ALERT_EMAIL)
    except Exception as e:
        logger.error("告警邮件发送失败: %s", e)


async def notify_all(subject: str, text: str):
    """向所有已配置渠道（邮件 / 飞书 / 企业微信）推送同一条通知。"""
    await asyncio.gather(
        send_email(subject, text),
        send_feishu(text),
        send_wecom(text),
    )


async def check_scheduled_publish():
    """检查到期的定时发布任务并执行。"""
    items = db.get_scheduled_items()
    if not items:
        return

    logger.info("发现 %d 条到期的定时发布任务", len(items))
    for item in items:
        item_id = item["id"]
        platform = item["platform"]
        logger.info("定时发布: id=%d, platform=%s, title=%s", item_id, platform, item["title"])

        truth_error = truth_guard.publish_error(item)
        if truth_error:
            db.update_queue_status(item_id, "failed", truth_error)
            db.add_publish_log(item_id, platform, item["title"], "failed", truth_error)
            logger.warning("真实性门禁阻止定时发布: id=%d", item_id)
            continue

        ok, reason = ratelimit.can_publish_now(platform)
        if not ok:
            next_at = ratelimit.next_run_time(datetime.now())
            db.update_queue_status(item_id, "queued", f"频控顺延: {reason}", scheduled_at=next_at)
            logger.info("频控顺延: id=%d, %s -> %s", item_id, reason, next_at)
            continue

        attachments = _json.loads(item.get('attachments') or '[]')
        images = [a['path'] for a in attachments if a.get('type') == 'image']
        video = next((a['path'] for a in attachments if a.get('type') == 'video'), None)
        account = db.get_account(item["target_account_id"]) if item.get("target_account_id") else None
        if account and account.get("owner_id") != item.get("created_by"):
            account = None
        result = await publisher.dispatch(
            platform=platform,
            title=item["title"],
            content=item["body"],
            tags=item.get("hashtags", []), owner_id=item.get("created_by"),
            images=images if images else None, video=video, account=account,
        )

        if result["success"]:
            db.update_queue_status(item_id, "published")
            db.add_publish_log(item_id, platform, item["title"], "published")
            logger.info("定时发布成功: id=%d", item_id)
            await send_success_notify(item)
        else:
            retry_count = db.get_retry_count(item_id)
            if retry_count < 3:
                db.increment_retry_count(item_id)
                db.update_queue_status(item_id, "queued", f"重试中 ({retry_count + 1}/3): {result.get('error', '')}")
                db.add_publish_log(item_id, platform, item["title"], "retry", result.get("error"))
                logger.warning("定时发布失败，将重试: id=%d, retry=%d", item_id, retry_count + 1)
            else:
                db.update_queue_status(item_id, "failed", result.get("error", "Unknown error"))
                db.add_publish_log(item_id, platform, item["title"], "failed", result.get("error"))
                logger.error("定时发布最终失败: id=%d", item_id)
                # 发送告警
                _maybe_mark_expired(platform, result.get("error", ""), owner_id=item.get("created_by"))
                await send_alert(item, result.get("error", "未知错误"))


def _suggest_for_error(error: str) -> str:
    """根据错误内容给出处理建议。"""
    e = (error or "").lower()
    if "cookie" in e or "登录" in error or "login" in e:
        return "Cookie/登录失效，请重新登录账号"
    if "token" in e:
        return "Token 失效，请刷新授权"
    if "timeout" in e or "超时" in error:
        return "网络或接口超时，请稍后重试"
    return "请登录系统查看并处理"


def _maybe_mark_expired(platform: str, error: str, owner_id: int | None = None):
    """登录/cookie/token 类错误：只把该用户同平台账号置 expired。"""
    e = (error or "").lower()
    if "cookie" in e or "登录" in error or "login" in e or "token" in e:
        for acc in db.get_accounts(platform, owner_id=owner_id):
            db.update_account_status(acc["id"], "expired")
            logger.warning("账号置为 expired: platform=%s, id=%d", platform, acc["id"])


async def send_alert(item: dict, error: str):
    """发布失败时向所有渠道推送告警。"""
    suggest = _suggest_for_error(error)
    subject = f"[SA-LogiFlow] 发布失败告警 - {item['platform']}"
    text = (
        f"🔴 发布失败\n"
        f"平台：{item['platform']}\n"
        f"标题：{item['title']}\n"
        f"失败原因：{error}\n"
        f"建议：{suggest}\n"
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await notify_all(subject, text)


async def send_success_notify(item: dict):
    """发布成功时推送通知（仅当配置了 IM 渠道时有意义）。"""
    if not (FEISHU_WEBHOOK or WECOM_WEBHOOK):
        return
    text = (
        f"🟢 发布成功\n"
        f"平台：{item['platform']}\n"
        f"标题：{item['title']}\n"
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await asyncio.gather(send_feishu(text), send_wecom(text))


def start_scheduler():
    """启动定时调度器。"""
    # 每分钟检查一次定时发布任务
    scheduler.add_job(check_scheduled_publish, "interval", minutes=1, id="scheduled_publish", replace_existing=True)
    scheduler.add_job(
        hotspot_fetcher.fetch_hotspots, "interval", hours=6, id="south_africa_hotspots",
        kwargs={
            "static_dir": Path(__file__).with_name("static"),
            "video_channels": hotspot_fetcher.configured_video_channels(),
        },
        replace_existing=True,
    )
    scheduler.add_job(
        cleanup_media_retention,
        "cron",
        hour=3,
        minute=30,
        id="media_retention_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        prewarm_authorized_hotspot_media,
        "interval",
        days=3,
        id="hotspot_hook_library_sync",
        # 全量入库是后台维护任务，不得在每次服务重启时抢占聊天与正式成片的
        # 模型预算。首次及后续执行都按三天窗口；用户无 Hook 时的定向补采仍由
        # request_targeted_hotspot_refresh 单独入队。
        next_run_time=datetime.now() + timedelta(days=3),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cleanup_hotspot_hook_library,
        "cron",
        hour=int(os.environ.get("HOTSPOT_HOOK_CLEANUP_HOUR", "3")),
        minute=45,
        id="hotspot_hook_library_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("定时调度器已启动（每分钟检查定时发布任务）")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("定时调度器已停止")
