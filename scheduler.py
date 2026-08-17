"""定时发布调度 + 通知告警 - SA-LogiFlow v3.0."""
import asyncio
import json as _json
import logging
import re
import smtplib
import os
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
import publisher
import ratelimit
import truth_guard
import xhs_diff_guard
import hotspot_fetcher
import hotspot_intake_policy
import hotspot_video_sources
import topic_hook_pipeline
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
    admin = db.get_first_admin_user()
    if not admin:
        logger.warning("热点预热跳过：缺少管理员用户")
        return {"status": "missing_admin", "reason": "缺少管理员用户"}
    # 规则只做硬性门禁（授权、时长、状态）。三天全量任务不会再用关键词或
    # 预下载模型筛选把大部分资讯视频留在库外；是否形成 Hook 由下载后的
    # 内置视觉/ASR/策展/事实核验模型决定。
    metadata_candidates = [
        item for item in db.list_active_authorized_hotspot_media_for_full_intake()
        if item.get("media_kind") in {"video_link", "video_file"}
        and hotspot_intake_policy.is_full_intake_eligible(item)
    ]
    requested_media_ids = {int(value) for value in (media_ids or []) if str(value).strip().isdigit()}
    if requested_media_ids:
        # Explicit operator retries may target a legacy ``prefiltered_skip``
        # row. Re-admit only the requested authorised rows; the normal full
        # cycle still leaves those rows untouched until a later review.
        requested_rows = {
            int(item.get("id") or 0): item
            for item in db.list_hotspot_media(lifecycle_status="active", limit=500)
            if int(item.get("id") or 0) in requested_media_ids
            and item.get("media_kind") in {"video_link", "video_file"}
            and item.get("authorization_status") == "authorized"
            and item.get("download_status") == "prefiltered_skip"
        }
        for item in requested_rows.values():
            item["download_status"] = "metadata_ready"
            item["processing_status"] = "not_started"
            item["error_message"] = None
            item["progress_detail"] = "受控重试：重新进入视觉分析，不沿用历史题材预筛结果"
        metadata_candidates.extend(requested_rows.values())
        # A mother that already completed ASR/OCR/vision analysis but produced
        # zero Hooks must also be eligible for an explicit operator re-curation.
        # The normal three-day intake intentionally does not loop over these
        # terminal rows, while a targeted retry must not silently report
        # ``no_candidates`` and leave a possible planner miss unexamined.
        requested_ready_rows = {
            int(item.get("id") or 0): item
            for item in db.list_hotspot_media(lifecycle_status="active", limit=500)
            if int(item.get("id") or 0) in requested_media_ids
            and item.get("media_kind") in {"video", "video_link", "video_file"}
            and item.get("authorization_status") == "authorized"
            and item.get("download_status") == "downloaded"
            and item.get("processing_status") == "ready"
            and item.get("asset_id")
            and not db.list_hotspot_event_clips(asset_id=int(item["asset_id"]))
        }
        for item in requested_ready_rows.values():
            item["processing_status"] = "not_started"
            item["error_message"] = None
            item["progress_detail"] = "受控重策展：复用已分析母片，重新执行 Hook 策展与事实核验"
        metadata_candidates.extend(requested_ready_rows.values())
        requested_paused_rows = {
            int(item.get("id") or 0): item
            for item in db.list_hotspot_media(lifecycle_status="active", limit=500)
            if int(item.get("id") or 0) in requested_media_ids
            and item.get("media_kind") in {"video_link", "video_file"}
            and item.get("authorization_status") == "authorized"
            and item.get("download_status") == hotspot_intake_policy.METADATA_FAILED_STATUS
        }
        for item in requested_paused_rows.values():
            item["download_status"] = "metadata_ready"
            item["retry_after"] = None
            item["progress_detail"] = "受控重试：重新读取失效视频元数据"
            db.update_hotspot_media_state(
                int(item["id"]),
                download_status="metadata_ready",
                retry_after=None,
                progress_detail=item["progress_detail"],
            )
        metadata_candidates.extend(requested_paused_rows.values())
        metadata_candidates = [
            item for item in metadata_candidates
            if int(item.get("id") or 0) in requested_media_ids
        ]
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
    import random
    import hotspot_media

    try:
        concurrency = int(str(os.environ.get("SA_HOTSPOT_DL_CONCURRENCY", "3")).strip() or "3")
    except ValueError:
        concurrency = 3
    concurrency = max(1, min(5, concurrency))
    semaphore = asyncio.Semaphore(concurrency)
    materialized: list[dict] = []
    materialized_lock = asyncio.Lock()

    async def _materialize_one(item: dict) -> None:
        # 有界并发：提交前抖动，避免同一代理瞬时打满。
        await asyncio.sleep(random.uniform(0.5, 1.5))
        async with semaphore:
            existing_asset = (
                db.get_asset(int(item["asset_id"]))
                if str(item.get("asset_id") or "").isdigit()
                else None
            )
            reuse_downloaded_asset = bool(
                existing_asset and str(existing_asset.get("file_status") or "available") == "available"
            )
            # 下载前预筛（与 materializer 内再检互为保险；此处提前标记避免无谓排队）。
            if not reuse_downloaded_asset and item.get("media_kind") != "image":
                allowed, reason = hotspot_media.prefilter_mother_candidate(item)
                if not allowed:
                    logger.info("prefilter skip id=%s reason=%s", item["id"], reason)
                    db.update_hotspot_media_state(
                        item["id"],
                        download_status="prefiltered_skip",
                        download_progress=0,
                        progress_detail=f"prefilter skip: {reason}",
                        processing_status="not_started",
                        error_message=None,
                    )
                    async with materialized_lock:
                        materialized.append({
                            "media_id": int(item["id"]), "asset_id": None,
                            "download_status": "prefiltered_skip",
                            "processing_status": "not_started",
                            "hook_count": 0,
                            "progress_detail": f"prefilter skip: {reason}",
                        })
                    return
            intake_decision = {
                "admission_mode": "all_authorized_video_analysis",
                "why": "三天全量任务：已授权资讯视频必须由项目内置模型完成镜头与事件分析。",
                "source_title": str(item.get("intake_title") or "")[:300],
                "source_summary": str(item.get("intake_summary") or "")[:1_200],
            }
            # Keep previously recorded sample windows when resuming a downloaded mother.
            prior = {}
            try:
                prior = _json.loads(str(item.get("intake_decision_json") or "{}"))
            except (TypeError, ValueError, _json.JSONDecodeError):
                prior = {}
            if isinstance(prior, dict) and prior.get("sample_offsets"):
                intake_decision["sample_offsets"] = prior["sample_offsets"]
                if prior.get("analysis_height") is not None:
                    intake_decision["analysis_height"] = prior.get("analysis_height")
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
            async with materialized_lock:
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

    await asyncio.gather(*(_materialize_one(item) for item in decision_pool))
    ready = sum(1 for row in materialized if row.get("processing_status") == "ready")
    in_flight = sum(
        1 for row in materialized
        if row.get("download_status") in {"pending", "downloading"}
        or row.get("processing_status") in {"processing", "not_started"}
    )
    prefiltered = sum(1 for row in materialized if row.get("download_status") == "prefiltered_skip")
    confirmed_hooks = sum(int(row.get("hook_count") or 0) for row in materialized)
    logger.info(
        "热点预热汇总 concurrency=%s ready=%s in_flight=%s pending=%s prefiltered_skip=%s confirmed_hooks=%s",
        concurrency, ready, in_flight, len(decision_pool) - ready - prefiltered, prefiltered, confirmed_hooks,
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
            "dl_concurrency": concurrency,
        },
        "selected_media_ids": [int(item["id"]) for item in decision_pool],
        "materialized": materialized,
        "summary": {
            "ready": ready,
            "in_flight": in_flight,
            "pending": max(0, len(decision_pool) - ready - prefiltered),
            "prefiltered_skip": prefiltered,
            "confirmed_hooks": confirmed_hooks,
        },
    }


