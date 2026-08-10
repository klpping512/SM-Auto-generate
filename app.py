"""SA-LogiFlow v3.0 - FastAPI Backend."""
import asyncio
import hashlib
import json as _json
import logging
import math
import os
import re
import threading
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from uuid import uuid4 as _uuid4
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request, Response, Depends, UploadFile, File, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import csv
import io

import database as db
import ai_engine
import chat_intent
import producible_topics
import publisher
import scheduler as sched
import wechat_article_generator
from scripts.create_article_draft import validate_package
from scripts.select_article_images import search_candidates, apply_selections
from scripts.render_article_package import render_package
from scripts.render_article_long_image import render_long_image
from xhs_cards import normalize_pages, pages_from_content, render_carousel
from xhs_quality_gate import check_before_render, check_rendered
from xhs_photo_match import pick_photos
import xhs_diff_guard
import media_assets
import video_renderer
import truth_guard
import hotspot_fetcher
import hotspot_content
import semantic_matching
import asset_processing
import inspiration_assets
import hotspot_media
import hotspot_event_matching
import hotspot_event_media
import hotspot_hook_curator
import media_retention
import video_generation
import local_asset_import
import evidence_harness
import model_router
import sample_harness
import hotspot_logistics_planner
import hotspot_video_planner
import hotspot_preview_narration
import hotspot_package_service
import hotspot_hook_selector
import hotspot_lexicon
import hotspot_video_sources
import video_quality.service as video_quality_service
import douyin_copywriting_sop
from auth import (
    hash_password,
    get_current_user, require_role,
)
import random
from models import (
    GenerateRequest, GenerateResponse,
    QueueCreateRequest, AccountCreateRequest, AccountCredentialsRequest, ReviewRequest, ChatRequest, ChatDualLibraryVideoRequest,
    UserRole, SemanticMatchRequest, MatchSelectionRequest, SegmentClassificationRequest,
    AssetClassifyAllRequest,
    InspirationCreateRequest, InspirationBatchRequest, InspirationRightsRequest,
    InspirationMaterializeRequest,
    HotspotMediaAttachRequest, HotspotMediaRightsRequest, HotspotMediaMaterializeRequest,
    HotspotLibraryClearRequest,
    BrandEvidenceCreateRequest, BrandEvidenceConfirmRequest,
    EvidencePackageCreateRequest,
    TopicBriefCreateRequest, TopicBriefUpdateRequest, TopicEvidenceSelectionRequest, TopicBriefGenerateRequest,
    TopicHotspotRecommendationRequest, TopicAutoPilotRequest,
    ModelRouteRequest, TtsPreviewRequest,
)
import publish_readiness
from routes import auth_routes, config_routes, hotspot_package_routes, page_routes, video_generation_routes, admin_router
from topic_library import TOPIC_CATEGORIES, TOPIC_MAP

# 从项目本地的 .env 加载敏感配置；.env 已加入 .gitignore，不会进入源码仓库。
load_dotenv(Path(__file__).with_name(".env"))