async def refresh_targeted_hotspot_hooks() -> dict:
    """Run topic-scoped intake for chat discovery jobs; persist stage evidence."""
    import chat_intent

    if not topic_hook_pipeline.autofetch_enabled():
        return {"status": "disabled", "reason": "TOPIC_HOOK_AUTOFETCH_ENABLED=0"}
    admin = db.get_first_admin_user()
    pending = []
    seen = set()
    for status in ("pending", "queued", "processing", "fetching", "downloading", "analyzing", "reviewing"):
        for item in db.list_hotspot_discovery_requests(status=status, limit=100):
            request_id = int(item["id"])
            if request_id in seen:
                continue
            seen.add(request_id)
            mode = chat_intent.classify_content_mode(item.get("topic") or "")
            if chat_intent.should_request_hotspot_retrieval(mode) or chat_intent.assess_event_anchor(item.get("topic") or "").get("has_event_anchor"):
                pending.append(item)
    cancelled = db.cancel_misrouted_comparison_discovery_requests()
    if not pending:
        return {"status": "idle", "cancelled_misrouted": len(cancelled)}
    for item in pending:
        db.update_hotspot_discovery_request(
            int(item["id"]), status="fetching", stage="fetching", error_message=None,
        )
    try:
        query = pending[0].get("query") or topic_hook_pipeline.structure_topic(pending[0].get("topic") or "")
        channels = topic_hook_pipeline.prefer_official_channels(
            hotspot_fetcher.configured_video_channels(), query,
        )
        feeds = topic_hook_pipeline.prefer_official_feeds(hotspot_fetcher.configured_feeds(), query)
        fetched = await asyncio.wait_for(
            hotspot_fetcher.fetch_hotspots(
                static_dir=Path(__file__).with_name("static"),
                created_by=admin["id"] if admin else None,
                feeds=feeds,
                video_channels=channels,
                video_limit=min(
                    hotspot_video_sources.MAX_CHANNEL_VIDEO_LIMIT,
                    int(query.get("max_candidates") or topic_hook_pipeline.autofetch_max_candidates()),
                ),
            ),
            timeout=topic_hook_pipeline.autofetch_timeout_seconds(),
        )
        targeted_media_ids = [
            int(item) for item in (fetched.get("media_ids") or [])
            if str(item).isdigit()
        ]
        for item in pending:
            db.update_hotspot_discovery_request(
                int(item["id"]),
                status="downloading",
                stage="downloading",
                candidate_count=len(targeted_media_ids),
            )
        # 定向补采只能物化本轮新发现的媒体，不能调用无参数全量预热，
        # 否则一次聊天请求会越权启动三天/六小时全库补库。
        intake = await asyncio.wait_for(
            prewarm_authorized_hotspot_media(media_ids=targeted_media_ids),
            timeout=topic_hook_pipeline.autofetch_timeout_seconds(),
        )
        for item in pending:
            db.update_hotspot_discovery_request(int(item["id"]), status="analyzing", stage="analyzing")
        outcomes = []
        for item in pending:
            topic = str(item.get("topic") or "")
            query = item.get("query") or topic_hook_pipeline.structure_topic(topic)
            db.update_hotspot_discovery_request(int(item["id"]), status="reviewing", stage="reviewing")
            events = db.list_hotspot_event_clips()
            media_by_asset = {}
            for event in events:
                asset_id = int(event.get("asset_id") or 0)
                if asset_id and asset_id not in media_by_asset:
                    media = db.get_hotspot_media_by_asset_id(asset_id)
                    if media:
                        media_by_asset[asset_id] = media
            buckets = topic_hook_pipeline.match_topic_hooks(
                query,
                events,
                media_by_asset=media_by_asset,
                is_ready=lambda event: (
                    str(event.get("review_status") or "") == "confirmed"
                    and str(event.get("clip_status") or "") == "ready"
                    and bool(str(event.get("clip_path") or "").strip())
                    and hotspot_intake_policy.has_real_logistics_scene(event)
                    and not hotspot_intake_policy.is_placeholder_logistics_question(
                        (event.get("evidence") or {}).get("logistics_question")
                    )
                ),
                is_audit=lambda event: str(event.get("review_status") or "") == "confirmed",
            )
            if buckets["matched_ready"]:
                matched_media_id = buckets["matched_ready"][0].get("asset_id")
                media_row = media_by_asset.get(int(matched_media_id or 0)) or db.get_hotspot_media_by_asset_id(int(matched_media_id or 0))
                media_id = int((media_row or {}).get("id") or 0) or _discovery_match_media_id_for_topic(topic)
                if media_id:
                    db.mark_hotspot_discovery_request_matched([int(item["id"])], media_id)
                    db.update_hotspot_discovery_request(int(item["id"]), status="matched", stage="ready")
                    outcomes.append({"request_id": int(item["id"]), "status": "matched", "matched_media_id": media_id})
                    continue
            matched_media_id = _discovery_match_media_id_for_topic(topic)
            if matched_media_id:
                db.mark_hotspot_discovery_request_matched([int(item["id"])], matched_media_id)
                db.update_hotspot_discovery_request(int(item["id"]), status="matched", stage="ready")
                outcomes.append({"request_id": int(item["id"]), "status": "matched", "matched_media_id": matched_media_id})
            else:
                db.update_hotspot_discovery_request(
                    int(item["id"]),
                    status="no_match",
                    stage="no_match",
                    error_message="定向采集完成，暂无与该主题匹配且可成片的物流 Hook；未用无关新闻填补",
                )
                outcomes.append({"request_id": int(item["id"]), "status": "no_match"})
        report = {
            "status": "completed",
            "fetch": fetched,
            "intake": intake,
            "cancelled_misrouted": len(cancelled),
            "outcomes": outcomes,
        }
        logger.info(
            "聊天定向热点复扫完成：新增=%s，视频候选=%s，入库=%s，匹配=%s",
            fetched.get("new", 0), fetched.get("video_media", 0), intake.get("status"),
            sum(1 for row in outcomes if row["status"] == "matched"),
        )
        return report
    except Exception as exc:
        logger.exception("聊天定向热点复扫失败")
        for item in pending:
            db.update_hotspot_discovery_request(
                int(item["id"]),
                status="failed",
                stage="failed",
                error_message=f"补采失败：{str(exc)[:180]}",
            )
        return {"status": "failed", "error": str(exc)[:300], "cancelled_misrouted": len(cancelled)}


def _discovery_match_media_id_for_topic(topic: str) -> int | None:
    """Best-effort link from a discovery topic to a mother asset that already has Hooks."""
    tokens = [token for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", str(topic or "").casefold()) if token]
    if not tokens:
        return None
    with db.get_conn() as conn:
        for event in conn.execute(
            """SELECT e.asset_id, e.title_zh, e.title_en, e.evidence_json, e.keywords_json
               FROM hotspot_event_clips e
               WHERE e.review_status='confirmed'
               ORDER BY e.id DESC LIMIT 300"""
        ).fetchall():
            try:
                evidence = _json.loads(event["evidence_json"] or "{}")
            except Exception:
                evidence = {}
            try:
                keywords = _json.loads(event["keywords_json"] or "[]")
            except Exception:
                keywords = []
            blob = " ".join([
                str(event["title_zh"] or ""), str(event["title_en"] or ""),
                str(evidence.get("what_happened") or ""), str(evidence.get("event_identity") or ""),
                " ".join(str(item) for item in keywords),
            ]).casefold()
            if sum(1 for token in tokens if token in blob) >= max(1, min(2, len(tokens))):
                media = conn.execute(
                    "SELECT id FROM hotspot_media WHERE asset_id=? ORDER BY id DESC LIMIT 1",
                    (event["asset_id"],),
                ).fetchone()
                if media:
                    return int(media["id"])
    return None


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

        # 批次 2 覆盖缺口修复：定时发布不绕过差异化守卫（只拦不排程）
        if platform == "xiaohongshu":
            guard_account_id = account["id"] if account else item.get("target_account_id")
            guard_ok, guard_reason = xhs_diff_guard.check(item, db, guard_account_id)
            if not guard_ok:
                db.update_queue_status(item_id, item.get("status") or "queued", guard_reason)
                logger.warning("差异化守卫拦截定时发布: id=%d, %s", item_id, guard_reason)
                continue

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
            db.ensure_xhs_ledger(item_id)
            logger.info("定时发布成功: id=%d", item_id)
            await send_success_notify(item)
        else:
            detail = publisher.failure_status_detail(result)
            shot = publisher.debug_screenshot_from_error(result.get("error"))
            category = result.get("category")
            retry_count = db.get_retry_count(item_id)
            if retry_count < 3:
                db.increment_retry_count(item_id)
                db.update_queue_status(item_id, "queued", f"重试中 ({retry_count + 1}/3): {detail}")
                db.add_publish_log(
                    item_id, platform, item["title"], "retry", result.get("error"),
                    failure_category=category, debug_screenshot=shot,
                )
                logger.warning("定时发布失败，将重试: id=%d, retry=%d", item_id, retry_count + 1)
            else:
                db.update_queue_status(item_id, "failed", detail)
                db.add_publish_log(
                    item_id, platform, item["title"], "failed", result.get("error"),
                    failure_category=category, debug_screenshot=shot,
                )
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


async def fetch_hotspots_then_incremental_hook_intake():
    """6h fetch followed by bounded incremental Hook materialization."""
    admin = db.get_first_admin_user()
    fetched = await hotspot_fetcher.fetch_hotspots(
        static_dir=Path(__file__).with_name("static"),
        created_by=admin["id"] if admin else None,
        video_channels=hotspot_fetcher.configured_video_channels(),
    )
    # Prefer targeted discovery queue first, then a small incremental authorized batch.
    targeted = await refresh_targeted_hotspot_hooks()
    batch_size = max(1, int(os.environ.get("HOTSPOT_INCREMENTAL_HOOK_BATCH", "8")))
    selection = hotspot_intake_policy.select_incremental_media(
        [
            item for item in db.list_active_authorized_hotspot_media_for_full_intake()
            if item.get("media_kind") in {"video_link", "video_file"}
        ],
        batch_size,
    )
    incremental_ids = selection["selected_ids"]
    intake = {"status": "skipped"}
    if incremental_ids:
        intake = await prewarm_authorized_hotspot_media(media_ids=incremental_ids)
    confirmed_hooks = int((intake.get("summary") or {}).get("confirmed_hooks") or intake.get("confirmed_hooks") or 0)
    report = {
        "fetch": fetched,
        "targeted": targeted,
        "incremental": intake,
        "incremental_ids": incremental_ids,
        "candidate_count": selection["candidate_count"],
        "eligible_count": selection["eligible_count"],
        "skipped_count": selection["skipped_count"],
        "skipped_failed_ids": selection["skipped_failed_ids"],
        "failed_ids": [item.get("media_id") for item in failed],
        "failed_reasons": failed,
        "analyzed": intake.get("selected_media_ids") or [],
        "confirmed_hooks": confirmed_hooks,
        "official_publishers": selection.get("official_publishers") or [],
        "known_stuck_in_incremental": selection.get("known_stuck_in_incremental") or [],
    }
    logger.info(
        "抓取后增量 Hook 入库：candidates=%s eligible=%s skipped=%s skipped_failed=%s "
        "incremental=%s ids=%s failed=%s confirmed_hooks=%s official=%s fetch_new=%s",
        selection["candidate_count"],
        selection["eligible_count"],
        selection["skipped_count"],
        selection["skipped_failed_ids"][:12],
        intake.get("status"),
        incremental_ids,
        failed,
        confirmed_hooks,
        selection.get("official_publishers") or [],
        fetched.get("new"),
    )
    return report


async def cleanup_hotspot_hook_cycle():
    """每天检查 12 天周期 Hook 清理；门禁未过则只预览，不按年龄粗删。"""
    static_dir = os.environ.get("STATIC_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "static"
    )
    try:
        import hotspot_hook_cycle_cleanup as hook_cycle

        report = await asyncio.to_thread(
            hook_cycle.run_scheduled_hook_cycle_cleanup,
            static_dir,
        )
        status = report.get("status")
        alerts = (report.get("gates") or {}).get("alerts") or []
        if status == "blocked" or alerts:
            logger.warning(
                "Hook 周期清理未执行删除 status=%s alerts=%s qualified=%s",
                status,
                alerts,
                (report.get("gates") or {}).get("qualified_count"),
            )
        else:
            logger.info(
                "Hook 周期清理完成 status=%s deleted=%s dry_run=%s",
                status,
                report.get("deleted_count"),
                report.get("dry_run"),
            )
    except Exception:
        logger.exception("Hook 周期清理任务失败")


def _library_sync_next_run() -> datetime:
    """Restore the durable 3-day cycle; restarts must not push it another 3 days."""
    now = datetime.now()
    state = db.get_scheduler_job_state("hotspot_hook_library_sync") or {}
    raw = str(state.get("next_run_time") or "").strip()
    if raw:
        try:
            stored = datetime.fromisoformat(raw)
            if stored.tzinfo is not None:
                stored = stored.replace(tzinfo=None)
            if stored > now:
                return stored
            return now + timedelta(seconds=45)
        except ValueError:
            pass
    nxt = now + timedelta(days=3)
    db.upsert_scheduler_job_state(
        "hotspot_hook_library_sync",
        next_run_time=nxt.isoformat(timespec="seconds"),
    )
    return nxt


def _persist_library_sync_schedule(event):
    if getattr(event, "job_id", None) != "hotspot_hook_library_sync":
        return
    job = scheduler.get_job("hotspot_hook_library_sync")
    nxt = getattr(job, "next_run_time", None) if job else None
    if nxt is None:
        nxt = datetime.now() + timedelta(days=3)
    if getattr(nxt, "tzinfo", None) is not None:
        nxt = nxt.replace(tzinfo=None)
    db.upsert_scheduler_job_state(
        "hotspot_hook_library_sync",
        next_run_time=nxt.isoformat(timespec="seconds"),
        last_run_time=datetime.now().isoformat(timespec="seconds"),
    )


def start_scheduler():
    """启动定时调度器。"""
    # 每分钟检查一次定时发布任务（加 grace time 避免错过）
    scheduler.add_job(check_scheduled_publish, "interval", minutes=1, id="scheduled_publish", replace_existing=True, misfire_grace_time=120)
    scheduler.add_job(
        fetch_hotspots_then_incremental_hook_intake,
        "interval",
        hours=6,
        id="south_africa_hotspots",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
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
        misfire_grace_time=3600,  # 1 小时 grace time，避免连续错过导致任务失效
    )
    library_sync_next = _library_sync_next_run()
    scheduler.add_job(
        prewarm_authorized_hotspot_media,
        "interval",
        days=3,
        id="hotspot_hook_library_sync",
        next_run_time=library_sync_next,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_listener(_persist_library_sync_schedule, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    db.upsert_scheduler_job_state(
        "hotspot_hook_library_sync",
        next_run_time=library_sync_next.isoformat(timespec="seconds"),
    )
    # 批22：按 12 天完整周期检查清理，禁止用旧版 10 天年龄清理代替。
    scheduler.add_job(
        cleanup_hotspot_hook_cycle,
        "cron",
        hour=int(os.environ.get("HOTSPOT_HOOK_CLEANUP_HOUR", "3")),
        minute=int(os.environ.get("HOTSPOT_HOOK_CLEANUP_MINUTE", "15")),
        id="hotspot_hook_cycle_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    if topic_hook_pipeline.autofetch_enabled():
        pending_discovery = db.list_hotspot_discovery_requests(status="pending", limit=20)
        if pending_discovery:
            request_targeted_hotspot_refresh()
    logger.info(
        "定时调度器已启动（每分钟发布检查 + 三天热点预热 next_run=%s + 12 天 Hook 周期清理）",
        library_sync_next.isoformat(timespec="seconds"),
    )


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("定时调度器已停止")