# ==================== Logging ====================
_LOG_DIR = Path(__file__).with_name("logs")
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_DIR / "app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _ensure_bootstrap_admin() -> dict | None:
    """Create the first admin from env when the user table is empty.

    Empty databases must provide BOOTSTRAP_ADMIN_USERNAME and
    BOOTSTRAP_ADMIN_PASSWORD. Hardcoded admin/admin123 is forbidden.
    """
    existing = db.get_users()
    if existing:
        admins = [u for u in existing if str(u.get("role") or "") == "admin" and str(u.get("status") or "active") == "active"]
        if admins:
            return db.get_user_by_username(admins[0]["username"]) or admins[0]
        return db.get_user_by_username(existing[0]["username"]) or existing[0]

    username = (os.environ.get("BOOTSTRAP_ADMIN_USERNAME") or "").strip()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or ""
    if not username or not password:
        raise RuntimeError(
            "空库首次启动必须设置 BOOTSTRAP_ADMIN_USERNAME 与 BOOTSTRAP_ADMIN_PASSWORD；"
            "已禁止自动创建 admin/admin123。"
        )
    if len(password) < 8:
        raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD 至少 8 位")
    display = (os.environ.get("BOOTSTRAP_ADMIN_DISPLAY_NAME") or "系统管理员").strip() or "系统管理员"
    db.create_user(username, hash_password(password), "admin", display)
    logger.info("已通过 Bootstrap 创建管理员: %s", username)
    return db.get_user_by_username(username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The organization confirmed that its configured feeds and channel links
    # are authorized.  Environment values still override these project defaults.
    os.environ.setdefault("HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED", "1")
    os.environ.setdefault("HOTSPOT_PREWARM_ENABLED", "1")
    db.init_db()
    repaired_manual_categories = db.sync_assets_to_manual_segment_categories()
    if repaired_manual_categories:
        logger.info("已同步 %s 个母片分类到人工确认的镜头主场景", repaired_manual_categories)
    recovered_fetch_runs = db.recover_interrupted_hotspot_fetch_runs()
    if recovered_fetch_runs:
        logger.warning("已恢复 %s 个被服务重启中断的热点抓取任务", recovered_fetch_runs)
    admin_user = _ensure_bootstrap_admin()
    seeded_sources = hotspot_fetcher.seed_default_sources(admin_user["id"] if admin_user else None)
    if seeded_sources:
        logger.info("已补齐 %s 个南非官方热点信源", seeded_sources)
    # TTS 单轨：旁白合成仅走 MiMo。
    os.environ.setdefault("TTS_PROVIDER", "mimo")
    mimo_key = os.environ.get("MIMO_API_KEY", "")
    if mimo_key:
        logger.info("MiMo API key 已加载（chat/planner/vision/tts）")
    else:
        logger.warning("未配置 MIMO_API_KEY：聊天与规划将不可用")
    # 清理卡住的渲染任务（启动时自动清理超时任务）
    video_renderer.cleanup_stale_jobs()
    recovered_asset_jobs = db.recover_interrupted_asset_processing_jobs()
    if recovered_asset_jobs:
        logger.warning("已恢复 %s 个被服务重启中断的素材处理任务", recovered_asset_jobs)
    # pending 任务在重启后不会自动消失；这里重新 create_task，避免排队半途作废。
    redispatched_pending = 0
    for pending_job_id in db.list_pending_asset_processing_job_ids():
        asyncio.create_task(_run_asset_processing_job(pending_job_id))
        redispatched_pending += 1
    if redispatched_pending:
        logger.info("已重新派发 %s 个排队中的素材处理任务", redispatched_pending)
    recovered_hook_curations = db.recover_retryable_hotspot_hook_curation()
    if recovered_hook_curations:
        logger.warning("已重新排队 %s 个因模型策展暂时不可用而未完成的热点母片", recovered_hook_curations)
    recovered_import_jobs = db.recover_interrupted_local_asset_import_jobs()
    if recovered_import_jobs:
        logger.warning("已标记 %s 个被服务重启中断的本地素材导入任务", recovered_import_jobs)
    # 聊天视频唯一状态机：video_generation_jobs（chat_video_tasks 已退役）。
    # 启动定时调度器
    sched.start_scheduler()
    video_worker_stop = asyncio.Event()
    video_worker_task = asyncio.create_task(
        video_generation.worker_loop(
            video_worker_stop,
            _build_video_generation_handlers(STATIC_DIR),
        )
    )

    async def _periodic_stale_cleanup():
        while True:
            try:
                video_renderer.cleanup_stale_jobs()
            except Exception:
                logger.exception("周期清理渲染任务失败")
            await asyncio.sleep(60)

    stale_cleanup_task = asyncio.create_task(_periodic_stale_cleanup())

    logger.info("SA-LogiFlow v3.0 启动完成 | 数据库: %s", db.DB_PATH)
    try:
        yield
    finally:
        video_worker_stop.set()
        await video_worker_task
        stale_cleanup_task.cancel()
        await asyncio.gather(stale_cleanup_task, return_exceptions=True)
        sched.stop_scheduler()
        logger.info("SA-LogiFlow v3.0 关闭")


app = FastAPI(title="SA-LogiFlow", version="3.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
app.include_router(auth_routes.router)
app.include_router(config_routes.router)
app.include_router(hotspot_package_routes.router)
app.include_router(page_routes.create_router(STATIC_DIR))
app.include_router(video_generation_routes.create_router(lambda: STATIC_DIR))
app.include_router(admin_router.router)

# 扫码登录会话仅用于本机单进程部署；前端据此获得明确的成功/超时/错误状态。
scan_login_sessions: dict[str, dict] = {}
# 手动发布会话：有头浏览器自动填好内容停在发布页，等用户人工点「发布」。
manual_publish_sessions: dict[str, dict] = {}
asset_processing_semaphore = asyncio.Semaphore(max(1, int(os.environ.get("MEDIA_ANALYSIS_CONCURRENCY", "1"))))
local_asset_import_tasks: set[asyncio.Task] = set()


def _normalized_hotspot_intake_decision(raw: object) -> dict:
    """Read legacy intake metadata without letting malformed JSON block curation."""
    try:
        parsed = _json.loads(str(raw or "{}"))
    except (TypeError, ValueError, _json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ==================== API: Dashboard ====================

@app.get("/api/dashboard")
async def dashboard(user=Depends(get_current_user)):
    stats = db.get_queue_stats(created_by=user["id"])
    accounts = db.get_accounts(owner_id=user["id"])
    recent = db.get_recent_activity(5, created_by=user["id"])
    weekly = db.get_weekly_stats(created_by=user["id"])

    active_count = sum(1 for a in accounts if a["status"] == "active")
    expired_count = sum(1 for a in accounts if a["status"] == "expired")

    platform_stats = {}
    for a in accounts:
        p = a["platform"]
        if p not in platform_stats:
            platform_stats[p] = {"total": 0, "active": 0}
        platform_stats[p]["total"] += 1
        if a["status"] == "active":
            platform_stats[p]["active"] += 1

    return {
        "queue_stats": stats,
        "weekly_stats": weekly,
        "account_total": len(accounts),
        "account_active": active_count,
        "account_expired": expired_count,
        "platform_stats": platform_stats,
        "recent_activity": recent,
        "team_performance": db.get_team_performance(None if user["role"] == UserRole.ADMIN.value else user["id"]),
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _render_xhs_carousel(title: str, pages: list | None, topic: str = "", category: str = ""):
    """分类配图 + 渲染；attachments 携带 asset_id（不足时全量兜底）。"""
    normalized = normalize_pages(title, pages)
    pool = pick_photos(db, STATIC_DIR, topic or title, category or "", len(normalized))
    return render_carousel(title, pages, STATIC_DIR, photo_pool=pool or None)


# ==================== API: Topics ====================

@app.get("/api/topics")
async def list_topics():
    return [cat.model_dump() for cat in TOPIC_CATEGORIES]


@app.get("/api/topics/{category_id}")
async def get_topic(category_id: str):
    cat = TOPIC_MAP.get(category_id)
    if not cat:
        raise HTTPException(404, f"Category '{category_id}' not found")
    return cat.model_dump()


# ==================== API: AI Generation ====================

@app.post("/api/generate", response_model=GenerateResponse)
async def generate_content(req: GenerateRequest, user=Depends(get_current_user)):
    # 引用企业知识库：按分类取文档全文，注入 prompt
    kb_context = db.get_kb_context(req.kb_category_ids) if req.kb_category_ids else ""

    if not ai_engine.chat_model_available():
        logger.warning("MiMo API key 未配置，使用 fallback 模板")
        contents = [ai_engine._fallback_content(p, req.topic, req.category) for p in req.platforms]
        for content in contents:
            if content.platform.value == "xiaohongshu":
                content.image_pages, content.attachments = _render_xhs_carousel(
                    content.title, content.image_pages, req.topic, req.category,
                )
                render_errors = check_rendered(content.image_pages, content.attachments, STATIC_DIR)
                if render_errors:
                    logger.error("小红书渲染完整性告警(fallback): %s", render_errors)
                    content.quality_warnings = list(content.quality_warnings or []) + [
                        f"渲染完整性: {e}" for e in render_errors
                    ]
        return GenerateResponse(topic=req.topic, contents=contents, generated_at=datetime.now().isoformat(), source="fallback")

    contents = await ai_engine.generate_content(
        topic=req.topic, category=req.category, platforms=req.platforms,
        tone=req.tone, length=req.length, instruction=req.instruction,
        kb_context=kb_context,
        assets=db.list_assets(status="active"),
    )
    for content in contents:
        if content.platform.value == "xiaohongshu":
            content.image_pages, content.attachments = _render_xhs_carousel(
                content.title, content.image_pages, req.topic, req.category,
            )
            render_errors = check_rendered(content.image_pages, content.attachments, STATIC_DIR)
            if render_errors:
                logger.error("小红书渲染完整性告警: %s", render_errors)
                content.quality_warnings = list(content.quality_warnings or []) + [
                    f"渲染完整性: {e}" for e in render_errors
                ]
    db.add_audit_log(user["id"], user["username"], "generate_content", target=req.topic)
    return GenerateResponse(topic=req.topic, contents=contents, generated_at=datetime.now().isoformat(), source="ai")


# ==================== API: Accounts ====================

@app.get("/api/accounts")
async def list_accounts(platform: str = None, user=Depends(get_current_user)):
    result = []
    for a in db.get_accounts(platform, owner_id=user["id"]):
        r = publish_readiness.readiness(a["platform"], a.get("credentials"))
        a.pop("credentials", None)  # 脱敏：不把凭据明文返回前端
        a["ready"] = r["ready"]
        a["missing"] = r["missing"]
        a["credential_kind"] = r["kind"]
        result.append(a)
    return result


def _account_for_user(account_id: int, user: dict) -> dict:
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if account.get("owner_id") != user["id"]:
        raise HTTPException(403, "不能操作其他用户的账号")
    return account


@app.post("/api/accounts")
async def create_account(req: AccountCreateRequest, user=Depends(get_current_user)):
    try:
        db.create_account(req.platform.value, req.name, req.account_id, req.config_summary, owner_id=user["id"])
        db.add_audit_log(user["id"], user["username"], "create_account", target=f"{req.platform.value}:{req.name}")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, user=Depends(get_current_user)):
    _account_for_user(account_id, user)
    db.delete_account(account_id)
    db.add_audit_log(user["id"], user["username"], "delete_account", target=str(account_id))
    return {"status": "ok"}


@app.put("/api/accounts/{account_id}/credentials")
async def set_account_credentials(
    account_id: int, req: AccountCredentialsRequest,
    user=Depends(get_current_user),
):
    acc = _account_for_user(account_id, user)
    db.update_account_credentials(acc["account_id"], _json.dumps(req.credentials, ensure_ascii=False))
    db.update_account_status(account_id, "active")  # 填了凭据即恢复可用
    db.add_audit_log(user["id"], user["username"], "set_credentials", target=f"{acc['platform']}:{acc['account_id']}")
    r = publish_readiness.readiness(acc["platform"], _json.dumps(req.credentials))
    return {"ok": True, "ready": r["ready"], "missing": r["missing"]}


async def _run_scan_login(account: dict, session_id: str):
    """后台：有头浏览器让用户扫码，轮询登录态 → 存 cookie → 置 active。本地单机场景。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter, browser_launch_options, build_credentials
    adapter = get_adapter(account["platform"])
    if not isinstance(adapter, RpaAdapter):
        scan_login_sessions[session_id] = {
            "status": "error", "error": "该平台不支持扫码登录",
            "account_id": account["id"], "platform": account["platform"],
        }
        return
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options(headless=False, use_proxy=getattr(adapter, "use_proxy", False)))
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(adapter.login_url, timeout=60000, wait_until="domcontentloaded")
                # wait_for_selector 会跨页面跳转继续等待，避免 query_selector 遇到重定向时
                # 抛出 "Execution context was destroyed"。
                await page.wait_for_selector(adapter._logged_in_selector(), timeout=180000)
                cookies = await context.cookies()
                if not cookies:
                    raise RuntimeError("已检测到登录页面，但未获取到 Cookie")
                db.update_account_credentials(account["account_id"], build_credentials(cookies))
                db.update_account_status(account["id"], "active")
                scan_login_sessions[session_id].update({"status": "success"})
                logger.info("扫码登录成功: %s", account["account_id"])
            finally:
                await browser.close()
    except Exception as exc:
        error_name = type(exc).__name__
        is_timeout = error_name in {"TimeoutError", "PlaywrightTimeoutError"} or "Timeout" in error_name
        status = "timeout" if is_timeout else "error"
        if status == "timeout":
            db.update_account_status(account["id"], "expired")
        scan_login_sessions[session_id].update({"status": status, "error": str(exc)})
        logger.exception("扫码登录异常: %s", account["account_id"])


@app.post("/api/accounts/{account_id}/scan-login")
async def scan_login(account_id: int, user=Depends(get_current_user)):
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    acc = _account_for_user(account_id, user)
    adapter = get_adapter(acc["platform"])
    if not isinstance(adapter, RpaAdapter):
        raise HTTPException(400, "该平台不使用扫码登录，请填写凭据")
    session_id = str(_uuid4())
    # 限制内存中的历史会话数量。
    if len(scan_login_sessions) >= 100:
        for old_id in list(scan_login_sessions)[:20]:
            scan_login_sessions.pop(old_id, None)
    scan_login_sessions[session_id] = {
        "status": "waiting", "account_id": acc["id"], "platform": acc["platform"],
    }
    asyncio.create_task(_run_scan_login(acc, session_id))
    db.add_audit_log(user["id"], user["username"], "scan_login", target=f"{acc['platform']}:{acc['account_id']}")
    return {"started": True, "session_id": session_id, "status": "waiting",
            "message": "已启动扫码登录，请在弹出的浏览器完成扫码"}


@app.get("/api/accounts/{account_id}/scan-login/{session_id}")
async def scan_login_status(account_id: int, session_id: str, user=Depends(get_current_user)):
    _account_for_user(account_id, user)
    session = scan_login_sessions.get(session_id)
    if not session or session.get("account_id") != account_id:
        raise HTTPException(404, "扫码登录会话不存在")
    return {"status": session["status"], "error": session.get("error")}


@app.post("/api/accounts/{account_id}/test-connection")
async def test_account_connection(account_id: int, user=Depends(get_current_user)):
    """测试账号可用性：cookie 平台实际打开浏览器校验登录态；token 平台校验字段完整。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    acc = _account_for_user(account_id, user)
    r = publish_readiness.readiness(acc["platform"], acc.get("credentials"))
    if not r["ready"]:
        db.update_account_status(account_id, "expired")
        return {"ok": False, "reason": "缺少凭据，请先连接/填写凭据", "missing": r["missing"]}
    adapter = get_adapter(acc["platform"])
    if isinstance(adapter, RpaAdapter):
        ok = await adapter.check_login(acc)
        db.update_account_status(account_id, "active" if ok else "expired")
        db.add_audit_log(user["id"], user["username"], "test_connection",
                         target=f"{acc['platform']}:{acc['account_id']}", detail="ok" if ok else "expired")
        return {"ok": ok, "reason": "" if ok else "cookie/登录已失效，请重新扫码登录"}
    # token 平台：字段完整即视为就绪（真实在线校验依赖各平台 API，暂不请求）
    db.add_audit_log(user["id"], user["username"], "test_connection",
                     target=f"{acc['platform']}:{acc['account_id']}")
    return {"ok": True, "reason": "凭据字段完整（token 平台未做在线校验）"}


# ==================== API: Queue ====================

def _queue_item_for_user(item_id: int, user: dict) -> dict:
    item = db.get_queue_item_by_id(item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    if item.get("created_by") != user["id"]:
        raise HTTPException(403, "不能操作其他用户的内容")
    return item

@app.get("/api/queue")
async def list_queue(status: str = None, platform: str = None, user=Depends(get_current_user)):
    items = db.get_queue(status, platform, created_by=user["id"])
    account_map = {account["id"]: account for account in db.get_accounts(owner_id=user["id"])}
    for item in items:
        account = account_map.get(item.get("target_account_id"))
        item["target_account_name"] = account["name"] if account else None
    return items


@app.post("/api/queue")
async def add_queue(req: QueueCreateRequest, user=Depends(get_current_user)):
    # editor 提交后自动进入 pending_review
    initial_status = req.status.value if req.status else "draft"
    if user["role"] == "editor" and initial_status == "draft":
        initial_status = "pending_review"

    verification = truth_guard.evaluate(req.title, req.body, req.source_refs)
    added = 0
    for platform in req.platforms:
        platform_attachments = req.attachments
        if platform.value == "xiaohongshu" and not any(a.get("type") == "image" for a in platform_attachments):
            _, platform_attachments = _render_xhs_carousel(
                req.title, pages_from_content(req.title, req.body), req.title, "",
            )
        if platform.value == "douyin" and not any(a.get("type") == "video" for a in platform_attachments):
            raise HTTPException(400, "抖音内容必须先生成或上传 MP4 视频")
        targets = req.account_targets.get(platform.value) or [None]
        for target_id in targets:
            if target_id is not None:
                account = _account_for_user(target_id, user)
                if account["platform"] != platform.value:
                    raise HTTPException(400, "目标账号与发布平台不匹配")
            db.add_to_queue(
                title=req.title, body=req.body, platform=platform.value,
                hashtags=req.hashtags, scheduled_at=req.scheduled_at,
                status=initial_status, created_by=user["id"],
                attachments=platform_attachments,
                source_refs=req.source_refs, verification_status=verification["status"],
                target_account_id=target_id,
                seo_meta=req.seo_meta if platform.value == "xiaohongshu" else None,
            )
            added += 1
    db.add_audit_log(user["id"], user["username"], "add_to_queue", target=req.title, detail=f"{added} account routes")
    return {"status": "ok", "added": added, "verification": verification}


@app.put("/api/queue/{item_id}/evidence")
async def update_evidence(item_id: int, body: dict, user=Depends(get_current_user)):
    item = _queue_item_for_user(item_id, user)
    source_refs = body.get("source_refs") if isinstance(body.get("source_refs"), list) else []
    verification = truth_guard.evaluate(item["title"], item["body"], source_refs)
    db.update_queue_evidence(item_id, source_refs, verification["status"])
    db.add_audit_log(user["id"], user["username"], "update_evidence", target=str(item_id), detail=verification["status"])
    return verification


@app.put("/api/queue/{item_id}/status")
async def update_status(item_id: int, body: dict, user=Depends(get_current_user)):
    item = _queue_item_for_user(item_id, user)
    if body.get("status") in {"queued", "published"}:
        error = truth_guard.publish_error(item)
        if error:
            raise HTTPException(409, error)
    scheduled_at = body.get("scheduled_at")
    if scheduled_at is not None:
        db.update_queue_status(item_id, body.get("status"), body.get("error_msg"), scheduled_at=scheduled_at)
    else:
        db.update_queue_status(item_id, body.get("status"), body.get("error_msg"))
    return {"status": "ok"}


@app.delete("/api/queue/{item_id}")
async def delete_queue_item(item_id: int, user=Depends(get_current_user)):
    _queue_item_for_user(item_id, user)
    db.delete_queue_item(item_id)
    db.add_audit_log(user["id"], user["username"], "delete_queue_item", target=str(item_id))
    return {"status": "ok"}


# ==================== API: Articles（公众号图文长文，阶段 0） ====================

def _article_for_user(article_id: int, user: dict) -> dict:
    article = db.get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    if user["role"] != "admin" and article.get("created_by") != user["id"]:
        raise HTTPException(403, "不能操作其他用户的内容")
    return article


def _decode_article_row(article: dict) -> dict:
    decoded = dict(article)
    defaults = {
        "materials_json": [],
        "generated_content_json": {},
        "evidence_footnotes_json": [],
        "unresolved_claims_json": [],
        "image_selections_json": {},
    }
    for field, fallback in defaults.items():
        raw = decoded.get(field)
        if raw:
            try:
                decoded[field] = _json.loads(raw)
            except (TypeError, ValueError):
                decoded[field] = fallback
        else:
            decoded[field] = fallback
    return decoded


@app.get("/api/articles")
async def list_articles(status: str = None, user=Depends(get_current_user)):
    articles = db.list_articles(status=status)
    if user["role"] != "admin":
        articles = [a for a in articles if a.get("created_by") == user["id"]]
    return [_decode_article_row(a) for a in articles]


@app.post("/api/articles")
async def create_article(req: dict, user=Depends(get_current_user)):
    package = {
        "slug": str(req.get("slug") or "").strip(),
        "title": str(req.get("title") or "").strip(),
        "topic_brief": str(req.get("topic_brief") or "").strip(),
        "reference_style": str(req.get("reference_style") or "").strip(),
        "materials": req.get("materials") if isinstance(req.get("materials"), list) else [],
    }
    errors = validate_package(package)
    if errors:
        return JSONResponse(status_code=422, content={"errors": errors})
    article_id = db.create_article(
        slug=package["slug"],
        title=package["title"],
        topic_brief=package["topic_brief"],
        materials_json=_json.dumps(package["materials"], ensure_ascii=False),
        reference_style=package["reference_style"],
        created_by=user["id"],
    )
    db.add_audit_log(user["id"], user["username"], "create_article", target=str(article_id), detail=package["slug"])
    return JSONResponse(status_code=201, content={"id": article_id})


@app.get("/api/articles/{article_id}")
async def get_article(article_id: int, user=Depends(get_current_user)):
    return _decode_article_row(_article_for_user(article_id, user))


@app.post("/api/articles/{article_id}/generate")
def generate_article(article_id: int, user=Depends(get_current_user)):
    # sync def：generate_article 内部用 asyncio.run（复用 hotspot_hook_curator 的既有写法），
    # async def 端点在运行中的 event loop 里调用会抛 asyncio.run() cannot be called from a running event loop。
    _article_for_user(article_id, user)
    result = wechat_article_generator.generate_article(article_id)
    db.add_audit_log(user["id"], user["username"], "generate_article", target=str(article_id),
                     detail=result.get("status"))
    return result


@app.get("/api/articles/{article_id}/image-candidates")
async def article_image_candidates(article_id: int, keyword: str = "", user=Depends(get_current_user)):
    _article_for_user(article_id, user)
    return search_candidates(keyword)


@app.post("/api/articles/{article_id}/images")
async def save_article_images(article_id: int, body: dict, user=Depends(get_current_user)):
    _article_for_user(article_id, user)
    selections = body.get("selections") if isinstance(body.get("selections"), dict) else {}
    return apply_selections(article_id, selections)


@app.post("/api/articles/{article_id}/render")
async def render_article(article_id: int, body: dict = None, user=Depends(get_current_user)):
    _article_for_user(article_id, user)
    body = body or {}
    force = bool(body.get("force"))
    fmt = str(body.get("format") or "md").strip().lower()
    try:
        width = int(body.get("width") or 750)
    except (TypeError, ValueError):
        return {"status": "error", "error": "width 必须是整数"}
    if fmt not in ("md", "longimg", "both"):
        return {"status": "error", "error": f"未知渲染格式 {fmt}（可选 md/longimg/both）"}
    if not (600 <= width <= 1200):
        return {"status": "error", "error": f"长图宽度需在 600–1200 之间，当前 {width}"}
    if fmt == "md":
        result = render_package(article_id, force=force)
    elif fmt == "longimg":
        result = render_long_image(article_id, width=width, force=force)
    else:  # both
        md_result = render_package(article_id, force=force)
        if md_result["status"] != "ok":
            result = md_result
        else:
            li_result = render_long_image(article_id, width=width, force=force)
            if li_result["status"] != "ok":
                result = li_result
            else:
                result = {**md_result, **li_result, "formats": ["md", "longimg"]}
    db.add_audit_log(user["id"], user["username"], "render_article", target=str(article_id),
                     detail=result.get("status"))
    return result


@app.post("/api/articles/{article_id}/publish")
async def publish_article(article_id: int, user=Depends(get_current_user)):
    article = _article_for_user(article_id, user)
    if article["status"] != "ready":
        raise HTTPException(409, f"只有 ready 才能发布（当前状态 {article['status']}）")
    db.update_article(article_id, status="published")
    db.add_audit_log(user["id"], user["username"], "publish_article", target=str(article_id))
    return {"status": "published"}


# ==================== API: Review (审批流) ====================

@app.get("/api/review/pending")
async def get_pending_reviews(user=Depends(require_role(UserRole.REVIEWER, UserRole.ADMIN))):
    items = db.get_queue(status="pending_review")
    return items


@app.post("/api/review/{item_id}")
async def review_item(item_id: int, req: ReviewRequest, user=Depends(require_role(UserRole.REVIEWER, UserRole.ADMIN))):
    item = db.get_queue_item_by_id(item_id)
    if not item:
        raise HTTPException(404, "内容不存在")
    if item["status"] != "pending_review":
        raise HTTPException(400, f"当前状态 '{item['status']}' 不允许审核")

    if req.action == "approve":
        error = truth_guard.publish_error(item)
        if error:
            raise HTTPException(409, error)
        db.update_queue_review(item_id, user["id"], "approved", req.note)
        # 自动加入发布队列
        db.update_queue_status(item_id, "queued")
        db.add_audit_log(user["id"], user["username"], "approve_content", target=str(item_id), detail=req.note)
    elif req.action == "reject":
        db.update_queue_review(item_id, user["id"], "rejected", req.note)
        db.add_audit_log(user["id"], user["username"], "reject_content", target=str(item_id), detail=req.note)
    else:
        raise HTTPException(400, "action 必须是 'approve' 或 'reject'")

    return {"status": "ok", "action": req.action}


# ==================== API: Publish ====================

@app.post("/api/xhs/render")
async def render_xhs_assets(body: dict, user=Depends(get_current_user)):
    title = str(body.get("title") or "小红书物流指南").strip()
    content = str(body.get("body") or "").strip()
    pages = body.get("image_pages") if isinstance(body.get("image_pages"), list) else []
    from_legacy = False
    if not pages:
        pages = pages_from_content(title, content)
        from_legacy = True  # legacy 文案不跑渲染前门禁（铁律 3）

    quality_warnings: list[str] = []
    if not from_legacy:
        gate = check_before_render(title, content, pages)
        quality_warnings.extend(gate.warnings)
        if gate.errors:
            logger.warning("小红书 /api/xhs/render 渲染前门禁拒绝: %s", gate.errors)
            return {
                "image_pages": pages,
                "attachments": [],
                "quality_warnings": quality_warnings + gate.errors,
                "render_warnings": [],
            }

    topic = str(body.get("topic") or title).strip()
    category = str(body.get("category") or "").strip()
    normalized, attachments = _render_xhs_carousel(title, pages, topic, category)
    render_warnings = check_rendered(normalized, attachments, STATIC_DIR)
    if render_warnings:
        logger.error("小红书 /api/xhs/render 渲染完整性告警: %s", render_warnings)
    return {
        "image_pages": normalized,
        "attachments": attachments,
        "quality_warnings": quality_warnings,
        "render_warnings": render_warnings,
    }


def _repair_xhs_queue_media(item: dict, attachments: list[dict]) -> list[dict]:
    if item["platform"] != "xiaohongshu" or any(a.get("type") == "image" for a in attachments):
        return attachments
    _, generated = _render_xhs_carousel(
        item["title"], pages_from_content(item["title"], item["body"]), item["title"], "",
    )
    db.update_queue_attachments(item["id"], generated)
    return generated


def _enforce_xhs_diff_guard(item: dict, account: dict | None) -> None:
    """差异化守卫：拦截返回 409，不消耗重试、不改 status。"""
    if item.get("platform") != "xiaohongshu":
        return
    account_id = account["id"] if account else item.get("target_account_id")
    ok, reason = xhs_diff_guard.check(item, db, account_id)
    if not ok:
        db.update_queue_status(item["id"], item.get("status") or "queued", reason)
        raise HTTPException(409, reason)


@app.post("/api/publish/{item_id}")
async def publish_item(item_id: int, user=Depends(get_current_user)):
    item = _queue_item_for_user(item_id, user)
    error = truth_guard.publish_error(item)
    if error:
        raise HTTPException(409, error)

    attachments = _json.loads(item.get('attachments') or '[]')
    attachments = _repair_xhs_queue_media(item, attachments)
    item = {**item, "attachments": attachments}
    images = [a['path'] for a in attachments if a.get('type') == 'image']
    video = next((a['path'] for a in attachments if a.get('type') == 'video'), None)
    account = _account_for_user(item["target_account_id"], user) if item.get("target_account_id") else None
    _enforce_xhs_diff_guard(item, account)
    result = await publisher.dispatch(
        platform=item["platform"], title=item["title"],
        content=item["body"], tags=item.get("hashtags", []),
        images=images if images else None, video=video, account=account, owner_id=user["id"],
    )

    if result["success"]:
        db.update_queue_status(item_id, "published")
        db.add_publish_log(item_id, item["platform"], item["title"], "published")
        db.ensure_xhs_ledger(item_id)
    else:
        detail = publisher.failure_status_detail(result)
        shot = publisher.debug_screenshot_from_error(result.get("error"))
        category = result.get("category")
        retry_count = db.get_retry_count(item_id)
        if retry_count < 3:
            db.increment_retry_count(item_id)
            db.update_queue_status(item_id, "queued", f"重试中 ({retry_count + 1}/3): {detail}")
            db.add_publish_log(
                item_id, item["platform"], item["title"], "retry", result.get("error"),
                failure_category=category, debug_screenshot=shot,
            )
        else:
            db.update_queue_status(item_id, "failed", detail)
            db.add_publish_log(
                item_id, item["platform"], item["title"], "failed", result.get("error"),
                failure_category=category, debug_screenshot=shot,
            )

    db.add_audit_log(user["id"], user["username"], "publish", target=f"{item_id}:{item['platform']}")
    return result


@app.post("/api/publish/batch")
async def publish_batch(body: dict, user=Depends(get_current_user)):
    item_ids = body.get("item_ids", [])
    if not item_ids:
        return {"results": []}

    results = []
    for item_id in item_ids:
        item = db.get_queue_item_by_id(item_id)
        if not item:
            results.append({"item_id": item_id, "success": False, "error": "Not found"})
            continue
        if item.get("created_by") != user["id"]:
            results.append({"item_id": item_id, "success": False, "error": "Forbidden"})
            continue
        error = truth_guard.publish_error(item)
        if error:
            results.append({"item_id": item_id, "success": False, "error": error})
            continue

        attachments = _json.loads(item.get('attachments') or '[]')
        attachments = _repair_xhs_queue_media(item, attachments)
        item = {**item, "attachments": attachments}
        images = [a['path'] for a in attachments if a.get('type') == 'image']
        video = next((a['path'] for a in attachments if a.get('type') == 'video'), None)
        account = _account_for_user(item["target_account_id"], user) if item.get("target_account_id") else None
        try:
            _enforce_xhs_diff_guard(item, account)
        except HTTPException as exc:
            results.append({"item_id": item_id, "success": False, "error": exc.detail})
            continue
        result = await publisher.dispatch(
            platform=item["platform"], title=item["title"],
            content=item["body"], tags=item.get("hashtags", []),
            images=images if images else None, video=video, account=account, owner_id=user["id"],
        )

        if result["success"]:
            db.update_queue_status(item_id, "published")
            db.add_publish_log(item_id, item["platform"], item["title"], "published")
            db.ensure_xhs_ledger(item_id)
        else:
            detail = publisher.failure_status_detail(result)
            shot = publisher.debug_screenshot_from_error(result.get("error"))
            category = result.get("category")
            retry_count = db.get_retry_count(item_id)
            if retry_count < 3:
                db.increment_retry_count(item_id)
                db.update_queue_status(item_id, "queued", f"重试中 ({retry_count + 1}/3): {detail}")
                db.add_publish_log(
                    item_id, item["platform"], item["title"], "retry", result.get("error"),
                    failure_category=category, debug_screenshot=shot,
                )
            else:
                db.update_queue_status(item_id, "failed", detail)
                db.add_publish_log(
                    item_id, item["platform"], item["title"], "failed", result.get("error"),
                    failure_category=category, debug_screenshot=shot,
                )

        results.append({"item_id": item_id, **result})

    logger.info("批量发布完成: %d 条", len(item_ids))
    return {"results": results}

async def _run_manual_publish(item: dict, session_id: str):
    """有头浏览器打开发布页、自动填好内容但不点发布；等用户手动发布后（页面跳离发布页）标记已发布。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter, browser_launch_options, parse_cookies
    adapter = get_adapter(item["platform"])
    if not isinstance(adapter, RpaAdapter) or not hasattr(adapter, "fill_publish_form"):
        manual_publish_sessions[session_id].update({"status": "error", "error": "该平台不支持手动发布"})
        return
    account = next((a for a in db.get_accounts(item["platform"], owner_id=item.get("created_by"))
                    if publish_readiness.readiness(item["platform"], a.get("credentials"))["ready"]), None)
    if not account:
        manual_publish_sessions[session_id].update({"status": "error", "error": "无可用账号，请先在「账号管理」登录"})
        return
    attachments = _json.loads(item.get("attachments") or "[]")
    attachments = _repair_xhs_queue_media(item, attachments)
    images, missing_images = publisher._resolve_uploaded_media(
        [a["path"] for a in attachments if a.get("type") == "image"]
    )
    if missing_images:
        manual_publish_sessions[session_id].update({
            "status": "error",
            "error": f"attachment_missing: 附件缺失: {missing_images}",
        })
        return
    video_path = next((a["path"] for a in attachments if a.get("type") == "video"), None)
    resolved_video = None
    if video_path:
        resolved_videos, missing_videos = publisher._resolve_uploaded_media([video_path])
        if missing_videos:
            manual_publish_sessions[session_id].update({
                "status": "error",
                "error": f"attachment_missing: 附件缺失: {missing_videos}",
            })
            return
        resolved_video = resolved_videos[0] if resolved_videos else None
    if item["platform"] == "xiaohongshu" and not images:
        manual_publish_sessions[session_id].update({"status": "error", "error": "小红书必须配图"})
        return
    if item["platform"] == "douyin" and not resolved_video:
        manual_publish_sessions[session_id].update({"status": "error", "error": "抖音必须有视频素材"})
        return
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(**browser_launch_options(headless=False, use_proxy=adapter.use_proxy))
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="zh-CN",
            permissions=[],  # 拒绝所有权限（位置信息等）
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            window.chrome = {runtime: {}};
        """)
        cookies = parse_cookies(account.get("credentials"))
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        _page_closed = {"flag": False}
        def _on_page_close():
            _page_closed["flag"] = True
            logger.warning("手动发布：页面已关闭 item=%s", item["id"])
        page.on("close", _on_page_close)
        try:
            await adapter.fill_publish_form(
                page, title=item["title"], content=item["body"],
                tags=item.get("hashtags") or [], images=images, video=resolved_video,
            )
            manual_publish_sessions[session_id].update({"status": "ready"})
            while True:
                await asyncio.sleep(3)
                if _page_closed["flag"]:
                    break
            logger.info("手动发布：浏览器已关闭 item=%s", item["id"])
            manual_publish_sessions[session_id].update({"status": "closed"})
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass
    except Exception as exc:
        logger.exception("手动发布异常")
        manual_publish_sessions[session_id].update({"status": "error", "error": str(exc)})


@app.post("/api/publish/{item_id}/manual")
async def manual_publish(item_id: int, user=Depends(get_current_user)):
    item = _queue_item_for_user(item_id, user)
    error = truth_guard.publish_error(item)
    if error:
        raise HTTPException(409, error)
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    if not isinstance(get_adapter(item["platform"]), RpaAdapter):
        raise HTTPException(400, "该平台不支持手动发布（仅小红书/抖音等浏览器发布平台）")
    if len(manual_publish_sessions) >= 50:
        for old_id in list(manual_publish_sessions)[:20]:
            manual_publish_sessions.pop(old_id, None)
    session_id = str(_uuid4())
    manual_publish_sessions[session_id] = {
        "status": "starting", "item_id": item_id, "user_id": user["id"],
    }
    asyncio.create_task(_run_manual_publish(item, session_id))
    db.add_audit_log(user["id"], user["username"], "manual_publish", target=str(item_id))
    return {"started": True, "session_id": session_id}


@app.get("/api/publish/manual/{session_id}")
async def manual_publish_status(session_id: str, user=Depends(get_current_user)):
    s = manual_publish_sessions.get(session_id)
    if not s or s.get("user_id") != user["id"]:
        raise HTTPException(404, "手动发布会话不存在")
    return {"status": s["status"], "error": s.get("error")}


@app.get("/api/publish/status")
async def publish_status():
    from adapters import ADAPTERS
    return {
        "adapters": {p: type(a).__name__ for p, a in ADAPTERS.items()},
        "supported_platforms": list(ADAPTERS.keys()),
    }


@app.get("/api/publish/accounts")
async def publish_accounts():
    return await publisher.list_huimei_accounts()


# ==================== API: Audit Logs ====================

@app.get("/api/audit-logs")
async def get_audit_logs(limit: int = 100, user=Depends(require_role(UserRole.ADMIN))):
    return db.get_audit_logs(limit)


@app.get("/api/publish/logs")
async def get_publish_logs(limit: int = 50, user=Depends(get_current_user)):
    created_by = None if user["role"] == UserRole.ADMIN.value else user["id"]
    return db.get_publish_logs(limit, created_by=created_by)


# ==================== API: 小红书发布台账 ====================

def _xhs_ledger_owner_filter(user: dict) -> int | None:
    return None if user["role"] == UserRole.ADMIN.value else user["id"]


def _build_xhs_ledger_csv(from_date: str, to_date: str) -> str:
    summary = db.weekly_xhs_ledger_summary(from_date, to_date)
    ov = summary["overview"]
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["# 概览"])
    writer.writerow(["周区间", "发布条数", "达标数", "达标率", "平均阅读", "平均互动率"])
    writer.writerow([
        f"{from_date}~{to_date}",
        ov["count"],
        ov["passed"],
        f"{ov['pass_rate']:.2%}",
        f"{ov['avg_reads']:.1f}",
        f"{ov['avg_interaction_rate']:.4f}",
    ])
    writer.writerow([])

    headers = ["rank", "标题", "选题级别", "封面类型", "主词", "阅读", "赞藏", "评论", "涨粉", "48h 判定"]
    writer.writerow(["# Top 选题（按阅读）"])
    writer.writerow(headers)
    for i, row in enumerate(summary["top"], 1):
        writer.writerow([
            i, row["title"], row["topic_level"], row["cover_type"], row["main"],
            row["reads"], row["likes_saves"], row["comments"], row["followers_gained"],
            row["verdict_48h"],
        ])
    writer.writerow([])

    writer.writerow(["# Bottom 选题（按阅读）"])
    writer.writerow(headers)
    for i, row in enumerate(summary["bottom"], 1):
        writer.writerow([
            i, row["title"], row["topic_level"], row["cover_type"], row["main"],
            row["reads"], row["likes_saves"], row["comments"], row["followers_gained"],
            row["verdict_48h"],
        ])
    writer.writerow([])

    writer.writerow(["# 封面类型分布"])
    writer.writerow(["封面类型", "条数", "平均阅读", "平均赞藏"])
    for row in summary["cover_dist"]:
        writer.writerow([
            row["cover_type"], row["count"],
            f"{row['avg_reads']:.1f}", f"{row['avg_likes_saves']:.1f}",
        ])
    writer.writerow([])

    writer.writerow(["# 关键词表现"])
    writer.writerow(["主词", "条数", "平均阅读", "平均互动率"])
    for row in summary["keyword_perf"]:
        writer.writerow([
            row["main"], row["count"],
            f"{row['avg_reads']:.1f}", f"{row['avg_interaction_rate']:.4f}",
        ])
    return buf.getvalue()


@app.get("/api/xhs/ledger/candidates")
async def xhs_ledger_candidates(user=Depends(get_current_user)):
    return db.list_xhs_ledger_candidates()


@app.get("/api/xhs/ledger/export")
async def xhs_ledger_export(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    user=Depends(get_current_user),
):
    if not from_date or not to_date:
        week_start, week_end = db.utc_week_range()
        from_date = from_date or week_start
        to_date = to_date or week_end
    csv_text = _build_xhs_ledger_csv(from_date, to_date)
    filename = f"xhs-ledger-week-{from_date}-{to_date}.csv"
    return StreamingResponse(
        iter([csv_text.encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/xhs/ledger")
async def xhs_ledger_list(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    user=Depends(get_current_user),
):
    return db.list_xhs_ledger(
        from_date=from_date or None,
        to_date=to_date or None,
        created_by=_xhs_ledger_owner_filter(user),
    )


@app.post("/api/xhs/ledger")
async def xhs_ledger_create(body: dict, user=Depends(get_current_user)):
    """历史补建：正常路径是发布自动预建 → PUT 填指标。"""
    queue_id = body.get("queue_id")
    if not queue_id:
        raise HTTPException(400, "queue_id 必填")
    item = db.get_queue_item_by_id(int(queue_id))
    if not item:
        raise HTTPException(404, "队列条目不存在")
    if item.get("platform") != "xiaohongshu":
        raise HTTPException(400, "仅支持小红书条目建档")
    if db.get_xhs_ledger_by_queue(int(queue_id)):
        raise HTTPException(409, "该条目已建档")
    # 必须有 published 日志（ensure 内部也会校验）
    with db.get_conn() as conn:
        published = conn.execute(
            "SELECT 1 FROM publish_log WHERE queue_id=? AND status='published' AND platform='xiaohongshu' LIMIT 1",
            (int(queue_id),),
        ).fetchone()
    if not published:
        raise HTTPException(400, "未发布条目不许建档")

    ledger_id = db.ensure_xhs_ledger(int(queue_id))
    if not ledger_id:
        raise HTTPException(400, "建档失败：条目不符合条件")
    fields = {k: body.get(k) for k in (
        "topic_level", "cover_type", "reads", "likes_saves", "comments",
        "followers_gained", "verdict_48h", "notes",
    ) if k in body}
    if fields:
        try:
            db.update_xhs_ledger(ledger_id, fields)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    row = db.get_xhs_ledger(ledger_id)
    db.add_audit_log(user["id"], user["username"], "xhs_ledger_create", target=str(queue_id))
    return row


@app.put("/api/xhs/ledger/{ledger_id}")
async def xhs_ledger_update(ledger_id: int, body: dict, user=Depends(get_current_user)):
    row = db.get_xhs_ledger(ledger_id)
    if not row:
        raise HTTPException(404, "台账行不存在")
    if user["role"] != UserRole.ADMIN.value and row.get("created_by") != user["id"]:
        raise HTTPException(403, "不能编辑其他用户的台账")
    fields = {k: body.get(k) for k in (
        "topic_level", "cover_type", "reads", "likes_saves", "comments",
        "followers_gained", "verdict_48h", "notes",
    ) if k in body}
    try:
        db.update_xhs_ledger(ledger_id, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.add_audit_log(user["id"], user["username"], "xhs_ledger_update", target=str(ledger_id))
    return {"status": "ok"}


# ==================== API: Knowledge Base ====================

@app.get("/api/kb/categories")
async def kb_list_categories(user=Depends(get_current_user)):
    return db.get_kb_categories()


@app.post("/api/kb/categories")
async def kb_create_category(body: dict, user=Depends(get_current_user)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "分类名称不能为空")
    try:
        cat_id = db.create_kb_category(name, body.get("description", ""))
    except Exception:
        raise HTTPException(400, "分类已存在")
    db.add_audit_log(user["id"], user["username"], "kb_create_category", target=name)
    return {"status": "ok", "id": cat_id}


@app.delete("/api/kb/categories/{cat_id}")
async def kb_delete_category(cat_id: int, user=Depends(require_role(UserRole.ADMIN, UserRole.EDITOR))):
    db.delete_kb_category(cat_id)
    db.add_audit_log(user["id"], user["username"], "kb_delete_category", target=str(cat_id))
    return {"status": "ok"}


@app.get("/api/kb/documents")
async def kb_list_documents(category_id: int = None, user=Depends(get_current_user)):
    return db.get_kb_documents(category_id)


@app.get("/api/kb/documents/{doc_id}")
async def kb_get_document(doc_id: int, user=Depends(get_current_user)):
    doc = db.get_kb_document(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    return doc


@app.post("/api/kb/documents")
async def kb_create_document(body: dict, user=Depends(get_current_user)):
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    category_id = body.get("category_id")
    if not title or not content:
        raise HTTPException(400, "标题和内容不能为空")
    doc_id = db.create_kb_document(category_id, title, content, body.get("source_type", "text"), user["id"])
    db.add_audit_log(user["id"], user["username"], "kb_create_document", target=title)
    return {"status": "ok", "id": doc_id}


@app.put("/api/kb/documents/{doc_id}")
async def kb_update_document(doc_id: int, body: dict, user=Depends(get_current_user)):
    if not db.get_kb_document(doc_id):
        raise HTTPException(404, "文档不存在")
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "标题和内容不能为空")
    db.update_kb_document(doc_id, title, content, body.get("category_id"))
    db.add_audit_log(user["id"], user["username"], "kb_update_document", target=title)
    return {"status": "ok"}


@app.delete("/api/kb/documents/{doc_id}")
async def kb_delete_document(doc_id: int, user=Depends(get_current_user)):
    db.delete_kb_document(doc_id)
    db.add_audit_log(user["id"], user["username"], "kb_delete_document", target=str(doc_id))
    return {"status": "ok"}


@app.post("/api/kb/upload")
async def kb_upload(file: UploadFile = File(...), user=Depends(get_current_user)):
    """上传 TXT/MD 文件，解析为纯文本返回（前端再决定存哪个分类）。"""
    name = file.filename or "未命名"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ("txt", "md", "markdown"):
        raise HTTPException(400, "仅支持 TXT / Markdown 文件")
    raw = await file.read()
    if len(raw) > 1024 * 1024:  # 1MB 上限
        raise HTTPException(400, "文件过大（上限 1MB）")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(400, "无法识别文件编码，请用 UTF-8")
    title = name.rsplit(".", 1)[0]
    return {"status": "ok", "title": title, "content": text, "source_type": ext}


# ==================== API: File Upload ====================

ALLOWED_IMAGE = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
ALLOWED_VIDEO = {'video/mp4', 'video/quicktime', 'video/webm'}
ALLOWED_EXTENSIONS = {
    'image/jpeg': {'.jpg', '.jpeg'}, 'image/png': {'.png'},
    'image/gif': {'.gif'}, 'image/webp': {'.webp'},
    'video/mp4': {'.mp4'}, 'video/quicktime': {'.mov'},
    'video/webm': {'.webm'},
}
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10MB
MAX_VIDEO_SIZE = 50 * 1024 * 1024   # 50MB


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    if file.content_type not in ALLOWED_IMAGE | ALLOWED_VIDEO:
        raise HTTPException(400, "不支持的文件类型，仅接受图片(JPEG/PNG/GIF/WebP)和视频(MP4/MOV/WebM)")
    original_name = file.filename or ''
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS[file.content_type]:
        raise HTTPException(400, "文件扩展名与文件类型不匹配")
    content = await file.read()
    if not content:
        raise HTTPException(400, "文件内容为空")
    max_size = MAX_VIDEO_SIZE if file.content_type.startswith('video') else MAX_IMAGE_SIZE
    if len(content) > max_size:
        raise HTTPException(400, f"文件过大（最大 {max_size // (1024*1024)}MB）")
    file_type = 'image' if file.content_type.startswith('image') else 'video'
    filename = f"{_uuid4().hex}{ext}"
    filepath = f"uploads/{file_type}/{filename}"
    save_dir = STATIC_DIR / filepath
    save_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(save_dir, 'wb') as f:
        f.write(content)
    return {
        'url': f'/static/{filepath}',
        'path': filepath,
        'type': file_type,
        'filename': file.filename,
        'size': len(content),
    }


# ==================== API: Media Assets ====================

@app.get("/api/assets")
async def list_media_assets(type: str = None, category: str = None, query: str = None, status: str = "active", user=Depends(get_current_user)):
    items = db.list_assets(type, category, query, status)
    asset_ids = [item["id"] for item in items]
    brand_tags = db.list_asset_brand_tags(asset_ids)
    segment_counts = db.list_asset_segment_counts(asset_ids)
    result = []
    for item in items:
        public = media_assets.public_asset(item)
        public["brand_tags"] = brand_tags.get(int(item["id"]), [])
        public["segment_count"] = segment_counts.get(int(item["id"]), 0)
        result.append(public)
    return result


@app.get("/api/asset-segments")
async def list_asset_segments(asset_id: int = None, query: str = "", limit: int = 200,
                              user=Depends(get_current_user)):
    if query.strip():
        return db.search_asset_segments(query.strip(), limit=limit)
    return db.list_asset_segments(asset_id=asset_id, limit=limit)


def _is_confirmed_renderable_hotspot_hook(event: dict) -> bool:
    """Only expose Hooks with verified fact, logistics bridge and local proxy.

    This legacy safety net deliberately rejects public-affairs footage that can
    mention a generic "delivery" question but sits outside Buffalo's confirmed
    ecommerce, cross-border, warehousing and last-mile scope.  Downloaded media
    are curated by the built-in model after analysis; the local gate keeps older,
    pre-SOP rows from silently re-entering the usable Hook library.
    """
    evidence = event.get("evidence") or {}
    required = ("what_happened", "hook_reason", "logistics_question")
    values = [str(evidence.get(key) or "").strip() for key in required]
    placeholders = ("未记录", "待确认", "unknown", "n/a")
    event_text = " ".join((
        str(event.get("title_zh") or ""), str(event.get("title_en") or ""),
        str(evidence.get("what_happened") or ""), str(evidence.get("logistics_question") or ""),
    )).casefold()
    out_of_scope = (
        # 原有基线
        "市政", "环卫", "垃圾", "污水", "供水", "管道破裂", "公园", "野生动物",
        "治安", "犯罪", "政治", "委员会", "证词", "娱乐",
        "municipal", "refuse", "waste", "sewage", "wildlife", "testimony", "commission",
        # 2026-08-06 扩词：听证/法庭/选举/体育/社会等 35 条漏网（门禁只挡委员会/证词/政治）
        "听证", "法庭", "法院", "出庭", "受审", "庭审", "作证",
        "议员", "部长", "总统", "总理", "选举", "大选", "选情", "政客",
        "峰会", "部长级", "非盟",
        "足球", "联赛", "世界杯", "球队", "女足", "决赛", "赛事", "橄榄球", "板球", "网球",
        "教育", "学校", "学生", "校园",
        "医疗", "医院", "手术", "艾滋", "hiv", "器官", "捐献", "疫苗",
        "庆典", "周年", "颁奖", "发布会", "品牌",
        "监狱", "囚犯", "杀手", "遇害", "谋杀",
        "难民", "移民",
        "住宅火灾",
    )
    # A legacy Hook can have a real oil-price frame but still carry an old,
    # unsupported bridge such as “South African transport costs have already
    # risen”.  Keep the factual frame out of the usable library until it is
    # re-curated with a cautious RAG-supported question.
    unsupported_cost_leap = bool(
        any(term in event_text for term in ("红海", "国际油价", "red sea", "oil price"))
        and any(term in event_text for term in ("同步攀升", "成本已", "运费已", "costs have risen"))
    )
    return bool(
        str(event.get("review_status") or "") == "confirmed"
        and str(event.get("clip_status") or "") == "ready"
        and str(event.get("clip_path") or "").strip()
        and all(value and not value.casefold().startswith(placeholders) for value in values)
        and not any(term in event_text for term in out_of_scope)
        and not unsupported_cost_leap
    )


@app.get("/api/hotspot-events")
async def list_hotspot_events(asset_id: int | None = None, hotspot_id: int | None = None,
                              eligible_only: bool = True, user=Depends(get_current_user)):
    events = db.list_hotspot_event_clips(asset_id=asset_id, hotspot_id=hotspot_id)
    if eligible_only:
        events = [event for event in events if _is_confirmed_renderable_hotspot_hook(event)]
    segments = db.list_asset_segments(limit=20_000)
    for event in events:
        _decorate_hotspot_event(event, segments)
    return events


@app.post("/api/hotspot-events/cleanup-ineligible")
async def cleanup_ineligible_hotspot_events(user=Depends(require_role(UserRole.ADMIN))):
    """Remove legacy Hook rows that cannot state a fact, a Hook reason and a logistics bridge."""
    events = db.list_hotspot_event_clips()
    invalid_ids = [int(event["id"]) for event in events if not _is_confirmed_renderable_hotspot_hook(event)]
    result = db.delete_hotspot_event_clips(invalid_ids)
    file_result = _delete_hotspot_library_files(result.pop("file_paths"))
    db.add_audit_log(
        user["id"], user["username"], "cleanup_ineligible_hotspot_events",
        target="hotspot-hook-library", detail=_json.dumps({**result, **file_result}, ensure_ascii=False),
    )
    return {"status": "cleaned", **result, **file_result}


def _decorate_hotspot_event(event: dict, segments: list[dict] | None = None) -> dict:
    """Expose a hotspot event as a previewable virtual asset without copying its mother video."""
    asset = db.get_asset(int(event["asset_id"])) or {}
    public = media_assets.public_asset(asset) if asset else {}
    # 批17：卡片时效徽标取父热点真实发布时间（RSS 为 RFC2822 / YouTube 经回填为 ISO）
    parent = db.get_hotspot(int(event["hotspot_id"])) if event.get("hotspot_id") else {}
    event["published_at"] = (parent or {}).get("published_at")
    start_second = max(0, int(event.get("start_ms") or 0)) / 1000
    end_second = max(start_second, int(event.get("end_ms") or 0) / 1000)
    thumbnail = (
        "/static/" + event["thumbnail_path"]
        if event.get("thumbnail_path")
        else public.get("thumbnail_url")
    )
    has_proxy = event.get("clip_status") == "ready" and event.get("clip_path")
    preview_url = "/static/" + event["clip_path"] if has_proxy else public.get("url")
    preview_start = 0 if has_proxy else start_second
    preview_end = (int(event.get("duration_ms") or 0) / 1000) if has_proxy else end_second
    event["matches"] = hotspot_event_matching.match_event(event, segments or db.list_asset_segments(limit=20_000))
    event["virtual_asset"] = {
        "id": event["virtual_asset_id"],
        "name": event["title_zh"],
        "title_en": event["title_en"],
        "library_origin": "hotspot_event",
        "duration_ms": event["duration_ms"],
        "thumbnail_url": thumbnail,
        "preview_url": preview_url,
        "preview_start_second": preview_start,
        "preview_end_second": preview_end,
        "preview_status": "ready" if has_proxy else ("mother_fallback" if public.get("url") else "unavailable"),
        "clip_status": event.get("clip_status") or "pending",
        "clip_error": event.get("clip_error"),
        "source_asset_id": event["asset_id"],
        "source_label": public.get("source_label") or "",
    }
    return event


@app.get("/api/hotspot-events/{event_id}")
async def get_hotspot_event(event_id: int, user=Depends(get_current_user)):
    event = db.get_hotspot_event_clip(event_id)
    if not event:
        raise HTTPException(404, "热点事件不存在")
    if event.get("clip_status") != "ready":
        asset = db.get_asset(int(event["asset_id"]))
        if asset and asset.get("file_type") == "video":
            try:
                await asyncio.to_thread(hotspot_event_media.materialize_event_clip, STATIC_DIR, asset, event)
                event = db.get_hotspot_event_clip(event_id) or event
            except Exception as exc:
                db.update_hotspot_event_clip_media(event_id, None, event.get("thumbnail_path"), "failed", str(exc)[:300])
                event = db.get_hotspot_event_clip(event_id) or event
    return _decorate_hotspot_event(event)


@app.get("/api/hotspot-events/{event_id}/matches")
async def get_hotspot_event_matches(event_id: int, user=Depends(get_current_user)):
    events = db.list_hotspot_event_clips()
    event = next((item for item in events if item["id"] == event_id), None)
    if not event:
        raise HTTPException(404, "热点事件不存在")
    return hotspot_event_matching.match_event(event, db.list_asset_segments(limit=20_000))


def _topic_keywords(value: str) -> list[str]:
    """Small deterministic parser: do not spend a model call on basic topic extraction."""
    return hotspot_lexicon.topic_keyword_hits(value)


_CHAT_HOOK_BROAD_TERMS = hotspot_lexicon.BROAD_TERMS


def _chat_hook_topic_profile(topic_text: str) -> set[str]:
    return hotspot_lexicon.category_profile(topic_text, mode="topic")


def _chat_hook_event_profile(fact_text: str) -> set[str]:
    # Deliberately grounded only in verified visual/fact evidence, not the
    # curator's model-authored "logistics_question" bridge — that text is
    # forced onto every event regardless of true relevance, so letting it
    # feed category tagging created false-positive overlap against almost
    # any topic (e.g. a pure road-congestion event tagged "warehouse"
    # because its bridge sentence happened to mention overseas-warehouse
    # intake).
    return hotspot_lexicon.category_profile(fact_text, mode="event")


def _build_topic_brief_payload(body: TopicBriefCreateRequest) -> dict:
    raw = body.raw_input.strip()
    keywords = _topic_keywords(raw)
    # The user's natural-language request is the narrative contract.  A list
    # of extracted logistics nodes is only for media retrieval; using it as
    # the subject flattened requests such as a Takealot experience video into
    # merely "配送", after which the Hook headline could take over the video.
    subject = "南非物流" if "南非" in keywords and "物流" in keywords and len(raw) <= 8 else raw[:300]
    nodes = list(dict.fromkeys(body.logistics_nodes + [item for item in keywords if item in {"清关", "末端", "配送", "仓储", "港口", "运输"}]))
    broad = len(raw) <= 8 or raw.rstrip("？?。.") in {"南非物流", "物流", "跨境物流"}
    angle = body.angle.strip()
    if not angle and not broad:
        focus = "、".join(nodes) if nodes else subject
        angle = f"从{focus}说明进入南非市场前需要核对的流程、风险与准备。"
    return {
        **body.model_dump(), "subject": subject, "logistics_nodes": nodes,
        "angle": angle, "status": "angle_only" if broad else "draft",
    }


_TOPIC_ANCHOR_CONTRACTS = (
    {
        "terms": ("takealot",),
        "title": ("takealot",),
        "title_all": ("库存", "配送", "体验"),
        "narrative": ("库存", "配送", "用户体验", "履约"),
        "label": "Takealot 的库存、配送和用户体验",
    },
    {
        "terms": ("海外仓", "本地团队"),
        "title": ("海外仓", "本地团队"),
        "narrative": ("海外仓", "本地团队", "仓内", "仓储"),
        "label": "海外仓是本地团队",
    },
    {
        "terms": ("低价货代", "物流最容易亏钱", "亏钱的4个坑"),
        "title": ("低价货代", "货代", "成本", "亏钱"),
        "narrative": ("低价货代", "货代", "成本", "亏钱", "风险"),
        "label": "低价货代的成本与风险",
    },
    {
        "terms": ("备用供应链", "突发延误", "全盘停摆"),
        "title": ("备用供应链", "供应链", "延误", "备用方案"),
        "narrative": ("备用供应链", "供应链", "延误", "备用", "预案"),
        "label": "突发延误下的备用供应链",
    },
)


def _topic_anchor_contract(topic: str) -> dict | None:
    """Return a narrow, explicit topic contract when a request names one.

    It deliberately does not try to perform generic semantic scoring locally.
    The model is free to phrase normal topics naturally, while named C-end
    propositions must survive the Hotspot Hook opening verbatim or by a clear
    supplied synonym.
    """
    lowered = str(topic or "").casefold()
    for contract in _TOPIC_ANCHOR_CONTRACTS:
        if any(term.casefold() in lowered for term in contract["terms"]):
            return contract
    return None


def _validate_generated_topic_anchor(generated: dict, brief: dict) -> None:
    """Reject a fluent but off-topic script before it becomes a project."""
    contract = _topic_anchor_contract(str(brief.get("raw_input") or brief.get("subject") or ""))
    if not contract:
        return
    title = str(generated.get("title") or "").casefold()
    voiceovers = " ".join(
        str(scene.get("voiceover") or "") for scene in generated.get("scenes") or []
    ).casefold()
    if not any(term.casefold() in title for term in contract["title"]):
        raise ValueError(f"内容规划模型标题没有回应用户主题：{contract['label']}")
    required_title_terms = contract.get("title_all") or ()
    if any(term.casefold() not in title for term in required_title_terms):
        raise ValueError(f"内容规划模型标题没有完整回应用户主题：{contract['label']}")
    if not any(term.casefold() in voiceovers for term in contract["narrative"]):
        raise ValueError(f"内容规划模型旁白没有展开用户主题：{contract['label']}")


_EMPTY_FORMAL_COPY = ("先核对清单", "配送节奏要稳", "库存要对得上")


def _validate_formal_copy_specificity(generated: dict) -> None:
    """Keep a valid-length draft from becoming a sequence of empty slogans."""
    for index, scene in enumerate(generated.get("scenes") or [], 1):
        voiceover = str(scene.get("voiceover") or "").strip()
        if any(phrase in voiceover for phrase in _EMPTY_FORMAL_COPY):
            raise ValueError(f"内容规划模型第 {index} 个分镜使用了脱离画面的空泛短句")
        if "请核对" in voiceover:
            raise ValueError(f"内容规划模型第 {index} 个分镜使用了“请核对”模板句")


def _retrieve_topic_evidence(brief: dict) -> tuple[list[dict], dict]:
    """Return bounded, role-separated candidates.  This only stores references, never copies media."""
    terms = _topic_keywords(" ".join([brief.get("raw_input", ""), brief.get("subject", ""), brief.get("angle", "")]))
    lowered = " ".join(terms).casefold()
    facts, media, owned, brand = [], [], [], []
    for hotspot in db.list_hotspots(limit=100):
        text = " ".join(str(hotspot.get(key) or "") for key in ("title", "title_zh", "summary", "summary_zh", "event_type", "locations_json")).casefold()
        score = sum(1 for term in terms if term.casefold() in text)
        if score:
            facts.append({"evidence_type": "fact", "source_id": hotspot["id"], "content_role": "fact_context", "relevance_score": score * 20, "match_reason": "主题词与热点事实相符", "rights_status": "traceable"})
    for item in db.list_hotspot_media(lifecycle_status="active"):
        if str(item.get("authorization_status") or "pending_review") not in {"authorized", "pending_review"}:
            continue
        text = " ".join(str(item.get(key) or "") for key in ("publisher", "source_page_url", "platform", "rights_note")).casefold()
        score = sum(1 for term in terms if term.casefold() in text)
        if score or item.get("confirmed_at"):
            media.append({"evidence_type": "hotspot_media", "source_id": item["id"], "content_role": "hotspot_hook", "relevance_score": score * 20 + (5 if item.get("confirmed_at") else 0), "match_reason": "已登记授权状态的热点媒体候选", "rights_status": item.get("authorization_status")})
    for segment in db.list_asset_segments(limit=20_000):
        if segment.get("asset_hotspot_id") is not None or str(segment.get("asset_file_type") or "") != "video":
            continue
        category = str(segment.get("primary_category") or "")
        if category not in {"warehouse", "delivery", "staff", "facility", "brand", "customer"}:
            continue
        score = 30 + (10 if category in {"delivery", "warehouse", "customs"} and any(x in terms for x in ("配送", "清关", "仓储")) else 0)
        owned.append({"evidence_type": "owned_segment", "source_id": segment["id"], "content_role": "brand_proof", "relevance_score": score, "match_reason": f"Buffalo 原本素材分类匹配：{category}", "rights_status": "owned"})
    for item in db.list_brand_evidence(status="confirmed"):
        brand.append({"evidence_type": "brand_claim", "source_id": item["id"], "content_role": "brand_proof", "relevance_score": 50, "match_reason": "已确认 Buffalo 品牌证据", "rights_status": "confirmed"})
    selected = (facts[:5] + media[:3] + owned[:3] + brand[:3])
    coverage = {"facts": len(facts[:5]), "hotspot_media": len(media[:3]), "owned_segments": len(owned[:3]), "brand_evidence": len(brand[:3])}
    coverage["status"] = "internal_preview" if coverage["facts"] and coverage["owned_segments"] else "needs_review"
    return selected, coverage


def _scene_voiceover_max_chars(scene: dict) -> int | None:
    """Keep formal narration inside the one-pass visual duration before TTS starts."""
    if str(scene.get("evidence_type") or "") == "brand_endcard":
        return None
    try:
        duration_seconds = max(0.0, float(scene.get("duration_ms") or 0) / 1000)
    except (TypeError, ValueError):
        return None
    # TTS 的实际语速会因专有名词与停顿显著波动。正式规划先按约 3.6
    # 字/秒约束，渲染器仍会用实测音频作最后一次本地收紧，不能把短 Hook
    # 交给一次很长的旁白再寄望于循环或明显加速。
    return max(8, int(duration_seconds * 3.6)) if duration_seconds else None


def _scene_voiceover_min_chars(scene: dict) -> int | None:
    """Keep formal beats narrated enough to avoid a silent real-video tail."""
    if str(scene.get("evidence_type") or "") == "brand_endcard":
        return None
    try:
        duration_seconds = max(0.0, float(scene.get("duration_ms") or 0) / 1000)
    except (TypeError, ValueError):
        return None
    # TTS is normally much faster than the minimum pacing requirement.
    # Five Chinese characters can cover the shortest three-second beat with a
    # natural pause.  Requiring a sixth character repeatedly made the repair
    # model append stock phrases such as "请核对订单信息".
    if not duration_seconds:
        return None
    return 5 if duration_seconds <= 3.5 else max(6, int(math.ceil(duration_seconds * 2.0)))


_UNSUPPORTED_HOTSPOT_INTENSIFIERS = ("堵死", "全面瘫痪", "完全停摆", "全线停摆")
_UNSUPPORTED_HOTSPOT_VIEWER_ROUTE_PROMPTS = ("这条线", "这一批", "走到哪段路线", "在不在这一批")
_OCR_TOKEN_RE = re.compile(r"(?<![A-Za-z])[A-Z][A-Z0-9._-]{2,}")


def _safe_hotspot_repair_line(scene: dict) -> str:
    title = str(scene.get("visual") or "")
    if "侧翻" in title:
        return "现场卡车侧翻在路边。这段现场只说明道路异常。"
    if "拥堵" in title or "滞留" in title or "排队" in title:
        return "现场卡车正在排队滞留。这段现场只说明通行受影响。"
    return "现场情况正在变化。这段现场只说明当前状态。"


def _enforce_formal_scene_copy_contract(generated: dict, scenes: list[dict]) -> dict:
    """Keep planner wording inside the reviewed picture facts after model generation."""
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if len(repaired["scenes"]) != len(scenes):
        raise ValueError("内容规划模型返回的分镜数量无法进行画面文案校验")
    for item, scene in zip(repaired["scenes"], scenes):
        role = str(scene.get("scene_role") or "")
        voiceover = str(item.get("voiceover") or "").strip()
        if role == "owned_proof" and _OCR_TOKEN_RE.search(voiceover):
            # The planner is already constrained to this reviewed action. Do
            # not replace SOP-aware copy with a catalog label: that turned
            # every video into the same warehouse narration.
            anchor = str(scene.get("copy_anchor") or "镜头中的仓内作业。")
            item["voiceover"] = anchor
            item["text_overlay"] = anchor.rstrip("。")[:24]
        elif role == "hotspot_evidence" and any(
            phrase in voiceover for phrase in _UNSUPPORTED_HOTSPOT_VIEWER_ROUTE_PROMPTS
        ):
            safe_line = _safe_hotspot_repair_line(scene)
            item["voiceover"] = safe_line
            item["text_overlay"] = safe_line.rstrip("。")[:24]
        elif _OCR_TOKEN_RE.search(voiceover):
            item["voiceover"] = "镜头中的动作需要逐项核对。"
            item["text_overlay"] = "逐项核对现场动作"
    return repaired


def _planner_json(
    content: str,
    expected_scenes: int,
    voiceover_limits: list[int | None] | None = None,
    voiceover_minimums: list[int | None] | None = None,
    hotspot_scene_count: int = 0,
) -> dict:
    """Parse one bounded planner response; the model never chooses file references."""
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = _json.loads(raw)
    except Exception as exc:
        raise ValueError("内容规划模型未返回合法 JSON") from exc
    title = str(payload.get("title") or "").strip()[:120]
    angle = str(payload.get("angle") or "").strip()[:500]
    scenes = payload.get("scenes") or []
    if not title or not angle or not isinstance(scenes, list) or len(scenes) != expected_scenes:
        raise ValueError("内容规划模型缺少标题、角度或有效分镜")
    normalized = []
    for index, item in enumerate(scenes, 1):
        if not isinstance(item, dict):
            raise ValueError("内容规划模型返回了无效分镜")
        voiceover = str(item.get("voiceover") or "").strip()[:180]
        overlay = str(item.get("text_overlay") or "").strip()[:24]
        if not voiceover:
            raise ValueError(f"内容规划模型第 {index} 个分镜缺少旁白")
        max_chars = voiceover_limits[index - 1] if voiceover_limits and index <= len(voiceover_limits) else None
        compact_length = len("".join(voiceover.split()))
        if max_chars is not None and compact_length > max_chars:
            raise ValueError(f"内容规划模型第 {index} 个分镜旁白超过 {max_chars} 字时长上限")
        min_chars = voiceover_minimums[index - 1] if voiceover_minimums and index <= len(voiceover_minimums) else None
        if min_chars is not None and compact_length < min_chars:
            raise ValueError(f"内容规划模型第 {index} 个分镜旁白少于 {min_chars} 字时长下限")
        if index <= hotspot_scene_count:
            for phrase in _UNSUPPORTED_HOTSPOT_INTENSIFIERS:
                if phrase in voiceover:
                    raise ValueError(f"内容规划模型第 {index} 个热点分镜包含未经证实的夸张断言：{phrase}")
        normalized.append({"voiceover": voiceover, "text_overlay": overlay or voiceover[:24]})
    return {"title": title, "angle": angle, "scenes": normalized}


def _extend_short_formal_voiceovers(
    generated: dict,
    scenes: list[dict],
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
) -> dict:
    """Reject short narration rather than filling it with stock copy."""
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if len(repaired["scenes"]) != len(scenes):
        raise ValueError("内容规划模型返回的分镜数量无法进行旁白时长修复")
    for index, (item, scene) in enumerate(zip(repaired["scenes"], scenes)):
        minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        voiceover = str(item.get("voiceover") or "").strip()
        compact_length = len("".join(voiceover.split()))
        if minimum is None or compact_length >= minimum:
            continue
        raise ValueError(f"内容规划模型第 {index + 1} 个分镜旁白少于 {minimum} 字时长下限")
    return repaired


def _compact_long_formal_voiceovers(generated: dict, voiceover_limits: list[int | None]) -> dict:
    """Trim an otherwise-valid model line to its locked real-video beat.

    The planner already chose the factual wording.  When it merely overruns a
    measured beat by a few characters, another remote model call is slow and
    often repeats the same error.  This local repair removes only the tail,
    preferring an existing punctuation boundary and never inventing a service
    fact or selecting different media.
    """
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    for index, item in enumerate(repaired["scenes"]):
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        voiceover = str(item.get("voiceover") or "").strip()
        compact = "".join(voiceover.split())
        if maximum is None or len(compact) <= maximum:
            continue
        prefix = compact[:maximum]
        boundary = max((prefix.rfind(mark) for mark in "。！？；，、"), default=-1)
        # Do not leave a one-word fragment merely because a comma happened at
        # the start; a full-width stop within the latter half is a clean cut.
        if boundary >= max(4, int(maximum * 0.55)):
            marker = prefix[boundary]
            shortened = prefix[:boundary + 1]
            if marker in "，、":
                shortened = shortened[:-1].rstrip() + "。"
        else:
            shortened = prefix[:max(1, maximum - 1)].rstrip("，、；：- ") + "。"
        item["voiceover"] = shortened
        item["text_overlay"] = str(item.get("text_overlay") or shortened)[:24]
    return repaired


def _compact_topic_evidence(brief: dict, event: dict, scenes: list[dict]) -> dict:
    """Only send selected, short evidence summaries to the remote planner."""
    facts = [{
        "title": str(event.get("title_zh") or event.get("title_en") or "")[:160],
        "summary": str(event.get("summary_zh") or event.get("summary") or "")[:400],
        "location": str(event.get("location") or "")[:80],
        "published_at": str(event.get("published_at") or "")[:40],
    }]
    allowed_scenes = [{
        "scene": item["scene"], "role": item["scene_role"],
        "visual": str(item.get("copy_anchor") or item.get("visual") or "")[:120],
        "category": str(item.get("primary_category") or ""), "duration_seconds": round(item["duration_ms"] / 1000, 1),
        "voiceover_max_chars": _scene_voiceover_max_chars(item),
        "voiceover_min_chars": _scene_voiceover_min_chars(item),
    } for item in scenes]
    return {
        "brief": {
            **{key: brief.get(key) for key in ("raw_input", "subject", "angle", "audience", "goal", "logistics_nodes", "must_avoid")},
            "topic_anchor_contract": _topic_anchor_contract(
                str(brief.get("raw_input") or brief.get("subject") or "")
            ),
        },
        "facts": facts,
        "allowed_scenes": allowed_scenes,
    }


def _event_date_seconds(value) -> int:
    """批17：兼容 ISO（含 UTC 偏移）与 RSS RFC2822 日期 → epoch 秒；无法解析/1970 哨兵返回 0。

    注意：不能把 ISO 截到 [:19]（会丢掉 '+00:00' 时区，产生 8h 偏移）；RFC2822
    带 '+0200' 时区，直接 .timestamp() 才按真实 UTC epoch 折算。
    """
    if not value:
        return 0
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text)          # '2026-07-30 03:51:10' / '...+00:00' / '2026-07-30'
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)       # 'Tue, 21 Jul 2026 13:00:00 +0200'
        except Exception:  # noqa: BLE001
            return 0
    ts = dt.timestamp()
    return int(ts) if ts > 0 else 0


def _marketing_hook_candidates(
    brief: dict,
    limit: int = 8,
    *,
    hook_kind: str | None = None,
    require_scene_overlap: bool = False,
    allow_broad_match: bool = False,
) -> tuple[list[dict], str, list[dict], dict]:
    """RAG retrieval for marketing hooks, not a claim that every headline proves Buffalo."""
    topic_text = " ".join(str(brief.get(key) or "") for key in ("raw_input", "subject", "angle", "goal"))
    terms = _topic_keywords(topic_text)
    specific_terms = [term for term in terms if term.casefold() not in _CHAT_HOOK_BROAD_TERMS]
    topic_profile = _chat_hook_topic_profile(topic_text)
    # 服务主题（如“海外仓介绍”）允许由近期道路、边境、天气等真实事件
    # 作上下文开场；只有用户明确点名了事件、道路或事故时才要求事实文本
    # 精确命中，避免把 R60 事故换成另一个不相关热点。
    event_specific_terms = {
        "边境", "拥堵", "堵车", "卡车", "道路", "事故", "侧翻", "路线", "路况",
        "火灾", "救援", "封路", "beitbridge", "r60", "robertson", "worcester",
        "r328", "swartberg", "road", "traffic", "congestion",
    }
    strict_terms = [term for term in terms if term in event_specific_terms]
    all_categories = [int(item["id"]) for item in db.get_kb_categories()]
    kb_context = db.get_kb_context(all_categories, max_docs=4, max_chars=2_000)
    brand_evidence = db.list_brand_evidence(status="confirmed")[:5]
    funnel = {
        "scanned": 0,
        "scene_mismatch": 0,
        "relevance_low": 0,
        "not_playable": 0,
        "kind_filtered": 0,
        "duplicate_or_recent": 0,
        "passed": 0,
        "selected": 0,
    }
    events_by_hotspot: dict[int, list[dict]] = {}
    for event in db.list_hotspot_event_clips():
        funnel["scanned"] += 1
        kind = str(event.get("hook_kind") or "timely_event")
        if hook_kind and kind != hook_kind:
            funnel["kind_filtered"] += 1
            continue
        if not _is_confirmed_renderable_hotspot_hook(event):
            funnel["not_playable"] += 1
            continue
        events_by_hotspot.setdefault(int(event.get("hotspot_id") or 0), []).append(event)
    candidates = []
    hotspot_rows = db.list_hotspots(limit=100)
    if hook_kind == "generic_logistics":
        # Generic evergreen parents carry a 1970 sentinel retrieved_at and sink
        # below the newest-100 window, so their qualified clips would never be
        # scored.  Any parent that owns a clip already passing the renderable
        # gate above is appended explicitly — no gate is loosened, and the
        # timely_event path (hook_kind != generic_logistics) stays unchanged.
        listed_ids = {int(item["id"]) for item in hotspot_rows}
        for hotspot_id in sorted(events_by_hotspot):
            if int(hotspot_id) in listed_ids:
                continue
            row = db.get_hotspot(int(hotspot_id))
            if row:
                hotspot_rows.append(row)
    for hotspot in hotspot_rows:
        event_clips = events_by_hotspot.get(int(hotspot["id"]), [])
        if not event_clips:
            continue
        # Parent headlines can be a generic daily-news title.  The confirmed
        # Hook evidence is the auditable factual record a customer actually
        # sees, so retrieval must search it as well instead of treating a
        # generic parent title as an absent match.
        parent_text = " ".join(
            str(hotspot.get(key) or "")
            for key in ("title", "title_zh", "summary", "summary_zh", "event_type", "locations_json")
        )
        # A curator's logistics question can legitimately mention “海外仓如何
        # 应对边境排队”, but that does not turn the border clip into an
        # overseas-warehouse event.  Match the user's subject only against
        # source/visual facts; retain the question solely for later planning.
        hook_fact_text = " ".join(
            str(value or "")
            for event in event_clips
            for value in (
                event.get("title_zh"), event.get("title_en"),
                (event.get("evidence") or {}).get("what_happened"),
            )
        )
        hook_context_text = " ".join(
            str((event.get("evidence") or {}).get("logistics_question") or "")
            for event in event_clips
        )
        text = f"{parent_text} {hook_fact_text}".casefold()
        kind = hotspot_logistics_planner.classify_hotspot(hotspot)
        if kind == "unknown" and hook_fact_text:
            kind = hotspot_logistics_planner.classify_hotspot({"title": hook_fact_text})
        direct = sum(1 for term in specific_terms if term.casefold() in text)
        specific_direct = sum(1 for term in strict_terms if term.casefold() in text)
        # Logistics event types can be a marketing *question* even when the
        # headline does not repeat a broad user wording.  A specific request
        # (“海外仓”“道路事故”) must still match the actual event/visual facts.
        kind_in_topics = kind in hotspot_logistics_planner.TOPICS
        # Grounded only in verified fact text — the curator's invented
        # "logistics_question" bridge sentence is excluded from category
        # tagging so it cannot manufacture false topic/event overlap.
        event_profile = set()
        for event in event_clips:
            scenes = event.get("logistics_scenes") or []
            if scenes:
                event_profile.update(str(item) for item in scenes)
            else:
                event_profile |= _chat_hook_event_profile(
                    " ".join(
                        str(value or "")
                        for value in (
                            event.get("title_zh"), event.get("title_en"),
                            (event.get("evidence") or {}).get("what_happened"),
                        )
                    )
                )
        profile_overlap = len(topic_profile & event_profile)
        if require_scene_overlap and topic_profile and not profile_overlap:
            funnel["scene_mismatch"] += 1
            continue
        # Broad-only topics (南非/物流 with no logistics category profile) must
        # not hit random accident Hooks. Topics that already resolved to a
        # category (cost_risk, warehouse, …) may still use intent_bridge below.
        if not allow_broad_match and not topic_profile and not specific_terms:
            funnel["scene_mismatch"] += 1
            continue
        intent_bridge = 0
        if "cost_risk" in topic_profile and event_profile & {"disruption", "border", "warehouse"}:
            intent_bridge = 12
        if "warehouse" in topic_profile and "border" in event_profile:
            intent_bridge = max(intent_bridge, 12)
        if strict_terms and not specific_direct:
            funnel["relevance_low"] += 1
            continue
        # A hotspot's coarse type classification alone (kind_in_topics) is
        # not admitted as a qualifying signal — every hotspot in the small
        # confirmed library tends to classify into some logistics-adjacent
        # "kind", which used to let unrelated events into every topic's
        # candidate set. It still contributes a small tie-break score below
        # once a candidate already qualifies on a real signal.
        if not allow_broad_match and not direct and not profile_overlap and not intent_bridge:
            funnel["relevance_low"] += 1
            continue
        event_fit = 1 if kind_in_topics else 0
        hooks = hotspot_hook_selector.rank_hook_clips(event_clips)
        if not hooks:
            funnel["relevance_low"] += 1
            continue
        if kind == "strike":
            question = "路线出现变化时，卖家应先核对哪些履约节点？"
        elif kind == "risk":
            question = "当地风险变化时，末端异常如何被提前识别和沟通？"
        elif kind == "infrastructure":
            question = "港口、道路或基础设施变化，会先影响哪一段交付链路？"
        elif kind == "weather":
            question = "天气变化时，发货前应如何预留履约判断空间？"
        elif kind == "policy":
            question = "政策或规则变化后，进入南非市场前要重查哪些准备？"
        else:
            question = "订单与配送需求变化时，仓配动作如何影响客户体验？"
        headline = str(hotspot.get("title_zh") or hotspot.get("title") or "").strip()
        if not headline or headline.casefold().startswith("what’s happening across") or headline.casefold().startswith("what's happening across"):
            headline = str(hooks[0].get("content_description") or "") if hooks else headline
        reuse_bias = 2 if len(hooks) >= 2 else 0
        mismatch_penalty = 0
        if "border" in event_profile and not topic_profile & {"border", "warehouse"}:
            mismatch_penalty += 14
        if topic_profile and not (profile_overlap or direct):
            mismatch_penalty += 8
        if kind == "unknown" and not profile_overlap:
            mismatch_penalty += 10
        # 批17：时效入链。只对新闻锚点 Hook（timely_event）施加新鲜度加分；
        # 常青开场（generic_logistics）不随事件时间衰减，加分恒 0。
        freshness_bonus = 0
        published_ts = _event_date_seconds(hotspot.get("published_at"))
        if published_ts and str(event_clips[0].get("hook_kind") or "timely_event") != "generic_logistics":
            age_days = (datetime.now().timestamp() - published_ts) / 86400.0
            if age_days < 1:
                freshness_bonus = 8
            elif age_days < 3:
                freshness_bonus = 5
            elif age_days < 7:
                freshness_bonus = 2
            elif age_days >= 30:
                freshness_bonus = -3
        # P1: 加入素材使用惩罚
        asset_id = hooks[0].get("asset_id") if hooks else None
        usage_penalty = 0
        if asset_id and str(asset_id).isdigit():
            asset = db.get_asset(int(asset_id))
            if asset:
                usage_count = int(asset.get("usage_count") or 0)
                # 取 min(usage, 5) 避免过度惩罚 + _usage_freshness_penalty(last_used_at)
                last_used_at = asset.get("last_used_at")
                freshness_pen = hotspot_video_planner._usage_freshness_penalty(last_used_at)
                usage_boundary = min(usage_count, 5) + freshness_pen
                usage_penalty = usage_boundary
        
        candidates.append({
            "hotspot_id": hotspot["id"], "event_clip_id": hooks[0]["event_clip_id"] if hooks else None,
            "title": headline[:200],
            "summary": (str(hotspot.get("summary_zh") or hotspot.get("summary") or "") + " " + hook_context_text)[:500],
            "source_url": hotspot.get("source_url"), "published_at": hotspot.get("published_at"),
            "published_ts": published_ts,
            "hook_type": "direct" if direct else "contextual", "logistics_signal": kind,
            "logistics_scenes": sorted(event_profile),
            "relevance": {
                "level": "strong_direct" if direct else "strong_logistics_context",
                "reason": (
                    "热点事实与用户问题存在直接物流节点重合。"
                    if direct else
                    "热点现场可直接解释当前物流节点的上游风险或准备动作。"
                ),
            },
            "marketing_question": question,
            "hook_clips": hooks, "can_render_video": len(hooks) >= 1,
            "score": (
                direct * 40
                + profile_overlap * 16
                + event_fit * 5
                + intent_bridge
                + reuse_bias
                - mismatch_penalty
                + freshness_bonus
                - usage_penalty
            ),
            "_tiebreak": random.random(),
            "usage_boundary": "热点只用于提出问题或解释外部背景；Buffalo 服务能力只能由知识库、品牌证据和自有镜头说明。",
            "reuse_policy": "只有该 Hook 是当前主题最强事实现场时才复用；若存在同等强相关候选，优先选择不同事件，避免连续视频看起来都用同一个开场。",
        })
    # P1: 同分随机 tie-break
    candidates.sort(
        key=lambda item: (item["score"], item.get("published_ts") or 0, item.get("_tiebreak", 0.0)),
        reverse=True,
    )
    limited = candidates[:max(1, limit)] if candidates else []
    
    # 如果有多条同分，随机打乱后取前 limit
    if len(limited) > 1:
        scores = [item["score"] for item in limited]
        if len(set(scores)) == 1:
            # 全部分数相同，随机打乱
            random.shuffle(limited)
        else:
            # 按分数分组，每组内随机打乱
            groups = {}
            for item in limited:
                score = item["score"]
                if score not in groups:
                    groups[score] = []
                groups[score].append(item)
            reshuffled = []
            for score in sorted(groups.keys(), reverse=True):
                random.shuffle(groups[score])
                reshuffled.extend(groups[score])
            limited = reshuffled[:max(1, limit)]
    funnel["passed"] = len(limited)
    rag = [{"claim": item.get("claim", "")[:300], "note": item.get("evidence_note", "")[:300]} for item in brand_evidence]
    return limited, kb_context, rag, funnel


def _recommendation_json(content: str, allowed_ids: set[int], limit: int) -> list[dict]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        rows = _json.loads(raw).get("recommendations") or []
    except Exception as exc:
        raise ValueError("热点推荐模型未返回合法 JSON") from exc
    result = []
    for item in rows:
        hotspot_id = int(item.get("hotspot_id") or 0)
        if hotspot_id not in allowed_ids or any(row["hotspot_id"] == hotspot_id for row in result):
            continue
        hook_event_ids = []
        for value in item.get("hook_event_ids") or []:
            try:
                event_id = int(value)
            except (TypeError, ValueError):
                continue
            if event_id not in hook_event_ids:
                hook_event_ids.append(event_id)
        result.append({"hotspot_id": hotspot_id, "marketing_question": str(item.get("marketing_question") or "").strip()[:180],
                       "why": str(item.get("why") or "").strip()[:240], "hook_event_ids": hook_event_ids[:3]})
        if len(result) >= limit:
            break
    if not result:
        raise ValueError("热点推荐模型未选择有效候选")
    return result


async def _model_decide_marketing_hooks(
    brief: dict, candidates: list[dict], kb_context: str, brand_evidence: list[dict], limit: int,
) -> tuple[list[dict], dict]:
    """The deployed planner, not Codex or a human, makes the content decision.

    Rules only provide a bounded, analysed candidate set and remain the fallback
    when the configured internal model is unavailable.
    """
    if not model_router.key_is_available("planner_text"):
        return candidates[:limit], {"used": False, "fallback": "内容模型未配置，规则候选待模型复核"}
    messages = [
        {"role": "system", "content": (
            "你是部署在物流内容系统内的内容决策模型，不是聊天助手。基于已分析热点候选、Buffalo RAG资料，"
            "只选择强相关热点：Hook 的画面和已核验事实必须能直接解释用户当前的物流节点；无法自然解释就拒绝，不能硬接。"
            "为每个入选热点从给定 hook_clips 中选 1–2 个片段。"
            "不要把同一个 Hook 当作通用开场模板；只有它明显是当前主题最强事实现场时才可复用。"
            "若多个候选同样强相关，优先选择不同事件和更贴近当前问题的画面。单条视频内不得重复同一时间范围。"
            "优先包含道路、卡车、港口、现场动作；避开主播、订阅页、泛新闻背景。先说明热点事实，再说明物流影响，最后才引出 Buffalo 可见动作。"
            "热点不证明 Buffalo 服务；不得编造新闻或服务结果。只返回 JSON："
            "{\"recommendations\":[{\"hotspot_id\":0,\"hook_event_ids\":[0,0,0],\"marketing_question\":\"\",\"why\":\"\"}]}。"
        )},
        {"role": "user", "content": _json.dumps({"brief": brief, "candidates": candidates,
                                                       "kb_context": kb_context, "brand_evidence": brand_evidence}, ensure_ascii=False)},
    ]
    job_id = model_router.route_scoped_job_id(
        f"topic-model-decision-{brief['id'][:12]}-{_uuid4().hex[:12]}", "planner_text"
    )
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=4_000,
        max_output_tokens=model_router.required_output_budget("planner_text", 800),
    )
    try:
        result = await model_router.call_text(
            job_id, "planner_text", messages, prompt_version="topic-content-decision-v3", max_output_tokens=800,
        )
        picked = _recommendation_json(result["content"], {item["hotspot_id"] for item in candidates}, limit)
    except Exception as exc:
        logger.warning("内容决策模型失败，保留规则候选待复核：%s", str(exc)[:160])
        return candidates[:limit], {"used": False, "fallback": "规则候选", "error": str(exc)[:120]}
    by_id = {item["hotspot_id"]: item for item in candidates}
    selected = []
    for item in picked:
        candidate = dict(by_id[item["hotspot_id"]])
        hooks_by_id = {int(hook["event_clip_id"]): hook for hook in candidate.get("hook_clips") or []}
        hooks = [hooks_by_id[event_id] for event_id in item["hook_event_ids"] if event_id in hooks_by_id]
        # A model may explicitly reject a candidate by omitting three valid clips.
        grounded_reason = "；".join(filter(None, [
            f"热点事实：{candidate.get('title')}",
            f"可用现场 Hook：{'、'.join(str(hook.get('content_description') or '')[:80] for hook in hooks[:3])}",
        ]))[:500]
        candidate.update({**item, "why": grounded_reason, "hook_clips": hooks, "event_clip_id": hooks[0]["event_clip_id"] if hooks else None,
                          # 一个强现场 Hook 足以承担当条视频的热点开场；同事件
                          # 存在第二段时再增强，不能为了固定数量丢掉真实可用素材。
                          "can_render_video": len(hooks) >= 1})
        selected.append(candidate)
    return selected, {"used": True, "cache_hit": result.get("cache_hit", False), "usage": result.get("usage"),
                      "prompt_version": "topic-content-decision-v3"}


@app.post("/api/topic-briefs", status_code=201)
async def create_topic_brief(body: TopicBriefCreateRequest, user=Depends(get_current_user)):
    payload = _build_topic_brief_payload(body)
    brief = db.create_topic_brief(payload, user["id"])
    # Broad topics deliberately stop at angles; no content or media work is triggered.
    angles = []
    if brief["status"] == "angle_only":
        base = brief["subject"] or "物流"
        angles = [f"{base}的清关准备", f"{base}的末端配送风险", f"{base}的仓配流程核对"]
    db.add_audit_log(user["id"], user["username"], "create_topic_brief", target=brief["id"])
    return {"brief": brief, "angles": angles, "generation_allowed": bool(brief.get("angle"))}


@app.get("/api/topic-briefs/{brief_id}")
async def get_topic_brief(brief_id: str, user=Depends(get_current_user)):
    brief = db.get_topic_brief(brief_id, user["id"])
    if not brief:
        raise HTTPException(404, "选题简报不存在")
    return {"brief": brief, "evidence": db.list_topic_evidence_items(brief_id)}


@app.put("/api/topic-briefs/{brief_id}")
async def update_topic_brief(brief_id: str, body: TopicBriefUpdateRequest, user=Depends(get_current_user)):
    payload = _build_topic_brief_payload(body)
    brief = db.update_topic_brief(brief_id, payload, user["id"])
    if not brief:
        raise HTTPException(404, "选题简报不存在")
    return brief


@app.post("/api/topic-briefs/{brief_id}/retrieve-evidence")
async def retrieve_topic_brief_evidence(brief_id: str, user=Depends(get_current_user)):
    brief = db.get_topic_brief(brief_id, user["id"])
    if not brief:
        raise HTTPException(404, "选题简报不存在")
    if not brief.get("angle"):
        raise HTTPException(409, "请先选择或填写内容角度，再检索证据")
    candidates, coverage = _retrieve_topic_evidence(brief)
    evidence = db.replace_topic_evidence_items(brief_id, candidates)
    db.update_topic_brief(brief_id, {"status": coverage["status"]}, user["id"])
    return {"brief": db.get_topic_brief(brief_id, user["id"]), "evidence": evidence, "coverage": coverage}


@app.post("/api/topic-briefs/{brief_id}/recommend-hotspots")
async def recommend_topic_brief_hotspots(brief_id: str, body: TopicHotspotRecommendationRequest, user=Depends(get_current_user)):
    """Retrieve marketing-relevant hooks; models rank a bounded RAG result, never invent headlines."""
    brief = db.get_topic_brief(brief_id, user["id"])
    if not brief:
        raise HTTPException(404, "选题简报不存在")
    candidates, kb_context, brand_evidence, _funnel = _marketing_hook_candidates(brief, limit=12)
    if not candidates:
        return {"recommendations": [], "status": "no_relevant_hotspot", "message": "当前热点池没有可作为物流营销引子的事件；可先生成常青内容，但不得伪装为热点。"}
    selected = candidates[:body.limit]
    model_meta = {"used": False, "fallback": "调用参数禁用内容模型"}
    if body.use_model:
        selected, model_meta = await _model_decide_marketing_hooks(brief, candidates, kb_context, brand_evidence, body.limit)
    db.add_audit_log(user["id"], user["username"], "recommend_topic_brief_hotspots", target=brief_id, detail=f"count={len(selected)}")
    return {"recommendations": selected, "status": "ready", "model": model_meta,
            "rag_coverage": {"kb_chars": len(kb_context), "brand_evidence": len(brand_evidence)},
            "message": "推荐结果是营销引子，不等同于 Buffalo 服务证明；生成时仍会执行热点、品牌和媒体门禁。"}


@app.post("/api/topic-briefs/{brief_id}/autopilot")
async def autopilot_topic_brief_video(brief_id: str, body: TopicAutoPilotRequest, user=Depends(get_current_user)):
    """Department-facing zero-pick flow: choose indexed Hook clips, then generate one project."""
    brief = db.get_topic_brief(brief_id, user["id"])
    if not brief:
        raise HTTPException(404, "选题简报不存在")
    candidates, kb_context, brand_evidence, _funnel = _marketing_hook_candidates(brief, limit=20)
    candidates, model_meta = await _model_decide_marketing_hooks(brief, candidates, kb_context, brand_evidence, limit=5)
    chosen = next((item for item in candidates if item.get("can_render_video") and item.get("event_clip_id")), None)
    if not chosen:
        raise HTTPException(409, {
            "message": "预热库暂无与该主题相关且可播放的已确认 Hook，暂不能自动出片。",
            "next_action": "等待下一轮热点预热完成，或先补充对应主题的长视频。",
        })
    result = await _generate_topic_brief_video(
        brief_id,
        TopicBriefGenerateRequest(hotspot_event_id=int(chosen["event_clip_id"]), platform=body.platform,
                                  target_duration_ms=body.target_duration_ms),
        user,
    )
    return {**result, "autopilot": {"hotspot_id": chosen["hotspot_id"], "hook_clips": chosen["hook_clips"],
                                     "content_model": model_meta}}


@app.put("/api/topic-briefs/{brief_id}/evidence/{item_id}")
async def select_topic_brief_evidence(brief_id: str, item_id: str, body: TopicEvidenceSelectionRequest, user=Depends(get_current_user)):
    if not db.get_topic_brief(brief_id, user["id"]):
        raise HTTPException(404, "选题简报不存在")
    item = db.update_topic_evidence_item(brief_id, item_id, body.selected, body.review_status)
    if not item:
        raise HTTPException(404, "证据项不存在")
    return item


async def _generate_topic_brief_video(
    brief_id: str,
    body: TopicBriefGenerateRequest,
    user: dict,
    source_snapshot: dict | None = None,
    target_project_id: str | None = None,
    target_revision_id: str | None = None,
):
    """Use one model call to turn a verified 50–90s dual-library plan into a project draft."""
    brief = db.get_topic_brief(brief_id, user["id"])
    if not brief:
        raise HTTPException(404, "选题简报不存在")
    event = db.get_hotspot_event_clip(body.hotspot_event_id)
    if not event:
        raise HTTPException(404, "热点事件不存在")
    approved_hook_event_ids = list(dict.fromkeys(
        int(event_id) for event_id in body.approved_hook_event_ids if int(event_id) > 0
    ))
    if approved_hook_event_ids:
        if int(event["id"]) not in approved_hook_event_ids:
            approved_hook_event_ids.insert(0, int(event["id"]))
        if not 1 <= len(approved_hook_event_ids) <= 2:
            raise HTTPException(409, "聊天成片必须锁定一至两段已确认 Hook。")
        locked_events = [db.get_hotspot_event_clip(event_id) for event_id in approved_hook_event_ids]
        if (
            any(item is None or not _is_confirmed_renderable_hotspot_hook(item) for item in locked_events)
            or any(int(item["asset_id"]) != int(event["asset_id"]) or int(item["hotspot_id"]) != int(event["hotspot_id"])
                   for item in locked_events)
            or not _is_same_confirmed_hotspot_event(locked_events)
        ):
            raise HTTPException(409, "锁定的热点 Hook 已失效，或不属于同一已确认热点事件。")
        if len(locked_events) == 2:
            first, second = sorted(locked_events, key=lambda item: int(item["start_ms"]))
            if int(second["start_ms"]) < int(first["end_ms"]):
                raise HTTPException(409, "锁定的热点 Hook 时间范围重叠，不能用于同一成片。")
    source_hotspot = db.get_hotspot(int(event["hotspot_id"])) or {}
    owned_segments = [item for item in db.list_asset_segments(limit=20_000) if not item.get("asset_hotspot_id")]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
    ]
    related_events = db.list_hotspot_event_clips(asset_id=event.get("asset_id"), hotspot_id=event.get("hotspot_id"))
    # 批18：并入跨父已确认事件——chat 流允许锁不同父的 Hook，planner 之前静默丢弃。
    if approved_hook_event_ids:
        known_ids = {int(e.get("id") or 0) for e in related_events}
        for clip_id in approved_hook_event_ids:
            clip = db.get_hotspot_event_clip(int(clip_id))
            if clip and int(clip.get("id") or 0) not in known_ids and _is_confirmed_renderable_hotspot_hook(clip):
                related_events.append(clip)
    planning_brief = hotspot_logistics_planner.build_brief({**source_hotspot, **event}, owned_segments, brief)
    planning_brief.update({
        "hotspot_id": event.get("hotspot_id"),
        "source_asset_id": event.get("asset_id"),
        # 用户在热点 Hook 库中明确点选的片段必须成为成片的首个热点证据，
        # 不能被同一母片的其他 Hook 静默替换。
        "primary_event_id": event.get("id"),
    })
    if approved_hook_event_ids:
        planning_brief["approved_hook_event_ids"] = approved_hook_event_ids
    endcard_duration_ms = sum(
        int(item["duration_ms"]) for item in hotspot_video_planner.BRAND_ENDCARD_SCENES
    )
    base_duration_ms = max(50_000, body.target_duration_ms - endcard_duration_ms)
    try:
        scenes = hotspot_video_planner.plan_followup_scenes(
            planning_brief, related_events, owned_segments, target_duration_ms=base_duration_ms,
            owned_images=owned_images,
            allow_adaptation=True,
        )
    except ValueError as exc:
        # Planner input originates from verified Hooks and reviewed internal
        # assets. A sparse or non-renderable combination is a user-actionable
        # coverage gate, never an opaque server failure in the chat flow.
        logger.info("双素材视频规划未通过素材门禁: %s", exc)
        raise HTTPException(409, {
            "message": "当前已锁定热点，但可用素材组合无法形成可渲染分镜。",
            "reason": str(exc)[:240],
            "next_action": "请确认 Hook 仍可播放，或补充至少一段未重复、每段不少于 3 秒的 Buffalo 自有视频。",
        }) from exc
    hotspot_count = sum(item.get("evidence_type") == "hotspot_video" for item in scenes)
    owned_count = sum(item.get("evidence_type") == "owned_video" for item in scenes)
    image_count = sum(item.get("evidence_type") == "image" for item in scenes)
    planned_duration_ms = sum(int(item.get("duration_ms") or 0) for item in scenes)
    # 品牌 CTA 会在下方统一追加；准入时应按终片时长判断，而不是把尚未追加的
    # 固定结尾误判为“素材不足”。
    final_planned_duration_ms = planned_duration_ms + sum(
        int(item["duration_ms"]) for item in hotspot_video_planner.BRAND_ENDCARD_SCENES
    )
    adaptation = hotspot_video_planner.describe_plan_adaptation(scenes)
    # Hook is the hard gate. Thin owned inventory is adaptation, not a block.
    if hotspot_count < 1 or not scenes:
        raise HTTPException(409, {
            "message": "证据不足：缺少可用热点 Hook 镜头，不能生成成片。",
            "coverage": {
                "hotspot_video": hotspot_count,
                "owned_video": owned_count,
                "image": image_count,
                "duration_ms": final_planned_duration_ms,
            },
            "required": {"hotspot_video": 1, "owned_video": "adaptive"},
            "adaptation": adaptation,
            "next_action": "重新锁定强相关热点 Hook，或换用已确认可渲染的事件片段。",
        })
    if not model_router.key_is_available("planner_text"):
        raise HTTPException(503, "内容规划模型未配置，无法生成正式文案；请配置 MIMO_API_KEY 后重试。")
    context = _compact_topic_evidence(brief, event, scenes)
    if hotspot_count == 1:
        hotspot_story_contract = (
            "叙事开场只有第1段热点 Hook：前两秒给出强现场事实和一个与卖家有关的问题。"
            "第1段只能描述允许 Hook 中可见或已给出的热点事实，不得写卖家已经采取了什么动作。"
            "第2段开头必须用一句简短的剪辑衔接（如‘镜头转到仓内’），随后只描述 Buffalo 镜头可见动作。"
        )
    else:
        hotspot_story_contract = (
            "前两段是同一事件的热点事实：第1段前两秒给出强现场事实和卖家问题，第2段只补充同一现场可见情况。"
            "前两段只能描述允许 Hook 中可见或已给出的热点事实；第2段不得写卖家已经采取了什么动作。"
            "第3段开头必须用一句简短的剪辑衔接（如‘镜头转到仓内’），随后只描述 Buffalo 镜头可见动作。"
        )
    messages = [
        {"role": "system", "content": (
            "你是南非跨境物流短视频策划。只依据提供的事实和允许分镜生成一条 50–90 秒抖音文案。"
            + hotspot_story_contract
            + f"允许分镜只有 {hotspot_count} 个热点 Hook；不得凭空补出其他热点事实。"
            "热点事实不得写‘堵死’、全面瘫痪、完全停摆或全线停摆等原始事实未证实的夸张断言。"
            "Buffalo 只描述镜头可见的动作，不能把热点当作品牌服务证明。不得复述空泛的“热点变化、提前准备、承接每一步”等套话；"
            "自有镜头旁白只能描述画面可见动作；没有清关、入库前或派送前事实时，不得凭画面推断这些节点已经发生。"
            "每段必须提供新的具体信息。不得编造清关完成、时效、安全、覆盖率或客户结果。不得改变场景数量、不得推荐新素材。"
            "用户主题是整条视频的标题和叙事主线；热点 Hook 只能作为开场事实或外部背景，绝不能改写、取代或缩窄用户主题。"
            "若 brief.topic_anchor_contract 存在，标题必须命中其 title 任一词，并且 title_all 中的词必须全部出现；旁白必须展开其 narrative 任一词。"
            "不满足时不要改写成热点标题，必须按原用户主题重写标题和旁白。"
            "每个允许分镜中的 voiceover_max_chars 和 voiceover_min_chars 都是硬边界（null 的品牌 CTA 除外）。旁白必须落在两者之间：不能超出真实画面，也不能过短而留下无声的真实画面。请用事实、可见动作或条件式核对问题自然补足，不得用空泛口号填充。"
            + douyin_copywriting_sop.prompt_for_video_planner()
            + "只返回 JSON：{\"title\":\"\",\"angle\":\"\",\"scenes\":[{\"voiceover\":\"\",\"text_overlay\":\"\"}]}。"
        )},
        {"role": "user", "content": _json.dumps(context, ensure_ascii=False)},
    ]
    job_id = model_router.route_scoped_job_id(
        f"topic-plan-{brief_id[:12]}-{_uuid4().hex[:12]}", "planner_text"
    )
    model_router.create_budget(
        job_id, max_calls=3, max_input_tokens=15_000,
        max_output_tokens=3 * model_router.required_output_budget("planner_text", 1_000),
    )
    try:
        result = await model_router.call_text(
            job_id, "planner_text", messages, prompt_version="topic-brief-video-plan-v10", max_output_tokens=1_000,
        )
        voiceover_limits = [_scene_voiceover_max_chars(scene) for scene in scenes]
        voiceover_minimums = [_scene_voiceover_min_chars(scene) for scene in scenes]
        try:
            # First parse the model JSON and its factual boundaries, then make
            # a local beat-length repair before enforcing the per-scene cap.
            # This avoids a second remote call for the common "one clause too
            # long" case while keeping malformed JSON on the normal repair path.
            generated_candidate = _planner_json(
                result["content"], len(scenes), hotspot_scene_count=hotspot_count,
            )
            generated_candidate = _compact_long_formal_voiceovers(
                generated_candidate, voiceover_limits,
            )
            generated = _planner_json(
                _json.dumps(generated_candidate, ensure_ascii=False), len(scenes), voiceover_limits,
                hotspot_scene_count=hotspot_count,
            )
            generated = _extend_short_formal_voiceovers(
                generated, scenes, voiceover_minimums, voiceover_limits,
            )
            _validate_formal_copy_specificity(generated)
            _validate_generated_topic_anchor(generated, brief)
        except ValueError as initial_error:
            # The planner selected no file references, so one bounded rewrite can
            # safely repair malformed JSON or a short-scene narration overflow
            # without changing the approved Hook pair or the evidence plan.
            repair_messages = [
                {
                    "role": "system",
                    "content": (
                        "你是短视频脚本 JSON 修复器。只返回完整 JSON，不要解释。"
                        "保留既定分镜数量、顺序、事实边界和所有旁白字数上下限；"
                        "不得推荐或选择新素材，不得使用信息图、地图、流程图或文字卡。"
                        "逐段读取 allowed_scenes 的字数上下限。短句必须改成完整、自然且与该镜头可见动作相关的句子；"
                        "不得保留‘先核对清单’、‘配送节奏要稳’这类脱离画面的短口号，也不得用‘请核对订单信息’补字。"
                        + douyin_copywriting_sop.prompt_for_video_planner()
                    ),
                },
                {
                    "role": "user",
                    "content": _json.dumps({
                        "validation_error": str(initial_error),
                        "invalid_draft": result["content"],
                        "allowed_scenes": context["allowed_scenes"],
                        "required_json": {"title": "", "angle": "", "scenes": [
                            {"voiceover": "", "text_overlay": ""}
                        ]},
                    }, ensure_ascii=False),
                },
            ]
            repair_result = None
            repair_error = initial_error
            invalid_draft = result["content"]
            for repair_attempt in range(2):
                repair_messages[1]["content"] = _json.dumps({
                    "validation_error": str(repair_error),
                    "invalid_draft": invalid_draft,
                    "allowed_scenes": context["allowed_scenes"],
                    "required_json": {"title": "", "angle": "", "scenes": [
                        {"voiceover": "", "text_overlay": ""}
                    ]},
                }, ensure_ascii=False)
                repair_result = await model_router.call_text(
                    job_id, "planner_text", repair_messages,
                    prompt_version="topic-brief-video-plan-v10-repair", max_output_tokens=1_000,
                )
                try:
                    repaired_candidate = _planner_json(
                        repair_result["content"], len(scenes), hotspot_scene_count=hotspot_count,
                    )
                    repaired_candidate = _compact_long_formal_voiceovers(
                        repaired_candidate, voiceover_limits,
                    )
                    generated = _planner_json(
                        _json.dumps(repaired_candidate, ensure_ascii=False), len(scenes), voiceover_limits,
                        voiceover_minimums, hotspot_scene_count=hotspot_count,
                    )
                    _validate_formal_copy_specificity(generated)
                    _validate_generated_topic_anchor(generated, brief)
                    break
                except ValueError as exc:
                    repair_error = exc
                    invalid_draft = repair_result["content"]
            else:
                raise repair_error
            result = {
                **repair_result,
                "cache_hit": bool(result.get("cache_hit")) and bool(repair_result.get("cache_hit")),
                "usage": {
                    "input_tokens": int((result.get("usage") or {}).get("input_tokens") or 0)
                    + int((repair_result.get("usage") or {}).get("input_tokens") or 0),
                    "output_tokens": int((result.get("usage") or {}).get("output_tokens") or 0)
                    + int((repair_result.get("usage") or {}).get("output_tokens") or 0),
                    "repair_attempted": True,
                },
            }
    except Exception as exc:
        logger.exception("内容规划失败: %s", brief_id)
        raise HTTPException(502, f"内容规划失败：{str(exc)[:160]}") from exc
    generated = _enforce_formal_scene_copy_contract(generated, scenes)
    generated = _compact_long_formal_voiceovers(generated, voiceover_limits)
    generated = _extend_short_formal_voiceovers(
        generated, scenes, voiceover_minimums, voiceover_limits,
    )
    try:
        _validate_formal_copy_specificity(generated)
        _validate_generated_topic_anchor(generated, brief)
        generated = _planner_json(
            _json.dumps(generated, ensure_ascii=False), len(scenes), voiceover_limits,
            voiceover_minimums, hotspot_scene_count=hotspot_count,
        )
    except ValueError as exc:
        raise HTTPException(409, f"内容质量门禁未通过：{exc}") from exc
    # 清关 preparation 模式文案门禁：所有文案修复/字数校验之后的最后一道
    # 确定性拦截。非真 customs 素材在清关节点下宣称已完成受监管结果时，
    # 不放行模型那句，回退安全准备式模板，确保过度宣称无法进入渲染。
    overclaim_records = hotspot_preview_narration.apply_overclaim_guard(
        generated["scenes"], scenes, planning_brief.get("logistics_nodes") or [],
    )
    for scene, generated_scene in zip(scenes, generated["scenes"]):
        scene.update(generated_scene)
    scenes = hotspot_video_planner.append_brand_endcard_scenes(scenes)
    duration_ms = sum(int(item.get("duration_ms") or 0) for item in scenes)
    title = f"{generated['title']}｜{duration_ms // 1000}秒动态视频"
    project_snapshot = {
        "topic_brief_id": brief_id, "hotspot_event_id": event["id"], "brief": planning_brief,
        "model": model_router.get_route("planner_text").get("model"), "model_cache_hit": result.get("cache_hit", False),
        "copywriting_sop": douyin_copywriting_sop.metadata(),
        "overclaim_guard": overclaim_records,
        "adaptation": adaptation,
        "provenance": {
            "hotspot_video": hotspot_count,
            "owned_video": owned_count,
            "image": image_count,
            "duration_ms": duration_ms,
            "adapted": bool(adaptation.get("adapted")),
            "strategies": list(adaptation.get("strategies") or []),
        },
    }
    if source_snapshot:
        project_snapshot["chat"] = source_snapshot
    revision_payload = {
        "title": generated["title"], "platform": body.platform, "duration_target_ms": duration_ms,
        "target_duration_ms": duration_ms,
        "source_type": "topic_brief_dual_library",
        "brief": {**planning_brief, "angle": generated["angle"]}, "scenes": scenes,
        "adaptation": adaptation,
        "provenance": project_snapshot["provenance"],
    }
    if target_project_id and target_revision_id:
        updated_revision = db.update_video_project_revision_payload(
            target_revision_id,
            revision_payload,
            user["id"],
            title=title,
            target_duration_ms=duration_ms,
        )
        if not updated_revision:
            raise HTTPException(404, "视频项目修订不存在")
        project = db.get_video_project(target_project_id, created_by=user["id"])
    else:
        project = db.create_video_project(
            created_by=user["id"], source_type="topic_brief_dual_library",
            source_snapshot=project_snapshot,
            title=title, platform=body.platform, target_duration_ms=duration_ms, target_orientation="portrait",
        )
        db.create_video_project_revision(project["id"], revision_payload, user["id"])
        project = db.get_video_project(project["id"], created_by=user["id"])
    db.add_audit_log(user["id"], user["username"], "generate_topic_brief_video", target=project["id"], detail=f"brief={brief_id}; model_plan")
    return {
        "project": project,
        "model": {"cache_hit": result.get("cache_hit", False), "usage": result.get("usage")},
        "coverage": {
            "hotspot_video": hotspot_count,
            "owned_video": owned_count,
            "image": image_count,
            "duration_ms": duration_ms,
        },
        "adaptation": adaptation,
        "overclaim_guard": overclaim_records,
        "provenance": project_snapshot["provenance"],
    }


def _chat_dual_library_idempotency_key(
    topic: str,
    hook_event_ids: list[int],
    platform: str,
    target_duration_ms: int,
) -> str:
    canonical = _json.dumps(
        {
            "source": "ai_chat_dual_library_video",
            "topic": " ".join(str(topic or "").strip().split()),
            "hook_event_ids": [int(item) for item in hook_event_ids],
            "platform": str(platform or "douyin").strip().lower(),
            "target_duration_ms": int(target_duration_ms),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _video_project_snapshot(project: dict | None) -> dict:
    snapshot = (project or {}).get("source_snapshot") or {}
    if isinstance(snapshot, str):
        try:
            snapshot = _json.loads(snapshot)
        except _json.JSONDecodeError:
            return {}
    return snapshot if isinstance(snapshot, dict) else {}


def _build_video_generation_handlers(static_dir: Path):
    handlers = video_generation.build_default_handlers(static_dir)
    default_queued = handlers[video_generation.PipelineStage.QUEUED]

    def _chat_snapshot_for_job(job: dict) -> tuple[dict, dict]:
        project = db.get_video_project(job["project_id"], job["created_by"])
        snapshot = _video_project_snapshot(project)
        if snapshot.get("source") != "ai_chat" or not snapshot.get("async_generation"):
            return project or {}, {}
        return project or {}, snapshot

    async def queued(job: dict):
        _project, snapshot = await asyncio.to_thread(_chat_snapshot_for_job, job)
        if not snapshot:
            return await default_queued(job)
        report = dict(job.get("quality_report") or {})
        report["chat_generation"] = {
            "next_step": "生成主题简报",
            "stage": video_generation.PipelineStage.TOPIC_BRIEF.value,
        }
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.QUEUED],
            quality_report=report,
        )
        return video_generation.PipelineStage.TOPIC_BRIEF

    async def topic_brief(job: dict):
        _project, snapshot = await asyncio.to_thread(_chat_snapshot_for_job, job)
        if not snapshot:
            return video_generation.PipelineStage.PLANNING
        brief_id = str(snapshot.get("topic_brief_id") or "")
        brief = await asyncio.to_thread(db.get_topic_brief, brief_id, job["created_by"])
        if not brief:
            raise RuntimeError("聊天视频主题简报不存在，无法恢复生成任务")
        report = dict(job.get("quality_report") or {})
        report["chat_generation"] = {
            "topic_brief_id": brief_id,
            "topic_anchor": brief.get("subject") or brief.get("raw_input"),
            "next_step": "锁定已确认 Hook",
            "stage": video_generation.PipelineStage.TOPIC_BRIEF.value,
        }
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.TOPIC_BRIEF],
            quality_report=report,
        )
        return video_generation.PipelineStage.HOOK_LOCKING

    async def hook_locking(job: dict):
        _project, snapshot = await asyncio.to_thread(_chat_snapshot_for_job, job)
        if not snapshot:
            return video_generation.PipelineStage.PLANNING
        hook_ids = [int(item) for item in snapshot.get("matched_event_clip_ids") or []]
        events = [await asyncio.to_thread(db.get_hotspot_event_clip, event_id) for event_id in hook_ids]
        if not 1 <= len(events) <= 2 or any(event is None or not _is_confirmed_renderable_hotspot_hook(event) for event in events):
            raise RuntimeError("锁定的热点 Hook 已失效，请重新发起对话检索")
        ordered = sorted(events, key=lambda event: int(event["start_ms"]))
        if not _is_same_confirmed_hotspot_event(ordered):
            raise RuntimeError("锁定的热点 Hook 不属于同一已确认事件")
        if len(ordered) == 2 and int(ordered[1]["start_ms"]) < int(ordered[0]["end_ms"]):
            raise RuntimeError("锁定的热点 Hook 时间范围重叠")
        report = dict(job.get("quality_report") or {})
        report["chat_generation"] = {
            **(report.get("chat_generation") or {}),
            "locked_hook_event_ids": [int(item["id"]) for item in ordered],
            "next_step": "生成正式脚本",
            "stage": video_generation.PipelineStage.HOOK_LOCKING.value,
        }
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.HOOK_LOCKING],
            quality_report=report,
        )
        return video_generation.PipelineStage.SCRIPTING

    async def scripting(job: dict):
        project, snapshot = await asyncio.to_thread(_chat_snapshot_for_job, job)
        if not snapshot:
            return video_generation.PipelineStage.PLANNING
        hook_ids = [int(item) for item in snapshot.get("matched_event_clip_ids") or []]
        if not hook_ids:
            raise RuntimeError("缺少锁定 Hook，无法生成正式脚本")
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.SCRIPTING],
        )
        result = await _generate_topic_brief_video(
            str(snapshot["topic_brief_id"]),
            TopicBriefGenerateRequest(
                hotspot_event_id=hook_ids[0],
                approved_hook_event_ids=hook_ids,
                platform=str(project.get("platform") or snapshot.get("platform") or "douyin"),
                target_duration_ms=int(project.get("target_duration_ms") or snapshot.get("target_duration_ms") or 60_000),
            ),
            {
                "id": int(job["created_by"]),
                "username": str(snapshot.get("username") or "system"),
            },
            source_snapshot=snapshot,
            target_project_id=job["project_id"],
            target_revision_id=job["revision_id"],
        )
        latest = await asyncio.to_thread(db.get_video_generation_job, job["id"])
        report = dict((latest or job).get("quality_report") or {})
        report["chat_generation"] = {
            **(report.get("chat_generation") or {}),
            "model": result.get("model") or {},
            "coverage": result.get("coverage") or {},
            "adaptation": result.get("adaptation") or {},
            "provenance": result.get("provenance") or {},
            "next_step": "建项并进入质量门禁",
            "stage": video_generation.PipelineStage.SCRIPTING.value,
        }
        if result.get("adaptation"):
            report["adaptation"] = result["adaptation"]
        if result.get("provenance"):
            report["provenance"] = result["provenance"]
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.PROJECT_BUILDING],
            quality_report=report,
        )
        return video_generation.PipelineStage.PROJECT_BUILDING

    async def project_building(job: dict):
        report = dict(job.get("quality_report") or {})
        report["chat_generation"] = {
            **(report.get("chat_generation") or {}),
            "next_step": "脚本质量检查",
            "stage": video_generation.PipelineStage.PROJECT_BUILDING.value,
        }
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.PROJECT_BUILDING],
            quality_report=report,
        )
        return video_generation.PipelineStage.PLANNING

    handlers[video_generation.PipelineStage.QUEUED] = queued
    handlers[video_generation.PipelineStage.TOPIC_BRIEF] = topic_brief
    handlers[video_generation.PipelineStage.HOOK_LOCKING] = hook_locking
    handlers[video_generation.PipelineStage.SCRIPTING] = scripting
    handlers[video_generation.PipelineStage.PROJECT_BUILDING] = project_building
    return handlers


@app.post("/api/topic-briefs/{brief_id}/generate")
async def generate_topic_brief_video(brief_id: str, body: TopicBriefGenerateRequest, user=Depends(get_current_user)):
    return await _generate_topic_brief_video(brief_id, body, user)


@app.get("/api/hotspot-events/{event_id}/logistics-plan")
async def get_hotspot_logistics_plan(event_id: int, topic_brief_id: str | None = None, user=Depends(get_current_user)):
    """为当前热点生成动态物流角度和可渲染证据分镜，不直接调用付费模型。"""
    event = db.get_hotspot_event_clip(event_id)
    if not event:
        raise HTTPException(404, "热点事件不存在")
    owned_segments = [
        item for item in db.list_asset_segments(limit=20_000)
        if not item.get("asset_hotspot_id")
    ]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
    ]
    related_events = db.list_hotspot_event_clips(
        asset_id=event.get("asset_id"), hotspot_id=event.get("hotspot_id")
    )
    source_hotspot = db.get_hotspot(int(event["hotspot_id"])) or {}
    planning_event = {**source_hotspot, **event}
    topic_brief = db.get_topic_brief(topic_brief_id, user["id"]) if topic_brief_id else None
    if topic_brief_id and not topic_brief:
        raise HTTPException(404, "选题简报不存在")
    brief = hotspot_logistics_planner.build_brief(planning_event, owned_segments, topic_brief)
    brief.update({
        "hotspot_id": event.get("hotspot_id"),
        "source_asset_id": event.get("asset_id"),
        "primary_event_id": event.get("id"),
    })
    scenes = hotspot_video_planner.plan_followup_scenes(
        brief, related_events, owned_segments, owned_images=owned_images,
        allow_adaptation=True,
    )
    hotspot_video_count = sum(item.get("evidence_type") == "hotspot_video" for item in scenes)
    owned_video_count = sum(item.get("evidence_type") == "owned_video" for item in scenes)
    image_count = sum(item.get("evidence_type") == "image" for item in scenes)
    planned_duration_ms = sum(int(item.get("duration_ms") or 0) for item in scenes)
    cta_duration_ms = sum(int(item["duration_ms"]) for item in hotspot_video_planner.BRAND_ENDCARD_SCENES)
    final_duration_ms = planned_duration_ms + cta_duration_ms
    adaptation = hotspot_video_planner.describe_plan_adaptation(scenes)
    return {
        "event": _decorate_hotspot_event(event),
        "brief": brief,
        "scenes": scenes,
        "adaptation": adaptation,
        "evidence_summary": {
            "hotspot_video": hotspot_video_count,
            "owned_video": owned_video_count,
            "images": image_count,
            "planned_duration_ms": planned_duration_ms,
            "cta_duration_ms": cta_duration_ms,
            "duration_ms": final_duration_ms,
            "ready": hotspot_video_count >= 1 and bool(scenes),
            "ideal_ready": (
                hotspot_video_count >= 1 and owned_video_count >= 4
                and image_count <= 3
                and (not final_duration_ms or image_count * hotspot_video_planner.CONTEXT_IMAGE_DURATION_MS / final_duration_ms <= 0.15)
                and 50_000 <= final_duration_ms <= 90_000
            ),
        },
    }


@app.put("/api/asset-segments/{segment_id}/classification")
async def update_segment_classification(
    segment_id: int, req: SegmentClassificationRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    if req.primary_category not in asset_processing.PRIMARY_CATEGORIES:
        raise HTTPException(400, "主分类无效")
    segment = db.get_asset_segment(segment_id)
    if not segment:
        raise HTTPException(404, "镜头不存在")
    tags = []
    for item in req.tags:
        dimension, value = str(item.get("dimension") or "").strip(), str(item.get("value") or "").strip()
        if not dimension or not value:
            continue
        tags.append({
            "dimension": dimension[:40], "value": value[:100], "confidence": 1.0,
            "source": "manual", "confirmed": True,
        })
    db.update_asset_segment_classification(segment_id, req.primary_category, req.quality_score)
    db.replace_segment_tags(segment_id, tags, updated_by=user["id"])
    db.add_audit_log(user["id"], user["username"], "classify_asset_segment", target=str(segment_id))
    return db.get_asset_segment(segment_id)


@app.post("/api/assets/{asset_id}/classify-all")
async def classify_all_asset_segments(
    asset_id: int, req: AssetClassifyAllRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    """一键打标：把同一主场景（及可选细标签）应用到母片全部镜头。"""
    if req.primary_category not in asset_processing.PRIMARY_CATEGORIES:
        raise HTTPException(400, "主分类无效")
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    try:
        result = db.classify_all_asset_segments(
            asset_id,
            req.primary_category,
            req.tags,
            replace_tags=bool(req.replace_tags),
            updated_by=user["id"],
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(404 if ("不存在" in detail or "尚无" in detail) else 400, detail)
    db.add_audit_log(
        user["id"], user["username"], "classify_all_asset_segments",
        target=str(asset_id),
        detail=f"{result['updated']}/{result['total']} {req.primary_category}",
    )
    asset = db.get_asset(asset_id)
    public = media_assets.public_asset(asset) if asset else {"id": asset_id}
    public["brand_tags"] = db.list_asset_brand_tags([asset_id]).get(asset_id, [])
    public["segment_count"] = result["total"]
    return {**result, "asset": public}


@app.post("/api/semantic-match", status_code=201)
async def create_semantic_match(req: SemanticMatchRequest, user=Depends(get_current_user)):
    payload = req.model_dump()
    # 与项目创建和渲染器保持一致：兼容旧客户端的横屏参数，但素材匹配
    # 始终按 9:16 成片裁切需求评估，不能把一条用户视频分成不同画幅。
    payload["orientation"] = "portrait"
    atoms = semantic_matching.build_semantic_atoms(payload)
    if not atoms:
        raise HTTPException(400, "请提供口播文案或结构化分镜")
    assignments = semantic_matching.assign_candidates(atoms, db.list_asset_segments(limit=20_000), top_k=3)
    session_id = db.create_match_session(user["id"], payload)
    for assignment in assignments:
        atom_id = db.create_semantic_atom(session_id, assignment)
        db.replace_match_candidates(atom_id, assignment["candidates"])
    db.add_audit_log(user["id"], user["username"], "semantic_asset_match", target=session_id)
    return db.get_match_session(session_id, created_by=user["id"])


@app.get("/api/semantic-match/{session_id}")
async def get_semantic_match(session_id: str, user=Depends(get_current_user)):
    session = db.get_match_session(session_id, created_by=user["id"])
    if not session:
        raise HTTPException(404, "匹配会话不存在")
    return session


@app.put("/api/semantic-match/{session_id}/atoms/{atom_id}")
async def select_semantic_match(
    session_id: str, atom_id: int, req: MatchSelectionRequest,
    user=Depends(get_current_user),
):
    session = db.get_match_session(session_id, created_by=user["id"])
    atom = next((item for item in (session or {}).get("atoms", []) if item["id"] == atom_id), None)
    if not atom:
        raise HTTPException(404, "匹配会话或语义片段不存在")
    candidate_ids = {item["segment_id"] for item in atom["candidates"]}
    if req.segment_id is not None and req.segment_id not in candidate_ids:
        raise HTTPException(400, "只能选择本片段的候选镜头")
    db.update_semantic_atom_selection(atom_id, req.segment_id, req.locked, req.review_confirmed)
    db.add_match_feedback(session_id, atom_id, req.segment_id, user["id"], req.action, req.reason[:300])
    return db.get_match_session(session_id, created_by=user["id"])


async def _store_inspiration(req: InspirationCreateRequest, user: dict) -> tuple[dict, bool]:
    try:
        canonical = inspiration_assets.normalize_url(req.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    metadata = {}
    try:
        metadata = await inspiration_assets.fetch_oembed(canonical)
    except Exception:
        logger.info("公开 oEmbed 元数据读取失败，保留用户输入: %s", canonical)
    source_type = inspiration_assets.source_type_for(canonical)
    secondary_discovery = source_type == "secondary_discovery"
    inspiration_id, created = db.upsert_inspiration_item({
        "source_type": source_type,
        "source_role": "hotspot_discovery" if secondary_discovery else "creative_reference",
        "source_url": req.url,
        "canonical_url": canonical,
        "title": req.title.strip() or metadata.get("title") or canonical,
        "summary": req.summary.strip(),
        "author": metadata.get("author") or "",
        "thumbnail_url": metadata.get("thumbnail_url"),
        "media_kind": "video_link" if source_type in {"youtube", "tiktok"} else "link",
        "primary_category": req.primary_category if req.primary_category in asset_processing.PRIMARY_CATEGORIES else None,
        "rights_status": "restricted" if secondary_discovery else "unknown",
        "materialization_status": "reference_only",
        "created_by": user["id"],
    })
    return db.get_inspiration_item(inspiration_id), created


@app.get("/api/inspirations")
async def list_inspirations(query: str = "", source_type: str = None, limit: int = 200,
                            user=Depends(get_current_user)):
    return db.list_inspiration_items(query=query.strip(), source_type=source_type, limit=limit)


@app.post("/api/inspirations")
async def create_inspiration(req: InspirationCreateRequest, response: Response,
                             user=Depends(get_current_user)):
    item, created = await _store_inspiration(req, user)
    response.status_code = 201 if created else 200
    db.add_audit_log(user["id"], user["username"], "add_inspiration", target=str(item["id"]))
    return {**item, "created": created}


@app.post("/api/inspirations/batch")
async def create_inspiration_batch(req: InspirationBatchRequest, user=Depends(get_current_user)):
    results, errors = [], []
    for index, item in enumerate(req.items):
        try:
            stored, created = await _store_inspiration(item, user)
            results.append({**stored, "created": created})
        except HTTPException as exc:
            errors.append({"index": index, "url": item.url, "error": exc.detail})
    db.add_audit_log(user["id"], user["username"], "batch_add_inspirations",
                     detail=f"success={len(results)}, errors={len(errors)}")
    return {"items": results, "errors": errors}


@app.put("/api/inspirations/{inspiration_id}/rights")
async def confirm_inspiration_rights(
    inspiration_id: int, req: InspirationRightsRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    if not db.get_inspiration_item(inspiration_id):
        raise HTTPException(404, "灵感链接不存在")
    if req.rights_status not in {"confirmed", "licensed", "rejected"}:
        raise HTTPException(400, "版权状态无效")
    try:
        evidence_url = inspiration_assets.normalize_url(req.rights_evidence_url)
    except ValueError as exc:
        raise HTTPException(400, f"授权证据链接无效：{exc}") from exc
    if req.rights_status != "rejected" and (not req.license_name.strip() or not req.attribution.strip()):
        raise HTTPException(400, "必须填写授权名称与署名")
    db.update_inspiration_rights(
        inspiration_id, req.rights_status, req.license_name.strip()[:200],
        req.attribution.strip()[:300], evidence_url, user["id"],
    )
    db.add_audit_log(user["id"], user["username"], "confirm_inspiration_rights", target=str(inspiration_id))
    return db.get_inspiration_item(inspiration_id)


async def _run_inspiration_materialization(inspiration_id: int, created_by: int):
    item = db.get_inspiration_item(inspiration_id)
    try:
        asset = await asyncio.to_thread(inspiration_assets.download_authorized_media, item, STATIC_DIR, created_by)
        db.update_inspiration_materialization(inspiration_id, "materialized", asset["id"])
        job_id = db.create_asset_processing_job(asset["id"], created_by, asset_processing.PROCESSING_VERSION)
        await _run_asset_processing_job(job_id)
    except Exception:
        db.update_inspiration_materialization(inspiration_id, "failed")
        logger.exception("已授权外部素材下载失败: %s", inspiration_id)


@app.post("/api/inspirations/{inspiration_id}/materialize", status_code=202)
async def materialize_inspiration(
    inspiration_id: int, req: InspirationMaterializeRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    item = db.get_inspiration_item(inspiration_id)
    if not item:
        raise HTTPException(404, "灵感链接不存在")
    try:
        inspiration_assets.validate_materialization(item, user["role"], req.confirmed)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if item["materialization_status"] in {"pending", "materialized"}:
        raise HTTPException(409, "该链接正在素材化或已经完成")
    db.update_inspiration_materialization(inspiration_id, "pending")
    asyncio.create_task(_run_inspiration_materialization(inspiration_id, user["id"]))
    db.add_audit_log(user["id"], user["username"], "materialize_inspiration", target=str(inspiration_id))
    return {"id": inspiration_id, "status": "pending"}


@app.get("/api/hotspots")
async def list_hotspots(limit: int = 100, user=Depends(get_current_user)):
    return db.list_hotspots(max(1, min(limit, 500)))


@app.get("/api/hotspots/fetch-status")
async def get_hotspot_fetch_status(user=Depends(get_current_user)):
    return {"run": db.get_latest_hotspot_fetch_run()}


@app.get("/api/hotspots/{hotspot_id}")
async def get_hotspot_detail(hotspot_id: int, user=Depends(get_current_user)):
    hotspot = db.get_hotspot(hotspot_id)
    if not hotspot:
        raise HTTPException(404, "热点不存在")
    return hotspot


def _translation_payload(content: str) -> tuple[str, str]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = _json.loads(raw)
    except Exception as exc:
        raise ValueError("翻译模型未返回合法 JSON") from exc
    title_zh = str(payload.get("title_zh") or "").strip()
    summary_zh = str(payload.get("summary_zh") or "").strip()
    if not title_zh or not summary_zh:
        raise ValueError("翻译结果缺少中文标题或摘要")
    return title_zh[:500], summary_zh[:4_000]


@app.post("/api/hotspots/{hotspot_id}/translate")
async def translate_hotspot(hotspot_id: int, user=Depends(get_current_user)):
    hotspot = db.get_hotspot(hotspot_id)
    if not hotspot:
        raise HTTPException(404, "热点不存在")
    if (
        hotspot.get("translation_status") == "ready"
        and hotspot.get("translation_snapshot_sha256") == hotspot.get("snapshot_sha256")
        and hotspot.get("title_zh") and hotspot.get("summary_zh")
    ):
        return {**hotspot, "translation_cache_hit": True}
    db.update_hotspot_translation_status(hotspot_id, "translating")
    messages = [
        {
            "role": "system",
            "content": (
                "把南非新闻标题和摘要准确翻译成简体中文。不得补充原文没有的事实。"
                "只返回 JSON：{\"title_zh\":\"\",\"summary_zh\":\"\"}。"
            ),
        },
        {
            "role": "user",
            "content": f"英文标题：{hotspot['title']}\n英文摘要：{hotspot.get('summary') or ''}",
        },
    ]
    job_id = model_router.route_scoped_job_id(
        f"hotspot-translation-{hotspot_id}-{hotspot['snapshot_sha256'][:12]}", "planner_text"
    )
    try:
        result = await model_router.call_text(
            job_id, "planner_text", messages, prompt_version="hotspot-translation-v1"
        )
        title_zh, summary_zh = _translation_payload(result["content"])
        model = model_router.get_route("planner_text").get("model") or "configured"
        db.update_hotspot_translation(
            hotspot_id, title_zh, summary_zh, hotspot["snapshot_sha256"], model
        )
    except Exception as exc:
        db.update_hotspot_translation_status(hotspot_id, "failed")
        logger.exception("热点翻译失败: %s", hotspot_id)
        raise HTTPException(502, f"热点翻译失败：{str(exc)[:160]}") from exc
    return {**db.get_hotspot(hotspot_id), "translation_cache_hit": bool(result.get("cache_hit"))}


@app.get("/api/hotspot-media")
async def list_hotspot_media(
    hotspot_id: int | None = None,
    media_kind: str | None = None,
    authorization_status: str | None = None,
    limit: int = 200,
    user=Depends(get_current_user),
):
    if media_kind and media_kind not in {"image", "video_link", "video_file"}:
        raise HTTPException(400, "热点素材类型无效")
    if authorization_status and authorization_status not in {"authorized", "pending_review", "blocked"}:
        raise HTTPException(400, "热点素材授权状态无效")
    items = db.list_hotspot_media(
        hotspot_id=hotspot_id,
        media_kind=media_kind,
        authorization_status=authorization_status,
        limit=limit,
    )
    for item in items:
        item["preview_url"] = item.get("thumbnail_url")
        if item.get("asset_id"):
            asset = db.get_asset(item["asset_id"])
            local_preview = (asset or {}).get("thumbnail") or (asset or {}).get("filepath")
            if local_preview:
                item["preview_url"] = (
                    local_preview if str(local_preview).startswith("/static/")
                    else "/static/" + str(local_preview).lstrip("/")
                )
    return items


def _hotspot_library_file_summary(paths: list[str]) -> dict:
    """Resolve only paths under static before showing or deleting local files."""
    targets: dict[str, Path] = {}
    for value in paths:
        target = media_retention._safe_media_path(STATIC_DIR, value)
        if target is not None:
            targets[str(target)] = target
    existing = [target for target in targets.values() if target.is_file()]
    return {
        "local_file_count": len(existing),
        "estimated_bytes": sum(target.stat().st_size for target in existing),
        "safe_path_count": len(targets),
    }


def _delete_hotspot_library_files(paths: list[str]) -> dict:
    targets: dict[str, Path] = {}
    for value in paths:
        target = media_retention._safe_media_path(STATIC_DIR, value)
        if target is not None:
            targets[str(target)] = target
    deleted_count = 0
    released_bytes = 0
    errors: list[str] = []
    for target in targets.values():
        try:
            if target.is_file():
                released_bytes += target.stat().st_size
                target.unlink()
                deleted_count += 1
        except OSError as exc:
            errors.append(f"{target.name}: {str(exc)[:120]}")
    return {
        "local_file_count": deleted_count,
        "released_bytes": released_bytes,
        "file_errors": errors,
    }


@app.get("/api/hotspot-library/cleanup-preview")
async def get_hotspot_library_cleanup_preview(user=Depends(require_role(UserRole.ADMIN))):
    preview = db.hotspot_library_cleanup_preview()
    return {**preview, **_hotspot_library_file_summary(preview.pop("file_paths"))}


@app.delete("/api/hotspot-library")
async def clear_hotspot_library(
    req: HotspotLibraryClearRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    if req.confirmation.strip() != "清空热点素材库":
        raise HTTPException(422, "请输入“清空热点素材库”后再执行")
    if db.hotspot_library_media_is_busy():
        raise HTTPException(409, "仍有热点素材正在下载或分析，请等待任务完成后再清空")
    result = db.delete_hotspot_library()
    assert result is not None
    file_result = _delete_hotspot_library_files(result.pop("file_paths"))
    db.add_audit_log(
        user["id"], user["username"], "clear_hotspot_library",
        detail=_json.dumps({**result, **file_result}, ensure_ascii=False),
    )
    return {"status": "deleted", **result, **file_result}


@app.delete("/api/hotspot-media/{media_id}")
async def delete_hotspot_media_item(
    media_id: int,
    user=Depends(require_role(UserRole.ADMIN)),
):
    if db.hotspot_library_media_is_busy(media_id):
        raise HTTPException(409, "该热点素材正在下载或分析，暂不能删除")
    result = db.delete_hotspot_library(media_id=media_id)
    if result is None:
        raise HTTPException(404, "热点素材不存在")
    file_result = _delete_hotspot_library_files(result.pop("file_paths"))
    db.add_audit_log(
        user["id"], user["username"], "delete_hotspot_media",
        target=str(media_id), detail=_json.dumps({**result, **file_result}, ensure_ascii=False),
    )
    return {"status": "deleted", **result, **file_result}


@app.delete("/api/hotspot-event-assets/{asset_id}")
async def delete_hotspot_event_asset(
    asset_id: int,
    user=Depends(require_role(UserRole.ADMIN)),
):
    result = db.delete_hotspot_event_asset(asset_id)
    if result is None:
        raise HTTPException(404, "热点事件源素材不存在")
    file_result = _delete_hotspot_library_files(result.pop("file_paths"))
    db.add_audit_log(
        user["id"], user["username"], "delete_hotspot_event_asset",
        target=str(asset_id), detail=_json.dumps({**result, **file_result}, ensure_ascii=False),
    )
    return {"status": "deleted", **result, **file_result}


@app.delete("/api/hotspot-events/{event_id}")
async def delete_hotspot_event_clip(
    event_id: int,
    user=Depends(require_role(UserRole.ADMIN)),
):
    """Delete one Hook card only; its mother source and sibling Hooks remain."""
    result = db.delete_hotspot_event_clip(event_id)
    if result is None:
        raise HTTPException(404, "热点 Hook 不存在或已删除")
    file_result = _delete_hotspot_library_files(result.pop("file_paths"))
    db.add_audit_log(
        user["id"], user["username"], "delete_hotspot_event_clip",
        target=str(event_id), detail=_json.dumps({**result, **file_result}, ensure_ascii=False),
    )
    return {"status": "deleted", **result, **file_result}


@app.post("/api/hotspot-media/{media_id}/prepare", status_code=202)
async def prepare_hotspot_media(media_id: int, user=Depends(require_role(UserRole.ADMIN))):
    try:
        result = hotspot_package_service.prepare_media(media_id, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    asyncio.create_task(_run_hotspot_media_materialization(media_id, user["id"]))
    return result


@app.post("/api/hotspots/{hotspot_id}/media/discover")
async def discover_hotspot_media(
    hotspot_id: int,
    user=Depends(require_role(UserRole.ADMIN)),
):
    hotspot = db.get_hotspot(hotspot_id)
    if not hotspot:
        raise HTTPException(404, "热点不存在")
    try:
        html, final_url = await hotspot_media.fetch_source_page(hotspot["source_url"])
        candidates = hotspot_media.discover_media_candidates(html, final_url)
        candidates, skipped_unavailable = await hotspot_media.filter_reachable_image_candidates(
            candidates
        )
    except Exception as exc:
        raise HTTPException(502, f"热点媒体发现失败：{str(exc)[:200]}") from exc
    items, created_count = [], 0
    for candidate in candidates:
        media_id, created = db.upsert_hotspot_media({
            **candidate,
            "hotspot_id": hotspot_id,
            "publisher": hotspot.get("publisher") or "",
            "author": hotspot.get("publisher") or "",
            "published_at": hotspot.get("published_at"),
            "authorization_status": "pending_review",
            "download_status": "metadata_ready",
            "processing_status": "not_started",
        })
        created_count += int(created)
        items.append(db.get_hotspot_media(media_id))
    db.add_audit_log(
        user["id"], user["username"], "discover_hotspot_media", target=str(hotspot_id),
        detail=f"created={created_count}, total={len(items)}",
    )
    return {
        "created": created_count,
        "items": items,
        "skipped_unavailable": skipped_unavailable,
    }


@app.post("/api/hotspots/{hotspot_id}/media/attach", status_code=201)
async def attach_hotspot_video(
    hotspot_id: int,
    req: HotspotMediaAttachRequest,
    response: Response,
    user=Depends(require_role(UserRole.ADMIN)),
):
    hotspot = db.get_hotspot(hotspot_id)
    if not hotspot:
        raise HTTPException(404, "热点不存在")
    try:
        canonical, platform, platform_id = hotspot_media.normalize_video_url(req.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    metadata = {}
    if platform in {"youtube", "tiktok"}:
        try:
            metadata = await inspiration_assets.fetch_oembed(canonical)
        except Exception:
            logger.info("热点视频公开元数据读取失败，保留链接: %s", canonical)
    media_id, created = db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": platform,
        "platform_media_id": platform_id,
        "source_page_url": hotspot["source_url"],
        "original_media_url": canonical,
        "thumbnail_url": metadata.get("thumbnail_url"),
        "publisher": metadata.get("author") or hotspot.get("publisher") or "",
        "author": metadata.get("author") or "",
        "published_at": hotspot.get("published_at"),
        "authorization_status": "pending_review",
        "download_status": "metadata_ready",
        "processing_status": "not_started",
    })
    response.status_code = 201 if created else 200
    db.add_audit_log(user["id"], user["username"], "attach_hotspot_video", target=str(media_id))
    return db.get_hotspot_media(media_id)


@app.put("/api/hotspot-media/{media_id}/rights")
async def update_hotspot_media_rights(
    media_id: int,
    req: HotspotMediaRightsRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    item = db.get_hotspot_media(media_id)
    if not item:
        raise HTTPException(404, "热点素材不存在")
    if req.rights_evidence_url:
        try:
            hotspot_media._validate_public_https(req.rights_evidence_url)
        except ValueError as exc:
            raise HTTPException(400, f"授权证据链接无效：{exc}") from exc
    db.update_hotspot_media_authorization(
        media_id,
        req.authorization_status,
        req.rights_note.strip(),
        req.license_name.strip() or None,
        req.attribution.strip() or None,
        req.rights_evidence_url.strip() or None,
        user["id"],
    )
    db.add_audit_log(user["id"], user["username"], "update_hotspot_media_authorization", target=str(media_id))
    return db.get_hotspot_media(media_id)


def _remap_hooks_to_original_timestamps(
    hooks: list[dict],
    sample_offsets: list[tuple[float, float]] | None,
) -> list[dict]:
    """分析件相对时间 → 原片真实时间；保留 analysis_* 供回退切片。"""
    import inspiration_assets

    windows = [(float(a), float(b)) for a, b in (sample_offsets or [])]
    if not windows:
        return hooks
    remapped = []
    for hook in hooks:
        analysis_start = int(hook.get("start_ms") or 0)
        analysis_end = int(hook.get("end_ms") or 0)
        evidence = dict(hook.get("evidence") or {})
        evidence["analysis_start_ms"] = analysis_start
        evidence["analysis_end_ms"] = analysis_end
        evidence["sample_offsets"] = windows
        remapped.append({
            **hook,
            "start_ms": inspiration_assets.analysis_ms_to_original_ms(analysis_start, windows),
            "end_ms": inspiration_assets.analysis_ms_to_original_ms(analysis_end, windows),
            "evidence": evidence,
        })
    return remapped


async def _run_hotspot_media_materialization(media_id: int, created_by: int):
    item = db.get_hotspot_media(media_id)
    if not item:
        return
    # yt-dlp is a blocking library executed in a worker thread.  A timeout can
    # return control before that thread exits, therefore a late progress event
    # must never overwrite the terminal timeout state in the database.
    download_state_lock = threading.Lock()
    download_timed_out = False
    sample_offsets: list[tuple[float, float]] = []
    try:
        # 单个外部媒体源不可无限占用三天全量队列。yt-dlp 的 socket timeout
        # 只能覆盖网络读写，无法约束解析器/源站卡住的总时长；这里由项目工作流
        # 施加总超时，失败后记录原因并继续分析下一条授权资讯视频。
        download_timeout = max(
            1, int(os.environ.get("HOTSPOT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", "300"))
        )
        asset = None
        if item.get("download_status") == "downloaded" and item.get("asset_id"):
            asset = db.get_asset(int(item["asset_id"]))
            intake = _normalized_hotspot_intake_decision(item.get("intake_decision_json"))
            raw_offsets = intake.get("sample_offsets") or []
            try:
                sample_offsets = [(float(pair[0]), float(pair[1])) for pair in raw_offsets]
            except (TypeError, ValueError, IndexError, KeyError):
                sample_offsets = []
        if asset:
            db.update_hotspot_media_state(
                media_id, download_status="downloaded", download_progress=65,
                progress_detail="复用已下载母片，继续内置模型分析",
                processing_status="processing", error_message=None,
            )
        else:
            # 下载前纯元数据预筛：音乐片/超短/超长直播直接跳过，不进 H-hit 分母。
            if item.get("media_kind") != "image":
                allowed, reason = hotspot_media.prefilter_mother_candidate(item)
                if not allowed:
                    logger.info("prefilter skip id=%s reason=%s", media_id, reason)
                    db.update_hotspot_media_state(
                        media_id,
                        download_status="prefiltered_skip",
                        processing_status="not_started",
                        download_progress=0,
                        progress_detail=f"prefilter skip: {reason}",
                        error_message=None,
                    )
                    return
            db.update_hotspot_media_state(
                media_id, download_status="downloading", download_progress=10,
                progress_detail="正在连接媒体来源", processing_status="not_started", error_message=None
            )
            downloader = (
                hotspot_media.download_authorized_image
                if item.get("media_kind") == "image"
                else hotspot_media.download_authorized_video
            )
            if item.get("media_kind") == "image":
                asset = await asyncio.wait_for(
                    asyncio.to_thread(downloader, item, STATIC_DIR, created_by),
                    timeout=download_timeout,
                )
            else:
                last_progress = 10

                def report_download_progress(event):
                    nonlocal last_progress
                    with download_state_lock:
                        if download_timed_out:
                            return
                        if event.get("status") == "finished":
                            last_progress = 60
                            db.update_hotspot_media_state(
                                media_id, download_progress=60, progress_detail="下载完成，正在合并媒体"
                            )
                            return
                        downloaded = int(event.get("downloaded_bytes") or 0)
                        total = int(event.get("total_bytes") or event.get("total_bytes_estimate") or 0)
                        if total <= 0:
                            return
                        progress = min(59, max(12, 10 + round(downloaded / total * 49)))
                        if progress < last_progress + 2:
                            return
                        last_progress = progress
                        db.update_hotspot_media_state(
                            media_id,
                            download_progress=progress,
                            progress_detail=f"已下载 {downloaded / 1048576:.1f} / {total / 1048576:.1f} MB",
                        )

                asset = await asyncio.wait_for(
                    asyncio.to_thread(
                        downloader, item, STATIC_DIR, created_by, report_download_progress,
                    ),
                    timeout=download_timeout,
                )
                raw_offsets = asset.get("sample_offsets") or []
                try:
                    sample_offsets = [(float(pair[0]), float(pair[1])) for pair in raw_offsets]
                except (TypeError, ValueError, IndexError, KeyError):
                    sample_offsets = []
                # Persist sample windows on the media row so resume/retry can remap timestamps.
                # analysis_clip_seconds records the downloaded analysis file length;
                # never let it overwrite original duration_seconds (channel metadata).
                intake = _normalized_hotspot_intake_decision(item.get("intake_decision_json"))
                intake["sample_offsets"] = sample_offsets
                intake["analysis_height"] = int(asset.get("height") or 0) or None
                if asset.get("duration") is not None:
                    try:
                        intake["analysis_clip_seconds"] = float(asset["duration"])
                    except (TypeError, ValueError):
                        pass
                db.update_hotspot_media_state(
                    media_id,
                    intake_decision_json=_json.dumps(intake, ensure_ascii=False),
                )
                item = {**item, "intake_decision_json": _json.dumps(intake, ensure_ascii=False)}
        reusable_segments = []
        if asset and asset.get("file_type") == "video":
            reusable_segments = [
                segment for segment in db.list_asset_segments(asset_id=asset["id"], limit=500)
                if segment.get("processing_version") == asset_processing.PROCESSING_VERSION
                and str(segment.get("description") or "").strip()
            ]
        has_reusable_analysis = bool(reusable_segments)
        db.set_asset_retention(
            asset["id"],
            "hotspot_source",
            int(os.environ.get("HOTSPOT_SOURCE_RETENTION_DAYS", "7")),
        )
        if not has_reusable_analysis:
            db.update_asset_semantic_state(
                asset["id"], "other", "pending", rights_status="licensed",
            )
        state = {
            "media_kind": "image" if asset.get("file_type") == "image" else "video_file",
            "local_path": asset.get("filepath"),
            "width": asset.get("width"),
            "height": asset.get("height"),
            "sha256": asset.get("sha256"),
            "asset_id": asset["id"],
            "download_status": "downloaded",
            "download_progress": 65,
            "progress_detail": (
                "复用已完成的内置镜头分析，重试 Hook 策展"
                if has_reusable_analysis
                else "本地文件已保存，准备分析镜头"
            ),
            "processing_status": "processing",
            "error_message": None,
        }
        # Preserve original duration_seconds from channel metadata. Analysis clips
        # may be only 60/120s; writing that back would break re-runs and planners.
        original_duration = float(item.get("duration_seconds") or 0)
        analysis_clip = asset.get("duration")
        if original_duration <= 0 and analysis_clip is not None and not sample_offsets:
            # Full-file download with no prior metadata: safe to record measured length.
            state["duration_seconds"] = analysis_clip
        if asset.get("file_type") == "video":
            state["mime_type"] = "video/mp4"
        db.update_hotspot_media_state(media_id, **state)
        if not has_reusable_analysis:
            job_id = db.create_asset_processing_job(
                asset["id"], created_by, asset_processing.PROCESSING_VERSION
            )
            await _run_asset_processing_job(job_id)
            job = db.get_asset_processing_job(job_id) or {}
            if job.get("status") != "succeeded":
                raise RuntimeError(job.get("error") or "热点视频分析未完成")
        hook_count = 0
        curation_status = "图片素材已完成分析"
        if asset.get("file_type") == "video":
            hotspot = db.get_hotspot(int(item.get("hotspot_id") or 0)) or {}
            segments = reusable_segments or db.list_asset_segments(asset_id=asset["id"], limit=500)
            intake_decision = _normalized_hotspot_intake_decision(item.get("intake_decision_json"))
            expected_hook = str(intake_decision.get("expected_hook") or "").strip()
            source_context = "\n".join(value for value in (
                f"本轮已获准的画面范围：{expected_hook}" if expected_hook else "",
                f"本轮物流切入问题：{str(intake_decision.get('logistics_question') or '').strip()}" if expected_hook else "",
                f"Buffalo RAG 仅支持的可见服务边界：{str(intake_decision.get('service_fit') or '').strip()}" if expected_hook else "",
                str(item.get("intake_title") or "").strip() if not expected_hook else "",
                str(item.get("intake_summary") or "").strip() if not expected_hook else "",
                str(hotspot.get("title_zh") or hotspot.get("title") or "").strip() if not expected_hook else "",
                str(hotspot.get("summary_zh") or hotspot.get("summary") or "").strip() if not expected_hook else "",
            ) if value)[:1200]
            # Hook 策展是可重试的下游步骤：模型临时不可用不能把已经完成的下载和
            # 镜头分析误记成失败，也绝不能回退为机械切片入库。
            try:
                hooks, curation = await asyncio.to_thread(
                    hotspot_hook_curator.curate_hook_clips,
                    int(asset["id"]),
                    str(hotspot.get("title_zh") or hotspot.get("title") or ""),
                    segments,
                    source_context,
                )
            except Exception as exc:
                logger.warning("热点 Hook 策展未完成，母片不入 Hook 库: %s", exc)
                hooks, curation = [], {
                    "status": "temporarily_unavailable",
                    "reason": f"内置 Hook 策展暂时不可用：{str(exc)[:120]}",
                }
            hook_count = len(hooks)
            if hooks:
                # Multi-window analysis mothers use relative timestamps; remap
                # confirmed hooks back to original-video time before persisting.
                hooks = _remap_hooks_to_original_timestamps(hooks, sample_offsets)
                # A Hook inherits the admission decision that caused its mother
                # to be downloaded.  This keeps the RAG service boundary with
                # the short reusable clip instead of losing it after analysis.
                for hook in hooks:
                    evidence = dict(hook.get("evidence") or {})
                    evidence.update({
                        "service_fit": str(intake_decision.get("service_fit") or "")[:120],
                        "rag_evidence_ids": list(intake_decision.get("rag_evidence_ids") or [])[:12],
                        "admission_mode": str(intake_decision.get("admission_mode") or "")[:24],
                        "sop_version": str(intake_decision.get("sop_version") or "")[:32],
                    })
                    hook["evidence"] = evidence
                hotspot_event_media.remove_materialized_event_clips(STATIC_DIR, int(asset["id"]))
                created = db.replace_hotspot_event_clips(asset["id"], int(item["hotspot_id"]), hooks)
                await asyncio.to_thread(
                    hotspot_event_media.materialize_event_clips,
                    STATIC_DIR,
                    asset,
                    created,
                    media_item=item,
                    sample_offsets=sample_offsets,
                )
                curation_status = f"内置模型已筛出 {hook_count} 条精华 Hook 片段"
            else:
                # 未通过模型策展的镜头不会以“待确认事件”形式进入 Hook 素材库。
                curation_status = "镜头已分析，但内置模型未筛出可复用 Hook"
                if curation.get("reason"):
                    curation_status += f"：{curation['reason']}"
                # 仅当模型完成了有效决策并拒绝全部候选时才清空旧 Hook；模型服务
                # 暂时不可用时保留已经验证过的素材，等待后续任务重试。
                if curation.get("status") == "no_qualified_hooks":
                    hotspot_event_media.remove_materialized_event_clips(STATIC_DIR, int(asset["id"]))
                    db.replace_hotspot_event_clips(asset["id"], int(item["hotspot_id"]), [])
        retryable_curation_failure = (
            asset.get("file_type") == "video"
            and (curation or {}).get("status") == "temporarily_unavailable"
        )
        db.update_hotspot_media_state(
            media_id,
            processing_status="processing_failed" if retryable_curation_failure else "ready",
            download_progress=100,
            progress_detail=curation_status,
            error_message=curation_status if retryable_curation_failure else None,
        )
    except TimeoutError as exc:
        with download_state_lock:
            download_timed_out = True
        current = db.get_hotspot_media(media_id) or {}
        downloaded = current.get("download_status") == "downloaded"
        message = f"下载超时：超过 {download_timeout} 秒仍未完成，已跳过并继续后续素材"
        db.update_hotspot_media_state(
            media_id,
            download_status="downloaded" if downloaded else "download_failed",
            processing_status="processing_failed" if downloaded else "not_started",
            progress_detail=message,
            error_message=message,
        )
        logger.warning("热点媒体下载超时: %s", media_id)
    except Exception as exc:
        current = db.get_hotspot_media(media_id) or {}
        downloaded = current.get("download_status") == "downloaded"
        message = str(exc)
        # 最新窗暂不可用：可重试，不按硬失败永久丢弃（突发新闻台延后重试，常青台可再扫深处）。
        if (not downloaded) and hotspot_video_sources.is_not_available_error(message):
            retry_after = hotspot_video_sources.retry_after_iso()
            db.update_hotspot_media_state(
                media_id,
                download_status="materialization_retryable",
                processing_status="not_started",
                progress_detail=f"可重试：视频暂不可下载，将于 {retry_after} 后重试。{message[:120]}",
                error_message=message[:500],
                materialization_retryable=1,
                retry_after=retry_after,
            )
            logger.warning("热点媒体暂不可用，已标记 materialization_retryable: %s", media_id)
        else:
            db.update_hotspot_media_state(
                media_id,
                download_status="downloaded" if downloaded else "download_failed",
                processing_status="processing_failed" if downloaded else "not_started",
                progress_detail=f"处理失败：{message[:180]}",
                error_message=message[:500],
                materialization_retryable=0,
                retry_after=None,
            )
            logger.exception("热点媒体素材化失败: %s", media_id)


@app.post("/api/hotspot-media/{media_id}/materialize", status_code=202)
async def materialize_hotspot_media(
    media_id: int,
    req: HotspotMediaMaterializeRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    item = db.get_hotspot_media(media_id)
    if not item:
        raise HTTPException(404, "热点素材不存在")
    capacity = media_retention.disk_guard(STATIC_DIR)
    if capacity.get("blocked"):
        raise HTTPException(
            507,
            f"服务器磁盘剩余仅 {capacity.get('free_percent', 0)}%，已暂停热点素材下载，请先清理或扩容",
        )
    try:
        hotspot_media.validate_materialization(item, user["role"], req.confirmed)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if item.get("download_status") in {"pending", "downloading", "downloaded"}:
        raise HTTPException(409, "该热点媒体正在下载或已经素材化")
    if not item.get("confirmed_at"):
        hotspot = db.get_hotspot(item.get("hotspot_id")) or {}
        try:
            evidence_url = hotspot_media._validate_public_https(
                str(item.get("source_page_url") or item.get("original_media_url") or "")
            )
        except ValueError as exc:
            raise HTTPException(400, f"热点媒体来源链接无效：{exc}") from exc
        attribution = (
            item.get("publisher") or item.get("author") or hotspot.get("publisher")
            or "来源见原始页面"
        )
        db.update_hotspot_media_authorization(
            media_id,
            "authorized",
            "管理员确认下载到热点素材库（内部使用）",
            None,
            str(attribution).strip()[:500],
            evidence_url,
            user["id"],
        )
        db.add_audit_log(
            user["id"], user["username"], "confirm_hotspot_media_download", target=str(media_id)
        )
    db.update_hotspot_media_state(
        media_id, download_status="pending", download_progress=5,
        progress_detail="任务已提交，等待下载", processing_status="not_started", error_message=None
    )
    asyncio.create_task(_run_hotspot_media_materialization(media_id, user["id"]))
    db.add_audit_log(user["id"], user["username"], "materialize_hotspot_media", target=str(media_id))
    return {"id": media_id, "status": "pending"}


@app.get("/api/hotspots/{hotspot_id}/sample-bundles")
async def list_hotspot_sample_bundles(
    hotspot_id: int,
    limit: int = 10,
    user=Depends(get_current_user),
):
    if not db.get_hotspot(hotspot_id):
        raise HTTPException(404, "热点不存在")
    created_by = None if user["role"] == UserRole.ADMIN.value else user["id"]
    return db.list_sample_bundles_for_hotspot(
        hotspot_id, max(1, min(limit, 50)), created_by=created_by,
    )


@app.get("/api/brand-evidence")
async def list_brand_evidence(user=Depends(get_current_user)):
    return db.list_brand_evidence()


@app.post("/api/brand-evidence", status_code=201)
async def create_brand_evidence(
    req: BrandEvidenceCreateRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    evidence_id = db.create_brand_evidence(req.model_dump(), user["id"])
    db.add_audit_log(
        user["id"], user["username"], "create_brand_evidence", target=str(evidence_id)
    )
    return db.get_brand_evidence(evidence_id)


@app.put("/api/brand-evidence/{evidence_id}/confirm")
async def confirm_brand_evidence(
    evidence_id: int,
    req: BrandEvidenceConfirmRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    if not db.get_brand_evidence(evidence_id):
        raise HTTPException(404, "品牌证据不存在")
    result = db.confirm_brand_evidence(evidence_id, user["id"], req.status)
    db.add_audit_log(
        user["id"], user["username"], "confirm_brand_evidence",
        target=str(evidence_id), detail=req.status,
    )
    return result


@app.post("/api/hotspots/{hotspot_id}/evidence-package", status_code=201)
async def create_evidence_package(
    hotspot_id: int,
    req: EvidencePackageCreateRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    try:
        package = evidence_harness.build_package(
            hotspot_id,
            created_by=user["id"],
            brand_evidence_ids=req.brand_evidence_ids,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.add_audit_log(
        user["id"], user["username"], "create_evidence_package", target=package["id"]
    )
    return package


@app.get("/api/evidence-packages/{package_id}")
async def get_evidence_package(package_id: str, user=Depends(get_current_user)):
    package = db.get_evidence_package(package_id)
    if not package:
        raise HTTPException(404, "证据包不存在")
    if user["role"] != UserRole.ADMIN.value and package.get("created_by") != user["id"]:
        raise HTTPException(404, "证据包不存在")
    return package


@app.post("/api/evidence-packages/{package_id}/sample-bundle", status_code=201)
async def create_sample_bundle(
    package_id: str,
    user=Depends(require_role(UserRole.ADMIN)),
):
    try:
        bundle = sample_harness.generate_bundle(package_id, created_by=user["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.add_audit_log(
        user["id"], user["username"], "create_sample_bundle", target=bundle["id"]
    )
    return bundle


@app.get("/api/sample-bundles/{bundle_id}")
async def get_sample_bundle(bundle_id: str, user=Depends(get_current_user)):
    bundle = db.get_sample_bundle(bundle_id)
    if not bundle:
        raise HTTPException(404, "样本包不存在")
    if user["role"] != UserRole.ADMIN.value and bundle.get("created_by") != user["id"]:
        raise HTTPException(404, "样本包不存在")
    return bundle


@app.get("/api/model-routes/{role}")
async def get_model_route(role: str, user=Depends(require_role(UserRole.ADMIN))):
    try:
        return model_router.get_route(role)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/model-routes/{role}")
async def update_model_route(
    role: str,
    req: ModelRouteRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    if not req.base_url.startswith("https://"):
        raise HTTPException(400, "模型地址必须使用 HTTPS")
    try:
        route = model_router.save_route(role, req.model_dump())
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.add_audit_log(
        user["id"], user["username"], "update_model_route", target=role,
        detail=f"provider={route['provider']}, model={route['model']}",
    )
    return route


@app.get("/api/model-budgets/{job_id}")
async def get_model_budget(job_id: str, user=Depends(require_role(UserRole.ADMIN))):
    budget = db.get_model_budget(job_id)
    if not budget:
        raise HTTPException(404, "模型预算任务不存在")
    return budget


@app.post("/api/hotspots/fetch")
async def fetch_hotspots_now(user=Depends(require_role(UserRole.ADMIN))):
    run = db.create_hotspot_fetch_run(user["id"])
    try:
        result = await hotspot_fetcher.fetch_hotspots(
            STATIC_DIR,
            created_by=user["id"],
            video_channels=hotspot_fetcher.configured_video_channels(),
        )
        health = result.get("source_health") or []
        failed_sources = sum(1 for item in health if item.get("status") == "error")
        if health and failed_sources == len(health):
            status = "failed"
        elif failed_sources or result.get("errors"):
            status = "partial"
        else:
            status = "succeeded"
        run = db.finish_hotspot_fetch_run(run["id"], status, result)
        db.add_audit_log(
            user["id"], user["username"], "fetch_hotspots",
            detail=_json.dumps(result, ensure_ascii=False),
        )
        return {**result, "run": run}
    except Exception as exc:
        failure = {"error": str(exc)[:500], "source_health": []}
        db.finish_hotspot_fetch_run(run["id"], "failed", failure)
        logger.exception("热点抓取失败")
        raise HTTPException(502, "热点抓取失败，请查看信源状态后重试") from exc


def _validate_hotspot_source(body: dict) -> tuple[str, str, list[str], bool]:
    from urllib.parse import urlparse
    import ipaddress
    name = str(body.get("name") or "").strip()
    feed_url = str(body.get("feed_url") or "").strip()
    parsed = urlparse(feed_url)
    host = (parsed.hostname or "").lower()
    if not name or parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise HTTPException(400, "可信源必须填写名称和无账号信息的 HTTPS Feed URL")
    try:
        ipaddress.ip_address(host)
        raise HTTPException(400, "可信源不允许使用 IP 地址")
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".local"):
        raise HTTPException(400, "可信源不允许使用本地域名")
    domains = body.get("allowed_domains") if isinstance(body.get("allowed_domains"), list) else []
    domains = sorted({str(value).strip().lower().lstrip(".") for value in domains if str(value).strip()}) or [host]
    if any("/" in domain or ":" in domain or domain == "localhost" or domain.endswith(".local") for domain in domains):
        raise HTTPException(400, "允许域名格式不正确")
    return name[:100], feed_url, domains, bool(body.get("enabled", True))


@app.get("/api/hotspot-sources")
async def list_hotspot_sources(user=Depends(require_role(UserRole.ADMIN))):
    return db.list_hotspot_sources()


@app.post("/api/hotspot-sources", status_code=201)
async def create_hotspot_source(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    name, feed_url, domains, enabled = _validate_hotspot_source(body)
    if enabled and len(db.list_hotspot_sources(enabled_only=True)) >= hotspot_fetcher.MAX_ENABLED_SOURCES:
        raise HTTPException(409, f"最多启用 {hotspot_fetcher.MAX_ENABLED_SOURCES} 个可信源，请先停用一个现有信源")
    try:
        source_id = db.create_hotspot_source(name, feed_url, domains, user["id"], enabled)
    except Exception as exc:
        raise HTTPException(400, "该 Feed URL 已存在") from exc
    return {"id": source_id, "status": "ok"}


@app.put("/api/hotspot-sources/{source_id}")
async def update_hotspot_source(source_id: int, body: dict, user=Depends(require_role(UserRole.ADMIN))):
    name, feed_url, domains, enabled = _validate_hotspot_source(body)
    current = next((item for item in db.list_hotspot_sources() if item["id"] == source_id), None)
    if not current:
        raise HTTPException(404, "可信源不存在")
    if enabled and not current["enabled"] and len(db.list_hotspot_sources(enabled_only=True)) >= hotspot_fetcher.MAX_ENABLED_SOURCES:
        raise HTTPException(409, f"最多启用 {hotspot_fetcher.MAX_ENABLED_SOURCES} 个可信源，请先停用一个现有信源")
    db.update_hotspot_source(source_id, name, feed_url, domains, enabled)
    return {"status": "ok"}


@app.delete("/api/hotspot-sources/{source_id}")
async def delete_hotspot_source(source_id: int, user=Depends(require_role(UserRole.ADMIN))):
    db.delete_hotspot_source(source_id)
    return {"status": "ok"}


@app.get("/api/hotspots/{hotspot_id}/draft")
async def hotspot_draft(hotspot_id: int, user=Depends(get_current_user)):
    hotspot = db.get_hotspot(hotspot_id)
    if not hotspot:
        raise HTTPException(404, "热点不存在")
    draft = hotspot_content.compose(hotspot)
    verification = truth_guard.evaluate(draft["title"], draft["body"], draft["source_refs"])
    if verification["status"] != "verified":
        raise HTTPException(409, "热点证据未能完整覆盖草稿，已阻止生成")
    return {**draft, "verification": verification}


@app.post("/api/assets/upload")
async def upload_media_asset(
    file: UploadFile = File(...), category: str = "other",
    user=Depends(require_role(UserRole.ADMIN)),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in media_assets.IMAGE_EXTS | media_assets.VIDEO_EXTS:
        raise HTTPException(400, "不支持的素材格式")
    max_size = media_assets.MAX_VIDEO if suffix in media_assets.VIDEO_EXTS else media_assets.MAX_IMAGE
    import tempfile
    temp_path = Path(tempfile.gettempdir()) / f"asset-{_uuid4().hex}{suffix}"
    try:
        await media_assets.stream_upload_to_path(file, temp_path, max_size)
        # auto 时按原始文件名猜分类（temp 文件名是 uuid，不含关键词，不能用来猜）
        original_name = Path(file.filename or "").stem
        category_was_explicit = category != "auto"
        if category == "auto":
            category = media_assets.guess_category(Path(file.filename or ""))
        asset = media_assets.ingest_file(temp_path, STATIC_DIR, category, "upload", user["id"], name=original_name or None)
        if category_was_explicit and category in asset_processing.PRIMARY_CATEGORIES:
            db.mark_asset_category_manual(asset["id"], category)
            asset = db.get_asset(asset["id"])
        is_dedup = bool(asset.get("_dedup"))
        reactivated = bool(asset.get("_reactivated"))
        processing_job_id = None
        if not is_dedup or reactivated or not db.list_asset_segments(asset_id=asset["id"]):
            processing_job_id = db.create_asset_processing_job(asset["id"], user["id"], asset_processing.PROCESSING_VERSION)
            asyncio.create_task(_run_asset_processing_job(processing_job_id))
        db.add_audit_log(user["id"], user["username"], "upload_asset", target=str(asset["id"]))
        return {**media_assets.public_asset(asset), "duplicated": is_dedup, "reactivated": reactivated,
                "processing_job_id": processing_job_id}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _track_local_import_task(task: asyncio.Task) -> None:
    local_asset_import_tasks.add(task)
    task.add_done_callback(local_asset_import_tasks.discard)


async def _run_local_asset_import_job(job_id: str, user_id: int) -> None:
    job = db.get_local_asset_import_job(job_id, user_id)
    if not job:
        return
    root = Path(job["root_path"])
    errors = list(job.get("errors") or [])
    imported = int(job.get("imported") or 0)
    duplicated = int(job.get("duplicated") or 0)
    skipped = int(job.get("skipped") or 0)
    failed = int(job.get("failed") or 0)
    scanned = int(job.get("scanned") or 0)
    try:
        db.update_local_asset_import_job(
            job_id,
            status="scanning",
            stage="scanning",
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        supported, unsupported = await asyncio.to_thread(local_asset_import.discover, root)
        total = len(supported) + len(unsupported)
        skipped += len(unsupported)
        scanned += len(unsupported)
        errors.extend(
            {"file": str(path.relative_to(root)), "error": "不支持的素材格式"}
            for path in unsupported[: max(0, 50 - len(errors))]
        )
        db.update_local_asset_import_job(
            job_id,
            status="importing",
            stage="importing",
            total=total,
            scanned=scanned,
            skipped=skipped,
            errors=errors[:50],
        )
        for path in supported:
            latest = db.get_local_asset_import_job(job_id, user_id)
            if not latest:
                return
            if latest.get("cancel_requested") or latest.get("status") == "cancel_requested":
                db.update_local_asset_import_job(
                    job_id,
                    status="canceled",
                    stage="canceled",
                    current_file="",
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                return
            relative_name = str(path.relative_to(root))
            db.update_local_asset_import_job(job_id, current_file=relative_name)
            try:
                asset = await asyncio.to_thread(
                    local_asset_import.ingest_one, path, root, STATIC_DIR, user_id
                )
                if asset.get("_dedup"):
                    duplicated += 1
                else:
                    imported += 1
                    processing_job_id = db.create_asset_processing_job(
                        asset["id"], user_id, asset_processing.PROCESSING_VERSION
                    )
                    asyncio.create_task(_run_asset_processing_job(processing_job_id))
            except Exception as exc:
                failed += 1
                if len(errors) < 50:
                    errors.append({"file": relative_name, "error": str(exc)[:200]})
                logger.exception("本地素材导入失败: %s", path)
            scanned += 1
            db.update_local_asset_import_job(
                job_id,
                scanned=scanned,
                imported=imported,
                duplicated=duplicated,
                skipped=skipped,
                failed=failed,
                errors=errors[:50],
            )
        db.update_local_asset_import_job(
            job_id,
            status="succeeded",
            stage="analysis_queued",
            current_file="",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        errors.append({"file": str(job.get("current_file") or ""), "error": str(exc)[:200]})
        db.update_local_asset_import_job(
            job_id,
            status="failed",
            stage="failed",
            current_file="",
            errors=errors[:50],
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
        logger.exception("本地素材批量导入失败: %s", job_id)


@app.post("/api/assets/local-imports")
async def create_local_asset_import(user=Depends(require_role(UserRole.ADMIN))):
    root = local_asset_import.configured_root()
    if not root.is_dir():
        raise HTTPException(409, f"本地素材目录不存在：{root}")
    job, created = db.create_or_get_local_asset_import_job(str(root), user["id"])
    if created:
        _track_local_import_task(asyncio.create_task(_run_local_asset_import_job(job["id"], user["id"])))
    return Response(
        content=_json.dumps({"job": job, "created": created}, ensure_ascii=False),
        media_type="application/json",
        status_code=202 if created else 200,
    )


@app.get("/api/assets/local-imports/active")
async def list_active_local_asset_imports(user=Depends(get_current_user)):
    return db.list_active_local_asset_import_jobs(user["id"])


@app.get("/api/assets/local-imports/{job_id}")
async def get_local_asset_import(job_id: str, user=Depends(get_current_user)):
    job = db.get_local_asset_import_job(job_id, user["id"])
    if not job:
        raise HTTPException(404, "本地素材导入任务不存在")
    return job


@app.post("/api/assets/local-imports/{job_id}/cancel")
async def cancel_local_asset_import(
    job_id: str, user=Depends(require_role(UserRole.ADMIN))
):
    job = db.request_local_asset_import_cancel(job_id, user["id"])
    if not job:
        raise HTTPException(404, "本地素材导入任务不存在")
    return job


@app.post("/api/assets/import")
async def import_media_assets(body: dict = None, user=Depends(require_role(UserRole.ADMIN))):
    import_root = (STATIC_DIR / "assets" / "import").resolve()
    import_root.mkdir(parents=True, exist_ok=True)
    # 默认 auto：按子目录名/文件名关键词自动归类；显式传具体分类则统一用该分类
    category = str((body or {}).get("category") or "auto")
    results, errors = [], []
    for path in sorted(import_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in media_assets.IMAGE_EXTS | media_assets.VIDEO_EXTS:
            continue
        try:
            asset = media_assets.ingest_file(
                path, STATIC_DIR, category, "directory", user["id"],
                import_root=import_root,
            )
            item = media_assets.public_asset(asset)
            if not asset.get("_dedup") or not db.list_asset_segments(asset_id=asset["id"]):
                job_id = db.create_asset_processing_job(asset["id"], user["id"], asset_processing.PROCESSING_VERSION)
                asyncio.create_task(_run_asset_processing_job(job_id))
                item["processing_job_id"] = job_id
            results.append(item)
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)[:200]})
    db.add_audit_log(user["id"], user["username"], "import_assets", detail=f"success={len(results)}, errors={len(errors)}")
    return {"imported": results, "errors": errors}


async def _run_asset_processing_job(job_id: str):
    async with asset_processing_semaphore:
        try:
            await asyncio.to_thread(asset_processing.process_asset_job, job_id, STATIC_DIR)
        except Exception:
            logger.exception("素材语义处理失败: %s", job_id)


@app.post("/api/assets/{asset_id}/process", status_code=202)
async def process_media_asset(asset_id: int, user=Depends(require_role(UserRole.ADMIN))):
    if not db.get_asset(asset_id):
        raise HTTPException(404, "素材不存在")
    job_id = db.create_asset_processing_job(asset_id, user["id"], asset_processing.PROCESSING_VERSION)
    asyncio.create_task(_run_asset_processing_job(job_id))
    db.add_audit_log(user["id"], user["username"], "process_asset", target=str(asset_id))
    return {"job_id": job_id, "status": "pending"}


@app.post("/api/assets/process-pending", status_code=202)
async def process_pending_assets(user=Depends(require_role(UserRole.ADMIN))):
    jobs = []
    for asset in db.list_assets_needing_processing(limit=200):
        job_id = db.create_asset_processing_job(asset["id"], user["id"], asset_processing.PROCESSING_VERSION)
        jobs.append(job_id)
        asyncio.create_task(_run_asset_processing_job(job_id))
    db.add_audit_log(user["id"], user["username"], "process_pending_assets", detail=f"jobs={len(jobs)}")
    return {"jobs": jobs, "count": len(jobs), "status": "pending"}


@app.post("/api/assets/rebuild-taxonomy", status_code=202)
async def rebuild_asset_taxonomy(body: dict | None = None, user=Depends(require_role(UserRole.ADMIN))):
    """按批次重建主场景和对象/动作标签；单次最多 100 条，避免全库瞬时失控。"""
    limit = int((body or {}).get("limit") or 100)
    assets = db.list_assets_needing_taxonomy_rebuild(asset_processing.PROCESSING_VERSION, limit)
    jobs = []
    for asset in assets:
        job_id = db.create_asset_processing_job(asset["id"], user["id"], asset_processing.PROCESSING_VERSION)
        jobs.append(job_id)
        asyncio.create_task(_run_asset_processing_job(job_id))
    db.add_audit_log(user["id"], user["username"], "rebuild_asset_taxonomy", detail=f"jobs={len(jobs)}")
    return {"jobs": jobs, "count": len(jobs), "limit": min(max(limit, 1), 100), "status": "pending"}


@app.post("/api/assets/backfill-buffalo-brand-tags")
async def backfill_buffalo_brand_tags(user=Depends(require_role(UserRole.ADMIN))):
    """从存量 OCR/描述恢复已经明确可见的 Buffalo 品牌露出，不调用外部模型。"""
    result = db.backfill_visible_brand_tags("Buffalo", asset_processing.BUFFALO_BRAND_MARKERS)
    db.add_audit_log(
        user["id"], user["username"], "backfill_beneficial_brand_tags",
        detail=f"brand=Buffalo assets={result['affected_assets']} created={result['created_tags']}",
    )
    return result


@app.get("/api/assets/processing/{job_id}")
async def get_asset_processing(job_id: str, user=Depends(get_current_user)):
    job = db.get_asset_processing_job(job_id)
    if not job:
        raise HTTPException(404, "素材处理任务不存在")
    return job


@app.get("/api/assets/processing-capabilities")
async def get_asset_processing_capabilities(user=Depends(get_current_user)):
    return asset_processing.processing_capabilities()


@app.get("/api/media-retention/preview")
async def get_media_retention_preview(user=Depends(require_role(UserRole.ADMIN))):
    return await asyncio.to_thread(media_retention.preview_cleanup, STATIC_DIR)


@app.get("/api/storage/summary")
async def get_storage_summary(user=Depends(require_role(UserRole.ADMIN))):
    return await asyncio.to_thread(media_retention.storage_summary, STATIC_DIR)


@app.post("/api/assets/{asset_id}/pin")
async def pin_media_asset(asset_id: int, user=Depends(require_role(UserRole.ADMIN))):
    asset = db.set_asset_pinned(asset_id, True)
    if not asset:
        raise HTTPException(404, "素材不存在")
    db.add_audit_log(user["id"], user["username"], "pin_asset", target=str(asset_id))
    return media_assets.public_asset(asset)


@app.post("/api/assets/{asset_id}/unpin")
async def unpin_media_asset(asset_id: int, user=Depends(require_role(UserRole.ADMIN))):
    asset = db.set_asset_pinned(asset_id, False)
    if not asset:
        raise HTTPException(404, "素材不存在")
    db.add_audit_log(user["id"], user["username"], "unpin_asset", target=str(asset_id))
    return media_assets.public_asset(asset)


@app.put("/api/assets/{asset_id}")
async def update_media_asset(asset_id: int, body: dict, user=Depends(require_role(UserRole.ADMIN))):
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    name = str(body.get("name") or asset["name"]).strip()[:100]
    category = str(body.get("category") or asset["category"])
    status = str(body.get("status") or asset["status"])
    if category not in media_assets.CATEGORIES or status not in {"active", "inactive"}:
        raise HTTPException(400, "分类或状态无效")
    db.update_asset(asset_id, name, category, status)
    return media_assets.public_asset(db.get_asset(asset_id))


@app.delete("/api/assets/{asset_id}")
async def delete_media_asset(asset_id: int, user=Depends(require_role(UserRole.ADMIN))):
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    if db.asset_is_referenced(asset_id):
        db.update_asset(asset_id, asset["name"], asset["category"], "inactive")
        return {"status": "inactive", "reason": "素材已被视频任务引用"}
    for relative in (asset["filepath"], asset.get("thumbnail")):
        if relative:
            target = (STATIC_DIR / relative).resolve()
            if STATIC_DIR.resolve() in target.parents:
                target.unlink(missing_ok=True)
    db.delete_asset(asset_id)
    return {"status": "deleted"}


@app.get("/api/media/capabilities")
async def media_capabilities(user=Depends(get_current_user)):
    result = media_assets.capabilities()
    result["mimo_api_key"] = bool(os.environ.get("MIMO_API_KEY"))
    result["tts_provider"] = os.environ.get("TTS_PROVIDER", "mimo")
    result["mimo_tts_model"] = os.environ.get("MIMO_TTS_MODEL", "mimo-v2.5-tts")
    result["mimo_tts_voice"] = os.environ.get("MIMO_TTS_VOICE", video_renderer.MIMO_TTS_VOICE)
    result["chat_model"] = (model_router.get_route("chat_text") or {}).get("model") or "mimo-v2.5"
    result["planner_model"] = (model_router.get_route("planner_text") or {}).get("model") or "mimo-v2.5-pro"
    result["vision_model"] = (model_router.get_route("vision_tagger") or {}).get("model") or "mimo-v2.5"
    # Ready for formal video: FFmpeg + MiMo TTS key.
    media_ok = bool(result.get("ffmpeg") and result.get("ffprobe"))
    tts_ok = bool(result["mimo_api_key"])
    result["ready"] = media_ok and tts_ok
    result["voice_options"] = video_renderer.tts_voice_options(
        mimo_available=result["mimo_api_key"],
    )
    result["voices"] = [item["id"] for item in result["voice_options"]]
    result["tts_preview_supported"] = True
    return result


@app.post("/api/media/tts-preview")
async def media_tts_preview(body: TtsPreviewRequest, user=Depends(get_current_user)):
    """Short voice preview only — never creates a formal video generation job."""
    try:
        preview = await asyncio.to_thread(
            video_renderer.synthesize_tts_preview,
            body.text,
            tts_provider=body.tts_provider,
            voice=body.voice,
            output_dir=STATIC_DIR / "uploads" / "tts-previews",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"音色试听失败：{exc}") from exc
    db.add_audit_log(
        user["id"], user["username"], "tts_preview",
        target=f"{preview.get('tts_provider')}:{preview.get('voice')}",
    )
    return preview


@app.get("/api/admin/sqlite-health")
async def sqlite_health(user=Depends(require_role(UserRole.ADMIN))):
    import sqlite_write_queue
    return sqlite_write_queue.get_sqlite_health()


# ==================== API: Prompt Templates ====================

@app.get("/api/prompt-templates")
async def list_prompt_templates(user=Depends(get_current_user)):
    return db.get_prompt_templates()


@app.post("/api/prompt-templates")
async def create_prompt_template(body: dict, user=Depends(get_current_user)):
    name = (body.get("name") or "").strip()
    content = (body.get("content") or "").strip()
    if not name or not content:
        raise HTTPException(400, "名称和内容不能为空")
    tpl_id = db.create_prompt_template(name, body.get("category", ""), content, user["id"])
    db.add_audit_log(user["id"], user["username"], "create_prompt_template", target=name)
    return {"status": "ok", "id": tpl_id}


@app.put("/api/prompt-templates/{tpl_id}")
async def update_prompt_template(tpl_id: int, body: dict, user=Depends(get_current_user)):
    template = db.get_prompt_template(tpl_id)
    if not template:
        raise HTTPException(404, "模板不存在")
    if user["role"] != UserRole.ADMIN.value and template.get("created_by") != user["id"]:
        raise HTTPException(403, "不能修改其他用户的提示词模板")
    name = (body.get("name") or "").strip()
    content = (body.get("content") or "").strip()
    if not name or not content:
        raise HTTPException(400, "名称和内容不能为空")
    db.update_prompt_template(tpl_id, name, body.get("category", ""), content)
    db.add_audit_log(user["id"], user["username"], "update_prompt_template", target=name)
    return {"status": "ok"}


@app.delete("/api/prompt-templates/{tpl_id}")
async def delete_prompt_template(tpl_id: int, user=Depends(get_current_user)):
    template = db.get_prompt_template(tpl_id)
    if not template:
        raise HTTPException(404, "模板不存在")
    if user["role"] != UserRole.ADMIN.value and template.get("created_by") != user["id"]:
        raise HTTPException(403, "不能删除其他用户的提示词模板")
    db.delete_prompt_template(tpl_id)
    db.add_audit_log(user["id"], user["username"], "delete_prompt_template", target=str(tpl_id))
    return {"status": "ok"}


# ==================== AI Chat ====================

def _select_chat_video_hook_pair(hooks: list[dict]) -> list[dict]:
    """Choose one or two coherent Hooks from one event for chat-to-video.

    The chat result can show multiple matching events, but a formal video must
    start from one coherent, confirmed source rather than blending unrelated
    headlines merely to satisfy a duration requirement.  A second Hook is an
    enhancement, not a prerequisite for using a strong verified opening clip.
    """
    by_asset_and_event: dict[tuple[int, str], list[dict]] = {}
    for hook in hooks:
        try:
            asset_id = int(hook.get("asset_id") or 0)
            event_id = int(hook.get("event_clip_id") or 0)
            start_ms = int(hook.get("start_ms") or 0)
            end_ms = int(hook.get("end_ms") or 0)
        except (TypeError, ValueError):
            continue
        if asset_id <= 0 or event_id <= 0 or end_ms <= start_ms:
            continue
        event_identity = str(hook.get("event_identity") or "").strip()
        if not event_identity:
            continue
        key = (asset_id, event_identity.casefold())
        if any(int(item.get("event_clip_id") or 0) == event_id for item in by_asset_and_event.get(key, [])):
            continue
        by_asset_and_event.setdefault(key, []).append(hook)
    single: list[dict] = []
    for candidates in by_asset_and_event.values():
        pair = []
        for hook in sorted(candidates, key=lambda item: (int(item.get("start_ms") or 0), int(item.get("event_clip_id") or 0))):
            if not pair or int(hook["start_ms"]) >= int(pair[-1]["end_ms"]):
                pair.append(hook)
            if len(pair) == 2:
                return pair
        if pair and not single:
            single = pair[:1]
    return single


def _is_same_confirmed_hotspot_event(events: list[dict]) -> bool:
    if not 1 <= len(events) <= 2:
        return False
    identities = {
        str((event.get("evidence") or {}).get("event_identity") or "").strip().casefold()
        for event in events
    }
    return len(identities) == 1 and bool(next(iter(identities), ""))


def _chat_hook_candidates_debug(candidates: list[dict], recently_used: set[int], selected_event_ids: set[int]) -> list[dict]:
    """Expose why each top candidate was (not) selected, for debugging/tuning."""
    debug = []
    for item in candidates[:8]:
        hook_clips = item.get("hook_clips") or []
        event_ids = [int(hook["event_clip_id"]) for hook in hook_clips if hook.get("event_clip_id") is not None]
        reused = [event_id for event_id in event_ids if event_id in recently_used]
        selected = bool(selected_event_ids & set(event_ids))
        if selected:
            reason = "已被内置模型选中"
        elif reused:
            reason = "本轮对话中近期已用过该 Hook，已降权"
        else:
            reason = "分数不足或与当前主题相关性较弱，未被模型选中"
        debug.append({
            "hotspot_id": item.get("hotspot_id"),
            "title": item.get("title"),
            "event_clip_ids": event_ids,
            "score": item.get("score"),
            "selected": selected,
            "recently_used_in_session": bool(reused),
            "reason": reason,
        })
    return debug


async def _retrieve_confirmed_chat_hooks(
    topic: str,
    user_id: int,
    session_id: str = "",
    *,
    content_mode: str = "hotspot",
    event_anchor: dict | None = None,
) -> dict:
    """Let the deployed internal planner retrieve only confirmed, renderable Hooks.

    Chat never downloads a video directly.  Broad topics without an event anchor
    only try audited generic logistics openers and never enqueue discovery.
    Concrete timely topics may open a targeted-collection request when the
    Hook library cannot support them.
    """
    normalized_topic = " ".join(str(topic or "").split())[:300]
    if not normalized_topic:
        return {"status": "not_requested", "hooks": [], "failure_class": "no_event_anchor"}
    anchor = event_anchor or chat_intent.assess_event_anchor(normalized_topic)
    brief = {
        "id": f"chat-{hashlib.sha256(normalized_topic.encode()).hexdigest()[:16]}",
        "raw_input": normalized_topic,
        "subject": normalized_topic[:120],
        "angle": normalized_topic[:180],
        "goal": "为 Buffalo 物流内容选择已确认热点 Hook",
    }
    use_generic = not anchor.get("has_event_anchor")
    hook_kind = "generic_logistics" if use_generic else "timely_event"
    candidates, kb_context, brand_evidence, funnel = _marketing_hook_candidates(
        brief,
        limit=8,
        hook_kind=hook_kind,
        require_scene_overlap=use_generic,
        allow_broad_match=use_generic,
    )
    # Evergreen topics often lack scene keywords; still try generic logistics
    # openers so one-click production can auto-lock a real Hook.
    if use_generic and not candidates:
        candidates, kb_context, brand_evidence, funnel = _marketing_hook_candidates(
            brief,
            limit=8,
            hook_kind="generic_logistics",
            require_scene_overlap=False,
            allow_broad_match=True,
        )
        funnel = {**(funnel or {}), "generic_relaxed": True}
    # 同一聊天 session 内不应连续把同一段 Hook 当开场：对近期已用过的候选
    # 降权（不排除），素材库很小时仍能在没有更优选项时复用。
    recently_used = db.recent_session_hook_event_ids(session_id, user_id) if session_id else set()
    if recently_used and candidates:
        for item in candidates:
            hook_event_ids = {
                int(hook["event_clip_id"]) for hook in item.get("hook_clips") or []
                if hook.get("event_clip_id") is not None
            }
            if hook_event_ids & recently_used:
                item["score"] = item.get("score", 0) - 25
                funnel["duplicate_or_recent"] = int(funnel.get("duplicate_or_recent") or 0) + 1
        candidates.sort(
            key=lambda item: (item["score"], item.get("published_ts") or 0, int(item["hotspot_id"])),
            reverse=True,
        )
    selected_event_ids: set[int] = set()
    selected = []
    model_meta: dict = {"used": False}
    if candidates:
        selected, model_meta = await _model_decide_marketing_hooks(
            brief, candidates, kb_context, brand_evidence, limit=2,
        )
        # One-click path: if the planner returns nothing, still auto-lock the
        # strongest rule candidate instead of bouncing the user to retype.
        if not selected:
            fallback = dict(candidates[0])
            hooks = _select_chat_video_hook_pair(fallback.get("hook_clips") or [])
            if hooks:
                fallback["hook_clips"] = hooks
                fallback["can_render_video"] = True
                selected = [fallback]
                model_meta = {
                    **(model_meta or {}),
                    "used": False,
                    "fallback": "auto_lock_top_candidate",
                }
        for item in selected:
            selected_hooks = _select_chat_video_hook_pair(item.get("hook_clips") or [])
            if not selected_hooks:
                continue
            selected_event_ids = {int(hook["event_clip_id"]) for hook in selected_hooks}
            funnel["selected"] = len(selected_event_ids)
            hooks = [{
                "event_clip_id": hook.get("event_clip_id"),
                "asset_id": hook.get("asset_id"),
                "title": item.get("title"),
                "event_identity": hook.get("event_identity"),
                "description": hook.get("content_description"),
                "marketing_question": item.get("marketing_question"),
            } for hook in selected_hooks]
            hook_count = len(selected_hooks)
            locked_events = [
                db.get_hotspot_event_clip(int(hook["event_clip_id"]))
                for hook in selected_hooks
            ]
            locked_events = [event for event in locked_events if event]
            delivery_readiness = _chat_video_delivery_readiness(normalized_topic, locked_events)
            ready_events = [
                event for event in db.list_hotspot_event_clips()
                if _is_confirmed_renderable_hotspot_hook(event)
            ]
            hotspots_by_id = {
                int(item["id"]): item
                for item in db.list_hotspots(limit=200)
            }
            producible = producible_topics.recommend_producible_topics(
                ready_events, limit=5, hotspots_by_id=hotspots_by_id,
            )
            return {
                "status": "matched", "topic": normalized_topic, "hooks": hooks, "model": model_meta,
                "failure_class": None,
                "event_anchor": anchor,
                "hook_kind": hook_kind,
                "funnel": funnel,
                "relevance": item.get("relevance") or {
                    "level": "strong", "reason": "内置模型确认该 Hook 可直接解释当前物流问题。",
                },
                "video": {
                    "status": "ready",
                    "hotspot_event_ids": [int(hook["event_clip_id"]) for hook in selected_hooks],
                    "source_asset_id": int(selected_hooks[0]["asset_id"]),
                    "delivery_readiness": delivery_readiness,
                },
                "producible_topics": producible,
                "candidates_debug": _chat_hook_candidates_debug(candidates, recently_used, selected_event_ids),
                "message": (
                    "已自动锁定热点 Hook；可在卡片中更换，或直接创建 60 秒视频项目。"
                    if use_generic else
                    (
                        "已由内置模型锁定同一事件的两段已确认 Hook；成片会用双段现场增强开场。"
                        if hook_count == 2 else
                        "已由内置模型锁定一段相关、已确认 Hook；成片会以该真实现场开场。"
                    )
                ),
            }

    ready_events = [
        event for event in db.list_hotspot_event_clips()
        if _is_confirmed_renderable_hotspot_hook(event)
    ]
    hotspots_by_id = {
        int(item["id"]): item
        for item in db.list_hotspots(limit=200)
    }
    producible = producible_topics.recommend_producible_topics(
        ready_events, limit=5, hotspots_by_id=hotspots_by_id,
    )

    if use_generic or not chat_intent.should_enqueue_hotspot_discovery(content_mode, anchor):
        failure = "no_event_anchor" if use_generic else (
            "gate_blocked" if int(funnel.get("scanned") or 0) > 0 and int(funnel.get("passed") or 0) == 0
            else "coverage_gap"
        )
        message = {
            "no_event_anchor": (
                "当前话题没有自动命中可用 Hook。"
                "请在下方点选一个库内 Hook（绑定到你的原主题，无需改写输入框），或补充具体口岸/港口/道路事件后再试。"
            ),
            "gate_blocked": (
                "库内有候选 Hook，但未通过确认/可播/场景相关度门禁；"
                "不会用无关事故画面硬配成片。请点选下方可绑定 Hook。"
            ),
            "coverage_gap": (
                "主题需要时效事件 Hook，但当前库未覆盖；"
                "请点选下方可绑定 Hook，或等待定向补采。"
            ),
        }.get(failure, "当前没有可用的强相关 Hook。")
        return {
            "status": "not_requested",
            "topic": normalized_topic,
            "hooks": [],
            "request_id": None,
            "video": {"status": "disabled"},
            "failure_class": failure,
            "event_anchor": anchor,
            "hook_kind": hook_kind,
            "funnel": funnel,
            "producible_topics": producible,
            "candidates_debug": _chat_hook_candidates_debug(candidates, recently_used, selected_event_ids),
            "reason": failure,
            "message": message,
        }

    request = db.enqueue_hotspot_discovery_request(normalized_topic, user_id)
    refresh_started = sched.request_targeted_hotspot_refresh()
    return {
        "status": "queued", "topic": normalized_topic, "hooks": [], "request_id": request["id"],
        "video": {"status": "disabled"},
        "failure_class": "coverage_gap",
        "event_anchor": anchor,
        "hook_kind": hook_kind,
        "funnel": funnel,
        "producible_topics": producible,
        "candidates_debug": _chat_hook_candidates_debug(candidates, recently_used, selected_event_ids),
        "message": (
            "当前没有与该主题相关且可播放的已确认热点 Hook；"
            + ("已立即复扫已授权信源并启动定向入库。" if refresh_started else "定向入库任务正在处理中。")
            + "完成下载、分析、模型裁剪和事实核验前不会生成无关素材的视频。"
        ),
    }


def _chat_video_logistics_nodes(topic: str, events: list[dict]) -> list[str]:
    """Derive a conservative owned-media role from the approved Hook evidence."""
    evidence_text = " ".join(
        str(value or "")
        for event in events
        for value in (
            event.get("title_zh"), event.get("title_en"),
            (event.get("evidence") or {}).get("logistics_question"),
        )
    )
    candidates = _topic_keywords(" ".join((topic, evidence_text)))
    nodes = [
        node for node in candidates
        if node in {"清关", "末端", "配送", "仓储", "运输"}
    ]
    lowered_evidence = evidence_text.casefold()
    # The chat wording may only say “交期” or “运输”, while the approved Hook
    # curator has already verified a narrower logistics question such as
    # overseas-warehouse intake or distribution.  Carry only those evidenced
    # nodes forward so an otherwise valid dual-library plan is not needlessly
    # starved of its matching Buffalo footage.
    if any(term in lowered_evidence for term in ("海外仓", "仓储", "仓库", "入库", "分拣")):
        nodes.append("仓储")
    if any(term in lowered_evidence for term in ("配送", "派送", "分拨", "末端")):
        nodes.append("配送")
    if any(term in lowered_evidence for term in ("卡车", "货运", "运输", "路线")):
        nodes.append("运输")
    # 道路中断并不能证明 Buffalo 已完成配送，但它会直接改变提货、入库前
    # 的准备和分拨安排。补入配送节点后，规划器可以用仓内/人员实拍解释这些
    # 可见准备动作，仍由文案门禁禁止承诺时效或路线结果。
    if any(term in " ".join((topic, evidence_text)).casefold() for term in ("道路", "路况", "路线", "卡车", "r60")):
        nodes.append("配送")
    # 关税/进口税属海关事务：映射到清关节点，让 owned 匹配要求 customs 证据，
    # 而不是把仓库/运输画面默认当作关税话题的合法素材（否则 eligible 会误落 delivery）。
    combined = " ".join((topic, evidence_text)).casefold()
    if any(term in combined for term in ("关税", "进口税", "出口税", "tariff", "duty")):
        nodes.append("清关")
    # 港口/码头作业属运输能力；显式补上，避免它靠 ["运输"] 兜底才凑巧命中。
    if any(term in combined for term in ("港口", "港区", "码头", "港口作业", "港口码头", "port", "harbour", "harbor", "海运")):
        nodes.append("运输")
    # 供应链中断/罢工/拥堵直接改变提货、入库前准备与分拨安排：归到配送节点，
    # 由规划器用仓内/人员实拍解释可见准备动作；文案门禁仍禁止承诺时效或路线结果。
    if any(term in combined for term in ("中断", "断链", "供应链", "停摆", "罢工", "拥堵", "延误", "disruption", "strike", "congestion")):
        nodes.append("配送")
    # The Hook curator has already confirmed a logistics bridge.  Without a
    # narrower node, use transport only as a preparation/action category; the
    # planner prompt still forbids claiming a completed delivery outcome.
    return list(dict.fromkeys(nodes)) or ["运输"]


def _hotspot_batch_age() -> str | None:
    run = db.get_latest_hotspot_fetch_run()
    if not run:
        return None
    return str(run.get("finished_at") or run.get("started_at") or "") or None


def _compose_owned_matching_diagnostics(
    brief: dict,
    owned_segments: list[dict],
    *,
    hotspot_events: list[dict] | None = None,
) -> dict:
    """Attach funnel diagnosis + starving_side. Observation only; never changes selection."""
    diagnostics = hotspot_video_planner.diagnose_owned_matching(owned_segments, brief)
    events = hotspot_events if hotspot_events is not None else db.list_hotspot_event_clips()
    hotspot_pool = hotspot_video_planner.count_matching_hotspot_hooks(brief, events)
    starve = hotspot_video_planner.diagnose_starving_side(
        owned_pool=int((diagnostics.get("funnel") or {}).get("after_dedup") or 0),
        hotspot_pool=hotspot_pool,
        hotspot_batch_age=_hotspot_batch_age(),
    )
    diagnostics.update(starve)
    return diagnostics


def _chat_video_delivery_readiness(topic: str, locked_events: list[dict]) -> dict:
    """Preflight the formal 50–90s plan without creating a project or calling a model.

    Hook absence remains a hard stop. Thin Buffalo inventory no longer blocks
    create — the planner adapts and returns visible adaptation metadata.
    """
    if not locked_events:
        return {
            "status": "needs_hook", "delivery_ready": False,
            "message": "尚未锁定可用于正式成片的热点 Hook。",
            "adaptation": {"adapted": False, "strategies": []},
        }
    primary = locked_events[0]
    owned_segments = [
        item for item in db.list_asset_segments(limit=20_000)
        if not item.get("asset_hotspot_id")
    ]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
    ]
    source_hotspot = db.get_hotspot(int(primary["hotspot_id"])) or {}
    nodes = _chat_video_logistics_nodes(topic, locked_events)
    planning_brief = hotspot_logistics_planner.build_brief(
        {**source_hotspot, **primary},
        owned_segments,
        {
            "id": f"chat-readiness-{hashlib.sha256(topic.encode()).hexdigest()[:16]}",
            "raw_input": topic,
            "subject": topic[:120],
            "angle": topic[:180],
            "goal": "基于已确认热点 Hook 生成 Buffalo 双素材库视频",
            "logistics_nodes": nodes,
            "platforms": ["douyin"],
        },
    )
    planning_brief.update({
        "hotspot_id": primary["hotspot_id"],
        "source_asset_id": primary["asset_id"],
        "primary_event_id": primary["id"],
        "approved_hook_event_ids": [int(event["id"]) for event in locked_events],
    })
    related_events = db.list_hotspot_event_clips(
        asset_id=primary["asset_id"], hotspot_id=primary["hotspot_id"],
    )
    # 批18：并入跨父 locked 事件（locked_events 可含不同父热点，之前被静默丢弃）。
    known_ids = {int(e.get("id") or 0) for e in related_events}
    for locked in locked_events:
        if int(locked.get("id") or 0) not in known_ids and _is_confirmed_renderable_hotspot_hook(locked):
            related_events.append(locked)
    cta_duration_ms = sum(
        int(item["duration_ms"]) for item in hotspot_video_planner.BRAND_ENDCARD_SCENES
    )
    try:
        scenes = hotspot_video_planner.plan_followup_scenes(
            planning_brief,
            related_events,
            owned_segments,
            target_duration_ms=60_000 - cta_duration_ms,
            owned_images=owned_images,
            allow_adaptation=True,
        )
        planner_issue = ""
    except ValueError as exc:
        scenes = []
        planner_issue = str(exc)[:240]
    hotspot_count = sum(scene.get("evidence_type") == "hotspot_video" for scene in scenes)
    owned_count = sum(scene.get("evidence_type") == "owned_video" for scene in scenes)
    image_count = sum(scene.get("evidence_type") == "image" for scene in scenes)
    duration_ms = sum(int(scene.get("duration_ms") or 0) for scene in scenes) + cta_duration_ms
    adaptation = hotspot_video_planner.describe_plan_adaptation(scenes)
    coverage = {
        "hotspot_video": hotspot_count,
        "owned_video": owned_count,
        "image": image_count,
        "duration_ms": duration_ms,
    }
    # Hard gate: locked Hook must yield at least one hotspot scene. Owned < 4
    # is adaptation, not a block.
    delivery_ready = bool(not planner_issue and hotspot_count >= 1)
    if delivery_ready and not adaptation.get("adapted"):
        message = "强相关热点 Hook 与 Buffalo 自有动态素材均已就绪，可生成正式 50–90 秒成片。"
        status = "delivery_ready"
    elif delivery_ready:
        message = (
            f"热点 Hook 已锁定；Buffalo 自有动态目前 {owned_count} 段"
            f"（理想 ≥4）。系统将按现有库存自适应规划并继续出片。"
        )
        status = "delivery_ready_adapted"
    elif planner_issue:
        message = "热点 Hook 已匹配，但当前素材组合无法形成可渲染分镜。"
        status = "needs_owned_media"
    else:
        message = "热点 Hook 已匹配，但规划未产出可用热点镜头。"
        status = "needs_owned_media"
    result = {
        "status": status,
        "delivery_ready": delivery_ready,
        "coverage": coverage,
        "required": {"hotspot_video": 1, "owned_video": "adaptive", "duration_ms": "50000–90000"},
        "ideal": {"hotspot_video": 1, "owned_video": 4, "duration_ms": "50000–90000"},
        "logistics_nodes": nodes,
        "message": message,
        "planner_issue": planner_issue or None,
        "adaptation": adaptation,
    }
    # Observation only when inventory looks thin or delivery is blocked.
    if owned_count < 4 or not delivery_ready:
        # hotspot_pool must scan the full confirmed hook library for the topic,
        # not only the locked parent video's related clips.
        result["diagnostics"] = _compose_owned_matching_diagnostics(
            planning_brief, owned_segments,
        )
    return result


@app.get("/api/diagnostics/owned-matching")
async def diagnostics_owned_matching(
    topic: str,
    hotspot_event_id: int | None = None,
    user=Depends(require_role(UserRole.ADMIN)),
):
    """Admin-only matching funnel diagnosis. Pure observation; no model calls."""
    topic = str(topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")
    owned_segments = [
        item for item in db.list_asset_segments(limit=20_000)
        if not item.get("asset_hotspot_id")
    ]
    locked_events: list[dict] = []
    event = None
    if hotspot_event_id is not None:
        event = db.get_hotspot_event_clip(int(hotspot_event_id))
        if not event:
            raise HTTPException(status_code=404, detail="热点事件不存在")
        locked_events = [event]
    nodes = _chat_video_logistics_nodes(topic, locked_events)
    source = {}
    if event:
        source = {**(db.get_hotspot(int(event["hotspot_id"])) or {}), **event}
    planning_brief = hotspot_logistics_planner.build_brief(
        source or {"title_zh": topic, "title_en": topic},
        owned_segments,
        {
            "id": f"diag-{hashlib.sha256(topic.encode()).hexdigest()[:16]}",
            "raw_input": topic,
            "subject": topic[:120],
            "angle": topic[:180],
            "goal": "匹配诊断（只读）",
            "logistics_nodes": nodes,
            "platforms": ["douyin"],
        },
    )
    if event:
        planning_brief.update({
            "hotspot_id": event.get("hotspot_id"),
            "source_asset_id": event.get("asset_id"),
            "primary_event_id": event.get("id"),
            "approved_hook_event_ids": [int(event["id"])],
        })
    diagnostics = _compose_owned_matching_diagnostics(planning_brief, owned_segments)
    payload = {
        "topic": topic,
        "logistics_nodes": nodes,
        "starving_side": diagnostics.get("starving_side"),
        "hotspot_pool": diagnostics.get("hotspot_pool"),
        "owned_pool": diagnostics.get("owned_pool"),
        "hotspot_batch_age": diagnostics.get("hotspot_batch_age"),
        "diagnostics": diagnostics,
    }
    if event is not None:
        payload["event_diagnostics"] = hotspot_event_matching.diagnose_event_matching(
            event, db.list_asset_segments(limit=20_000),
        )
    return payload


@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest, user=Depends(get_current_user)):
    """多轮 AI 对话 + 快捷指令。"""
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    latest_topic = str(req.topic or "").strip() or next(
        (str(message["content"]).strip() for message in reversed(messages) if message["role"] == "user"), ""
    )
    content_mode = chat_intent.classify_content_mode(latest_topic, context=req.context or "")
    event_anchor = chat_intent.assess_event_anchor(latest_topic, context=req.context or "")
    evidence = chat_intent.assess_comparison_evidence(
        messages, topic=latest_topic, context=req.context or "",
    )
    platforms = [p.value for p in req.platforms]
    authenticity_blocked = False
    brand_assets_insufficient = False
    degraded_from_comparison = False

    if content_mode == "comparison_research" and evidence["evidence_state"] != "sufficient":
        # 商务团队一键生产：对比题材无真实资料时，自动降级为科普视角，
        # 重写消息后走正常生产链（通用物流 Hook 兜底 → 可创建视频项目）。
        degraded_from_comparison = True
        latest_topic = chat_intent.comparison_to_evergreen_topic(latest_topic)
        messages = list(messages)
        if messages and messages[-1].get("role") == "user":
            messages[-1] = {**messages[-1], "content": latest_topic}
        content_mode = chat_intent.classify_content_mode(latest_topic)
        event_anchor = chat_intent.assess_event_anchor(latest_topic, context=req.context or "")

    if content_mode == "comparison_research" and evidence["evidence_state"] != "sufficient":
        hotspot_retrieval = {
            "status": "not_requested",
            "reason": "comparison_research_without_evidence",
            "message": "对比评测缺少实测资料，未启动热点 Hook 补采。",
            "hooks": [],
            "video": {"status": "disabled"},
            "failure_class": None,
            "event_anchor": event_anchor,
            "producible_topics": [],
        }
        outputs = ai_engine.build_comparison_framework(latest_topic, platforms, evidence)
    else:
        hotspot_task = None
        if chat_intent.should_attempt_hook_retrieval(content_mode):
            hotspot_task = asyncio.create_task(
                _retrieve_confirmed_chat_hooks(
                    latest_topic,
                    int(user["id"]),
                    session_id=req.session_id,
                    content_mode=content_mode,
                    event_anchor=event_anchor,
                )
            )
        else:
            hotspot_retrieval = {
                "status": "not_requested",
                "reason": f"content_mode_{content_mode}",
                "message": "当前主题不需要热点 Hook 补采。",
                "hooks": [],
                "video": {"status": "disabled"},
                "failure_class": None,
                "event_anchor": event_anchor,
                "producible_topics": [],
            }
        content_task = asyncio.create_task(ai_engine.chat_platforms(
            messages=messages,
            context=req.context or "",
            command=req.command,
            tone=req.tone,
            length=req.length,
            platforms=platforms,
            topic=latest_topic,
            assets=[item for item in db.list_assets(status="active") if not item.get("hotspot_id")],
        ))
        if hotspot_task is not None:
            hotspot_retrieval, outputs = await asyncio.gather(hotspot_task, content_task)
        else:
            outputs = await content_task
        if content_mode == "comparison_research":
            outputs, authenticity_blocked = ai_engine.enforce_comparison_authenticity(outputs, evidence)
            if authenticity_blocked:
                hotspot_retrieval = {
                    "status": "not_requested",
                    "reason": "authenticity_blocked",
                    "message": "评测结论缺少证据，已降级为对比框架，未启动热点补采。",
                    "hooks": [],
                    "video": {"status": "disabled"},
                    "failure_class": None,
                    "event_anchor": event_anchor,
                    "producible_topics": [],
                }

    if degraded_from_comparison:
        outputs, _ = ai_engine.enforce_comparison_authenticity(
            outputs, {"sufficient": False, "evidence_state": "insufficient"},
        )

    for item in outputs:
        if item["platform"] == "xiaohongshu" and item.get("title") != "生成失败":
            item["image_pages"], item["attachments"] = _render_xhs_carousel(
                item["title"], item.get("image_pages"), item.get("title") or "", "",
            )
    readiness = ((hotspot_retrieval or {}).get("video") or {}).get("delivery_readiness") or {}
    # Only treat as insufficient when Hook matched but adaptive planning still
    # cannot produce a renderable plan (delivery_ready=false). Thin owned stock
    # with delivery_ready=true is adaptation, not a chat stop state.
    brand_assets_insufficient = bool(
        (hotspot_retrieval or {}).get("status") == "matched"
        and readiness
        and not readiness.get("delivery_ready", True)
    )
    result_state = chat_intent.derive_result_state(
        content_mode=content_mode,
        evidence_state=evidence["evidence_state"],
        hotspot_retrieval=hotspot_retrieval,
        authenticity_blocked=authenticity_blocked,
        brand_assets_insufficient=brand_assets_insufficient,
        event_anchor=event_anchor,
    )
    generation_unavailable = any(
        str(item.get("source") or "") == "safe_fallback" for item in outputs
    )
    if generation_unavailable:
        result_state = "generation_unavailable"
    first = outputs[0]
    context_content = "\n\n".join(
        f"[{item['platform']}]\n{item['title']}\n{item['body']}"
        for item in outputs
    )
    failure_class = (hotspot_retrieval or {}).get("failure_class")
    if brand_assets_insufficient and not failure_class:
        failure_class = "brand_assets_insufficient"
    hook_kind = (
        (hotspot_retrieval or {}).get("hook_kind")
        or ("generic_logistics" if content_mode in {"evergreen", "general_copy"} else "timely_event")
    )
    return {
        "content": context_content,
        "title": first["title"], "body": first["body"],
        "hashtags": first["hashtags"], "outputs": outputs,
        "hotspot_retrieval": hotspot_retrieval,
        "content_mode": content_mode,
        "degraded_from_comparison": degraded_from_comparison,
        "degradation_message": (
            "对比评测需要真实报价/时效资料，已自动切换为科普视角生成视频；"
            "如手头有官方报价单或测试记录，可点『补充评测资料』生成正式对比评测。"
            if degraded_from_comparison else ""
        ),
        "result_state": result_state,
        "evidence_state": evidence,
        "event_anchor": event_anchor,
        "hook_requirement": "required",
        "hook_kind": hook_kind,
        "failure_class": failure_class,
        "funnel": (hotspot_retrieval or {}).get("funnel") or {},
        "producible_topics": (hotspot_retrieval or {}).get("producible_topics") or [],
        "delivery_readiness": readiness,
        "video_workflow": {
            "status": (
                "ready"
                if (
                    not generation_unavailable
                    and (hotspot_retrieval or {}).get("status") == "matched"
                    and ((hotspot_retrieval or {}).get("video") or {}).get("status") == "ready"
                    and readiness.get("delivery_ready", True)
                )
                else "blocked"
            ),
            "topic": latest_topic,
            "target_duration_ms": 60_000,
            "hotspot_event_ids": (
                ((hotspot_retrieval or {}).get("video") or {}).get("hotspot_event_ids") or []
            ),
            "delivery_readiness": readiness,
            "session_id": req.session_id,
            "block_reason": (
                "AI 文案服务不可用，当前仅为提示文本"
                if generation_unavailable
                else (
                    readiness.get("message")
                    or (hotspot_retrieval or {}).get("message")
                    or "尚未匹配可用于正式成片的热点 Hook"
                )
            ),
        },
    }


@app.get("/api/hotspot-discovery-requests/{request_id}")
async def get_hotspot_discovery_request_status(request_id: int, user=Depends(get_current_user)):
    """User-scoped progress for a chat-triggered Hook discovery request."""
    item = db.get_hotspot_discovery_request(request_id)
    if not item:
        raise HTTPException(404, "补采请求不存在")
    if user["role"] != "admin" and item.get("requested_by") not in (None, user["id"]):
        raise HTTPException(403, "无权查看该补采请求")
    hooks = []
    media_id = item.get("matched_media_id")
    if media_id:
        media = db.get_hotspot_media(int(media_id)) or {}
        asset_id = media.get("asset_id")
        if asset_id:
            for event in db.list_hotspot_event_clips(asset_id=int(asset_id)):
                if event.get("review_status") != "confirmed":
                    continue
                evidence = event.get("evidence") or {}
                hooks.append({
                    "event_clip_id": event["id"],
                    "title": event.get("title_zh") or event.get("title_en"),
                    "description": evidence.get("what_happened") or "",
                    "asset_id": event.get("asset_id"),
                })
    status = item.get("status") or "pending"
    stage = item.get("stage") or {
        "pending": "queued",
        "processing": "fetch_sources",
        "matched": "hooks_ready",
        "unmatched": "done",
        "failed": "done",
        "cancelled_misrouted": "archived",
    }.get(status, status)
    return {
        "id": item["id"],
        "topic": item.get("topic"),
        "status": status,
        "stage": stage,
        "error_message": item.get("error_message"),
        "matched_media_id": media_id,
        "hooks": hooks,
        "recovery": (
            "请更换更具体的时效事件主题后重试"
            if status in {"unmatched", "failed"}
            else ("该请求已归档，对比评测请补充资料后重新生成" if status == "cancelled_misrouted" else None)
        ),
        "updated_at": item.get("updated_at"),
    }


@app.post("/api/hotspot-discovery-requests/archive-misrouted-comparisons")
async def archive_misrouted_comparison_discovery(user=Depends(require_role(UserRole.ADMIN))):
    cancelled = db.cancel_misrouted_comparison_discovery_requests()
    db.add_audit_log(
        user["id"], user["username"], "archive_misrouted_comparison_discovery",
        detail=f"count={len(cancelled)}",
    )
    return {"cancelled": cancelled, "count": len(cancelled)}


@app.post("/api/hotspot-events/{event_id}/hook-kind")
async def set_hotspot_event_hook_kind(event_id: int, body: dict, user=Depends(require_role(UserRole.ADMIN))):
    """Admin: mark a confirmed Hook as timely_event or generic_logistics."""
    event = db.get_hotspot_event_clip(int(event_id))
    if not event:
        raise HTTPException(404, "Hook 不存在")
    kind = str((body or {}).get("hook_kind") or "").strip()
    scenes = (body or {}).get("logistics_scenes")
    if scenes is None:
        scenes = producible_topics.hook_logistics_scenes(event)
    if kind == "generic_logistics" and not producible_topics.is_generic_logistics_eligible({
        **event, "logistics_scenes": scenes,
    }):
        raise HTTPException(400, "该 Hook 不适合作为通用物流开场（疑似市政/政治/体育等）")
    try:
        updated = db.update_hotspot_event_hook_kind(
            int(event_id), hook_kind=kind, logistics_scenes=list(scenes or []),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.add_audit_log(user["id"], user["username"], "set_hotspot_event_hook_kind",
                     target=str(event_id), detail=kind)
    return {"event": updated}


def _validated_chat_video_events(event_ids: list[int]) -> list[dict]:
    """Validate locked Hooks before a durable task consumes model capacity."""
    event_ids = list(dict.fromkeys(int(event_id) for event_id in event_ids))
    events = [db.get_hotspot_event_clip(event_id) for event_id in event_ids]
    if not 1 <= len(event_ids) <= 2 or any(event is None or not _is_confirmed_renderable_hotspot_hook(event) for event in events):
        raise HTTPException(409, "匹配的热点 Hook 已失效，请重新发起对话检索。")
    ordered_events = sorted(events, key=lambda event: int(event["start_ms"]))
    primary = ordered_events[0]
    if not _is_same_confirmed_hotspot_event(ordered_events):
        raise HTTPException(409, "聊天视频的热点片段必须属于同一已确认事件。")
    if len(ordered_events) == 2:
        secondary = ordered_events[1]
        if (
            int(primary["asset_id"]) != int(secondary["asset_id"])
            or int(primary["hotspot_id"]) != int(secondary["hotspot_id"])
            or int(secondary["start_ms"]) < int(primary["end_ms"])
        ):
            raise HTTPException(409, "第二段热点 Hook 必须来自同一母片且不能与第一段重叠。")
    return ordered_events


def _queue_chat_dual_library_video_job(body: ChatDualLibraryVideoRequest, user: dict) -> dict:
    """Persist the user request; planning begins only after the HTTP response."""
    ordered_events = _validated_chat_video_events(body.hotspot_event_ids)
    readiness = _chat_video_delivery_readiness(body.topic.strip(), ordered_events)
    if not readiness.get("delivery_ready"):
        raise HTTPException(409, {
            "message": readiness.get("message") or "热点 Hook 未就绪，无法创建视频项目",
            "delivery_readiness": readiness,
        })
    try:
        tts_provider, voice = video_renderer.resolve_tts_selection(
            body.tts_provider, body.voice, strict=True,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    locked_hook_ids = [int(item["id"]) for item in ordered_events]
    idempotency_key = body.idempotency_key or _chat_dual_library_idempotency_key(
        body.topic, locked_hook_ids, body.platform, body.target_duration_ms
    )
    existing_job = db.get_active_video_generation_job_by_idempotency(user["id"], idempotency_key)
    if existing_job:
        project = db.get_video_project(existing_job["project_id"], created_by=user["id"])
        snapshot = _video_project_snapshot(project)
        topic_brief = db.get_topic_brief(str(snapshot.get("topic_brief_id") or ""), user["id"])
        return {
            "project": project,
            "job": existing_job,
            "job_id": existing_job["id"],
            "created": False,
            "topic_brief": topic_brief,
            "poll_url": f"/api/video-generation/jobs/{existing_job['id']}",
            "status": "queued",
            "message": "已恢复同一个视频生成任务",
            "delivery_readiness": readiness,
        }

    topic = body.topic.strip()
    brief_input = topic if len(topic) >= 3 else f"{topic}物流热点"
    logistics_nodes = _chat_video_logistics_nodes(topic, ordered_events)
    brief = db.create_topic_brief(
        _build_topic_brief_payload(TopicBriefCreateRequest(
            raw_input=brief_input,
            goal="基于已确认热点 Hook 生成 Buffalo 双素材库视频",
            logistics_nodes=logistics_nodes,
            platforms=[body.platform],
            content_form="video",
        )),
        user["id"],
    )
    source_snapshot = {
        "source": "ai_chat",
        "async_generation": True,
        "topic": topic,
        "session_id": body.session_id,
        "topic_brief_id": brief["id"],
        "matched_event_clip_ids": locked_hook_ids,
        "logistics_nodes": logistics_nodes,
        "platform": body.platform,
        "target_duration_ms": body.target_duration_ms,
        "tts_provider": tts_provider,
        "voice": voice,
        "username": user["username"],
        "adaptation": readiness.get("adaptation") or {},
        "delivery_readiness": {
            "status": readiness.get("status"),
            "delivery_ready": True,
            "coverage": readiness.get("coverage") or {},
            "message": readiness.get("message"),
        },
        "pipeline": [
            "topic_brief", "hook_locking", "scripting", "project_building",
            "script_quality_check", "asset_matching", "match_quality_check",
            "preview_rendering", "preview_quality_check", "final_rendering",
            "final_quality_check",
        ],
    }
    project = db.create_video_project(
        created_by=user["id"],
        source_type="topic_brief_dual_library",
        source_snapshot=source_snapshot,
        title=f"{brief_input}｜生成中",
        platform=body.platform,
        target_duration_ms=body.target_duration_ms,
        target_orientation="portrait",
    )
    revision = db.create_video_project_revision(project["id"], {
        "source_type": "topic_brief_dual_library",
        "status": "awaiting_script",
        "title": brief_input,
        "platform": body.platform,
        "duration_target_ms": body.target_duration_ms,
        "target_duration_ms": body.target_duration_ms,
        "tts_provider": tts_provider,
        "voice": voice,
        "brief": {
            "topic_brief_id": brief["id"],
            "logistics_topic": brief_input,
            "logistics_nodes": logistics_nodes,
            "primary_event_id": int(ordered_events[0]["id"]),
            "approved_hook_event_ids": locked_hook_ids,
        },
        "scenes": [],
    }, user["id"])
    job, created = db.create_or_get_video_generation_job(
        project["id"], revision["id"], user["id"], idempotency_key
    )
    if created:
        db.add_video_generation_event(job["id"], "job_created", "视频生成任务已创建，等待后台规划")
        db.add_video_generation_event(
            job["id"], "hooks_locked", "已锁定聊天匹配的热点 Hook",
            {"hook_event_ids": locked_hook_ids, "topic_brief_id": brief["id"]},
        )
    db.add_audit_log(
        user["id"], user["username"], "generate_chat_dual_library_video",
        target=project["id"],
        detail=f"queued; brief={brief['id']}; hooks={','.join(str(item) for item in locked_hook_ids)}",
    )
    adapted = bool((readiness.get("adaptation") or {}).get("adapted"))
    return {
        "project": db.get_video_project(project["id"], created_by=user["id"]),
        "job": job,
        "job_id": job["id"],
        "created": created,
        "topic_brief": brief,
        "poll_url": f"/api/video-generation/jobs/{job['id']}",
        "status": "queued",
        "message": (
            "视频项目已创建；将按现有库存自适应规划并后台生产"
            if adapted
            else "视频生成任务已创建，脚本规划和质检将在后台串行执行"
        ),
        "delivery_readiness": readiness,
    }


@app.post("/api/ai/chat/dual-library-video", status_code=202)
async def generate_chat_dual_library_video(body: ChatDualLibraryVideoRequest, user=Depends(get_current_user)):
    """Persist the action immediately; worker gates perform all planning."""
    return _queue_chat_dual_library_video_job(body, user)


# ==================== Debug (temporary agent instrumentation) ====================

@app.post("/api/_agent_debug_log")
async def agent_debug_log(request: Request):
    payload = await request.json()
    log_path = Path(__file__).resolve().parent / ".cursor" / "debug-34c455.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    return {"ok": True}

# ==================== Static Assets ====================

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 公众号图文素材包静态服务（data/articles/{slug}/ 下的 article.md / meta.json / 图片）
_articles_dir = Path(__file__).resolve().parent / "data" / "articles"
_articles_dir.mkdir(parents=True, exist_ok=True)
app.mount("/article-assets", StaticFiles(directory=str(_articles_dir)), name="article-assets")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
