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
import hotspot_hook_copy
import brand_outro_corpus
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
import hotspot_intake_policy
import hotspot_video_sources
import topic_hook_pipeline
import video_topic_contract
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
    # 批22：管理员配置即进入授权库；旧的全局绿/黄开关不再参与服务启动。
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
    # TTS 单轨：按 TTS_PROVIDER 选择 MiMo 或 MiniMax；ASR 保留独立路由。
    os.environ.setdefault("TTS_PROVIDER", "mimo")
    mimo_key = os.environ.get("MIMO_API_KEY", "")
    minimax_key = os.environ.get("MINIMAX_TOKEN_PLAN_KEY", "")
    if mimo_key or minimax_key:
        logger.info("模型密钥已加载：active_routes=%s", [
            role for role in model_router.ROLES if model_router.key_is_available(role)
        ])
    else:
        logger.warning("未配置可用模型密钥：聊天与规划将不可用")
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


XHS_CAROUSEL_USER_MESSAGE = "文案已生成，小红书配图失败，请重试配图"


def _safe_xhs_carousel(
    title: str,
    pages: list | None,
    topic: str = "",
    category: str = "",
    *,
    output_id: str | None = None,
) -> tuple[list, list, dict | None]:
    """Render Xiaohongshu images without letting OS errors fail the whole chat."""
    try:
        image_pages, attachments = _render_xhs_carousel(title, pages, topic, category)
        return image_pages, attachments, None
    except Exception as exc:
        kind = "permission_denied" if isinstance(exc, PermissionError) else type(exc).__name__
        logger.warning(
            "小红书配图失败 platform=xiaohongshu error_type=%s output_id=%s",
            kind, output_id or title[:40],
        )
        return list(pages or []), [], {
            "platform": "xiaohongshu",
            "error_type": kind,
            "output_id": output_id,
            "message": XHS_CAROUSEL_USER_MESSAGE,
        }


def _planner_validation_kind(error: Exception) -> str:
    text = str(error)
    if "合法 JSON" in text:
        return "invalid_json"
    if "标题、角度或有效分镜" in text:
        return "empty_plan_fields"
    if "旁白超过" in text and "时长上限" in text:
        return "voiceover_too_long"
    if "旁白少于" in text and "时长下限" in text:
        return "voiceover_too_short"
    if "旁白" in text:
        return "invalid_voiceover"
    if "无效分镜" in text:
        return "invalid_scene"
    return "validation_error"


def _targeted_repair_failure_signature(
    pending_rewrites: set[int],
    targeted_errors: dict[int, Exception],
) -> tuple[tuple[int, str], ...]:
    """Return the progress signal used by the MiniMax repair circuit breaker.

    Exact model wording is intentionally ignored. If the same scenes keep
    failing the same validation classes, the repair has made no contract-level
    progress and another paid remote call cannot improve pipeline availability.
    """
    return tuple(
        (index, _planner_validation_kind(targeted_errors[index]))
        for index in sorted(pending_rewrites)
        if index in targeted_errors
    )


def _immutable_topic_guard_nodes(planning_brief: dict) -> list[str]:
    """Return only user-topic nodes for policy/overclaim enforcement.

    ``build_brief`` may enrich retrieval with Hook or generic rescue nodes.
    Those nodes are useful for finding pictures, but they cannot activate a
    customs narration template that the user never requested.
    """
    contract = planning_brief.get("topic_contract")
    if not isinstance(contract, dict):
        topic = str(
            planning_brief.get("requested_topic")
            or planning_brief.get("logistics_topic")
            or ""
        )
        contract = video_topic_contract.build_topic_contract(
            topic, has_event_anchor=True,
        )
    return [str(node) for node in (contract.get("nodes") or []) if str(node).strip()]


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
                content.image_pages, content.attachments, render_error = _safe_xhs_carousel(
                    content.title, content.image_pages, req.topic, req.category,
                    output_id="generate-fallback",
                )
                if render_error:
                    content.quality_warnings = list(content.quality_warnings or []) + [
                        render_error["message"]
                    ]
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
            content.image_pages, content.attachments, render_error = _safe_xhs_carousel(
                content.title, content.image_pages, req.topic, req.category,
                output_id="generate",
            )
            if render_error:
                content.quality_warnings = list(content.quality_warnings or []) + [
                    render_error["message"]
                ]
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
        # 批20：审核通过即发布——无 scheduled_at 时兜底为 now，否则调度器永不取
        if item.get("scheduled_at"):
            db.update_queue_status(item_id, "queued")
        else:
            db.update_queue_status(
                item_id, "queued",
                scheduled_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
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


_HOTSPOT_OUT_OF_SCOPE_TERMS = (
    # 原有基线
    "市政", "环卫", "垃圾", "污水", "供水", "管道破裂", "公园", "野生动物",
    "治安", "犯罪", "政治", "委员会", "证词", "娱乐",
    "municipal", "refuse", "waste", "sewage", "wildlife", "testimony", "commission",
    # 听证/法庭/选举/体育/社会等非物流场景
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

# These facts can introduce a logistics risk or preparation question without
# claiming that Buffalo caused, solved, or directly participated in the news
# event.  Generic "vehicle" alone is intentionally not enough.
_SOFT_LOGISTICS_FACT_GROUPS = (
    (
        ("港口", "港区", "码头", "集装箱", "装卸", "port", "harbour", "harbor", "container", "cargo terminal"),
        "港口或装卸环节出现变化时，发货前要先核对哪些节点？",
    ),
    (
        ("边境", "口岸", "海关", "查验", "清关", "border", "customs", "clearance"),
        "边境或查验环节变化时，运输和单证要先核对什么？",
    ),
    (
        ("道路", "公路", "封路", "拥堵", "堵车", "排队", "滞留", "交通", "路线", "通行", "road", "route", "traffic", "congestion", "closed road"),
        "道路通行受影响时，货运路线和配送节点要先核对什么？",
    ),
    (
        ("仓储", "仓库", "分拣", "配送", "运输", "货运", "卡车", "物流", "warehouse", "delivery", "transport", "freight", "truck"),
        "仓配或运输现场出现变化时，哪些动作需要先核对？",
    ),
    (
        ("暴雪", "大雪", "积雪", "暴雨", "洪水", "风暴", "恶劣天气", "极端天气", "snow", "storm", "flood", "severe weather"),
        "天气变化影响通行时，运输和配送要先核对哪些条件？",
    ),
)

# A soft bridge may tolerate incidental context such as “students stranded by
# snow”, but it must never launder a purely political, sporting, criminal or
# medical story into a Buffalo advertisement.
_SOFT_BRIDGE_HARD_BLOCK_TERMS = (
    "政治", "议会", "政党", "选举", "总统", "部长", "峰会", "听证", "法庭", "法院",
    "足球", "联赛", "世界杯", "球队", "女足", "决赛", "赛事", "橄榄球", "板球", "网球",
    "医疗", "医院", "手术", "艾滋", "hiv", "疫苗", "谋杀", "遇害", "杀手", "难民", "移民",
)


def _hotspot_fact_text(event: dict) -> str:
    evidence = event.get("evidence") or {}
    return " ".join(
        str(event.get(key) or "")
        for key in ("title_zh", "title_en")
    ) + " " + str(evidence.get("what_happened") or "")


def _soft_bridge_visual_anchor(event: dict, question: str) -> bool:
    """Require the visual audit to show the logistics object/action too."""
    evidence = event.get("evidence") or {}
    visual = evidence.get("visual_audit") or {}
    if not visual:
        return False
    visible_text = " ".join(
        str(value or "")
        for key in ("scene_type", "visible_objects", "visible_actions", "reason")
        for value in ((visual.get(key) if isinstance(visual.get(key), list) else [visual.get(key)]))
    ).casefold()
    anchor_groups = {
        "港口或装卸环节出现变化时，发货前要先核对哪些节点？": (
            "港口", "码头", "集装箱", "吊机", "装卸", "货柜", "port", "container", "crane", "cargo",
        ),
        "边境或查验环节变化时，运输和单证要先核对什么？": (
            "边境", "海关", "查验", "机场", "航班", "行李", "手推车", "customs", "airport", "luggage", "baggage",
        ),
        "道路通行受影响时，货运路线和配送节点要先核对什么？": (
            "道路", "公路", "车道", "卡车", "货车", "车辆", "交通", "road", "truck", "vehicle", "traffic", "route",
        ),
        "仓配或运输现场出现变化时，哪些动作需要先核对？": (
            "仓库", "分拣", "包裹", "货物", "卡车", "货车", "叉车", "配送", "warehouse", "parcel", "forklift", "delivery",
        ),
        "天气变化影响通行时，运输和配送要先核对哪些条件？": (
            "道路", "公路", "车道", "卡车", "货车", "车辆", "积雪", "暴雪", "洪水", "road", "truck", "vehicle", "snow", "flood",
        ),
    }
    if not any(marker.casefold() in visible_text for marker in anchor_groups.get(question, ())):
        return False
    # An interview or stand-up shot with a parked service vehicle is not a
    # logistics Hook.  Permit a border/airport frame when passenger or baggage
    # movement is also visible, since that is a real clearance context.
    action_text = " ".join(str(value or "") for value in (visual.get("visible_actions") or [])).casefold()
    interview_only = ("采访", "话筒", "麦克风", "讲话", "发言", "播报", "面对镜头", "interview", "microphone", "speaking")
    logistics_actions = (
        "行驶", "驾驶", "通行", "装卸", "搬运", "分拣", "装载", "卸货", "推车", "行走",
        "排队等待放行", "queue at border", "driving", "loading", "unloading", "walking",
    )
    if any(marker in action_text for marker in interview_only) and not any(
        marker in action_text for marker in logistics_actions
    ):
        return False
    return True


def _derive_soft_logistics_question(event: dict) -> str:
    """Derive a conditional logistics question from verified visual facts only."""
    evidence = event.get("evidence") or {}
    if str(evidence.get("logistics_question") or "").strip() and event.get("logistics_bridge_mode") != "soft":
        return ""
    fact_text = _hotspot_fact_text(event).casefold()
    if any(term.casefold() in fact_text for term in _SOFT_BRIDGE_HARD_BLOCK_TERMS):
        return ""
    mobility_terms = (
        "道路", "公路", "交通", "路线", "通行", "车辆", "卡车", "运输", "货运",
        "road", "route", "traffic", "vehicle", "truck", "transport", "freight",
    )
    for markers, question in _SOFT_LOGISTICS_FACT_GROUPS:
        if not any(term.casefold() in fact_text for term in markers):
            continue
        # Weather is a logistics bridge only when the frame also contains a
        # mobility/route fact; a reporter standing in bad weather is not enough.
        if markers[0] in ("暴雪", "大雪", "积雪", "暴雨", "洪水", "风暴", "恶劣天气", "极端天气"):
            if not any(term.casefold() in fact_text for term in mobility_terms):
                continue
        if _soft_bridge_visual_anchor(event, question):
            return question
    return ""


def _with_soft_logistics_bridge(event: dict) -> dict:
    """Return an in-memory event with a cautious bridge; never rewrite audit data."""
    question = _derive_soft_logistics_question(event)
    if not question:
        return event
    bridged = dict(event)
    evidence = dict(event.get("evidence") or {})
    evidence["logistics_question"] = question
    bridged["evidence"] = evidence
    bridged["logistics_bridge_mode"] = "soft"
    bridged["derived_logistics_question"] = question
    return bridged


def _soft_bridge_context_allowed(event: dict) -> bool:
    """Allow incidental out-of-scope context only when logistics facts dominate."""
    fact_text = _hotspot_fact_text(event).casefold()
    if any(term.casefold() in fact_text for term in _SOFT_BRIDGE_HARD_BLOCK_TERMS):
        return False
    return bool(_derive_soft_logistics_question(event))


_HTTP_SOURCE_PREFIXES = ("http://", "https://")
_EXTERNAL_ASSET_SOURCES = frozenset({"youtube", "tiktok", "remote", "hotspot", "official_news"})


def _has_external_hotspot_provenance(event: dict) -> bool:
    """Require a real external source before an event may become a Hook.

    ``hotspot_event_clips`` are derived clips, not proof of origin by
    themselves.  A legacy local Buffalo asset can otherwise be wrapped in a
    ``generic_logistics`` event and later rendered as ``hotspot_evidence``.
    Keep local files usable as owned proof, but require the parent hotspot or
    asset provenance to carry an external URL before exposing the clip as a
    Hook.
    """
    asset = db.get_asset(int(event.get("asset_id") or 0)) if event.get("asset_id") else None
    parent = db.get_hotspot(int(event.get("hotspot_id") or 0)) if event.get("hotspot_id") else None
    asset_source_url = str((asset or {}).get("source_url") or "").strip()
    parent_source_url = str((parent or {}).get("source_url") or "").strip()
    asset_source = str((asset or {}).get("source") or "").strip().casefold()
    if asset_source in _EXTERNAL_ASSET_SOURCES:
        return True
    if asset_source_url.casefold().startswith(_HTTP_SOURCE_PREFIXES):
        return True
    if parent_source_url.casefold().startswith(_HTTP_SOURCE_PREFIXES):
        # A locally materialized copy is acceptable only when it is explicitly
        # linked back to the external parent.  An orphan local file with an
        # invented/legacy hotspot row must not pass on the parent's name alone.
        asset_hotspot_id = (asset or {}).get("hotspot_id")
        return asset_hotspot_id is not None and int(asset_hotspot_id or 0) == int(event.get("hotspot_id") or 0)
    return False


def _is_legacy_ready_hotspot_hook(event: dict) -> bool:
    """Restore existing timely Hooks that already have an audited clip.

    The logistics-question and source-quota gates were added after the first
    batch of event clips was generated.  Those clips already carry the
    pipeline's ``logistics_scenes`` evidence, so keep them directly usable
    without rewriting their stored audit data.  Review status, playable media,
    external provenance, and the two core fact fields remain mandatory.
    """
    evidence = event.get("evidence") or {}
    required = ("what_happened", "hook_reason")
    values = [str(evidence.get(key) or "").strip() for key in required]
    placeholders = ("未记录", "待确认", "unknown", "n/a")
    logistics_scenes = event.get("logistics_scenes") or []
    return bool(
        str(event.get("hook_kind") or "") == "timely_event"
        and logistics_scenes
        and str(event.get("review_status") or "") == "confirmed"
        and str(event.get("clip_status") or "") == "ready"
        and str(event.get("clip_path") or "").strip()
        and _has_external_hotspot_provenance(event)
        and all(value and not value.casefold().startswith(placeholders) for value in values)
    )


def _is_confirmed_generic_logistics_hook(event: dict) -> bool:
    """Allow reviewed evergreen logistics openers without calling them news.

    Generic logistics Hooks are Buffalo-owned operational openers materialized
    into the Hook library for evergreen topics.  They intentionally have no
    external news provenance.  Keep their gate separate from timely events so
    this exception cannot promote an arbitrary local asset into a news Hook.
    """
    if str(event.get("hook_kind") or "") != "generic_logistics":
        return False
    evidence = event.get("evidence") or {}
    required = ("what_happened", "hook_reason", "logistics_question")
    values = [str(evidence.get(key) or "").strip() for key in required]
    placeholders = ("未记录", "待确认", "unknown", "n/a")
    identity = str(evidence.get("event_identity") or "").strip().casefold()
    scenes = event.get("logistics_scenes") or []
    return bool(
        identity.startswith("generic-")
        and str(event.get("review_status") or "") == "confirmed"
        and str(event.get("clip_status") or "") == "ready"
        and str(event.get("clip_path") or "").strip()
        and scenes
        and hotspot_intake_policy.has_real_logistics_scene(event)
        and all(value and not value.casefold().startswith(placeholders) for value in values)
    )


def _is_confirmed_renderable_hotspot_hook(event: dict) -> bool:
    """Only expose verified Hooks with a defensible logistics bridge and proxy.

    Existing timely event clips with pipeline-generated logistics scene
    evidence retain their original direct-ready behavior.  New or incomplete
    records still need the cautious in-memory bridge below; pure politics,
    sports, interviews and public-affairs footage remain audited archive items.
    """
    if _is_confirmed_generic_logistics_hook(event):
        return True
    if _is_legacy_ready_hotspot_hook(event):
        return True
    candidate = _with_soft_logistics_bridge(event)
    evidence = candidate.get("evidence") or {}
    required = ("what_happened", "hook_reason", "logistics_question")
    values = [str(evidence.get(key) or "").strip() for key in required]
    placeholders = ("未记录", "待确认", "unknown", "n/a")
    event_text = " ".join((
        str(candidate.get("title_zh") or ""), str(candidate.get("title_en") or ""),
        str(evidence.get("what_happened") or ""), str(evidence.get("logistics_question") or ""),
    )).casefold()
    out_of_scope_hit = any(term.casefold() in event_text for term in _HOTSPOT_OUT_OF_SCOPE_TERMS)
    # A snow-blocked road may mention students because they are affected, but
    # the usable bridge is still the visible road/access condition.  Do not
    # make the same exception for a story whose facts are intrinsically
    # political, sporting, criminal or medical.
    out_of_scope_allowed = (
        not out_of_scope_hit
        or (
            candidate.get("logistics_bridge_mode") == "soft"
            and _soft_bridge_context_allowed(candidate)
        )
    )
    # A legacy Hook can have a real oil-price frame but still carry an old,
    # unsupported bridge such as “South African transport costs have already
    # risen”.  Keep the factual frame out of the usable library until it is
    # re-curated with a cautious RAG-supported question.
    unsupported_cost_leap = bool(
        any(term in event_text for term in ("红海", "国际油价", "red sea", "oil price"))
        and any(term in event_text for term in ("同步攀升", "成本已", "运费已", "costs have risen"))
    )
    real_logistics = (
        hotspot_intake_policy.has_real_logistics_scene(candidate)
        or candidate.get("logistics_bridge_mode") == "soft"
    )
    placeholder_question = hotspot_intake_policy.is_placeholder_logistics_question(
        evidence.get("logistics_question")
    )
    return bool(
        str(event.get("review_status") or "") == "confirmed"
        and str(event.get("clip_status") or "") == "ready"
        and str(event.get("clip_path") or "").strip()
        and _has_external_hotspot_provenance(event)
        and all(value and not value.casefold().startswith(placeholders) for value in values)
        and out_of_scope_allowed
        and not unsupported_cost_leap
        and real_logistics
        and not placeholder_question
    )


def _is_audited_hotspot_hook(event: dict) -> bool:
    """Expose a fact-backed audited clip without confusing it with a ready Hook.

    The audit library is intentionally broader than the renderable logistics
    library: a clip may have a confirmed visual fact and a local playable proxy
    while still needing a cautious logistics bridge before it can enter a
    video project.  The assets page can show those clips for review, but action
    endpoints continue to use ``_is_confirmed_renderable_hotspot_hook``.
    """
    evidence = event.get("evidence") or {}
    required = ("what_happened", "hook_reason")
    values = [str(evidence.get(key) or "").strip() for key in required]
    placeholders = ("未记录", "待确认", "unknown", "n/a")
    # Database-backed event rows must carry external provenance even when they
    # are only shown in the audited archive.  A few in-memory legacy callers
    # provide no identity at all; keep those fact-only records available for
    # diagnostics, while the renderable predicate remains strict.
    has_identity = bool(event.get("asset_id") or event.get("hotspot_id"))
    return bool(
        str(event.get("review_status") or "") == "confirmed"
        and str(event.get("clip_status") or "") == "ready"
        and str(event.get("clip_path") or "").strip()
        and (not has_identity or _has_external_hotspot_provenance(event))
        and all(value and not value.casefold().startswith(placeholders) for value in values)
    )


def _event_source_class(event: dict) -> str:
    media = None
    if event.get("asset_id"):
        media = db.get_hotspot_media_by_asset_id(int(event["asset_id"]))
    parent = db.get_hotspot(int(event["hotspot_id"])) if event.get("hotspot_id") else None
    return hotspot_intake_policy.resolve_source_class(media, parent)


@app.get("/api/hotspot-events")
async def list_hotspot_events(asset_id: int | None = None, hotspot_id: int | None = None,
                              eligible_only: bool = True, audited_only: bool = False,
                              library_status: str | None = None,
                              include_matches: bool | None = None,
                              user=Depends(get_current_user)):
    events = db.list_hotspot_event_clips(asset_id=asset_id, hotspot_id=hotspot_id)
    for event in events:
        event["source_class"] = _event_source_class(event)
    flags = hotspot_intake_policy.assign_ready_flags(
        events,
        is_hard_ready=_is_confirmed_renderable_hotspot_hook,
        source_class_of=lambda event: str(event.get("source_class") or "general_news"),
    )
    # Library cards only need Hook metadata. Matching 20k segments here made
    # /assets.html wait several seconds on a white empty shell.
    compute_matches = True if include_matches is True else (
        False if include_matches is False else asset_id is not None
    )
    segments = db.list_asset_segments(limit=20_000) if compute_matches else []
    for event in events:
        status = flags.get(int(event.get("id") or 0), {})
        legacy_ready = _is_legacy_ready_hotspot_hook(event)
        event["is_renderable"] = legacy_ready or bool(status.get("is_renderable"))
        event["quota_held"] = False if legacy_ready else bool(status.get("quota_held"))
        event["library_status"] = (
            "ready" if event["is_renderable"]
            else ("audit_only" if _is_audited_hotspot_hook(event) else "ineligible")
        )
        event["ineligible_reason"] = "" if event["is_renderable"] else (
            status.get("ineligible_reason") or "未通过可成片门禁"
        )
        _decorate_hotspot_event(event, segments, include_matches=compute_matches)
    if library_status in {"ready", "audit_only"}:
        events = [event for event in events if event.get("library_status") == library_status]
    elif audited_only:
        events = [event for event in events if _is_audited_hotspot_hook(event)]
    elif eligible_only:
        events = [event for event in events if event.get("is_renderable")]
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


def _decorate_hotspot_event(event: dict, segments: list[dict] | None = None,
                            include_matches: bool = True) -> dict:
    """Expose a hotspot event as a previewable virtual asset without copying its mother video."""
    bridged = _with_soft_logistics_bridge(event)
    if bridged is not event:
        event.clear()
        event.update(bridged)
    asset = db.get_asset(int(event["asset_id"])) or {}
    public = media_assets.public_asset(asset) if asset else {}
    # 批17：卡片时效徽标取父热点真实发布时间（RSS 为 RFC2822 / YouTube 经回填为 ISO）
    parent = db.get_hotspot(int(event["hotspot_id"])) if event.get("hotspot_id") else {}
    event["published_at"] = (parent or {}).get("published_at")
    # Library cards only load 200 hotspots for the filter dropdown; embed the
    # parent title here so 所属热点 does not fall back to「未绑定」.
    event["hotspot_title"] = str((parent or {}).get("title") or "").strip()
    event["hotspot_title_zh"] = str((parent or {}).get("title_zh") or "").strip()
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
    if include_matches:
        event["matches"] = hotspot_event_matching.match_event(
            event, segments or db.list_asset_segments(limit=20_000)
        )
    else:
        event["matches"] = {"owned_candidates": [], "owned_match_reason": ""}
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
        "source_class": event.get("source_class") or _event_source_class(event),
    }
    evidence = event.get("evidence") or {}
    source_title = str(event.get("title_zh") or event.get("title_en") or "").strip()
    attention_title = str(
        evidence.get("attention_title")
        or hotspot_hook_copy.attention_headline(
            str(evidence.get("what_happened") or ""),
            str(evidence.get("logistics_question") or ""),
            source_title,
        )
    ).strip()
    event["source_title"] = source_title
    event["attention_title"] = attention_title
    if attention_title:
        event["virtual_asset"]["name"] = attention_title
    event["logistics_scenes_real"] = hotspot_intake_policy.real_logistics_scenes(
        event.get("logistics_scenes"),
        " ".join(
            str(event.get(key) or "")
            for key in ("title_zh", "title_en")
        ) + " " + str(evidence.get("what_happened") or ""),
    )
    if "source_class" not in event:
        event["source_class"] = _event_source_class(event)
    return event


@app.get("/api/hotspot-events/{event_id}")
async def get_hotspot_event(event_id: int, user=Depends(get_current_user)):
    event = db.get_hotspot_event_clip(event_id)
    if not event:
        raise HTTPException(404, "热点事件不存在")
    event = _with_soft_logistics_bridge(event)
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


_CHAT_HOOK_VISIBLE_SCENE_TERMS: dict[str, tuple[str, ...]] = {
    "warehouse": (
        "仓库", "仓储", "分拣", "分拨", "入库", "出库", "装箱", "打包",
        "搬运", "装卸", "叉车", "扫码", "包裹", "warehouse", "sorting",
        "packing", "forklift", "parcel", "loading",
    ),
    "last_mile": (
        "末端", "配送", "派送", "交付", "交接", "快递员", "配送车辆",
        "courier", "last mile", "delivery vehicle", "parcel delivery",
    ),
    "border": (
        "边境", "口岸", "海关", "清关", "查验", "放行", "border", "customs",
        "clearance", "beitbridge",
    ),
    "customs": (
        "海关", "清关", "查验", "放行", "报关", "customs", "clearance",
    ),
    "port": (
        "港口", "码头", "集装箱", "堆场", "船舶", "港区", "port", "harbour",
        "harbor", "container", "terminal", "vessel",
    ),
    "road": (
        "公路", "道路", "高速", "卡车", "货车", "车队", "交通", "路线",
        "road", "highway", "truck", "traffic", "freight vehicle",
    ),
    "disruption": (
        "事故", "侧翻", "起火", "火灾", "积雪", "大雪", "暴雪", "洪水",
        "拥堵", "堵车", "封路", "停摆", "延误", "accident", "fire", "snow",
        "flood", "congestion", "closure", "delay",
    ),
}

_CHAT_HOOK_VISUAL_SCENE_MAP: dict[str, frozenset[str]] = {
    "port": frozenset({"port", "disruption"}),
    "border": frozenset({"border", "customs", "disruption"}),
    "road": frozenset({"road", "disruption"}),
    "warehouse": frozenset({"warehouse", "disruption"}),
    "delivery": frozenset({"last_mile", "disruption"}),
}

_CHAT_HOOK_ROAD_LOGISTICS_OBJECTS = (
    "卡车", "货车", "车辆", "车队", "货运", "运输", "配送", "派送",
    "快递", "包裹", "货物", "truck", "freight", "vehicle", "delivery",
    "courier", "parcel", "cargo",
)


def _grounded_chat_hook_scene_keys(event: dict) -> set[str]:
    """Return logistics scenes supported by immutable visible-fact evidence.

    Legacy rows can carry model-authored logistics tags, while the event
    lexicon also recognises ``货架``. Neither makes an interview in front of a
    beauty shelf a warehouse Hook. Automatic output therefore requires a
    concrete logistics location, object or action in the verified fact text.
    """
    evidence = event.get("evidence") or {}
    visual_audit = evidence.get("visual_audit") or {}
    audit_scene = str(visual_audit.get("scene_type") or "").strip().casefold()
    # Three-frame visual evidence is authoritative when present.  A legacy
    # title/summary can call a flooded doorway a warehouse, but an explicit
    # ``other``/``non_event`` visual verdict means there is no proven
    # logistics scene and the Hook must not enter automatic production.
    if visual_audit and audit_scene in {"other", "non_event", "agriculture"}:
        return set()

    fact_text = " ".join(str(value or "") for value in (
        event.get("title_zh"), event.get("title_en"),
        evidence.get("what_happened"), evidence.get("event_identity"),
    )).casefold()
    if not fact_text.strip():
        return set()
    grounded = {
        scene for scene, markers in _CHAT_HOOK_VISIBLE_SCENE_TERMS.items()
        if any(marker.casefold() in fact_text for marker in markers)
    }
    # A broken pavement, street interview or generic traffic sentence is not
    # a logistics Hook.  Road grounding also needs a visible freight,
    # delivery, vehicle or cargo object/action.
    if "road" in grounded and not any(
        marker.casefold() in fact_text for marker in _CHAT_HOOK_ROAD_LOGISTICS_OBJECTS
    ):
        grounded.discard("road")

    if visual_audit and audit_scene:
        allowed = _CHAT_HOOK_VISUAL_SCENE_MAP.get(audit_scene)
        if not allowed:
            return set()
        grounded &= set(allowed)
    return grounded & set(hotspot_intake_policy.REAL_LOGISTICS_SCENES)


def _build_topic_brief_payload(body: TopicBriefCreateRequest) -> dict:
    raw = video_topic_contract.normalize_topic_input(body.raw_input)
    keywords = _topic_keywords(raw)
    topic_contract = video_topic_contract.build_topic_contract(raw)
    # The user's natural-language request is the narrative contract.  A list
    # of extracted logistics nodes is only for media retrieval; using it as
    # the subject flattened requests such as a Takealot experience video into
    # merely "配送", after which the Hook headline could take over the video.
    subject = "南非物流" if "南非" in keywords and "物流" in keywords and len(raw) <= 8 else raw[:300]
    nodes = list(dict.fromkeys(
        list(topic_contract.get("nodes") or [])
        + body.logistics_nodes
        + [item for item in keywords if item in {"清关", "末端", "配送", "仓储", "港口", "运输", "分拣"}]
    ))
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


def _topic_anchor_contract(topic: str, *, has_event_anchor: bool = False) -> dict | None:
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
    first_principles = video_topic_contract.build_topic_contract(
        topic, has_event_anchor=has_event_anchor,
    )
    if first_principles.get("intent") != "custom_logistics_topic":
        return first_principles
    return None


def _validate_generated_topic_anchor(
    generated: dict,
    brief: dict,
    *,
    has_event_anchor: bool = False,
) -> None:
    """Reject a fluent but off-topic script before it becomes a project."""
    topic = str(brief.get("raw_input") or brief.get("requested_topic") or brief.get("subject") or "")
    contract = _topic_anchor_contract(topic, has_event_anchor=has_event_anchor)
    if not contract:
        return
    if contract.get("contract_version"):
        errors = video_topic_contract.validate_generated_topic_contract(generated, contract)
        if errors:
            raise ValueError("；".join(errors))
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


def _repair_generated_topic_contract(
    generated: dict,
    *,
    brief: dict,
    scenes: list[dict],
    event: dict | None,
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
) -> dict:
    """Apply the smallest auditable repair when a reachable model misses nodes.

    This is deliberately narrower than the offline fallback path.  It does not
    rewrite a script wholesale, invent a Hook, or claim a service outcome.  It
    only restores the immutable user-topic contract (title/opening/named
    logistics nodes) using phrases from that contract, then marks changed
    beats as ``policy_repair`` for the report.
    """
    topic = str(
        brief.get("raw_input")
        or brief.get("requested_topic")
        or brief.get("subject")
        or brief.get("logistics_topic")
        or ""
    )
    contract = _topic_anchor_contract(topic, has_event_anchor=bool(event))
    if not contract or not contract.get("contract_version"):
        return generated

    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if len(repaired["scenes"]) != len(scenes):
        raise ValueError("主题契约修复无法对应正式分镜数量")
    original = {**repaired, "scenes": [dict(item) for item in repaired["scenes"]]}

    # A reachable model must not replace the requested subject with a Hook
    # headline.  The contract's safe title is a bounded topic correction, not
    # a generic fallback slogan.
    title = str(repaired.get("title") or "")
    repaired["title"] = video_topic_contract.ensure_title_satisfies_contract(title, contract)

    # Topic-only openings are mandatory and are intentionally deterministic so
    # the first frame cannot become a generic “现场” opener.  For an event,
    # scene one remains the verified Hook and is never overwritten here.
    if not event and repaired["scenes"]:
        opening_hook = str(contract.get("opening_hook") or "").strip()
        first = str(repaired["scenes"][0].get("voiceover") or "")
        maximum = voiceover_limits[0] if voiceover_limits else None
        compact_length = len("".join(opening_hook.split()))
        if opening_hook and opening_hook not in first:
            if maximum is not None and compact_length > maximum:
                raise ValueError("主题型开场超过首镜头时长上限，无法进行最小主题修复")
            repaired["scenes"][0]["voiceover"] = opening_hook
            repaired["scenes"][0]["text_overlay"] = opening_hook.rstrip("。！？")[:24]

    missing_groups = video_topic_contract.missing_narrative_groups(repaired, contract)
    if not missing_groups:
        return repaired

    intent = str(contract.get("intent") or "")
    phrase_map: dict[str, tuple[str, ...]] = {
        "same_city_delivery_sla": (
            "从接单和取件开始计时，才看得出同城配送时效。",
            "分拣和交接，直接影响同城配送后续时效。",
            "出车和配送衔接顺不顺，决定同城路线时效。",
            "签收完成后记录时长，才算送达闭环。",
        ),
            "local_courier_comparison": (
                "取件和揽收，是比较同城快递的第一步，先核对。",
                "分拣和仓内交接，决定流程是否清楚。",
                "末端配送和交接，才看得出服务差异。",
                "对比要看完整维度，不能只看一个报价。",
            ),
        "peak_overflow_response": (
            "库位和库容，决定旺季仓储能否装得下。",
            "分拣一旦失序，后续配送就会被拖慢。",
            "交接不断点，配送才不容易形成积压。",
            "预案要写清触发点和应对顺序，才能执行。",
        ),
        "peak_full_cycle_review": (
            "入库和仓储，是旺季备战复盘的起点。",
            "分拣节点要提前校准，避免后面一起拥堵。",
            "出车和运输，需要提前排好衔接和顺序。",
            "交付和末端配送，决定复盘是否真正闭环。",
        ),
        "policy_change_verification": (
            "先确认官方发布机构，来源不明不能当结论。",
            "再确认政策适用对象，避免把范围理解错。",
            "生效日期必须先核清，时间点不能靠猜。",
            "清关准备要跟着官方原文走，不能只看标题。",
        ),
    }
    phrases = list(phrase_map.get(intent) or ())
    if not phrases:
        for group in missing_groups:
            terms = [str(term) for term in group[:2] if str(term).strip()]
            if terms:
                phrases.append("、".join(terms) + "，是这个物流主题的关键节点。")

    candidate_indexes: list[int] = []
    if event:
        bridge_indexes = set(_owned_bridge_window_indices(scenes, maximum=1))
        candidate_indexes = [
            index for index, scene in enumerate(scenes)
            if str(scene.get("scene_role") or "") == "owned_proof"
            and str(scene.get("evidence_type") or "") == "owned_video"
            # Scene one after the Hook owns the immutable
            # fact→impact→Buffalo bridge.  Topic-contract repair must use a
            # later proof beat, otherwise the final bridge repair overwrites
            # the inserted topic term and the same revision fails again.
            and index not in bridge_indexes
        ]
    else:
        candidate_indexes = [
            index for index, scene in enumerate(scenes)
            if str(scene.get("scene_role") or "") in {"topic_context", "owned_proof"}
            and str(scene.get("evidence_type") or "") == "owned_video"
        ]
    candidate_indexes += [
        index for index, scene in enumerate(scenes)
        if index not in candidate_indexes
        and str(scene.get("scene_role") or "") not in {"hotspot_evidence", "brand_cta"}
        and str(scene.get("evidence_type") or "") != "brand_endcard"
    ]
    used_indexes: set[int] = set()

    for group_index, group in enumerate(missing_groups):
        phrase_candidates = []
        if group_index < len(phrases):
            phrase_candidates.append(phrases[group_index])
        if group:
            compact_terms = "、".join(str(term) for term in group[:3])
            phrase_candidates.extend((
                f"{compact_terms}先核对。",
                f"{compact_terms}要逐项核对。",
                f"{compact_terms}要逐项核对并记录。",
                f"先核对{compact_terms}，再安排后续动作。",
                f"{compact_terms}是这个物流主题的关键节点。",
            ))
        phrase_candidates = list(dict.fromkeys(phrase_candidates))
        repaired_one = False
        for scene_index in candidate_indexes:
            if scene_index in used_indexes or scene_index >= len(repaired["scenes"]):
                continue
            current = str(repaired["scenes"][scene_index].get("voiceover") or "").strip()
            minimum = voiceover_minimums[scene_index] if scene_index < len(voiceover_minimums) else None
            maximum = voiceover_limits[scene_index] if scene_index < len(voiceover_limits) else None
            options = []
            for phrase in phrase_candidates:
                options.extend((f"{current.rstrip('。！？；，、')}，{phrase}", phrase))
            for option in options:
                compact_length = len("".join(option.split()))
                if minimum is not None and compact_length < minimum:
                    continue
                if maximum is not None and compact_length > maximum:
                    continue
                if video_topic_contract.incomplete_sentence_issues({"scenes": [{"voiceover": option}]}):
                    continue
                repaired["scenes"][scene_index]["voiceover"] = option
                repaired["scenes"][scene_index]["text_overlay"] = option.rstrip("。！？")[:24]
                used_indexes.add(scene_index)
                repaired_one = True
                break
            if repaired_one:
                break
        if not repaired_one:
            raise ValueError(
                "主题契约修复无法在现有分镜字数窗口内补齐：" + "/".join(str(term) for term in group)
            )

    return _annotate_copy_revisions(
        original, repaired, reason="topic_contract_rescue",
    )


def _enforce_generated_topic_opening(
    generated: dict,
    brief: dict,
    scenes: list[dict],
    event: dict | None,
) -> dict:
    """Lock the user's topic and owned-media opener before any model score.

    A non-event topic cannot acquire a fake news Hook.  The first two owned
    scenes carry a deterministic editorial Hook and its topic bridge.
    """
    topic = str(brief.get("raw_input") or brief.get("requested_topic") or brief.get("subject") or "")
    contract = video_topic_contract.build_topic_contract(topic, has_event_anchor=bool(event))
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if contract.get("intent") == "custom_logistics_topic":
        return repaired
    repaired["title"] = contract.get("safe_title") or contract["label"]
    if event:
        # The verified Hook remains scene one. The first owned scene after it
        # is reserved for the fact→risk→Buffalo brand bridge, so the immutable
        # topic contract is locked into the following owned proof beats.
        # This prevents a real Hook from replacing the user's requested topic
        # without ever asking a model to rewrite the approved event fact.
        owned_proof_indexes = [
            index for index, scene in enumerate(scenes)
            if str(scene.get("scene_role") or "") == "owned_proof"
            and str(scene.get("evidence_type") or "") == "owned_video"
        ]
        context_indexes = owned_proof_indexes[1:] if len(owned_proof_indexes) > 1 else owned_proof_indexes
        context_lines = list(contract.get("event_context_lines") or [contract["opening_bridge"]])
        for scene_index, line in zip(context_indexes, context_lines):
            if scene_index >= len(repaired["scenes"]):
                break
            repaired["scenes"][scene_index]["voiceover"] = line
            repaired["scenes"][scene_index]["text_overlay"] = line.rstrip("。！？")[:24]
    else:
        owned_indexes = [
            index for index, scene in enumerate(scenes)
            if str(scene.get("scene_role") or "") in {"topic_hook", "topic_context", "owned_proof"}
            and str(scene.get("evidence_type") or "") == "owned_video"
        ]
        locked = (contract["opening_hook"], contract["opening_bridge"])
        for ordinal, scene_index in enumerate(owned_indexes[:2]):
            if scene_index >= len(repaired["scenes"]):
                break
            repaired["scenes"][scene_index]["voiceover"] = locked[ordinal]
            repaired["scenes"][scene_index]["text_overlay"] = locked[ordinal].rstrip("。！？")[:24]
    # Static images are editorial rhythm bridges, not visual proof.  Lock their
    # copy after model generation so a fluent model cannot invent warehouse
    # actions (for example “分拣线在跑”) that the still image does not show.
    image_lines = list(contract.get("image_bridge_lines") or ["先核对主题，再安排对应动作。"])
    image_ordinal = 0
    for scene_index, source_scene in enumerate(scenes):
        if str(source_scene.get("scene_role") or "") != "owned_context_image":
            continue
        if scene_index >= len(repaired["scenes"]):
            break
        copy = image_lines[image_ordinal % len(image_lines)]
        image_ordinal += 1
        repaired["scenes"][scene_index]["voiceover"] = copy
        repaired["scenes"][scene_index]["text_overlay"] = copy.rstrip("。！？")[:24]
    return repaired


def _validate_complete_formal_voiceovers(generated: dict) -> None:
    issues = video_topic_contract.incomplete_sentence_issues(generated)
    if issues:
        raise ValueError("；".join(issues))


_EMPTY_FORMAL_COPY = ("先核对清单", "配送节奏要稳", "库存要对得上")


def _validate_formal_copy_specificity(generated: dict) -> None:
    """Keep a valid-length draft from becoming a sequence of empty slogans."""
    for index, scene in enumerate(generated.get("scenes") or [], 1):
        voiceover = str(scene.get("voiceover") or "").strip()
        if any(phrase in voiceover for phrase in _EMPTY_FORMAL_COPY):
            raise ValueError(f"内容规划模型第 {index} 个分镜使用了脱离画面的空泛短句")
        if "请核对" in voiceover:
            raise ValueError(f"内容规划模型第 {index} 个分镜使用了“请核对”模板句")


_EMPTY_HOOK_TRANSITIONS = ("镜头转到仓内", "先看执行现场", "问题摆在这里")
_HOOK_BRIDGE_TERMS = ("安全", "风险", "影响", "异常", "提醒", "变化", "火情", "火灾", "核对")
# A Hook fact and a Buffalo action are not yet a bridge. The copy must state
# an actual logistics consequence between them; “Buffalo 核对” alone used to
# pass because “核对” was incorrectly treated as an impact word.
_LOGISTICS_IMPACT_TERMS = (
    "延误", "延迟", "中断", "受阻", "拥堵", "排队", "滞留", "绕行",
    "停运", "封路", "波动", "不稳", "风险", "影响", "打乱", "拖慢",
    "节奏", "时效", "通行", "履约", "周转", "交付",
)
_VISIBLE_ACTION_TERMS = (
    # A brand name is not an action.  These terms describe operations that can
    # actually be seen in the reviewed Buffalo footage and therefore allow
    # MiniMax to vary its wording without being rejected by a tiny keyword set.
    "逐件", "逐项", "逐单", "分拣", "核对", "确认", "检查", "查验", "验货",
    "扫描", "扫码", "记录", "留档", "拍照", "称重", "测量", "贴标", "标签",
    "封条", "封箱", "包装", "打包", "隔离", "归位", "分区", "分类", "上架",
    "入库", "出库", "盘点", "搬运", "装卸", "装车", "卸货", "交接", "签收",
    "发运", "排车", "调度", "堆位", "动线", "复核", "点检", "检验",
    "清点", "扫件", "登记", "理货", "分流", "码放", "处理包裹",
)
_VISIBLE_ACTION_FAMILIES = (
    ("逐件", "逐项", "逐单", "扫描", "扫码", "扫件"),
    ("核对", "确认", "检查", "查验", "验货", "复核", "点检", "检验", "清点"),
    ("记录", "留档", "拍照", "留痕", "登记"),
    ("称重", "测量"),
    ("贴标", "标签", "封条", "封箱", "包装", "打包"),
    ("隔离", "归位", "分区", "分类", "上架", "入库", "出库", "盘点", "理货", "分流", "码放"),
    ("分拣", "理货", "分流", "处理包裹"),
    ("搬运", "装卸", "装车", "卸货", "交接", "签收"),
    ("发运", "排车", "调度", "堆位", "动线"),
)
_BRAND_ADVANTAGE_TERMS = (
    "做稳", "做细", "做实", "更稳", "稳定", "安全", "履约", "可靠",
    "可核对", "可追踪", "追踪", "可追溯", "可见", "透明", "留痕", "前置",
    "可控",
    "更清楚", "更可控", "更早发现", "减少差错", "降低风险", "责任清楚",
)
_HOOK_FACT_TERMS = (
    "燃烧", "火光", "火焰", "浓烟", "烟雾", "起火", "火灾", "结构",
    "道路", "公路", "人群", "车辆", "货车", "卡车", "拥堵", "排队", "滞留", "尘土",
    "积雪", "大雪", "暴雨", "洪水", "港口", "码头", "口岸", "边境", "海关", "清关", "查验",
    "集装箱", "堆场", "船舶", "货船", "航行", "直升机", "飞机", "警灯",
    "救援", "救护车", "警员", "担架", "事故", "碰撞",
    "末端", "配送", "派送", "仓储", "仓库", "分拣", "运输", "货运", "物流", "装卸",
)


def _hotspot_risk_lead(voiceover: str) -> str:
    """Turn the verified Hook fact into a short, non-causal marketing lead."""
    text = str(voiceover or "")
    if any(term in text for term in ("燃烧", "火光", "火焰", "浓烟", "烟雾", "起火", "火灾")):
        return "火情提醒风险"
    if any(term in text for term in ("拥堵", "排队", "滞留", "堵车")):
        return "拥堵提醒风险"
    if any(term in text for term in ("侧翻", "事故", "碰撞", "翻车")):
        return "事故提醒风险"
    if any(term in text for term in ("暴雨", "大雨", "洪水", "积水", "降雪", "天气")):
        return "天气提醒风险"
    if any(term in text for term in ("港口", "码头", "口岸", "边境", "清关")):
        return "节点变化风险"
    if any(term in text for term in ("封路", "罢工", "停运", "关闭")):
        return "通行变化风险"
    if any(term in text for term in ("末端", "配送", "派送", "仓储", "分拣", "运输", "货运", "物流")):
        return "物流环节更考验核对"
    return "现场变化更考验核对"


def _fallback_bridge_candidates(voiceover: str, category: str) -> list[str]:
    """Build short evidence-specific bridges for the rare model fallback.

    The sentence varies with the locked Hook and the visible Buffalo scene.
    It avoids the old generic ``风险影响仓配`` wording while still fitting a
    short narration window and satisfying impact/action/advantage grounding.
    """
    lead = _hotspot_risk_lead(voiceover)
    action_endings = {
        "warehouse": ("分拣留痕", "核对更清楚"),
        "staff": ("逐项记录留痕", "协同核对更清楚"),
        "facility": ("分区点检留痕", "逐项检查更可控"),
        "delivery": ("交接留痕", "逐单确认更清楚"),
    }
    endings = action_endings.get(category) or action_endings["warehouse"]
    return [f"{lead}，Buffalo{ending}。" for ending in endings]


def _hook_fact_terms(what_happened: str) -> list[str]:
    return [term for term in _HOOK_FACT_TERMS if term in str(what_happened or "")]


def _selected_hotspot_fact(scenes: list[dict], event: dict | None) -> str:
    """Use the selected atomic segment fact before the parent event summary.

    A parent news video can contain several scenes.  Matching and rendering
    operate on one audited subclip, so spoken copy must follow that subclip,
    not a stale or broader event-level description.
    """
    if scenes:
        selected = str(scenes[0].get("audited_visual_fact") or "").strip()
        if selected:
            return selected
        for reason in scenes[0].get("match_reasons") or []:
            text = str(reason or "").strip()
            if text.startswith("优先现场子片段："):
                selected = text.split("：", 1)[1].strip()
                if selected:
                    return selected
    return str(((event or {}).get("evidence") or {}).get("what_happened") or "").strip()


def _short_retention_hook_fact_line(fact: str) -> str:
    """A stop-worthy factual opener that still fits a 4–6 second Hook clip."""
    text = str(fact or "").strip()
    if any(term in text for term in ("救护车", "救援", "警员", "担架")):
        return "道路有救护车，配送预案备好吗？"
    if any(term in text for term in ("海关", "查验", "清关")):
        return "海关正在说明查验，发货前漏核哪项？"
    if any(term in text for term in ("燃烧", "火光", "火焰", "浓烟", "起火", "火灾")):
        return "火光浓烟已经出现，货运先核对哪步？"
    if any(term in text for term in ("排队", "拥堵", "滞留")) and any(
        term in text for term in ("道路", "公路", "车辆", "货车", "卡车")
    ):
        return "道路车辆排起长队，配送先卡在哪步？"
    if any(term in text for term in ("货车", "卡车", "车辆")) and any(
        term in text for term in ("道路", "公路", "行驶", "通行")
    ):
        return "货车正在道路行驶，交付安排先核对哪一步？"
    if any(term in text for term in ("港口", "码头", "集装箱", "装卸")):
        return "港口作业出现变化，发货安排先核对哪一环？"
    return ""


def _complete_grounded_hook_fact_lines(event: dict) -> list[str]:
    """Return complete, visible-fact openers for the locked logistics scene.

    The model's wording remains first choice. These lines are the bounded
    production fallback when it returns a fragment such as ``...through``.
    They describe only scene classes already proven by immutable Hook facts.
    """
    evidence = event.get("evidence") or {}
    fact_text = " ".join(str(value or "") for value in (
        event.get("title_zh"), event.get("title_en"),
        evidence.get("what_happened"), evidence.get("event_identity"),
    )).casefold()
    scene_keys = _grounded_chat_hook_scene_keys(event)
    lines: list[str] = []

    if "disruption" in scene_keys:
        if any(term in fact_text for term in ("燃烧", "火光", "火焰", "浓烟", "烟雾", "起火", "火灾")):
            lines.extend((
                "画面可见火光和浓烟，现场作业受到影响。",
                "画面可见现场起火并出现浓烟。",
            ))
        if any(term in fact_text for term in ("积雪", "大雪", "暴雪", "洪水", "暴雨")):
            lines.extend((
                "画面可见恶劣天气正在影响道路通行。",
                "画面可见道路积雪，车辆正在缓慢通行。",
            ))
    if "border" in scene_keys or "customs" in scene_keys:
        if any(term in fact_text for term in ("拥堵", "排队", "滞留", "卡车", "货车")):
            lines.extend((
                "画面可见口岸卡车正在排队等待。",
                "画面可见口岸货车持续排队，现场通行已经出现滞留。",
            ))
        lines.extend((
            "画面可见海关人员正在现场查验。",
            "画面可见海关人员正在现场说明查验要求。",
        ))
    if "port" in scene_keys:
        if all(term in fact_text for term in ("集装箱", "货船", "直升机")):
            lines.extend((
                "集装箱货船旁，一架直升机正在协同飞行。",
                "画面可见集装箱货船正在海面航行，附近一架直升机同时飞行，现场呈现清晰的海上航运动态。",
            ))
        lines.extend((
            "画面可见港口车辆与货物正在周转。",
            "画面可见港口车辆与集装箱持续周转。",
            "画面可见港口车辆与集装箱持续周转，现场装卸、堆场和船舶作业正在按各自节点同步推进。",
        ))
    if "road" in scene_keys:
        if "货车" in fact_text and any(term in fact_text for term in ("行驶", "驶过", "通行")):
            lines.append("画面可见一辆货车正在道路上持续行驶。")
        if any(term in fact_text for term in ("拥堵", "排队", "滞留", "停放", "车队")):
            lines.extend((
                "画面可见道路车辆正在排队等待。",
                "画面可见道路车辆持续排队，现场通行已经受到影响。",
            ))
        lines.extend((
            "画面可见货运车辆正在道路上行驶。",
            "画面可见道路货运车辆持续通行。",
        ))
    if "warehouse" in scene_keys:
        if any(term in fact_text for term in ("检查", "查验", "翻找", "核对")):
            lines.extend((
                "仓库内工作人员正在逐件检查货物。",
                "画面可见仓库内工作人员正在逐件翻找并检查货物。",
            ))
        lines.extend((
            "画面可见仓内人员正在分拣包裹。",
            "画面可见仓内人员正在逐件核对并分拣包裹。",
        ))
    if "last_mile" in scene_keys:
        lines.extend((
            "画面可见配送人员正在交接包裹。",
            "画面可见配送人员正在核对收件信息并交接包裹。",
        ))
    return list(dict.fromkeys(lines))


def _compact_logistics_question(question: str) -> str:
    """Keep the audited logistics question short enough for the Hook beat."""
    text = str(question or "").strip()
    if not text:
        return ""
    if "末端配送" in text or "最后三公里" in text:
        return "末端配送要怎么稳住最后三公里的履约？"
    if "仓配" in text or "仓储" in text:
        return "仓配变化时，哪些动作要先核对？"
    if "道路" in text or "路线" in text or "通行" in text:
        return "通行变化时，货运路线要先核对什么？"
    if "港口" in text or "装卸" in text:
        return "港口装卸变化时，要先核对哪些节点？"
    if "边境" in text or "查验" in text or "单证" in text:
        return "边境查验变化时，运输单证要先核对什么？"
    return text.rstrip("。！") + "？" if not text.endswith("？") else text


def _deterministic_hotspot_fact_line(what_happened: str) -> str:
    """Build a short factual opener when the planner omits the Hook fact."""
    text = str(what_happened or "").strip()
    if "卡车" in text and "仓库" in text and any(
        term in text for term in ("停放", "车队", "底盘")
    ):
        return "现场可见多排卡车整齐停放，随后仓库内有人行走交谈。"
    if "卡车" in text and any(term in text for term in ("停放", "车队", "底盘")):
        return "现场可见多排卡车整齐停放，车队规模清晰可见。"
    if any(term in text for term in ("积雪", "雪景", "大雪", "降雪", "暴雪")) and any(
        term in text for term in ("道路", "公路", "车辆", "交通")
    ):
        return "镜头记录车辆在积雪覆盖的公路上持续行驶，交通保持通行但速度需要关注。"
    if any(term in text for term in ("燃烧", "火光", "火焰", "浓烟", "烟雾", "起火", "火灾")):
        return "现场可见火光和浓烟，设施周边出现燃烧风险。"
    if any(term in text for term in ("港口", "码头", "口岸", "边境")):
        if any(term in text for term in ("拥堵", "排队", "滞留", "卡车")):
            return "口岸现场可见卡车排队，通行出现滞留。"
        return "现场可见港口节点的车辆与货物正在周转。"
    if any(term in text for term in ("拥堵", "排队", "滞留")):
        return "现场可见车辆在道路上排队，通行出现滞留。"
    if any(term in text for term in ("道路", "公路", "车辆", "卡车")):
        return "现场可见道路上的车辆持续行驶，通行状态需要关注。"
    return ""


def _repair_formal_narrative_hook(generated: dict, scenes: list[dict], event: dict | None) -> dict:
    """Keep the opening grounded in any verified Hook fact, not a fire-only lexicon."""
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if not event or not scenes or not repaired["scenes"]:
        return repaired
    if scenes[0].get("scene_role") != "hotspot_evidence":
        return repaired
    what_happened = _selected_hotspot_fact(scenes, event)
    logistics_question = str((event.get("evidence") or {}).get("logistics_question") or "").strip()
    terms = _hook_fact_terms(what_happened)
    first_voiceover = str(repaired["scenes"][0].get("voiceover") or "")
    compact_question = _compact_logistics_question(logistics_question)
    maximum = _scene_voiceover_max_chars(scenes[0])
    minimum = _scene_voiceover_min_chars(scenes[0])
    compact_length = len("".join(first_voiceover.split()))
    within_window = (
        (maximum is None or compact_length <= maximum)
        and (minimum is None or compact_length >= minimum)
    )
    question_already_present = bool(
        compact_question and compact_question.rstrip("？") in first_voiceover
    )
    fact_ok = (
        sum(term in first_voiceover for term in terms) >= min(2, len(terms))
        if terms else bool(first_voiceover.strip())
    )
    first_complete = bool(
        first_voiceover.strip()
        and first_voiceover.rstrip()[-1] in "。！？；"
        and not video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": first_voiceover}]}
        )
    )
    # A generic logistics Hook is allowed as a reusable opener, but its audited
    # logistics question must still reach the first spoken beat; otherwise the
    # planner can produce a fluent yet content-free “车在路上跑” sentence.
    if not terms and not compact_question:
        return repaired
    if fact_ok and first_complete and (not compact_question or question_already_present) and within_window:
        return repaired
    short_retention_line = _short_retention_hook_fact_line(what_happened)
    retention_line = hotspot_hook_copy.retention_opening(what_happened, compact_question)
    deterministic_fact_line = _deterministic_hotspot_fact_line(what_happened)
    candidates = [short_retention_line] if short_retention_line else []
    if retention_line:
        candidates.append(retention_line)
    candidates.extend(_complete_grounded_hook_fact_lines(event))
    if deterministic_fact_line and deterministic_fact_line not in candidates:
        candidates.append(deterministic_fact_line)
    if compact_question:
        candidates.append(compact_question)
    candidates.append(what_happened)
    if any(term in what_happened for term in ("拥堵", "排队", "滞留")):
        candidates.append("现场可见人群聚集，车辆出现拥堵。")
    elif any(term in what_happened for term in ("积雪", "大雪", "暴雨", "洪水")):
        candidates.append("现场可见恶劣天气，车辆通行受到影响。")
    elif any(term in what_happened for term in ("燃烧", "火光", "火焰", "浓烟", "烟雾", "起火")):
        candidates.append("现场可见火光和浓烟，风险需要核对。")
    elif any(term in what_happened for term in ("道路", "公路", "车辆", "卡车")):
        candidates.append("道路现场可见车辆活动，通行情况需要核对。")
    else:
        candidates.append("现场事实已核验，先看画面中的具体变化。")
    # Keep the audited nouns in the final fallback.  A generic line such as
    # “道路上的车辆持续行驶” can be fluent but factually wrong for a parked
    # truck fleet, and it also fails the two-term factual gate for mixed scenes
    # such as “卡车 + 仓库”.
    if len(terms) >= 2:
        candidates.append(f"画面先有{terms[0]}，后见{terms[1]}。")
        candidates.append(f"现场画面记录{terms[0]}，并可见{terms[1]}等现场元素。")
    for candidate in candidates:
        compact_length = len("".join(candidate.split()))
        candidate_complete = bool(
            candidate.strip()
            and candidate.rstrip()[-1] in "。！？；"
            and not video_topic_contract.incomplete_sentence_issues(
                {"scenes": [{"voiceover": candidate}]}
            )
        )
        candidate_fact_ok = (
            sum(term in candidate for term in terms) >= min(2, len(terms))
            if terms else bool(candidate.strip())
        )
        if candidate_complete and candidate_fact_ok and (maximum is None or compact_length <= maximum) and (minimum is None or compact_length >= minimum):
            repaired["scenes"][0]["voiceover"] = candidate
            repaired["scenes"][0]["text_overlay"] = candidate.rstrip("。")[:24]
            break
    return repaired


def _validate_formal_narrative(
    generated: dict,
    scenes: list[dict],
    event: dict | None,
    *,
    require_bridge: bool = True,
) -> None:
    """Reject a fluent script that skips the Hook-to-brand narrative bridge."""
    if not event or not any(scene.get("evidence_type") == "hotspot_video" for scene in scenes):
        return
    evidence = event.get("evidence") or {}
    what_happened = _selected_hotspot_fact(scenes, event)
    if not what_happened:
        return
    generated_scenes = list(generated.get("scenes") or [])
    if not generated_scenes:
        raise ValueError("热点成片缺少开场事实分镜")
    first = str(generated_scenes[0].get("voiceover") or "")
    if any(phrase in first for phrase in _EMPTY_HOOK_TRANSITIONS):
        raise ValueError("热点开场不能使用空转场句")
    fact_terms = _hook_fact_terms(what_happened)
    if fact_terms and sum(term in first for term in fact_terms) < min(2, len(fact_terms)):
        raise ValueError("热点开场没有明确说明 Hook 发生了什么")
    if not require_bridge:
        return
    bridge_indices = _owned_bridge_window_indices(scenes)
    if not bridge_indices:
        # Fact-only unit/curation checks intentionally pass a single Hook
        # scene.  They validate the opener, not a complete production plan.
        if not any(str(scene.get("scene_role") or "") == "owned_proof" for scene in scenes):
            return
        raise ValueError("热点后缺少 Buffalo 自有承接镜头")
    bridge_lines = [
        str(generated_scenes[index].get("voiceover") or "")
        for index in bridge_indices
        if index < len(generated_scenes)
    ]
    first_bridge = bridge_lines[0] if bridge_lines else ""
    bridge_text = "".join(bridge_lines)
    if any(phrase in first_bridge for phrase in _EMPTY_HOOK_TRANSITIONS):
        raise ValueError("热点后的第一个自有镜头不能只做无意义转场")
    if "buffalo" not in bridge_text.casefold():
        raise ValueError("热点后的承接段没有点明 Buffalo")
    if not any(term in bridge_text for term in _LOGISTICS_IMPACT_TERMS):
        raise ValueError("热点后的承接段没有说明物流安全承接关系")
    if not any(term in bridge_text for term in _VISIBLE_ACTION_TERMS):
        raise ValueError("热点后的承接段没有落到 Buffalo 可见动作")
    if not any(term in bridge_text for term in _BRAND_ADVANTAGE_TERMS):
        raise ValueError("热点后的承接段没有把可见动作转成 Buffalo 品牌优势")
    _validate_dynamic_brand_cta(generated_scenes, scenes)


def _validate_dynamic_brand_cta(generated_scenes: list[dict], scenes: list[dict]) -> None:
    """Keep the normal CTA model-authored; corpus wording is outage-only fallback."""
    cta_indices = [
        index for index, scene in enumerate(scenes)
        if str(scene.get("scene_role") or "") == "brand_cta"
    ]
    if not cta_indices:
        return
    if len(cta_indices) != 1:
        raise ValueError("正式成片必须且只能包含一个品牌 CTA 镜头")
    index = cta_indices[0]
    if index >= len(generated_scenes):
        raise ValueError("MiniMax 没有生成品牌 CTA 文案")
    item = generated_scenes[index]
    voiceover = str(item.get("voiceover") or "").strip()
    source = str(item.get("copy_source") or "model")
    _validate_dynamic_brand_cta_voiceover(voiceover, source=source)


def _validate_dynamic_brand_cta_voiceover(voiceover: str, *, source: str) -> None:
    """Validate a model-authored CTA without replacing any of its wording."""
    voiceover = str(voiceover or "").strip()
    if not voiceover:
        raise ValueError("MiniMax 品牌 CTA 为空")
    if source == "fallback":
        return
    corpus_lines = {
        str(row.get("voiceover") or "").strip()
        for row in brand_outro_corpus.BRAND_OUTRO_CORPUS
    }
    if voiceover in corpus_lines:
        raise ValueError("MiniMax 品牌 CTA 复用了确定性语料库")
    if "buffalo" not in voiceover.casefold():
        raise ValueError("MiniMax 品牌 CTA 没有点名 Buffalo")
    if not any(term in voiceover for term in _BRAND_ADVANTAGE_TERMS):
        raise ValueError("MiniMax 品牌 CTA 没有落到具体品牌优势")
    if not any(term in voiceover for term in _LOGISTICS_IMPACT_TERMS + _VISIBLE_ACTION_TERMS):
        raise ValueError("MiniMax 品牌 CTA 没有承接本片物流影响或可见动作")


def _stamp_copy_source(
    generated: dict,
    source: str,
    *,
    reason: str = "",
) -> dict:
    """Attach auditable copy provenance without changing model wording."""
    stamped = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    for item in stamped["scenes"]:
        item["copy_source"] = source
        if reason:
            item["copy_repair_reason"] = reason
        else:
            item.pop("copy_repair_reason", None)
    return stamped


def _annotate_copy_revisions(before: dict, after: dict, *, reason: str) -> dict:
    """Mark only locally changed lines as repairs; keep fallback identity intact."""
    annotated = {**after, "scenes": [dict(item) for item in after.get("scenes") or []]}
    before_scenes = list(before.get("scenes") or [])
    for index, item in enumerate(annotated["scenes"]):
        prior = before_scenes[index] if index < len(before_scenes) else {}
        source = str(prior.get("copy_source") or item.get("copy_source") or "model")
        changed = any(
            str(item.get(field) or "") != str(prior.get(field) or "")
            for field in ("voiceover", "text_overlay")
        )
        if changed and source != "fallback":
            source = "policy_repair"
            previous_reason = str(prior.get("copy_repair_reason") or "").strip()
            item["copy_repair_reason"] = "；".join(
                part for part in (previous_reason, reason) if part
            )
        elif prior.get("copy_repair_reason"):
            item["copy_repair_reason"] = prior["copy_repair_reason"]
        item["copy_source"] = source
    return annotated


def _copy_provenance_rows(scenes: list[dict]) -> list[dict]:
    return [
        {
            "scene": index,
            "scene_role": str(scene.get("scene_role") or scene.get("evidence_type") or ""),
            "source": str(scene.get("copy_source") or "fallback"),
            "reason": str(scene.get("copy_repair_reason") or ""),
            "voiceover": str(scene.get("voiceover") or ""),
        }
        for index, scene in enumerate(scenes, 1)
    ]


def _rotate_copy_candidates(candidates: list[str], seed: str) -> list[str]:
    """Use stable diversity for the rare offline fallback, not one global slogan."""
    if len(candidates) < 2:
        return candidates
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    offset = int.from_bytes(digest[:4], "big") % len(candidates)
    return candidates[offset:] + candidates[:offset]


def _repair_formal_narrative_bridge(
    generated: dict,
    scenes: list[dict],
    *,
    hook_binding_mode: str = "exact",
) -> dict:
    """Build the first bridge only for the explicit offline fallback path.

    Normal model and model-repair output must pass ``_validate_formal_narrative``
    unchanged. This helper is intentionally reserved for a failed remote model
    chain and rotates scene-specific candidates so one slogan cannot fill every
    video.
    """
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    first_voiceover = str(repaired["scenes"][0].get("voiceover") or "") if repaired["scenes"] else ""
    contrast_lead = _hotspot_risk_lead(first_voiceover)
    exact_candidates = {
        "warehouse": [
            "风险影响仓储，Buffalo分拣可核对。",
            "风险影响仓配，Buffalo分拣更可核对。",
            "这类风险会放大仓内误差，Buffalo逐件核对，让异常更早留痕。",
            "外部变化影响仓配节奏，Buffalo逐件分拣，让处理更可核对。",
            "现场风险提醒仓内先核对，Buffalo让每件货的状态更清楚。",
            "物流波动传到仓内，Buffalo分区核对，让异常处理更可控。",
        ],
        "staff": [
            "风险影响协同，Buffalo分拣可核对。",
            "风险影响协同，Buffalo分拣更可核对。",
            "这类变化考验现场协同，Buffalo逐项分拣，让动作更可核对。",
            "外部风险影响作业节奏，Buffalo协同核对，让异常及时留痕。",
            "现场变化越突然，Buffalo越要分工核对，让处理更可控。",
            "物流风险传到作业端，Buffalo逐项确认，让责任更清楚。",
        ],
        "facility": [
            "风险影响作业，Buffalo分区可核对。",
            "风险影响作业，Buffalo分区核对更可控。",
            "这类风险会传到设施作业，Buffalo分区核对，让异常更早留痕。",
            "外部变化影响设备衔接，Buffalo逐项核对，让流程更可控。",
            "现场风险提醒设施先检查，Buffalo把关键动作留痕。",
            "物流波动传到作业区，Buffalo分区确认，让状态更清楚。",
        ],
        "delivery": [
            "风险影响配送，Buffalo交接可核对。",
            "风险影响配送，Buffalo交接更可核对。",
            "这类变化会影响末端交接，Buffalo出车前核对，让过程更可控。",
            "外部风险传到配送端，Buffalo逐项交接，让异常更早留痕。",
            "道路变化影响配送节奏，Buffalo核对交接，让状态更清楚。",
            "物流波动来到末端，Buffalo逐单确认，让交接更可核对。",
        ],
    }
    industry_candidates = {
        "warehouse": [
            "风险传到仓内，Buffalo分拣可核对。",
            "现场只作提醒，Buffalo分拣更可核对。",
            "行业现场只作提醒，Buffalo逐件核对，让异常更早留痕。",
        ],
        "staff": [
            "风险传到现场，Buffalo分拣可核对。",
            "现场只作提醒，Buffalo协同更可核对。",
            "行业现场只作提醒，Buffalo分工核对，让处理更可控。",
        ],
        "facility": [
            "风险传到作业，Buffalo分区可核对。",
            "现场只作提醒，Buffalo分区核对更可控。",
            "行业现场只作提醒，Buffalo检查设施，让动作可留痕。",
        ],
        "delivery": [
            "风险传到末端，Buffalo交接可核对。",
            "现场只作提醒，Buffalo交接更可核对。",
            "行业现场只作提醒，Buffalo逐项交接，让状态更清楚。",
        ],
    }
    hotspot_seen = False
    bridge_checked = False
    for index, scene in enumerate(scenes):
        if index >= len(repaired["scenes"]):
            break
        if scene.get("scene_role") == "hotspot_evidence":
            hotspot_seen = True
            continue
        if not hotspot_seen or bridge_checked or scene.get("scene_role") != "owned_proof":
            continue
        bridge_checked = True
        voiceover = str(repaired["scenes"][index].get("voiceover") or "")
        valid_relation = any(term in voiceover for term in _LOGISTICS_IMPACT_TERMS)
        valid_action = any(term in voiceover for term in _VISIBLE_ACTION_TERMS)
        valid_advantage = any(term in voiceover for term in _BRAND_ADVANTAGE_TERMS)
        empty_transition = any(phrase in voiceover for phrase in _EMPTY_HOOK_TRANSITIONS)
        if not (empty_transition or not valid_relation or not valid_action or not valid_advantage):
            continue
        category = str(scene.get("primary_category") or "warehouse").casefold()
        pool = exact_candidates if hook_binding_mode == "exact" else industry_candidates
        dynamic_candidates = _rotate_copy_candidates(
            _fallback_bridge_candidates(first_voiceover, category),
            f"{first_voiceover}|{category}|{hook_binding_mode}|dynamic",
        )
        candidates = [*dynamic_candidates, *(pool.get(category) or pool["warehouse"])]
        candidates.extend([
            f"{contrast_lead}，Buffalo逐项核对，让异常更早留痕。",
            f"{contrast_lead}，Buffalo把现场动作做到可核对。",
        ])
        maximum = _scene_voiceover_max_chars(scene)
        minimum = _scene_voiceover_min_chars(scene)
        replacement = next(
            (
                candidate for candidate in candidates
                if (maximum is None or len("".join(candidate.split())) <= maximum)
                and (minimum is None or len("".join(candidate.split())) >= minimum)
                and any(term in candidate for term in _LOGISTICS_IMPACT_TERMS)
                and any(term in candidate for term in _VISIBLE_ACTION_TERMS)
                and any(term in candidate for term in _BRAND_ADVANTAGE_TERMS)
            ),
            None,
        )
        if replacement is None:
            raise ValueError(
                "确定性承接文案无法满足当前镜头字数窗口："
                f"minimum={minimum}, maximum={maximum}, category={category}"
            )
        repaired["scenes"][index]["voiceover"] = replacement
        repaired["scenes"][index]["text_overlay"] = replacement.rstrip("。")[:24]
        repaired["scenes"][index]["copy_source"] = "fallback"
        repaired["scenes"][index]["copy_repair_reason"] = "remote_model_chain_unavailable"
    return repaired


def _deterministic_formal_script(
    brief: dict,
    scenes: list[dict],
    event: dict | None,
    *,
    hook_binding_mode: str = "exact",
    fallback_reason: str = "remote_model_chain_unavailable",
) -> dict:
    """Build an evidence-bounded script when the remote planner is invalid.

    The model can improve wording, but it must never be a production
    availability dependency. This fallback uses only the locked Hook fact, the
    user's topic contract and reviewed source-scene anchors.
    """
    topic = str(
        brief.get("raw_input") or brief.get("requested_topic")
        or brief.get("subject") or brief.get("logistics_topic") or "南非物流"
    ).strip()
    contract = video_topic_contract.build_topic_contract(topic, has_event_anchor=bool(event))
    evidence = (event or {}).get("evidence") or {}
    fact = str(evidence.get("what_happened") or "").strip()
    fact_question = str(evidence.get("logistics_question") or "").strip()
    fact_line = hotspot_hook_copy.retention_opening(fact, fact_question)
    if not fact_line:
        fact_line = _deterministic_hotspot_fact_line(fact)
    visible_lines = {
        "warehouse": (
            "仓内人员正在逐件核对包裹。", "货物进入仓内后依次核对分拣。",
            "分拣现场按顺序处理每件货物。",
            "工作人员在仓内逐件核对包裹并记录结果。",
            "仓内人员按顺序核对货物并同步记录异常。",
        ),
        "staff": (
            "工作人员正在现场协同作业。", "现场人员按流程核对作业。",
            "现场人员按流程核对货物并同步做好记录。",
            "工作人员协同核对包裹并逐项确认动作。",
        ),
        "facility": (
            "仓内设备按区域配合作业。", "设施现场保持分区作业。",
            "仓内设备按区域配合作业并逐项核对货物。",
            "设施现场按分区完成准备并同步记录动作。",
        ),
        "delivery": (
            "配送车辆正在按动线作业。", "车辆出发前按流程核对交接。",
            "配送车辆出发前核对交接信息再按动线作业。",
            "车辆按现场动线完成交接准备再进入配送。",
        ),
    }
    image_lines = (
        "先回到当前物流主题。", "再看 Buffalo 的可见准备。",
        "画面转入仓配准备环节。", "外部变化只作行业提醒。",
        "真正要核对的是每个动作。", "继续看仓内的执行细节。",
        "流程是否稳定，要回到现场。", "这里只展示可见的仓配动作。",
        "主题结论要由可见动作承接。", "最后把核对落到每个环节。",
    )
    topic_lines_by_intent = {
            "local_courier_comparison": (
                "取件、分拣和末端交接都要用同一口径对比。",
                "现场分拣动作可见，再看包裹如何进入末端。",
                "同路线再比较交接记录，结果才有可比性。",
            ),
        "same_city_delivery_sla": (
            "接单、分拣、出车和签收都要统一计时。",
            "分拣和交接用时也要算进同城配送全程。",
            "出车只是中段，签收时间才完成全程记录。",
            "异常节点单独留痕，才能比较真实时效。",
        ),
        "peak_overflow_response": (
            "先核对库位和分拣顺序，再处理旺季增量。",
            "库位、分拣和交接失序，配送就会跟着变慢。",
            "爆仓预案要写明触发点、分工和处理顺序。",
        ),
        "peak_full_cycle_review": (
            "入库、分拣、出车和交付要逐段复盘。",
            "每个节点都要回看准备记录和异常处理。",
            "全流程复盘要把问题落到具体物流动作。",
        ),
        "policy_change_verification": (
            "先核对官方原文、适用对象和生效日期。",
            "来源和日期未核实，不能当成政策更新发布。",
            "清关准备要跟随已确认的官方口径调整。",
        ),
    }
    topic_lines = topic_lines_by_intent.get(
        str(contract.get("intent") or ""),
        (
            str(contract.get("opening_bridge") or "").strip(),
            f"围绕{topic[:12]}，逐项核对可见物流动作。",
        ),
    )
    topic_lines = tuple(line for line in topic_lines if line)

    def bounded(candidates: list[str], scene: dict, *, seed: str) -> str:
        minimum = _scene_voiceover_min_chars(scene)
        maximum = _scene_voiceover_max_chars(scene)
        for candidate in _rotate_copy_candidates(candidates, seed):
            candidate = str(candidate or "").strip()
            length = len("".join(candidate.split()))
            if (
                candidate and candidate[-1] in "。！？；"
                and (minimum is None or length >= minimum)
                and (maximum is None or length <= maximum)
            ):
                return candidate
        fallbacks = (
            "现场动作正在按流程逐项展开。",
            "画面中的物流动作正在按流程展开。",
            "画面中的物流作业动作正在现场逐项展开。",
            "现场动作逐项核对。",
            "仓内核对正在进行。",
            "逐项核对并记录。",
        )
        for candidate in fallbacks:
            length = len("".join(candidate.split()))
            if (minimum is None or length >= minimum) and (maximum is None or length <= maximum):
                return candidate
        # The downstream validator will report the impossible measured window.
        return fallbacks[-1]

    generated_scenes: list[dict] = []
    owned_ordinal = 0
    image_ordinal = 0
    bridge_done = False
    for scene in scenes:
        role = str(scene.get("scene_role") or "")
        category = str(scene.get("primary_category") or "").casefold()
        if role == "hotspot_evidence":
            candidates = [fact_line, str(scene.get("voiceover") or ""), "外部物流现场正在发生变化。"]
        elif role == "brand_cta":
            # The endcard already carries a scene-matched corpus line.  It is
            # used only in this explicit remote-interface fallback branch;
            # the normal path asks MiniMax to author the CTA with the rest of
            # the video's factual and marketing context.
            candidates = [
                brand_outro_corpus.select_brand_outro(brief)["voiceover"],
                str(scene.get("voiceover") or ""),
            ]
        elif role == "owned_proof" and not bridge_done:
            bridge_done = True
            category_bridges = {
                "warehouse": [
                    "风险影响仓储，Buffalo分拣可核对。",
                    "风险影响仓配，Buffalo分拣更可核对。",
                    "这类风险会放大仓内误差，Buffalo逐件核对，让异常更早留痕。",
                    "外部变化影响仓配节奏，Buffalo逐件分拣，让处理更可核对。",
                    "现场风险提醒仓内先核对，Buffalo让每件货的状态更清楚。",
                ],
                "staff": [
                    "风险影响协同，Buffalo分拣可核对。",
                    "风险影响协同，Buffalo分拣更可核对。",
                    "这类变化考验现场协同，Buffalo逐项分拣，让动作更可核对。",
                    "外部风险影响作业节奏，Buffalo协同核对，让异常及时留痕。",
                    "现场变化越突然，Buffalo越要分工核对，让处理更可控。",
                ],
                "facility": [
                    "风险影响作业，Buffalo分区可核对。",
                    "风险影响作业，Buffalo分区核对更可控。",
                    "这类风险会传到设施作业，Buffalo分区核对，让异常更早留痕。",
                    "外部变化影响设备衔接，Buffalo逐项核对，让流程更可控。",
                    "现场风险提醒设施先检查，Buffalo把关键动作留痕。",
                ],
                "delivery": [
                    "风险影响配送，Buffalo交接可核对。",
                    "风险影响配送，Buffalo交接更可核对。",
                    "这类变化会影响末端交接，Buffalo出车前核对，让过程更可控。",
                    "外部风险传到配送端，Buffalo逐项交接，让异常更早留痕。",
                    "道路变化影响配送节奏，Buffalo核对交接，让状态更清楚。",
                ],
            }
            candidates = [
                *_fallback_bridge_candidates(fact_line, category),
                *(category_bridges.get(category) or category_bridges["warehouse"]),
            ]
        elif role == "owned_context_image":
            candidates = [
                topic_lines[image_ordinal % len(topic_lines)],
                image_lines[image_ordinal % len(image_lines)],
                str(scene.get("voiceover") or ""),
            ]
            image_ordinal += 1
        else:
            category_lines = visible_lines.get(category, ("Buffalo把现场动作做到可核对。",))
            candidates = [
                str(scene.get("copy_anchor") or ""),
                topic_lines[owned_ordinal % len(topic_lines)],
                category_lines[owned_ordinal % len(category_lines)],
                str(scene.get("voiceover") or ""),
            ]
            owned_ordinal += 1
        copy = bounded(candidates, scene, seed=f"{topic}|{role}|{category}|{len(generated_scenes)}")
        scene_fallback_reason = fallback_reason
        if role == "brand_cta":
            scene_fallback_reason += f";brand_endcard_corpus:{scene.get('outro_id') or 'selected'}"
        generated_scenes.append({
            "voiceover": copy,
            "text_overlay": copy.rstrip("。！？；")[:24],
            "copy_source": "fallback",
            "copy_repair_reason": scene_fallback_reason,
        })
    return {
        "title": contract.get("safe_title") or topic,
        "angle": contract.get("safe_angle") or "从真实外部现场回到 Buffalo 可见仓配动作。",
        "scenes": generated_scenes,
    }


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
        if str(item.get("authorization_status") or "authorized") == "blocked":
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
    # The renderer preserves the planned duration for formal videos.  At the
    # previous ~2 chars/s floor a six-second beat frequently contained only
    # three seconds of speech followed by a conspicuous silent tail.  Keep a
    # narrow but natural 2.8–3.6 chars/s window so narration covers the visual
    # beat without forcing the renderer to loop speech or stretch the clip.
    if not duration_seconds:
        return None
    # Very short Hook clips need a slightly wider wording window so two
    # audited scene facts can still fit. Longer formal beats keep the stronger
    # floor that avoids multi-second silent tails.
    rate = 2.6 if duration_seconds <= 4.5 else 2.8
    return max(8, int(math.ceil(duration_seconds * rate)))


_UNSUPPORTED_HOTSPOT_INTENSIFIERS = ("堵死", "全面瘫痪", "完全停摆", "全线停摆")
_UNSUPPORTED_HOTSPOT_VIEWER_ROUTE_PROMPTS = ("这条线", "这一批", "走到哪段路线", "在不在这一批")
_UNSUPPORTED_OWNED_ROUTE_CLAIMS = (
    "记录同步给末端配送",
    "同步给末端配送",
    "末端配送，",
    "派送前再次核对",
)
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

    def visible_anchor(scene: dict) -> str:
        anchor = str(scene.get("copy_anchor") or "镜头中的仓内作业。")
        minimum = _scene_voiceover_min_chars(scene)
        maximum = _scene_voiceover_max_chars(scene)
        candidates = (
            anchor,
            "工作人员正在仓内逐件核对包裹并记录结果。",
            "仓内人员按顺序核对货物并同步记录异常。",
            "工作人员正在仓内逐件核对包裹。",
            "工作人员正在逐件核对每件包裹。",
            "仓内工作人员核对包裹。",
            "工作人员核对包裹。",
        )
        for candidate in candidates:
            length = len("".join(candidate.split()))
            if (minimum is None or length >= minimum) and (maximum is None or length <= maximum):
                return candidate
        return anchor

    for item, scene in zip(repaired["scenes"], scenes):
        role = str(scene.get("scene_role") or "")
        voiceover = str(item.get("voiceover") or "").strip()
        if role == "owned_proof" and _OCR_TOKEN_RE.search(voiceover):
            # The planner is already constrained to this reviewed action. Do
            # not replace SOP-aware copy with a catalog label: that turned
            # every video into the same warehouse narration.
            anchor = visible_anchor(scene)
            item["voiceover"] = anchor
            item["text_overlay"] = anchor.rstrip("。")[:24]
        elif (
            role == "owned_proof"
            and str(scene.get("primary_category") or "").casefold() in {"warehouse", "staff", "facility"}
            and any(phrase in voiceover for phrase in _UNSUPPORTED_OWNED_ROUTE_CLAIMS)
        ):
            # A warehouse/staff/facility frame cannot prove that a parcel was
            # handed to last-mile delivery. Keep the topic's safety context,
            # but make the line describe only the reviewed visible action.
            anchor = visible_anchor(scene)
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
    if not title or not angle or not isinstance(scenes, list) or len(scenes) < expected_scenes:
        raise ValueError("内容规划模型缺少标题、角度或有效分镜")
    scenes = scenes[:expected_scenes]
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
        normalized_scene = {"voiceover": voiceover, "text_overlay": overlay or voiceover[:24]}
        copy_source = str(item.get("copy_source") or "").strip()
        if copy_source in {"model", "repair", "policy_repair", "fallback", "corpus"}:
            normalized_scene["copy_source"] = copy_source
        copy_repair_reason = str(item.get("copy_repair_reason") or "").strip()
        if copy_repair_reason:
            normalized_scene["copy_repair_reason"] = copy_repair_reason[:240]
        normalized.append(normalized_scene)
    return {"title": title, "angle": angle, "scenes": normalized}


def _planner_repair_payload(context: dict, validation_error: Exception, invalid_draft: str) -> dict:
    """Give a repair call the topic facts and an exact-length JSON skeleton.

    A one-scene example is ambiguous when the formal plan has eight or more
    approved beats.  It also cannot reconstruct a missing title/angle unless
    the original topic contract is repeated.  Keep the evidence plan fixed,
    but make every required output slot explicit.
    """
    allowed_scenes = list(context.get("allowed_scenes") or [])
    scene_template = [
        {
            "scene": item.get("scene"),
            "role": item.get("role"),
            "voiceover": "",
            "text_overlay": "",
            "voiceover_min_chars": item.get("voiceover_min_chars"),
            "voiceover_max_chars": item.get("voiceover_max_chars"),
        }
        for item in allowed_scenes
    ]
    return {
        "validation_error": str(validation_error),
        "invalid_draft": str(invalid_draft or ""),
        "brief": context.get("brief") or {},
        "facts": context.get("facts") or [],
        "narrative_contract": context.get("narrative_contract") or {},
        "topic_requirements": context.get("topic_requirements") or {},
        "allowed_scenes": allowed_scenes,
        "required_json": {"title": "", "angle": "", "scenes": scene_template},
    }


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


def _repair_short_formal_voiceovers(
    generated: dict,
    scenes: list[dict],
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
    event: dict | None = None,
) -> dict:
    """Repair short beats from reviewed visual anchors before calling the model again.

    A short model sentence is a local pacing problem, not a reason to spend two
    more model calls.  Candidates come only from the audited Hook fact or the
    scene's reviewed ``copy_anchor``; if no bounded candidate exists, retain the
    strict error instead of inventing a claim.
    """
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if len(repaired["scenes"]) != len(scenes):
        raise ValueError("内容规划模型返回的分镜数量无法进行旁白时长修复")
    hotspot_evidence = (event or {}).get("evidence") or {}
    hotspot_fact = str(hotspot_evidence.get("what_happened") or "").strip()
    hotspot_question = str(hotspot_evidence.get("logistics_question") or "").strip()
    hotspot_fact_line = hotspot_hook_copy.retention_opening(hotspot_fact, hotspot_question)
    legacy_hotspot_fact_line = _deterministic_hotspot_fact_line(hotspot_fact)
    if not hotspot_fact_line:
        hotspot_fact_line = legacy_hotspot_fact_line
    for index, (item, scene) in enumerate(zip(repaired["scenes"], scenes)):
        minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        voiceover = str(item.get("voiceover") or "").strip()
        compact_length = len("".join(voiceover.split()))
        if minimum is None or compact_length >= minimum:
            continue
        candidates: list[str] = []
        # Late topic locks deliberately replace several owned beats with short,
        # topic-specific editorial lines.  Preserve that topic wording and
        # extend it with evidence-neutral, visible-action language before
        # considering category fallbacks.  The old implementation skipped the
        # current line entirely, so a perfectly feasible 14–18 character
        # window could fail merely because every hand-written fallback was
        # either shorter than 14 or longer than 18.
        current_stem = voiceover.rstrip("。！？；，、")
        current_candidates = (
                current_stem + "，再核对。",
                current_stem + "，逐项核对。",
                current_stem + "，现场核对。",
                current_stem + "，动作可核对。",
                current_stem + "，并记录结果。",
                current_stem + "，再核对现场动作。",
                current_stem + "，相关动作逐项核对。",
        ) if current_stem else ()
        if scene.get("scene_role") == "hotspot_evidence" and hotspot_fact_line:
            candidates.extend((
                hotspot_fact_line,
                hotspot_fact_line.rstrip("。！？；") + "。这段外部现场只作行业提醒。",
            ))
            if legacy_hotspot_fact_line and legacy_hotspot_fact_line != hotspot_fact_line:
                candidates.append(legacy_hotspot_fact_line)
        copy_anchor = str(scene.get("copy_anchor") or "").strip()
        if copy_anchor:
            candidates.extend((
                copy_anchor,
                copy_anchor.rstrip("。！？；") + "，画面中的动作正在现场展开。",
            ))
        # Reviewed scene copy remains the first choice for ordinary scenes.
        # Topic-lock extensions are considered immediately afterwards so a
        # narrow cloud narration window can preserve the immutable topic
        # instead of failing or falling back to unrelated generic wording.
        candidates.extend(current_candidates)
        category = str(scene.get("primary_category") or "").casefold()
        candidates.extend({
            "warehouse": [
                "仓内人员逐件核对包裹并记录。",
                "仓内人员按顺序核对货物并记录。",
                "工作人员正在仓内逐件核对包裹并同步做好记录。",
                "仓内人员按顺序核对货物，把异常留在发出前发现。",
            ],
            "staff": [
                "现场人员逐项核对货物并记录。",
                "工作人员按流程核对货物并记录。",
                "工作人员按流程核对现场货物并同步做好记录。",
                "现场人员协同核对包裹，把每一步动作逐项确认。",
            ],
            "facility": [
                "仓内设备按区域作业并记录动作。",
                "现场设备分区作业并核对货物。",
                "仓内设备按区域配合作业，现场动作正在逐项展开。",
                "设施现场按分区完成作业准备并逐项核对货物。",
            ],
            "delivery": [
                "车辆出发前逐项核对交接信息。",
                "配送车辆按动线交接并记录结果。",
                "配送车辆出发前核对交接信息，再按既定动线作业。",
                "车辆按现场动线完成交接准备，再进入配送环节。",
            ],
        }.get(category, [
            "当前画面只记录可见物流动作。",
            "这张图片只作主题过渡并提醒核对。",
            "画面正在说明当前物流环节，相关动作需要逐项看清。",
            "先看清画面中的物流动作，再回到当前主题逐项核对。",
        ]))
        candidates.extend((
            "逐项核对并记录。",
            "现场动作逐项核对。",
            "仓内核对正在进行。",
            "Buffalo逐项核对并记录。",
        ))
        replacement = next(
            (
                candidate for candidate in candidates
                if (maximum is None or len("".join(candidate.split())) <= maximum)
                and len("".join(candidate.split())) >= minimum
            ),
            None,
        )
        if replacement is None:
            raise ValueError(f"内容规划模型第 {index + 1} 个分镜旁白少于 {minimum} 字时长下限")
        item["voiceover"] = replacement
        item["text_overlay"] = replacement.rstrip("。")[:24]
    return repaired


def _coerce_fallback_voiceovers_into_window(
    generated: dict,
    scenes: list[dict],
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
) -> dict:
    """Last-resort length pad for the offline evidence script only.

    Formal beats have a measured 8-character floor.  If earlier repairs still
    leave a line shorter than that window, keep a complete visible-action
    sentence instead of failing the user's production job.
    """
    filler = "逐项核对并记录。"
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    for index, (item, scene) in enumerate(zip(repaired["scenes"], scenes)):
        if str(scene.get("evidence_type") or "") == "brand_endcard":
            continue
        minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        voiceover = str(item.get("voiceover") or "").strip()
        compact_length = len("".join(voiceover.split()))
        if voiceover and (minimum is None or compact_length >= minimum) and (
            maximum is None or compact_length <= maximum
        ):
            continue
        candidate = filler
        compact_length = len("".join(candidate.split()))
        while minimum is not None and compact_length < minimum:
            next_candidate = candidate.rstrip("。") + "，" + filler
            next_length = len("".join(next_candidate.split()))
            if maximum is not None and next_length > maximum:
                break
            candidate = next_candidate
            compact_length = next_length
        if maximum is not None and compact_length > maximum:
            clipped = "".join(candidate.split())[:maximum]
            candidate = clipped.rstrip("，、；") + "。"
            compact_length = len("".join(candidate.split()))
        if minimum is not None and compact_length < minimum:
            continue
        item["voiceover"] = candidate
        item["text_overlay"] = candidate.rstrip("。")[:24]
    return repaired


def _repair_dangling_formal_voiceovers(
    generated: dict,
    scenes: list[dict],
    voiceover_minimums: list[int | None] | None = None,
    voiceover_limits: list[int | None] | None = None,
) -> dict:
    """Replace incomplete model endings with already-reviewed scene copy.

    A narration can be inside its measured character window and still end in a
    connector such as ``通过`` or ``如果``.  That is a copy-shape failure, not
    a reason to relax the completeness gate.  Use only the reviewed source
    narration/copy anchor or a category-safe visible-action sentence; never
    append a new claim to the model's unfinished thought.
    """
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if len(repaired["scenes"]) != len(scenes):
        raise ValueError("内容规划模型返回的分镜数量无法进行悬空旁白修复")

    # A dangling sentence still has to fit the measured narration window.
    # The old one-line fallbacks were sometimes shorter than a 6–7s scene,
    # so the repair itself raised and the whole project failed. These are
    # reviewed visible-action statements, not new service claims.
    safe_by_category = {
        "warehouse": (
            "逐项核对并记录。",
            "仓内人员按区域逐件核对包裹。",
            "分拣台前工作人员按路线逐件核对包裹。",
            "现场人员按区域逐件核对包裹并完成分拣准备。",
            "仓内人员按区域逐件核对包裹并同步记录处理结果。",
            "仓内工作人员按顺序核对货物并逐项记录分拣结果。",
        ),
        "staff": (
            "逐项核对并记录。",
            "工作人员按流程逐项核对现场包裹。",
            "现场人员协同核对包裹并完成作业准备。",
            "现场工作人员协同核对货物并逐项记录当前作业结果。",
        ),
        "facility": (
            "逐项核对并记录。",
            "仓内设备按区域配合包裹分拣作业。",
            "设施现场按分区完成作业准备。",
            "仓内设备按区域配合作业并逐项记录可见处理动作。",
        ),
        "delivery": (
            "逐项核对并记录。",
            "配送车辆按既定动线完成发运准备。",
            "车辆出发前按流程核对交接信息。",
            "配送车辆按现场动线完成交接准备并逐项记录结果。",
            "车辆出发前按流程核对货物与交接信息并记录结果。",
        ),
        "road": (
            "画面可见道路车辆正在排队等待。",
            "画面可见道路货运车辆持续通行。",
            "画面可见道路车辆持续排队，现场通行已经受到影响。",
        ),
        "border": (
            "画面可见口岸卡车正在排队等待。",
            "画面可见口岸货车持续排队，现场通行已经出现滞留。",
        ),
        "customs": (
            "画面可见海关人员正在现场查验。",
            "画面可见海关人员正在现场说明查验要求。",
        ),
        "port": (
            "画面可见港口车辆与货物正在周转。",
            "画面可见港口车辆与集装箱持续周转。",
        ),
        "disruption": (
            "画面可见火光和浓烟，现场作业受到影响。",
            "画面可见恶劣天气正在影响道路通行。",
        ),
    }

    def _complete(candidate: str, minimum: int | None, maximum: int | None) -> bool:
        candidate = "".join(str(candidate or "").split())
        if not candidate or candidate[-1] not in "。！？；":
            return False
        if video_topic_contract.incomplete_sentence_issues({"scenes": [{"voiceover": candidate}]}):
            return False
        length = len(candidate)
        return (minimum is None or length >= minimum) and (maximum is None or length <= maximum)

    for index, (item, source_scene) in enumerate(zip(repaired["scenes"], scenes)):
        current = str(item.get("voiceover") or "").strip()
        if not video_topic_contract.incomplete_sentence_issues({"scenes": [{"voiceover": current}]}):
            continue
        minimum = voiceover_minimums[index] if voiceover_minimums and index < len(voiceover_minimums) else None
        maximum = voiceover_limits[index] if voiceover_limits and index < len(voiceover_limits) else None
        category = str(source_scene.get("primary_category") or "").casefold()
        candidates = [
            str(source_scene.get("voiceover") or "").strip(),
            str(source_scene.get("copy_anchor") or "").strip(),
            *safe_by_category.get(category, ("镜头中的物流作业动作清晰可见。",)),
        ]
        replacement = next((candidate for candidate in candidates if _complete(candidate, minimum, maximum)), None)
        if replacement is None:
            raise ValueError(f"内容规划模型第 {index + 1} 个分镜以悬空连接词结尾")
        item["voiceover"] = replacement
        item["text_overlay"] = replacement.rstrip("。！？；")[:24]
    return repaired


def _formal_voiceover_key(value: object) -> str:
    """Normalize a narration line for the formal anti-template gate."""
    return "".join(char for char in str(value or "") if char.isalnum())


def _repair_repeated_formal_voiceovers(
    generated: dict,
    scenes: list[dict],
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
) -> dict:
    """Give repeated model lines distinct, reviewed, visible-action wording.

    The model is allowed to describe several warehouse clips, but it cannot
    reuse one template sentence for all of them. Candidates stay within the
    scene's measured character window and describe only the already reviewed
    category/action; this is a copy repair, not a new service claim.
    """
    repaired = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if len(repaired["scenes"]) != len(scenes):
        raise ValueError("内容规划模型返回的分镜数量无法进行旁白去重")

    category_candidates = {
        "warehouse": (
            "工作人员在仓内逐件核对包裹。",
            "仓内工作人员按顺序核对货物。",
            "货物进入仓内后依次核对分拣。",
            "仓内作业按顺序完成分拣准备。",
            "现场人员按区域逐件核对包裹。",
            "分拣现场按顺序处理每件货物。",
            "每件货物在仓内完成核对分拣。",
            "仓内货物依次完成分拣准备。",
            "分拣人员围绕货物逐项核对。",
            "货物按区域摆放后继续核对。",
            "工作人员在仓内逐件核对包裹并记录结果。",
            "仓内人员按顺序核对货物并同步记录异常。",
            "货物进入仓内后逐件核对并完成分拣记录。",
            "分拣现场按顺序处理货物并留下核对记录。",
            "每件货物在仓内完成核对并确认分拣结果。",
            "仓内货物依次完成分拣准备并标记异常。",
            "现场人员按区域逐件核对包裹并同步结果。",
        ),
        "staff": (
            "工作人员正在现场协同作业。",
            "现场人员按流程核对作业。",
            "工作人员逐项确认现场动作。",
            "现场协同围绕货物逐步展开。",
            "现场人员按流程核对货物并同步做好记录。",
            "工作人员协同核对包裹并逐项确认动作。",
            "人员在现场分工处理货物并核对作业结果。",
            "现场协同围绕货物展开并逐项留下记录。",
        ),
        "facility": (
            "仓内设备按区域配合作业。",
            "设施现场保持分区作业。",
            "设备动作围绕货物逐项展开。",
            "现场设备配合分拣准备。",
            "仓内设备按区域配合作业并逐项核对货物。",
            "设施现场按分区完成准备并同步记录动作。",
            "设备作业围绕货物逐项展开并确认处理结果。",
            "现场设备配合分拣准备并留下作业记录。",
        ),
        "delivery": (
            "配送车辆正在按动线作业。",
            "车辆出发前先按流程核对。",
            "配送交接按现场动线展开。",
            "车辆作业围绕交接逐步展开。",
            "配送车辆出发前核对交接信息再按动线作业。",
            "车辆按现场动线完成交接准备再进入配送。",
            "配送交接按流程逐项确认并同步记录结果。",
            "车辆出发前核对货物与交接信息再安排配送。",
        ),
    }
    image_candidates = (
        "先看外部变化。",
        "再看仓内准备。",
        "画面切入仓内。",
        "节奏转入仓内。",
        "外部变化暂作背景。",
        "外部现场只作提醒，再回到当前物流主题。",
        "先看外部变化，再核对仓内的准备动作。",
        "画面转入仓内准备，相关动作需要逐项核对。",
    )
    subjects = {
        "warehouse": ("工作人员", "仓内人员", "现场人员", "分拣人员", "作业人员", "核对人员"),
        "staff": ("工作人员", "现场人员", "作业人员", "协同人员", "当班人员", "核对人员"),
        "facility": ("仓内设备", "现场设备", "作业设备", "分区设备", "处理设备", "辅助设备"),
        "delivery": ("配送车辆", "现场车辆", "作业车辆", "交接车辆", "发运车辆", "运输车辆"),
    }
    actions = {
        "warehouse": ("逐件核对包裹", "按顺序核对货物", "逐项记录分拣动作", "按区域处理货物"),
        "staff": ("按流程核对货物", "逐项确认现场动作", "协同处理可见货物", "记录当前作业状态"),
        "facility": ("按区域配合作业", "逐项处理可见货物", "记录当前设备动作", "配合完成分拣准备"),
        "delivery": ("按动线完成交接", "出发前核对货物", "逐项确认交接信息", "记录当前车辆动作"),
    }
    safe_tails = ("。", "并记录结果。", "让动作可核对。", "并确认现场状态。")
    seen: set[str] = set()
    for index, (item, scene) in enumerate(zip(repaired["scenes"], scenes)):
        voiceover = str(item.get("voiceover") or "").strip()
        current_key = _formal_voiceover_key(voiceover)
        if current_key and current_key not in seen:
            seen.add(current_key)
            continue
        minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        role = str(scene.get("scene_role") or "")
        category = str(scene.get("primary_category") or "").casefold()
        candidates = list(image_candidates) if role == "owned_context_image" else list(
            category_candidates.get(category, ("镜头中的作业动作清晰可见。",))
        )
        # 已审核的 copy_anchor 优先保留，但只有不重复且在时长窗口内才可用。
        anchor = str(scene.get("copy_anchor") or "").strip()
        if anchor:
            candidates[:0] = [
                anchor,
                anchor.rstrip("。！？；") + "，并同步做好现场记录。",
                anchor.rstrip("。！？；") + "，这一步动作正在现场展开。",
            ]
        if role == "owned_context_image":
            candidates.extend((
                "再看现场变化。", "回到当前主题。", "转入仓内准备。", "聚焦仓内动作。",
                "继续核对动作。", "再看分拣准备。", "核对现场动作。", "回到物流现场。",
                "继续观察现场。", "再看可见动作。", "聚焦当前环节。", "转入执行现场。",
            ))
        else:
            # Do not cap the formal chain at a small hand-written sentence
            # list. A 60-second plan can legitimately contain many clips of
            # the same reviewed category. Build a bounded cartesian set of
            # neutral, visible-action sentences so repetition becomes a local
            # repair trigger instead of a scripting-stage production failure.
            category_subjects = subjects.get(category, ("现场人员", "作业人员", "工作人员"))
            category_actions = actions.get(
                category,
                ("记录当前可见动作", "按流程核对现场动作", "逐项确认当前状态"),
            )
            candidates.extend(
                f"{subject}{action}{tail}"
                for subject in category_subjects
                for action in category_actions
                for tail in safe_tails
            )
        replacement = next(
            (
                candidate for candidate in candidates
                if _formal_voiceover_key(candidate) not in seen
                and (minimum is None or len("".join(candidate.split())) >= minimum)
                and (maximum is None or len("".join(candidate.split())) <= maximum)
            ),
            None,
        )
        if replacement is None:
            # Last-resort wording is deliberately meta and evidence-neutral:
            # it describes only that the current shot records a visible
            # logistics action. The ordinal keeps it unique without inventing
            # a service result.
            ordinal_candidates = (
                f"画面{index + 1}记录现场动作。",
                f"第{index + 1}段只记录可见物流动作。",
                f"第{index + 1}段只记录现场可见的物流动作。",
                f"第{index + 1}段只记录现场可见物流动作，不推断画面外结果。",
            )
            replacement = next(
                (
                    candidate for candidate in ordinal_candidates
                    if _formal_voiceover_key(candidate) not in seen
                    and (minimum is None or len("".join(candidate.split())) >= minimum)
                    and (maximum is None or len("".join(candidate.split())) <= maximum)
                ),
                None,
            )
        if replacement is None:
            raise ValueError(
                f"第 {index + 1} 个分镜的旁白字数窗口不可满足：minimum={minimum}, maximum={maximum}"
            )
        item["voiceover"] = replacement
        item["text_overlay"] = replacement.rstrip("。")[:24]
        seen.add(_formal_voiceover_key(replacement))
    return repaired


def _finalize_formal_script_candidate(
    generated: dict,
    *,
    brief: dict,
    scenes: list[dict],
    event: dict | None,
    hook_binding_mode: str,
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
    hotspot_count: int,
    allow_fallback_bridge: bool = False,
) -> dict:
    """Validate MiniMax copy unchanged; only repair an explicit offline fallback."""
    original = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    if not allow_fallback_bridge:
        normalized = _planner_json(
            _json.dumps(generated, ensure_ascii=False), len(scenes), voiceover_limits,
            voiceover_minimums, hotspot_scene_count=hotspot_count,
        )
        _validate_formal_copy_specificity(normalized)
        _validate_complete_formal_voiceovers(normalized)
        _validate_generated_topic_anchor(normalized, brief, has_event_anchor=bool(event))
        _validate_formal_narrative(normalized, scenes, event)
        return normalized
    generated = _enforce_formal_scene_copy_contract(generated, scenes)
    generated = _enforce_generated_topic_opening(generated, brief, scenes, event)
    generated = _repair_formal_narrative_hook(generated, scenes, event)
    if allow_fallback_bridge:
        generated = _repair_formal_narrative_bridge(
            generated, scenes, hook_binding_mode=hook_binding_mode,
        )
    generated = _compact_long_formal_voiceovers(
        generated,
        voiceover_limits,
        scenes,
        voiceover_minimums,
    )
    generated = _repair_short_formal_voiceovers(
        generated, scenes, voiceover_minimums, voiceover_limits, event,
    )
    generated = _repair_repeated_formal_voiceovers(
        generated, scenes, voiceover_minimums, voiceover_limits,
    )
    generated = _repair_dangling_formal_voiceovers(
        generated, scenes, voiceover_minimums, voiceover_limits,
    )
    # Dangling-sentence repair may replace the first beat with category copy.
    # Re-lock the immutable Hook fact afterwards so the last repair can never
    # erase what actually happened in the verified opening clip.
    generated = _repair_formal_narrative_hook(generated, scenes, event)
    # Duration, repetition and dangling-sentence repair can legitimately
    # replace the first owned beat. Re-assert the offline fallback bridge only
    # after those repairs, so the final line still contains the risk relation,
    # a Buffalo-visible action and a concrete advantage. This never runs for
    # accepted MiniMax copy.
    generated = _repair_formal_narrative_bridge(
        generated, scenes, hook_binding_mode=hook_binding_mode,
    )
    # The preceding duration/repetition repairs are intentionally allowed to
    # replace individual lines.  Restore every immutable topic group only
    # after those repairs, using later owned proof beats, then re-assert the
    # first Hook bridge once more.  Generation and validation now share one
    # contract instead of rejecting each other's output.
    generated = _repair_generated_topic_contract(
        generated,
        brief=brief,
        scenes=scenes,
        event=event,
        voiceover_minimums=voiceover_minimums,
        voiceover_limits=voiceover_limits,
    )
    generated = _repair_formal_narrative_bridge(
        generated, scenes, hook_binding_mode=hook_binding_mode,
    )
    if allow_fallback_bridge:
        generated = _coerce_fallback_voiceovers_into_window(
            generated, scenes, voiceover_minimums, voiceover_limits,
        )
    _validate_formal_copy_specificity(generated)
    _validate_complete_formal_voiceovers(generated)
    _validate_generated_topic_anchor(generated, brief, has_event_anchor=bool(event))
    _validate_formal_narrative(generated, scenes, event)
    normalized = _planner_json(
        _json.dumps(generated, ensure_ascii=False), len(scenes), voiceover_limits,
        voiceover_minimums, hotspot_scene_count=hotspot_count,
    )
    return _annotate_copy_revisions(
        original,
        normalized,
        reason="local_fact_duration_or_safety_guard",
    )


_MODEL_DANGLING_TAILS = (
    "因此", "所以", "同时", "从而", "并且", "再把", "让", "把", "通过",
    "以及", "而且", "并", "和", "与", "为", "的",
)


def _clean_model_fragment(value: str) -> str:
    """Return a complete compact fragment without inventing new copy."""
    cleaned = "".join(str(value or "").split()).strip("，、；：。！？ ")
    changed = True
    while cleaned and changed:
        changed = False
        for tail in _MODEL_DANGLING_TAILS:
            if cleaned.endswith(tail) and len(cleaned) > len(tail):
                cleaned = cleaned[:-len(tail)].rstrip("，、；：。！？ ")
                changed = True
                break
    return cleaned


def _model_term_fragments(text: str, term: str) -> list[str]:
    """Build richest-to-shortest spans around a term already written by MiniMax."""
    start = text.find(term)
    if start < 0:
        return []
    end = start + len(term)
    spans: list[str] = []
    for before, after in ((3, 3), (2, 2), (2, 0), (1, 1), (0, 0)):
        candidate = _clean_model_fragment(text[max(0, start - before):min(len(text), end + after)])
        if candidate and candidate not in spans:
            spans.append(candidate)
    return spans


def _compress_model_bridge_line(
    voiceover: str,
    maximum: int,
    minimum: int | None = None,
    grounded_actions: list[str] | None = None,
) -> str:
    """Compress MiniMax's four-beat bridge using only words from its own line.

    This is deliberately not a scene/category template.  It extracts the
    model's own risk relation, visible action and brand-advantage fragments,
    then only adds punctuation.  Richer source spans win when they fit.
    """
    compact = "".join(str(voiceover or "").split())
    risk_terms = [term for term in _LOGISTICS_IMPACT_TERMS if term in compact]
    allowed_actions = tuple(grounded_actions or _VISIBLE_ACTION_TERMS)
    action_terms = [term for term in allowed_actions if term in compact]
    # “逐件/逐项/逐单” only quantify an operation; they are not a complete
    # visible action by themselves.  Requiring a core verb prevents local
    # compaction from producing malformed copy such as “Buffalo逐件。”.
    core_action_terms = [
        term for term in action_terms if term not in {"逐件", "逐项", "逐单"}
    ]
    source_advantage_terms = [
        term for term in _BRAND_ADVANTAGE_TERMS if term in compact
    ]
    brand_name = "Buffalo" if "buffalo" in compact.casefold() else ""
    if not risk_terms or not core_action_terms:
        return ""

    def clause_prefixes(clause: str, terms: tuple[str, ...]) -> list[str]:
        """Use only complete source prefixes ending at a known model term."""
        values: list[str] = []
        cleaned = _clean_model_fragment(clause)
        if cleaned:
            values.append(cleaned)
        for term in terms:
            start = 0
            while True:
                found = clause.find(term, start)
                if found < 0:
                    break
                candidate = _clean_model_fragment(clause[:found + len(term)])
                if candidate and candidate not in values:
                    values.append(candidate)
                start = found + len(term)
        return values

    def clause_term_spans(clause: str, terms: tuple[str, ...]) -> list[str]:
        """Keep complete model terms or nearby term spans, never raw windows."""
        positions = sorted(
            (clause.find(term), clause.find(term) + len(term), term)
            for term in terms
            if term in clause
        )
        values: list[str] = []
        for start, end, term in positions:
            if term not in values:
                values.append(term)
            for next_start, next_end, _ in positions:
                if next_start <= start or next_start - end > 3:
                    continue
                candidate = _clean_model_fragment(clause[start:next_end])
                if candidate and candidate not in values:
                    values.append(candidate)
        return values

    clauses = [
        _clean_model_fragment(part)
        for part in re.split(r"[。！？；，、]", compact)
        if _clean_model_fragment(part)
    ]
    risk_clauses = [
        clause for clause in clauses
        if any(term in clause for term in _LOGISTICS_IMPACT_TERMS)
    ]
    action_clauses = [
        clause for clause in clauses
        if any(term in clause for term in core_action_terms)
    ]
    advantage_fragments: list[str] = []
    for clause in clauses:
        if not any(term in clause for term in source_advantage_terms):
            continue
        for value in (
            clause_prefixes(clause, tuple(source_advantage_terms))
            + clause_term_spans(clause, tuple(source_advantage_terms))
        ):
            if value and value not in advantage_fragments:
                advantage_fragments.append(value)
    # MiniMax sometimes writes the whole bridge without punctuation.  Split
    # only at an existing brand/action boundary, never at an arbitrary
    # character offset, so both halves still consist of complete model words.
    for clause in clauses:
        if not (
            any(term in clause for term in _LOGISTICS_IMPACT_TERMS)
            and any(term in clause for term in core_action_terms)
        ):
            continue
        brand_at = clause.casefold().find("buffalo")
        action_positions = [
            clause.find(term) for term in core_action_terms if term in clause
        ]
        split_at = brand_at if brand_at > 0 else min(
            (position for position in action_positions if position > 0),
            default=-1,
        )
        if split_at <= 0:
            continue
        risk_part = _clean_model_fragment(clause[:split_at])
        action_part = _clean_model_fragment(clause[split_at:])
        if risk_part and risk_part not in risk_clauses:
            risk_clauses.append(risk_part)
        if action_part and action_part not in action_clauses:
            action_clauses.append(action_part)
    risk_prefix_terms = tuple(dict.fromkeys(_LOGISTICS_IMPACT_TERMS + _HOOK_FACT_TERMS))
    action_prefix_terms = tuple(dict.fromkeys(allowed_actions + _BRAND_ADVANTAGE_TERMS))
    candidates: list[str] = []
    for risk_clause in risk_clauses:
        for action_clause in action_clauses:
            risk_options = clause_prefixes(risk_clause, risk_prefix_terms)
            risk_options += [
                value for value in clause_term_spans(risk_clause, risk_prefix_terms)
                if value not in risk_options
            ]
            action_options = clause_prefixes(action_clause, action_prefix_terms)
            action_options += [
                value for value in clause_term_spans(action_clause, action_prefix_terms)
                if value not in action_options
            ]
            advantage_options = [
                value
                for value in clause_term_spans(action_clause, action_prefix_terms)
                if any(term in value for term in _BRAND_ADVANTAGE_TERMS)
            ]
            advantage_options += [
                value for value in advantage_fragments
                if value not in advantage_options
            ]
            for risk in risk_options:
                for action in action_options:
                    branded_action = action
                    if brand_name and "buffalo" not in branded_action.casefold():
                        branded_action = f"{brand_name}{branded_action}"
                    for advantage in [""] + advantage_options:
                        if advantage and advantage in branded_action:
                            candidate = f"{risk}，{branded_action}。"
                        elif advantage:
                            candidate = f"{risk}，{branded_action}，{advantage}。"
                        else:
                            candidate = f"{risk}，{branded_action}。"
                        candidate = candidate.replace("，，", "，")
                        if (
                            len(candidate) <= maximum
                            and (minimum is None or len(candidate) >= minimum)
                            and candidate.casefold().count("buffalo") == (1 if brand_name else 0)
                            and any(term in candidate for term in _LOGISTICS_IMPACT_TERMS)
                            and any(term in candidate for term in core_action_terms)
                            and (
                                not source_advantage_terms
                                or any(term in candidate for term in source_advantage_terms)
                            )
                            and not video_topic_contract.incomplete_sentence_issues(
                                {"scenes": [{"voiceover": candidate}]}
                            )
                            and candidate not in candidates
                        ):
                            candidates.append(candidate)

    def score(candidate: str) -> tuple[int, int, int, int, int]:
        return (
            sum(term in candidate for term in _HOOK_FACT_TERMS),
            sum(term in candidate for term in _LOGISTICS_IMPACT_TERMS),
            sum(term in candidate for term in core_action_terms),
            sum(term in candidate for term in _BRAND_ADVANTAGE_TERMS),
            len(candidate),
        )

    return max(candidates, key=score, default="")


def _hard_clip_model_line(
    voiceover: str,
    maximum: int,
    minimum: int | None = None,
) -> str:
    """Last-resort length clip that keeps MiniMax as the sole copy author."""
    compact = "".join(str(voiceover or "").split())
    if len(compact) <= maximum:
        return compact
    upper = max(1, maximum - 1)
    lower = max(2, int(maximum * 0.55), int(minimum or 0))
    for end in range(upper, lower - 1, -1):
        clipped = _clean_model_fragment(compact[:end])
        if not clipped:
            continue
        candidate = clipped[:upper].rstrip("，、；：。！？ ") + "。"
        if (
            (minimum is None or len(candidate) >= minimum)
            and not video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": candidate}]}
            )
        ):
            return candidate
    return ""


def _compress_model_grounded_action_line(
    voiceover: str,
    grounded_actions: list[str],
    maximum: int,
    minimum: int | None = None,
) -> str:
    """Keep a complete MiniMax fragment that still names the filmed action."""
    compact = "".join(str(voiceover or "").split())
    if not compact or not grounded_actions:
        return ""
    candidates: list[str] = []

    def add(raw: str) -> None:
        candidate = _clean_model_fragment(raw).rstrip("，、；：。！？ ") + "。"
        length = len(candidate)
        if (
            candidate
            and length <= maximum
            and (minimum is None or length >= minimum)
            and any(term in candidate for term in grounded_actions)
            and not video_topic_contract.incomplete_sentence_issues(
                {"scenes": [{"voiceover": candidate}]}
            )
            and candidate not in candidates
        ):
            candidates.append(candidate)

    clauses = [part for part in re.split(r"[。！？；，、]", compact) if part]
    for index, clause in enumerate(clauses):
        add(clause)
        if index + 1 < len(clauses):
            add(f"{clause}，{clauses[index + 1]}")
        if index > 0:
            add(f"{clauses[index - 1]}，{clause}")
    add(compact[:max(1, maximum - 1)])
    return max(candidates, key=len, default="")


def _complete_model_sentence(voiceover: str) -> str:
    """Close a dangling MiniMax sentence by deleting only its unfinished tail."""
    compact = "".join(str(voiceover or "").split())
    if not compact:
        return ""
    if not video_topic_contract.incomplete_sentence_issues(
        {"scenes": [{"voiceover": compact}]}
    ):
        return compact
    lower = max(2, len(compact) - 12)
    for end in range(len(compact), lower - 1, -1):
        fragment = _clean_model_fragment(compact[:end])
        if not fragment:
            continue
        candidate = fragment.rstrip("，、；：。！？ ") + "。"
        if not video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": candidate}]}
        ):
            return candidate
    return ""


def _normalize_model_terminal_punctuation(voiceover: str) -> str:
    """Add only a missing terminal mark; never delete or replace model words."""
    compact = "".join(str(voiceover or "").split())
    if not compact:
        return ""
    if not video_topic_contract.incomplete_sentence_issues(
        {"scenes": [{"voiceover": compact}]}
    ):
        return compact
    if compact[-1] in "。！？；":
        return compact
    candidate = compact + "。"
    if not video_topic_contract.incomplete_sentence_issues(
        {"scenes": [{"voiceover": candidate}]}
    ):
        return candidate
    return compact


def _complete_remote_model_voiceovers(generated: dict) -> dict:
    """Apply punctuation-only completion to MiniMax lines, never stock copy."""
    completed = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    for index, item in enumerate(completed["scenes"]):
        current = str(item.get("voiceover") or "").strip()
        if not video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": current}]}
        ):
            continue
        replacement = _complete_model_sentence(current)
        if not replacement:
            raise ValueError(f"第{index + 1}镜旁白无法保留模型原意并补全句子")
        item["voiceover"] = replacement
        item["text_overlay"] = str(item.get("text_overlay") or replacement.rstrip("。"))[:24]
    return _annotate_copy_revisions(
        generated,
        completed,
        reason="model_sentence_completion",
    )


def _compact_long_formal_voiceovers(
    generated: dict,
    voiceover_limits: list[int | None],
    scenes: list[dict] | None = None,
    voiceover_minimums: list[int | None] | None = None,
    *,
    allow_scene_fallback: bool = True,
) -> dict:
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
        minimum = (
            voiceover_minimums[index]
            if voiceover_minimums and index < len(voiceover_minimums)
            else None
        )
        voiceover = str(item.get("voiceover") or "").strip()
        compact = "".join(voiceover.split())
        if maximum is None or len(compact) <= maximum:
            continue
        prefix = compact[:maximum]
        source_scene = scenes[index] if scenes and index < len(scenes) else {}
        grounded_actions = _visible_action_terms_for_scene(source_scene)
        shortened = ""
        if str(source_scene.get("scene_role") or "") == "owned_proof":
            if "buffalo" in compact.casefold() and any(
                term in compact for term in _LOGISTICS_IMPACT_TERMS
            ):
                shortened = _compress_model_bridge_line(
                    compact, maximum, minimum, grounded_actions,
                )
            if not shortened:
                shortened = _compress_model_grounded_action_line(
                    compact,
                    grounded_actions,
                    maximum,
                    minimum,
                )
        boundary = max((prefix.rfind(mark) for mark in "。！？；，、"), default=-1)
        # Do not leave a one-word fragment merely because a comma happened at
        # the start; a full-width stop within the latter half is a clean cut.
        if shortened:
            pass
        elif boundary >= max(4, int(maximum * 0.55)):
            marker = prefix[boundary]
            shortened = prefix[:boundary + 1]
            if marker in "，、":
                shortened = shortened[:-1].rstrip() + "。"
        elif allow_scene_fallback:
            # If the model wrote one unbroken clause, use the already-reviewed
            # scene contract instead of spending more remote calls on the same
            # wording error.  These candidates only describe the locked topic
            # beat or visible source action and always end as complete sentences.
            role = str(source_scene.get("scene_role") or "")
            category = str(source_scene.get("primary_category") or "").casefold()
            candidates = [
                str(source_scene.get("voiceover") or "").strip(),
                str(source_scene.get("copy_anchor") or "").strip(),
                {
                    "warehouse": "仓内正在进行分拣准备。",
                    "staff": "工作人员正在处理仓内包裹。",
                    "facility": "仓内设备正在处理包裹。",
                    "delivery": "车辆正在进行发运前准备。",
                }.get(category, ""),
            ]
            shortened = next(
                (
                    candidate for candidate in candidates
                    if candidate
                    and candidate[-1] in "。！？；"
                    and len("".join(candidate.split())) <= maximum
                ),
                "",
            )
            if not shortened or role == "hotspot_evidence":
                raise ValueError(f"内容规划模型第 {index + 1} 个分镜过长且没有可安全截断的完整分句")
        else:
            # MiniMax has supplied usable wording.  Prefer a clean semantic
            # boundary; for the first owned bridge, reconstruct a shorter line
            # exclusively from MiniMax's own risk/action/advantage fragments.
            # A final hard clip still keeps the model as the sole copy author.
            semantic_boundaries = (
                "。", "！", "？", "；", "，", "、",
                "因此", "所以", "同时", "从而", "并且", "再把", "让",
            )
            cut_at = max((prefix.rfind(mark) for mark in semantic_boundaries), default=-1)
            if cut_at >= max(4, int(maximum * 0.55)):
                shortened = prefix[:cut_at].rstrip("，、；： ") + "。"
            elif str(source_scene.get("scene_role") or "") == "owned_proof":
                shortened = _compress_model_bridge_line(
                    compact, maximum, minimum, grounded_actions,
                )
                if not shortened:
                    shortened = _hard_clip_model_line(compact, maximum, minimum)
            else:
                shortened = _hard_clip_model_line(compact, maximum, minimum)
            if not shortened:
                raise ValueError(f"内容规划模型第 {index + 1} 个分镜无法保留完整模型语义")
        if minimum is not None and len("".join(shortened.split())) < minimum:
            source_scene = scenes[index] if scenes and index < len(scenes) else {}
            if str(source_scene.get("scene_role") or "") == "owned_proof":
                shortened = _compress_model_bridge_line(
                    compact, maximum, minimum, grounded_actions,
                )
            if not shortened or len("".join(shortened.split())) < minimum:
                shortened = _hard_clip_model_line(compact, maximum, minimum)
        if video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": shortened}]}
        ):
            shortened = _hard_clip_model_line(compact, maximum, minimum)
        if (
            not shortened
            or (minimum is not None and len("".join(shortened.split())) < minimum)
            or video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": shortened}]}
            )
        ):
            raise ValueError(f"内容规划模型第 {index + 1} 个分镜无法形成完整模型句子")
        item["voiceover"] = shortened
        item["text_overlay"] = str(item.get("text_overlay") or shortened)[:24]
    return repaired


def _salvage_remote_formal_script(
    content: str,
    *,
    brief: dict,
    scenes: list[dict],
    event: dict | None,
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
    hotspot_count: int,
    source: str,
    reason: str,
) -> dict:
    """Keep a valid MiniMax draft when only a measured beat is too long.

    The model remains the author.  Local code can remove a trailing clause at
    punctuation/semantic boundaries, but cannot swap in category templates.
    Every other factual, topic, narrative and duration gate remains active.
    """
    parsed = _planner_json(
        content, len(scenes), None, None, hotspot_scene_count=hotspot_count,
    )
    stamped = _stamp_copy_source(parsed, source, reason=reason)
    # The Hook opening is evidence, not creative brand copy.  If MiniMax omits
    # audited nouns while rewriting for duration, lock only scene 1 back to the
    # verified database fact.  The marketing bridge and all owned-scene copy
    # remain model-authored and are never replaced with a fixed slogan here.
    fact_locked = _repair_formal_narrative_hook(stamped, scenes, event)
    stamped = _annotate_copy_revisions(
        stamped,
        fact_locked,
        reason="verified_hook_fact_lock",
    )
    stamped = _complete_remote_model_voiceovers(stamped)
    # Duration compaction is a staging operation.  Validate the compacted
    # document, then apply the narrow topic-contract rescue so a reachable
    # MiniMax response using a valid synonym is not rejected at scripting.
    compacted = _compact_long_formal_voiceovers(
        stamped,
        voiceover_limits,
        scenes,
        voiceover_minimums,
        allow_scene_fallback=False,
    )
    compacted = _annotate_copy_revisions(
        stamped,
        compacted,
        reason="model_duration_compaction",
    )
    compacted = _repair_generated_topic_contract(
        compacted,
        brief=brief,
        scenes=scenes,
        event=event,
        voiceover_minimums=voiceover_minimums,
        voiceover_limits=voiceover_limits,
    )
    normalized = _planner_json(
        _json.dumps(compacted, ensure_ascii=False),
        len(scenes),
        voiceover_limits,
        voiceover_minimums,
        hotspot_scene_count=hotspot_count,
    )
    _validate_formal_copy_specificity(normalized)
    _validate_complete_formal_voiceovers(normalized)
    _validate_generated_topic_anchor(normalized, brief, has_event_anchor=bool(event))
    _validate_formal_narrative(normalized, scenes, event)
    return normalized


def _first_owned_bridge_index(scenes: list[dict]) -> int | None:
    """Return the first Buffalo proof beat after the verified Hook."""
    hotspot_seen = False
    for index, scene in enumerate(scenes):
        if str(scene.get("scene_role") or "") == "hotspot_evidence":
            hotspot_seen = True
            continue
        if hotspot_seen and str(scene.get("scene_role") or "") == "owned_proof":
            return index
    return None


def _owned_bridge_window_indices(scenes: list[dict], *, maximum: int = 2) -> list[int]:
    """Use two short Buffalo beats for impact/action/advantage when available."""
    first = _first_owned_bridge_index(scenes)
    if first is None:
        return []
    indices = [first]
    for index in range(first + 1, len(scenes)):
        if str(scenes[index].get("scene_role") or "") == "owned_proof":
            indices.append(index)
            if len(indices) >= maximum:
                break
    return indices


def _visible_action_terms_for_scene(scene: dict) -> list[str]:
    """Whitelist action words that are present in the locked visual contract."""
    visual_contract = " ".join(
        str(scene.get(key) or "")
        for key in (
            "copy_anchor", "action_key", "visual", "audited_visual_fact", "voiceover",
        )
    )
    grounded = [term for term in _VISIBLE_ACTION_TERMS if term in visual_contract]
    expanded = list(grounded)
    for family in _VISIBLE_ACTION_FAMILIES:
        if any(term in visual_contract for term in family):
            expanded.extend(term for term in family if term not in expanded)
    return expanded


def _prepare_remote_formal_script_for_bridge(
    content: str,
    *,
    brief: dict,
    scenes: list[dict],
    event: dict | None,
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
    hotspot_count: int,
    source: str,
    reason: str,
) -> tuple[dict, list[int], int]:
    """Keep a MiniMax plan whose remaining bad beats can be patched precisely.

    The fourth remote call must not regenerate the whole plan or fall back to
    local stock copy.  It receives only the first Buffalo bridge plus any beats
    whose model-authored copy is outside the measured duration window.
    """
    bridge_index = _first_owned_bridge_index(scenes)
    if bridge_index is None:
        raise ValueError("正式分镜缺少热点后的 Buffalo 承接镜")
    parsed = _planner_json(
        content, len(scenes), None, None, hotspot_scene_count=hotspot_count,
    )
    stamped = _stamp_copy_source(parsed, source, reason=reason)
    fact_locked = _repair_formal_narrative_hook(stamped, scenes, event)
    stamped = _annotate_copy_revisions(
        stamped, fact_locked, reason="verified_hook_fact_lock",
    )
    stamped = _complete_remote_model_voiceovers(stamped)
    compacted = _compact_long_formal_voiceovers(
        stamped,
        voiceover_limits,
        scenes,
        voiceover_minimums,
        allow_scene_fallback=False,
    )
    compacted = _annotate_copy_revisions(
        stamped, compacted, reason="model_duration_compaction",
    )
    compacted = _repair_generated_topic_contract(
        compacted,
        brief=brief,
        scenes=scenes,
        event=event,
        voiceover_minimums=voiceover_minimums,
        voiceover_limits=voiceover_limits,
    )
    bridge_window = _owned_bridge_window_indices(scenes)
    rewrite_indices: set[int] = set(bridge_window or [bridge_index])
    for index, item in enumerate(compacted.get("scenes") or []):
        original_item = (stamped.get("scenes") or [])[index]
        if str(item.get("voiceover") or "") != str(original_item.get("voiceover") or ""):
            # Local compaction is only a staging aid.  A changed line must be
            # handed back to MiniMax so no locally shortened sentence reaches
            # the final report as if it were model-authored copy.
            rewrite_indices.add(index)
        compact_length = len("".join(str(item.get("voiceover") or "").split()))
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
        if (maximum is not None and compact_length > maximum) or (
            minimum is not None and compact_length < minimum
        ):
            rewrite_indices.add(index)
    try:
        _validate_dynamic_brand_cta(compacted.get("scenes") or [], scenes)
    except ValueError:
        # A reachable MiniMax response that returned a stock CTA or omitted
        # the theme-specific value proposition is repaired by MiniMax again;
        # it is never silently replaced by the local corpus.
        rewrite_indices.update(
            index for index, scene in enumerate(scenes)
            if str(scene.get("scene_role") or "") == "brand_cta"
        )
    relaxed_limits = list(voiceover_limits)
    relaxed_minimums = list(voiceover_minimums)
    for index in rewrite_indices:
        if index < len(relaxed_limits):
            relaxed_limits[index] = None
        if index < len(relaxed_minimums):
            relaxed_minimums[index] = None
    normalized = _planner_json(
        _json.dumps(compacted, ensure_ascii=False),
        len(scenes),
        relaxed_limits,
        relaxed_minimums,
        hotspot_scene_count=hotspot_count,
    )
    # ``normalized`` is an internal staging document. Every locally changed
    # or duration-invalid line is replaced by the fourth MiniMax call below.
    # Topic validation happens here after the narrow rescue, so a missing
    # literal such as “接单” can be repaired from the same semantic group.
    _validate_generated_topic_anchor(normalized, brief, has_event_anchor=bool(event))
    return normalized, sorted(rewrite_indices), bridge_index


def _parse_model_scene_rewrites(content: str) -> list[dict]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = _json.loads(raw)
    except Exception as exc:
        raise ValueError("MiniMax 镜头专项修订未返回合法 JSON") from exc
    rows = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("MiniMax 镜头专项修订返回结构无效")
    return rows


def _normalize_model_scene_rewrite_row(
    row: dict,
    *,
    index: int,
    bridge_index: int,
    scenes: list[dict],
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
) -> dict:
    """Validate one MiniMax patch without revalidating unfinished peers.

    Targeted retries may repair several beats.  A valid beat is frozen as soon
    as it passes this contract, so a later retry cannot regress it while
    fixing another scene.  Any duration compaction keeps only MiniMax-authored
    words and never substitutes a category template.
    """
    voiceover = str(row.get("voiceover") or "").strip()
    if not voiceover:
        raise ValueError(f"MiniMax 第 {index + 1} 镜专项修订缺少旁白")
    source_scene = scenes[index] if index < len(scenes) else {}
    maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
    minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
    compacted = _compact_long_formal_voiceovers(
        {"scenes": [{"voiceover": voiceover, "text_overlay": row.get("text_overlay")}]},
        [maximum],
        [source_scene],
        [minimum],
        allow_scene_fallback=False,
    )
    voiceover = str(compacted["scenes"][0].get("voiceover") or "").strip()
    # MiniMax occasionally returns a semantically complete sentence without
    # the terminal Chinese punctuation.  Preserve every model-authored word
    # and add punctuation only; malformed/truncated tails remain invalid and
    # continue through the targeted remote retry path below.
    voiceover = _normalize_model_terminal_punctuation(voiceover)
    if any(phrase in voiceover for phrase in (
        "Buffalo先核对", "Buffalo核对做稳", "核对做稳",
    )):
        raise ValueError("MiniMax 镜头专项修订仍使用固定承接句")
    compact_length = len("".join(voiceover.split()))
    if maximum is not None and compact_length > maximum:
        raise ValueError(f"MiniMax 第 {index + 1} 镜旁白超过 {maximum} 字时长上限")
    if minimum is not None and compact_length < minimum:
        raise ValueError(f"MiniMax 第 {index + 1} 镜旁白少于 {minimum} 字时长下限")
    if video_topic_contract.incomplete_sentence_issues(
        {"scenes": [{"voiceover": voiceover}]}
    ):
        raise ValueError(f"MiniMax 第 {index + 1} 镜旁白不是完整句子")
    if str(source_scene.get("scene_role") or "") == "brand_cta":
        _validate_dynamic_brand_cta_voiceover(voiceover, source="repair")

    grounded_actions = _visible_action_terms_for_scene(source_scene)
    if grounded_actions and not any(term in voiceover for term in grounded_actions):
        raise ValueError(f"MiniMax 第 {index + 1} 镜没有使用锁定镜头中的真实可见动作")
    bridge_window = _owned_bridge_window_indices(scenes)
    bridge_position = (
        bridge_window.index(index) if index in bridge_window else None
    )
    if bridge_position is not None:
        if not grounded_actions:
            raise ValueError("锁定 Buffalo 镜头没有可供模型引用的可见动作")
        if bridge_position == 0:
            if "buffalo" not in voiceover.casefold():
                raise ValueError("MiniMax 第一承接镜没有点明 Buffalo")
            if not any(term in voiceover for term in _LOGISTICS_IMPACT_TERMS):
                raise ValueError("MiniMax 第一承接镜没有说明热点带来的物流影响")
        # With two Buffalo beats, the second one must turn its visible action
        # into a concrete advantage. With only one beat, that line completes
        # the full four-beat bridge. This validates model output only; it never
        # substitutes local stock copy.
        if (bridge_position > 0 or len(bridge_window) == 1) and not any(
            term in voiceover for term in _BRAND_ADVANTAGE_TERMS
        ):
            raise ValueError(
                f"MiniMax 第 {index + 1} 镜没有把真实可见动作写成 Buffalo 品牌优势"
            )
    return {
        "scene": index + 1,
        "voiceover": voiceover,
        "text_overlay": str(row.get("text_overlay") or "").strip()[:24],
    }


def _apply_model_scene_rewrites(
    generated: dict,
    content: str,
    *,
    rewrite_indices: list[int],
    bridge_index: int,
    scenes: list[dict],
    brief: dict,
    event: dict | None,
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
    hotspot_count: int,
) -> dict:
    """Splice MiniMax-authored patches into only the measured invalid beats."""
    rows = _parse_model_scene_rewrites(content)
    expected = {index + 1 for index in rewrite_indices}
    received: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("MiniMax 镜头专项修订包含无效分镜")
        try:
            scene_number = int(row.get("scene"))
        except (TypeError, ValueError) as exc:
            raise ValueError("MiniMax 镜头专项修订缺少分镜编号") from exc
        if scene_number in received:
            raise ValueError("MiniMax 镜头专项修订包含重复分镜")
        received[scene_number] = row
    if set(received) != expected:
        raise ValueError("MiniMax 镜头专项修订未完整覆盖指定分镜")

    rewritten = {
        **generated,
        "scenes": [dict(item) for item in generated.get("scenes") or []],
    }
    for index in rewrite_indices:
        row = received[index + 1]
        voiceover = str(row.get("voiceover") or "").strip()
        if not voiceover:
            raise ValueError(f"MiniMax 第 {index + 1} 镜专项修订缺少旁白")
        rewritten["scenes"][index] = {
            **rewritten["scenes"][index],
            "voiceover": voiceover,
            "text_overlay": str(row.get("text_overlay") or "").strip()[:24],
        }
    # MiniMax sometimes returns a semantically correct line a few characters
    # beyond the locked TTS window.  Compact only words already present in the
    # model response; category templates and scene fallback are forbidden.
    rewritten = _compact_long_formal_voiceovers(
        rewritten,
        voiceover_limits,
        scenes,
        voiceover_minimums,
        allow_scene_fallback=False,
    )
    for index in rewrite_indices:
        voiceover = str(rewritten["scenes"][index].get("voiceover") or "").strip()
        text_overlay = str(rewritten["scenes"][index].get("text_overlay") or "").strip()[:24]
        if any(phrase in voiceover for phrase in (
            "Buffalo先核对", "Buffalo核对做稳", "核对做稳",
        )):
            raise ValueError("MiniMax 镜头专项修订仍使用固定承接句")
        compact_length = len("".join(voiceover.split()))
        maximum = voiceover_limits[index] if index < len(voiceover_limits) else None
        minimum = voiceover_minimums[index] if index < len(voiceover_minimums) else None
        if maximum is not None and compact_length > maximum:
            raise ValueError(f"MiniMax 第 {index + 1} 镜旁白超过 {maximum} 字时长上限")
        if minimum is not None and compact_length < minimum:
            raise ValueError(f"MiniMax 第 {index + 1} 镜旁白少于 {minimum} 字时长下限")
        if video_topic_contract.incomplete_sentence_issues(
            {"scenes": [{"voiceover": voiceover}]}
        ):
            raise ValueError(f"MiniMax 第 {index + 1} 镜旁白不是完整句子")

        source_scene = scenes[index] if index < len(scenes) else {}
        grounded_actions = _visible_action_terms_for_scene(source_scene)
        if grounded_actions and not any(term in voiceover for term in grounded_actions):
            raise ValueError(f"MiniMax 第 {index + 1} 镜没有使用锁定镜头中的真实可见动作")
        bridge_window = _owned_bridge_window_indices(scenes)
        bridge_position = (
            bridge_window.index(index) if index in bridge_window else None
        )
        if bridge_position is not None:
            if not grounded_actions:
                raise ValueError("锁定 Buffalo 镜头没有可供模型引用的可见动作")
            if bridge_position == 0:
                if "buffalo" not in voiceover.casefold():
                    raise ValueError("MiniMax 第一承接镜没有点明 Buffalo")
                if not any(term in voiceover for term in _LOGISTICS_IMPACT_TERMS):
                    raise ValueError("MiniMax 第一承接镜没有说明热点带来的物流影响")
            if (bridge_position > 0 or len(bridge_window) == 1) and not any(
                term in voiceover for term in _BRAND_ADVANTAGE_TERMS
            ):
                raise ValueError(
                    f"MiniMax 第 {index + 1} 镜没有把真实可见动作写成 Buffalo 品牌优势"
                )

        rewritten["scenes"][index] = {
            **rewritten["scenes"][index],
            "voiceover": voiceover,
            "text_overlay": text_overlay or voiceover.rstrip("。！？；")[:24],
            "copy_source": "repair",
            "copy_repair_reason": (
                "model_dynamic_brand_cta_rewrite"
                if str(source_scene.get("scene_role") or "") == "brand_cta"
                else (
                    "model_bridge_rewrite"
                    if index in bridge_window
                    else "model_targeted_scene_rewrite"
                )
            ),
        }

    normalized = _planner_json(
        _json.dumps(rewritten, ensure_ascii=False),
        len(scenes),
        voiceover_limits,
        voiceover_minimums,
        hotspot_scene_count=hotspot_count,
    )
    normalized = _repair_generated_topic_contract(
        normalized,
        brief=brief,
        scenes=scenes,
        event=event,
        voiceover_minimums=voiceover_minimums,
        voiceover_limits=voiceover_limits,
    )
    _validate_formal_copy_specificity(normalized)
    _validate_complete_formal_voiceovers(normalized)
    _validate_generated_topic_anchor(normalized, brief, has_event_anchor=bool(event))
    _validate_formal_narrative(normalized, scenes, event)
    return normalized


def _apply_model_bridge_rewrite(
    generated: dict,
    content: str,
    *,
    bridge_index: int,
    scenes: list[dict],
    brief: dict,
    event: dict | None,
    voiceover_minimums: list[int | None],
    voiceover_limits: list[int | None],
    hotspot_count: int,
) -> dict:
    """Compatibility wrapper for a one-scene dedicated bridge rewrite."""
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = _json.loads(raw)
    except Exception as exc:
        raise ValueError("MiniMax 承接镜专项修订未返回合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("MiniMax 承接镜专项修订返回结构无效")
    wrapped = _json.dumps({
        "scenes": [{
            "scene": bridge_index + 1,
            "voiceover": payload.get("voiceover"),
            "text_overlay": payload.get("text_overlay"),
        }],
    }, ensure_ascii=False)
    return _apply_model_scene_rewrites(
        generated,
        wrapped,
        rewrite_indices=[bridge_index],
        bridge_index=bridge_index,
        scenes=scenes,
        brief=brief,
        event=event,
        voiceover_minimums=voiceover_minimums,
        voiceover_limits=voiceover_limits,
        hotspot_count=hotspot_count,
    )


def _compact_topic_evidence(brief: dict, event: dict | None, scenes: list[dict]) -> dict:
    """Only send selected, short evidence summaries to the remote planner."""
    evidence = (event or {}).get("evidence") or {}
    visual_audit = evidence.get("visual_audit") or {}
    facts = [{
        "title": str(event.get("title_zh") or event.get("title_en") or "")[:160],
        "summary": str(event.get("summary_zh") or event.get("summary") or "")[:400],
        "location": str(event.get("location") or "")[:80],
        "published_at": str(event.get("published_at") or "")[:40],
        "what_happened": str(evidence.get("what_happened") or "")[:300],
        "hook_reason": str(evidence.get("hook_reason") or "")[:300],
        "logistics_question": str(evidence.get("logistics_question") or "")[:240],
        "visible_objects": list(visual_audit.get("visible_objects") or [])[:12],
        "visible_actions": list(visual_audit.get("visible_actions") or [])[:8],
    }] if event else []
    allowed_scenes = [{
        "scene": item["scene"], "role": item["scene_role"],
        "visual": str(item.get("copy_anchor") or item.get("visual") or "")[:120],
        "category": str(item.get("primary_category") or ""), "duration_seconds": round(item["duration_ms"] / 1000, 1),
        "voiceover_max_chars": _scene_voiceover_max_chars(item),
        "voiceover_min_chars": _scene_voiceover_min_chars(item),
    } for item in scenes]
    topic_contract = video_topic_contract.build_topic_contract(
        str(brief.get("raw_input") or brief.get("requested_topic") or brief.get("subject") or ""),
        has_event_anchor=bool(event),
    )
    narrative_contract = ({
        "beat_1": "明确说出 Hook 发生了什么，不能只说‘现场’或复述标题。",
        "beat_2": "解释这个事实为什么会影响运输、存储或配送安全；优先使用 facts[0].logistics_question 提出的具体物流问题。",
        "beat_3": "用 Buffalo 可见的仓储、分拣、运输或交付动作承接，并把一个具体优势落到画面。",
        "beat_4": (
            "每个后续镜头只讲一个可见动作；最后一个品牌 CTA 也由 MiniMax "
            "针对本片动态收束，承接已出现的物流影响或可见动作并落到具体 Buffalo 品牌优势。"
        ),
    } if event else {
        "beat_1": topic_contract["opening_hook"],
        "beat_2": topic_contract["opening_bridge"],
        "beat_3": topic_contract["safe_angle"],
        "beat_4": "每个后续镜头只讲一个与原主题相关的 Buffalo 可见动作和具体品牌优势。",
    })
    topic_requirements = {
        "intent": str(topic_contract.get("intent") or ""),
        "label": str(topic_contract.get("label") or ""),
        "opening_mode": str(topic_contract.get("opening_mode") or ""),
        "opening_hook": str(topic_contract.get("opening_hook") or ""),
        "opening_bridge": str(topic_contract.get("opening_bridge") or ""),
        "title_requirements": [
            list(group) for group in topic_contract.get("title_groups") or []
        ],
        "narrative_requirements": [
            list(group) for group in topic_contract.get("narrative_groups") or []
        ],
    }
    return {
        "brief": {
            **{key: brief.get(key) for key in ("raw_input", "subject", "angle", "audience", "goal", "logistics_nodes", "must_avoid")},
            "topic_anchor_contract": topic_contract,
            "topic_requirements": topic_requirements,
        },
        "facts": facts,
        "narrative_contract": narrative_contract,
        "topic_requirements": topic_requirements,
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
    compact_topic_text = re.sub(r"[\s，。！？；：、,.!?;:（）()\[\]【】\"'“”‘’]+", "", topic_text).casefold()
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
        event = _with_soft_logistics_bridge(event)
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
        curated_questions = [
            (int(event.get("id") or 0), str((event.get("evidence") or {}).get("logistics_question") or "").strip())
            for event in event_clips
        ]
        # A user who copies the Hook card's audited “物流切入” question is
        # explicitly selecting that bridge.  The bridge is not treated as a
        # visual fact, but it must be strong enough to retrieve the exact Hook;
        # otherwise a generic evergreen opener wins and the selected real scene
        # is silently replaced.
        curated_question_event_ids = {
            event_id for event_id, question in curated_questions
            if question and (
                re.sub(r"[\s，。！？；：、,.!?;:（）()\[\]【】\"'“”‘’]+", "", question).casefold() in compact_topic_text
                or compact_topic_text in re.sub(r"[\s，。！？；：、,.!?;:（）()\[\]【】\"'“”‘’]+", "", question).casefold()
            )
        }
        curated_question_exact = bool(curated_question_event_ids)
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
        if require_scene_overlap and topic_profile and not profile_overlap and not curated_question_exact:
            funnel["scene_mismatch"] += 1
            continue
        # Broad-only topics (南非/物流 with no logistics category profile) must
        # not hit random accident Hooks. Topics that already resolved to a
        # category (cost_risk, warehouse, …) may still use intent_bridge below.
        if not allow_broad_match and not topic_profile and not specific_terms and not curated_question_exact:
            funnel["scene_mismatch"] += 1
            continue
        intent_bridge = 0
        if "cost_risk" in topic_profile and event_profile & {"disruption", "border", "warehouse"}:
            intent_bridge = 12
        if "warehouse" in topic_profile and "border" in event_profile:
            intent_bridge = max(intent_bridge, 12)
        if strict_terms and not specific_direct and not curated_question_exact:
            funnel["relevance_low"] += 1
            continue
        # A hotspot's coarse type classification alone (kind_in_topics) is
        # not admitted as a qualifying signal — every hotspot in the small
        # confirmed library tends to classify into some logistics-adjacent
        # "kind", which used to let unrelated events into every topic's
        # candidate set. It still contributes a small tie-break score below
        # once a candidate already qualifies on a real signal.
        if not allow_broad_match and not direct and not profile_overlap and not intent_bridge and not curated_question_exact:
            funnel["relevance_low"] += 1
            continue
        event_fit = 1 if kind_in_topics else 0
        hooks = hotspot_hook_selector.rank_hook_clips(event_clips)
        if curated_question_event_ids:
            # Question matches are event-specific.  Do not let a higher-scoring
            # sibling clip from the same parent replace the exact Hook the user
            # copied from the card (e.g. snow-road clip 79 replacing fire clip 78).
            hooks = [
                hook for hook in hooks
                if int(hook.get("event_clip_id") or 0) in curated_question_event_ids
            ][:3]
        if not hooks:
            funnel["relevance_low"] += 1
            continue
        selected_curated_question = next(
            (question for event_id, question in curated_questions if event_id in curated_question_event_ids and question),
            "",
        )
        if selected_curated_question and curated_question_exact:
            question = selected_curated_question
        elif kind == "strike":
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
        source_headline = str(hotspot.get("title_zh") or hotspot.get("title") or "").strip()
        if not source_headline or source_headline.casefold().startswith("what’s happening across") or source_headline.casefold().startswith("what's happening across"):
            source_headline = str(hooks[0].get("content_description") or "") if hooks else source_headline
        hook_evidence = (event_clips[0].get("evidence") or {}) if event_clips else {}
        hook_fact = str(hook_evidence.get("what_happened") or "").strip()
        # Only an audited question may shape the retention headline.  The
        # broader retrieval question is for planning and can be unrelated to
        # the selected visual fact.
        hook_question = str(hook_evidence.get("logistics_question") or "").strip()
        attention_title = hotspot_hook_copy.attention_headline(hook_fact, hook_question, source_headline)
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
            "title": attention_title[:200] or source_headline[:200],
            "attention_title": attention_title[:200],
            "source_title": source_headline[:200],
            "summary": (str(hotspot.get("summary_zh") or hotspot.get("summary") or "") + " " + hook_context_text)[:500],
            "source_url": hotspot.get("source_url"), "published_at": hotspot.get("published_at"),
            "published_ts": published_ts,
            "hook_type": "direct" if direct else ("curated_bridge" if curated_question_exact else "contextual"), "logistics_signal": kind,
            "logistics_scenes": sorted(event_profile),
            "relevance": {
                "level": "strong_direct" if direct or curated_question_exact else "strong_logistics_context",
                "reason": (
                    "热点事实与用户问题存在直接物流节点重合。"
                    if direct else "用户主题与该 Hook 已审计的物流切入问题一致。"
                    if curated_question_exact else
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
                + (120 if curated_question_exact else 0)
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
    chain_mode = body.chain_mode or brief.get("chain_mode") or "hotspot_owned"
    if chain_mode == "owned_only":
        raise HTTPException(409, {
            "message": "正式出片必须至少绑定 1 条真实、已确认、可播放的热点 Hook。",
            "required": {"hotspot_video": 1},
            "next_action": "返回热点审核台，先选择与主题相关的 Hook，再创建视频项目。",
        })
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
                                  target_duration_ms=body.target_duration_ms, chain_mode=chain_mode),
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
    chain_mode = body.chain_mode or brief.get("chain_mode") or "hotspot_owned"
    if chain_mode == "owned_only":
        raise HTTPException(409, {
            "message": "正式出片必须至少绑定 1 条真实、已确认、可播放的热点 Hook；不再支持自有素材直出兜底。",
            "required": {"hotspot_video": 1},
            "next_action": "先绑定至少 1 条相关热点 Hook，再重新创建视频项目。",
        })
    event = db.get_hotspot_event_clip(body.hotspot_event_id) if body.hotspot_event_id else None
    if event:
        event = _with_soft_logistics_bridge(event)
    if not event:
        raise HTTPException(404, "热点事件不存在")
    requested_hook_event_ids = list(dict.fromkeys(
        int(event_id) for event_id in body.approved_hook_event_ids if int(event_id) > 0
    ))
    approved_hook_event_ids: list[int] = []
    source_hotspot: dict = {}
    related_events: list[dict] = []
    if requested_hook_event_ids:
        approved_hook_event_ids = requested_hook_event_ids
        if int(event["id"]) not in approved_hook_event_ids:
            approved_hook_event_ids.insert(0, int(event["id"]))
        if not 1 <= len(approved_hook_event_ids) <= 2:
            raise HTTPException(409, "正式出片必须锁定一至两段已确认 Hook。")
        locked_events = []
        for event_id in approved_hook_event_ids:
            clip = db.get_hotspot_event_clip(event_id)
            locked_events.append(_with_soft_logistics_bridge(clip) if clip else None)
    else:
        # The primary Hook is still a hard binding.  An empty explicit list only
        # means “let the planner add one complementary Hook from this same
        # confirmed source”; it must not narrow the planner to the primary clip.
        locked_events = [event]
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
    requested_topic = str(
        brief.get("raw_input") or brief.get("subject") or brief.get("angle") or "南非物流"
    ).strip()
    binding_assessment = _hook_binding_assessment(requested_topic, locked_events)
    hook_binding_mode = binding_assessment["mode"]
    source_hotspot = db.get_hotspot(int(event["hotspot_id"])) or {}
    related_events = [
        _with_soft_logistics_bridge(item)
        for item in db.list_hotspot_event_clips(asset_id=event.get("asset_id"), hotspot_id=event.get("hotspot_id"))
    ]
    # 批18：并入跨父已确认事件——chat 流允许锁不同父的 Hook，planner 之前静默丢弃。
    if approved_hook_event_ids:
        known_ids = {int(e.get("id") or 0) for e in related_events}
        for clip_id in approved_hook_event_ids:
            clip = db.get_hotspot_event_clip(int(clip_id))
            clip = _with_soft_logistics_bridge(clip) if clip else None
            if clip and int(clip.get("id") or 0) not in known_ids and _is_confirmed_renderable_hotspot_hook(clip):
                related_events.append(clip)
    owned_segments = [item for item in db.list_asset_segments(limit=20_000) if not item.get("asset_hotspot_id")]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
    ]
    planning_brief = hotspot_logistics_planner.build_brief(
        {**source_hotspot, **event} if event else {}, owned_segments, brief
    )
    # The user's topic is the immutable editorial contract.  A Hook may enrich
    # the opener and logistics question, but must never replace the title/topic
    # requirement used by scripting, resume or quality review.
    planning_brief["logistics_topic"] = requested_topic
    planning_brief["requested_topic"] = requested_topic
    planning_brief["topic_contract"] = video_topic_contract.build_topic_contract(
        requested_topic,
        has_event_anchor=True,
    )
    planning_brief["hook_binding_mode"] = hook_binding_mode
    planning_brief["hook_compatibility_issues"] = binding_assessment["issues"]
    if event:
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
    planner_rescue_reason = ""
    try:
        scenes = hotspot_video_planner.plan_followup_scenes(
            planning_brief, related_events, owned_segments, target_duration_ms=base_duration_ms,
            owned_images=owned_images,
            allow_adaptation=True,
            chain_mode=chain_mode,
        )
    except ValueError as exc:
        scenes = []
        planner_rescue_reason = str(exc)[:240]

    # Topic-specific indexing can be sparse even when the owned library has
    # enough unique, reviewed warehouse and delivery footage.  A production
    # request must not stop at that retrieval miss.  Retry once with the same
    # immutable user topic and Hook, but widen only the *visual* roles to the
    # common Buffalo operating chain.  The copy contract is unchanged, so the
    # fallback cannot pretend a generic warehouse shot proves the exact topic.
    planned_content_ms = sum(int(item.get("duration_ms") or 0) for item in scenes)
    if not scenes or planned_content_ms + endcard_duration_ms < video_renderer.FORMAL_MIN_DURATION_MS:
        rescue_brief = {
            **planning_brief,
            "logistics_nodes": list(dict.fromkeys(
                list(planning_brief.get("logistics_nodes") or [])
                + ["仓储", "入库", "分拣", "交接", "配送", "运输"]
            )),
            "required_evidence": {
                **(planning_brief.get("required_evidence") or {}),
                "owned_video": "adaptive",
            },
        }
        try:
            scenes = hotspot_video_planner.plan_followup_scenes(
                rescue_brief,
                related_events,
                owned_segments,
                target_duration_ms=50_000,
                owned_images=owned_images,
                allow_adaptation=True,
                chain_mode=chain_mode,
            )
            planner_rescue_reason = planner_rescue_reason or "topic_specific_media_capacity_below_50s"
            logger.warning(
                "主题素材规划降级为通用 Buffalo 作业链: brief=%s reason=%s",
                brief_id,
                planner_rescue_reason,
            )
        except ValueError as rescue_exc:
            logger.error(
                "双素材视频规划及通用作业链兜底均失败: brief=%s initial=%s rescue=%s",
                brief_id,
                planner_rescue_reason,
                rescue_exc,
            )
            raise HTTPException(409, {
                "message": "真实 Hook 或本地可播放素材已实际不可用，自动修复后仍无法形成成片。",
                "reason": str(rescue_exc)[:240],
                "next_action": "请恢复至少一条可播放真实 Hook；系统会自动使用现有 Buffalo 视频和图片完成其余镜头。",
            }) from rescue_exc
    content_scenes = scenes
    hotspot_count = sum(item.get("evidence_type") == "hotspot_video" for item in content_scenes)
    owned_count = sum(
        item.get("evidence_type") == "owned_video"
        and str(item.get("asset_source") or "") != "za_stock_license"
        for item in content_scenes
    )
    za_stock_count = sum(
        str(item.get("asset_source") or "") == "za_stock_license"
        for item in content_scenes
    )
    image_count = sum(item.get("evidence_type") == "image" for item in content_scenes)
    adaptation = hotspot_video_planner.describe_plan_adaptation(content_scenes)
    if planner_rescue_reason:
        adaptation["adapted"] = True
        adaptation["strategies"] = list(dict.fromkeys(
            list(adaptation.get("strategies") or []) + ["broad_owned_visual_rescue"]
        ))
        adaptation["planner_rescue_reason"] = planner_rescue_reason
    # The endcard visual is deterministic, but its copy belongs to the same
    # MiniMax request as every other beat. Appending it before context/limits
    # are built prevents a fixed sentence from overwriting a successful model
    # script after the remote call returns.
    scenes = hotspot_video_planner.append_brand_endcard_scenes(
        content_scenes, context=planning_brief,
    )
    final_planned_duration_ms = sum(
        int(item.get("duration_ms") or 0) for item in scenes
    )
    # Hook is the hard gate. Thin owned inventory is adaptation, not a block.
    if hotspot_count < 1 or not scenes:
        raise HTTPException(409, {
            "message": "证据不足：正式出片至少需要 1 条真实热点 Hook 镜头，不能生成成片。",
            "coverage": {
                "hotspot_video": hotspot_count,
                "owned_video": owned_count,
                "za_stock": za_stock_count,
                "image": image_count,
                "duration_ms": final_planned_duration_ms,
            },
            "required": {"hotspot_video": 1, "owned_video": "adaptive"},
            "adaptation": adaptation,
            "next_action": "重新锁定强相关热点 Hook，或换用已确认可渲染的事件片段。",
        })
    if final_planned_duration_ms < video_renderer.FORMAL_MIN_DURATION_MS:
        raise HTTPException(409, _formal_duration_insufficient_detail(
            final_duration_ms=final_planned_duration_ms,
            coverage={
                "hotspot_video": hotspot_count,
                "owned_video": owned_count,
                "za_stock": za_stock_count,
                "image": image_count,
                "duration_ms": final_planned_duration_ms,
            },
            adaptation=adaptation,
            chain_mode=chain_mode,
        ))
    # Remote copy generation can improve wording, but it is not an
    # availability gate.  Keep the deterministic scene bounds ready before
    # the call so a missing key, timeout, malformed JSON or two failed repairs
    # can all fall back to an evidence-bounded local script.
    voiceover_limits = [_scene_voiceover_max_chars(scene) for scene in scenes]
    voiceover_minimums = [_scene_voiceover_min_chars(scene) for scene in scenes]
    context = _compact_topic_evidence(brief, event, scenes)
    if chain_mode == "owned_only":
        hotspot_story_contract = (
            "全片只描述 Buffalo 镜头可见动作，不引用任何未提供的热点事实，也不得伪装为热点追更。"
            "第1段必须使用 brief.topic_anchor_contract.opening_hook，承担主题型开场；"
            "第2段必须使用 opening_bridge，明确展开原主题。后续每段只讲一个与原主题相关的可见动作。"
        )
        hotspot_quota_line = "无热点 Hook；全片使用自有镜头。"
        fact_requirement_line = (
            "owned_only 没有 facts；禁止虚构事件。第1、2段分别锁定主题开场和主题桥接，"
            "其余段落必须持续回应用户原主题。"
        )
    elif hotspot_count == 1:
        hotspot_story_contract = (
            "叙事开场只有第1段热点 Hook：必须用允许 Hook 的 what_happened 明确说明发生了什么，"
            "不能只说‘现场正在发生’、只复述标题或只抛一个问题。第1段只能描述允许 Hook 中可见或已给出的热点事实。"
            "第1个自有镜头不是无意义转场，必须承担营销桥接：先说明该事实为什么会影响运输、存储或配送安全，"
            "再把 Buffalo 镜头里的一个可见动作接上，并明确该动作体现的品牌优势；禁止把‘镜头转到仓内’作为完整旁白。"
        )
        hotspot_quota_line = f"允许分镜只有 {hotspot_count} 个热点 Hook；不得凭空补出其他热点事实。"
        fact_requirement_line = (
            "第1段必须引用 facts.what_happened 的可见事实；第1个自有镜头必须完成"
            "‘事件事实→物流安全问题→Buffalo可见动作→品牌优势’的桥接。"
        )
    else:
        hotspot_story_contract = (
            "前两段是同一事件的热点事实：第1段前两秒给出强现场事实和卖家问题，第2段只补充同一现场可见情况。"
            "前两段只能描述允许 Hook 中可见或已给出的热点事实；第2段不得写卖家已经采取了什么动作。"
            "第1个自有镜头必须承担事件到物流安全动作的营销桥接，并把动作转成 Buffalo 的一个可见品牌优势；禁止把‘镜头转到仓内’作为完整旁白。"
        )
        hotspot_quota_line = f"允许分镜只有 {hotspot_count} 个热点 Hook；不得凭空补出其他热点事实。"
        fact_requirement_line = (
            "第1段必须引用 facts.what_happened 的可见事实；第1个自有镜头必须完成"
            "‘事件事实→物流安全问题→Buffalo可见动作→品牌优势’的桥接。"
        )
    if hook_binding_mode != _HOOK_BINDING_EXACT:
        hotspot_story_contract = (
            "第1段仍必须如实说明已锁定热点 Hook 发生了什么，但它只是真实行业现场和注意力引子，"
            "不得宣称它直接证明、导致或代表用户当前主题。"
            "第1个 Buffalo 自有镜头必须明确说明‘外部现场只作提醒’，再回到用户原主题，"
            "通过可见的核对、分拣、仓配或交接动作展示 Buffalo 的稳定性和可核对优势。"
        )
        fact_requirement_line = (
            "第1段只陈述 facts.what_happened；后续必须把该现场标明为行业提醒而非主题证据，"
            "然后回到用户原主题和 Buffalo 可见动作。"
        )
    scene_count_line = f"必须严格输出 {len(scenes)} 个分镜，分镜条数与 allowed_scenes 完全一致，不得多不得少。"
    messages = [
        {"role": "system", "content": (
            "你是南非跨境物流短视频策划。只依据提供的事实和允许分镜生成一条 50–90 秒抖音文案。"
            + hotspot_story_contract
            + hotspot_quota_line
            + "热点事实不得写‘堵死’、全面瘫痪、完全停摆或全线停摆等原始事实未证实的夸张断言。"
            "Buffalo 只描述镜头可见的动作，不能把热点当作品牌服务证明；但必须把该动作转成一个有证据的品牌优势，如风险前置、动作可核对、异常可留痕或交接更稳。不得复述空泛的“热点变化、提前准备、承接每一步”等套话；"
            "承接文案必须根据本次热点事实和下一镜可见动作动态写，禁止输出‘Buffalo先核对’、‘Buffalo核对做稳’、‘核对做稳’等固定句；"
            "自有镜头旁白只能描述画面可见动作；没有清关、入库前或派送前事实时，不得凭画面推断这些节点已经发生。"
            "每段必须提供新的具体信息。" + fact_requirement_line
            + "禁止使用‘镜头转到仓内’、‘先看执行现场’、‘问题摆在这里’等空转场句作为整段旁白。"
            "不得编造清关完成、时效、安全、覆盖率或客户结果。不得改变场景数量、不得推荐新素材。"
            + scene_count_line
            + "用户主题是整条视频的标题和叙事主线；热点 Hook 只能作为开场事实或外部背景，绝不能改写、取代或缩窄用户主题。"
            "先读取 brief.topic_requirements。title_requirements 和 narrative_requirements 每组至少命中一个词；同组词是合法同义表达，不要求把整组词全部重复。"
            "opening_hook 存在时，主题型第一镜必须围绕它自然展开；opening_bridge 存在时，后续必须把主题桥接到物流动作。"
            "若 brief.topic_anchor_contract 存在，标题必须命中其 title_groups 每组至少一个词；旁白必须展开其 narrative_groups 每组至少一个词。"
            "不满足时不要改写成热点标题，必须按原用户主题重写标题和旁白。"
            "每个允许分镜中的 voiceover_max_chars 和 voiceover_min_chars 都是硬边界（null 的品牌 CTA 除外）。旁白必须落在两者之间：不能超出真实画面，也不能过短而留下无声的真实画面。请用事实、可见动作或条件式核对问题自然补足，不得用空泛口号填充。"
            "最后一个 role=brand_cta 镜头也必须由你针对本次主题动态写：点名 Buffalo，承接本片已经出现的物流影响或可见动作，并收束成一个具体品牌优势。不得照抄固定结束语，不得编造服务结果或承诺。"
            + douyin_copywriting_sop.prompt_for_video_planner()
            + "只返回 JSON：{\"title\":\"\",\"angle\":\"\",\"scenes\":[{\"voiceover\":\"\",\"text_overlay\":\"\"}]}。"
        )},
        {"role": "user", "content": _json.dumps(context, ensure_ascii=False)},
    ]
    job_id = model_router.route_scoped_job_id(
        f"topic-plan-{brief_id[:12]}-{_uuid4().hex[:12]}", "planner_text"
    )
    planner_output_budget = min(
        1_800,
        max(1, int(model_router.get_route("planner_text").get("max_tokens") or 1_800)),
    )
    model_router.create_budget(
        job_id, max_calls=9, max_input_tokens=60_000,
        max_output_tokens=9 * model_router.required_output_budget("planner_text", planner_output_budget),
    )
    try:
        if not model_router.key_is_available("planner_text"):
            raise RuntimeError("planner_text 模型当前不可用")
        result = await model_router.call_text(
            job_id, "planner_text", messages, prompt_version="topic-brief-video-plan-v14",
            max_output_tokens=planner_output_budget,
            cacheable=model_router.planner_plan_is_cacheable,
        )
        try:
            generated = _planner_json(
                result["content"], len(scenes), voiceover_limits,
                voiceover_minimums, hotspot_scene_count=hotspot_count,
            )
            generated = _stamp_copy_source(generated, "model")
            generated = _repair_generated_topic_contract(
                generated,
                brief=brief,
                scenes=scenes,
                event=event,
                voiceover_minimums=voiceover_minimums,
                voiceover_limits=voiceover_limits,
            )
            _validate_formal_copy_specificity(generated)
            _validate_complete_formal_voiceovers(generated)
            _validate_generated_topic_anchor(
                generated, brief, has_event_anchor=bool(event),
            )
            _validate_formal_narrative(generated, scenes, event)
        except ValueError as initial_error:
            logger.warning(
                "内容规划校验失败: role=planner_text model=%s prompt_version=%s validation=%s retry=%s cache_hit=%s",
                model_router.get_route("planner_text").get("model"),
                "topic-brief-video-plan-v14",
                _planner_validation_kind(initial_error),
                0,
                bool(result.get("cache_hit")),
            )
            # The planner selected no file references, so one bounded rewrite can
            # safely repair malformed JSON or a short-scene narration overflow
            # without changing the approved Hook pair or the evidence plan.
            repair_messages = [
                {
                    "role": "system",
                    "content": (
                        "你是短视频脚本 JSON 修复器。只返回完整 JSON，不要解释。"
                        "保留既定分镜数量、顺序、事实边界和所有旁白字数上下限；"
                        + scene_count_line
                        + "不得推荐或选择新素材，不得使用信息图、地图、流程图或文字卡。"
                        "先读取 topic_requirements；title_requirements 和 narrative_requirements 每组至少命中一个同义词，不能因为没有复述某个原词就判定主题缺失。"
                        "opening_hook/opening_bridge 是主题型开场与桥接要求，必须自然落实到对应分镜。"
                        "逐段读取 allowed_scenes 的字数上下限。短句必须改成完整、自然且与该镜头可见动作相关的句子；"
                        + fact_requirement_line
                        + "最后一个 role=brand_cta 镜头必须针对本片动态重写，点名 Buffalo，承接物流影响或可见动作并落到具体品牌优势；不得照抄固定结束语。"
                        + "不得保留‘镜头转到仓内’、‘先看执行现场’、‘先核对清单’、‘配送节奏要稳’这类空转场或脱离画面的短口号，也不得用‘请核对订单信息’补字。"
                        + "必须根据本次热点事实和下一镜可见动作重写营销承接，依次交代物流影响、Buffalo可见动作和品牌优势；禁止使用‘Buffalo先核对’、‘Buffalo核对做稳’、‘核对做稳’等固定句。"
                        + douyin_copywriting_sop.prompt_for_video_planner()
                    ),
                },
                {
                    "role": "user",
                    "content": _json.dumps(
                        _planner_repair_payload(context, initial_error, result["content"]),
                        ensure_ascii=False,
                    ),
                },
            ]
            repair_result = None
            repair_error = initial_error
            invalid_draft = result["content"]
            salvage_candidates = [(
                result["content"],
                "model",
                "model_initial_duration_compaction",
            )]
            for repair_attempt in range(2):
                repair_messages[1]["content"] = _json.dumps(
                    _planner_repair_payload(context, repair_error, invalid_draft),
                    ensure_ascii=False,
                )
                repair_prompt_version = "topic-brief-video-plan-v14-repair"
                repair_result = await model_router.call_text(
                    job_id, "planner_text", repair_messages,
                    prompt_version=repair_prompt_version,
                    max_output_tokens=planner_output_budget,
                    use_cache=False,
                    cacheable=model_router.planner_plan_is_cacheable,
                )
                try:
                    generated = _planner_json(
                        repair_result["content"], len(scenes), voiceover_limits,
                        voiceover_minimums, hotspot_scene_count=hotspot_count,
                    )
                    generated = _stamp_copy_source(
                        generated,
                        "repair",
                        reason=f"model_validation_retry_{repair_attempt + 1}",
                    )
                    generated = _repair_generated_topic_contract(
                        generated,
                        brief=brief,
                        scenes=scenes,
                        event=event,
                        voiceover_minimums=voiceover_minimums,
                        voiceover_limits=voiceover_limits,
                    )
                    _validate_formal_copy_specificity(generated)
                    _validate_complete_formal_voiceovers(generated)
                    _validate_generated_topic_anchor(
                        generated, brief, has_event_anchor=bool(event),
                    )
                    _validate_formal_narrative(generated, scenes, event)
                    break
                except ValueError as exc:
                    logger.warning(
                        "内容规划校验失败: role=planner_text model=%s prompt_version=%s validation=%s retry=%s cache_hit=%s",
                        model_router.get_route("planner_text").get("model"),
                        repair_prompt_version,
                        _planner_validation_kind(exc),
                        repair_attempt + 1,
                        bool(repair_result.get("cache_hit")),
                    )
                    repair_error = exc
                    invalid_draft = repair_result["content"]
                    salvage_candidates.append((
                        invalid_draft,
                        "repair",
                        f"model_validation_retry_{repair_attempt + 1}",
                    ))
            else:
                # A reachable MiniMax endpoint is not "unavailable" merely
                # because its otherwise-valid copy overruns one measured beat.
                # Preserve the latest model draft and compact only trailing
                # clauses.  Category templates remain reserved for actual
                # transport/auth/model-service failures handled below.
                # A later repair can regress while an earlier MiniMax draft is
                # already factually complete and only too long.  Audit all
                # model-authored candidates, newest first, and retain the
                # first one that passes the full fact/topic/bridge contract
                # after model-preserving compaction.
                salvage_failure: ValueError | None = None
                generated = None
                for draft, draft_source, draft_reason in reversed(salvage_candidates):
                    try:
                        generated = _salvage_remote_formal_script(
                            draft,
                            brief=brief,
                            scenes=scenes,
                            event=event,
                            voiceover_minimums=voiceover_minimums,
                            voiceover_limits=voiceover_limits,
                            hotspot_count=hotspot_count,
                            source=draft_source,
                            reason=draft_reason,
                        )
                        break
                    except ValueError as exc:
                        salvage_failure = exc
                if generated is not None:
                    repair_result = {
                        **(repair_result or {}),
                        "content": _json.dumps(generated, ensure_ascii=False),
                        "cache_hit": False,
                        "usage": {
                            **((repair_result or {}).get("usage") or {}),
                            "model_duration_compaction": True,
                            "deterministic_fallback": False,
                        },
                    }
                    logger.info(
                        "MiniMax 文案保留成功，仅压缩超出镜头时长的尾句: brief=%s validation=%s",
                        brief_id, repair_error,
                    )
                else:
                    bridge_base = None
                    rewrite_indices = None
                    bridge_index = None
                    bridge_base_failure: ValueError | None = salvage_failure
                    for draft, draft_source, draft_reason in reversed(salvage_candidates):
                        try:
                            bridge_base, rewrite_indices, bridge_index = _prepare_remote_formal_script_for_bridge(
                                draft,
                                brief=brief,
                                scenes=scenes,
                                event=event,
                                voiceover_minimums=voiceover_minimums,
                                voiceover_limits=voiceover_limits,
                                hotspot_count=hotspot_count,
                                source=draft_source,
                                reason=draft_reason,
                            )
                            break
                        except ValueError as exc:
                            bridge_base_failure = exc
                            logger.warning(
                                "MiniMax 镜头专项修订准备失败: brief=%s source=%s validation=%s",
                                brief_id,
                                draft_source,
                                _planner_validation_kind(exc),
                            )
                    if bridge_base is None or not rewrite_indices or bridge_index is None:
                        raise bridge_base_failure or repair_error
                    bridge_window = _owned_bridge_window_indices(scenes)
                    targeted_scenes = []
                    for rewrite_index in rewrite_indices:
                        locked_scene = scenes[rewrite_index]
                        targeted_scenes.append({
                            "scene": rewrite_index + 1,
                            "role": str(locked_scene.get("scene_role") or ""),
                            "visual": str(
                                locked_scene.get("copy_anchor")
                                or locked_scene.get("visual")
                                or ""
                            )[:240],
                            "allowed_visible_action_terms": _visible_action_terms_for_scene(
                                locked_scene
                            ),
                            "current_minimax_voiceover": str(
                                bridge_base["scenes"][rewrite_index].get("voiceover") or ""
                            ),
                            "voiceover_min_chars": voiceover_minimums[rewrite_index],
                            "voiceover_max_chars": voiceover_limits[rewrite_index],
                            "is_first_buffalo_bridge": rewrite_index == bridge_index,
                            "bridge_sequence_position": (
                                bridge_window.index(rewrite_index) + 1
                                if rewrite_index in bridge_window
                                else None
                            ),
                        })
                    bridge_context = {
                        "topic": str(
                            brief.get("raw_input")
                            or brief.get("requested_topic")
                            or brief.get("subject")
                            or ""
                        )[:300],
                        "hook_fact": _selected_hotspot_fact(scenes, event),
                        "logistics_question": str(
                            (((event or {}).get("evidence") or {}).get("logistics_question") or "")
                        )[:240],
                        "scenes_to_rewrite": targeted_scenes,
                    }
                    bridge_messages = [
                        {
                            "role": "system",
                            "content": (
                                "你只重写 scenes_to_rewrite 指定的短视频镜头，不改其他镜头。"
                                "每条在 allowed_visible_action_terms 非空时，必须直接使用其中至少一个词，"
                                "严格贴合锁定画面，不得添加镜头未证明的动作或服务承诺。"
                                "bridge_sequence_position=1 的镜头写热点事实带来的具体物流影响、"
                                "锁定镜头真实可见动作，并点名 Buffalo。"
                                "bridge_sequence_position=2 的镜头必须写该镜头真实可见动作及其体现的"
                                " Buffalo 品牌优势，例如更稳、可追踪、留痕、更可控、减少差错中的自然一种，"
                                "但不得照抄示例或套用固定句。"
                                "如果输入只有 bridge_sequence_position=1，则这一句必须同时写出品牌优势。"
                                "两个承接镜合起来必须形成‘物流影响→Buffalo 可见动作→品牌优势’。"
                                "其他镜头围绕原主题，只讲该镜头可见动作及其带来的具体价值。"
                                "role=brand_cta 的镜头必须点名 Buffalo，承接本片已经出现的物流影响或可见动作，"
                                "再收束到一个具体品牌优势；不得照抄固定结束语，也不得编造服务结果。"
                                "禁止‘Buffalo先核对’、‘Buffalo核对做稳’、‘核对做稳’等固定句。"
                                "每条严格满足各自字数上下限，写成一句完整、自然、适合抖音口播的中文。"
                                "每句必须以句号、问号或感叹号结尾；不得以逗号、顿号、连接词、"
                                "‘可’或‘去’等残词结尾。"
                                "只返回 JSON：{\"scenes\":[{\"scene\":2,\"voiceover\":\"\",\"text_overlay\":\"\"}]}；"
                                "scene 编号必须与输入完全一致，不得缺少或增加。"
                            ),
                        },
                        {"role": "user", "content": _json.dumps(bridge_context, ensure_ascii=False)},
                    ]
                    targeted_specs = {
                        int(spec["scene"]) - 1: spec for spec in targeted_scenes
                    }
                    accepted_rewrites: dict[int, dict] = {}
                    pending_rewrites = set(rewrite_indices)
                    targeted_errors: dict[int, ValueError] = {}
                    bridge_result = None
                    previous_failure_signature: tuple[tuple[int, str], ...] | None = None
                    # Valid beats are frozen after each pass.  Six bounded
                    # passes let MiniMax focus on the shrinking remainder;
                    # this is still model-authored repair, never stock-copy
                    # substitution.  Production traces commonly converged
                    # from six pending beats to one after the third pass.
                    targeted_max_attempts = 6
                    for targeted_attempt in range(1, targeted_max_attempts + 1):
                        attempt_context = {
                            **bridge_context,
                            "scenes_to_rewrite": [
                                targeted_specs[index]
                                for index in sorted(pending_rewrites)
                            ],
                        }
                        if targeted_errors:
                            attempt_context["previous_validation_errors"] = {
                                str(index + 1): str(error)[:240]
                                for index, error in targeted_errors.items()
                                if index in pending_rewrites
                            }
                        bridge_messages[1]["content"] = _json.dumps(
                            attempt_context,
                            ensure_ascii=False,
                        )
                        candidate_result = await model_router.call_text(
                            job_id,
                            "planner_text",
                            bridge_messages,
                            prompt_version="topic-brief-video-targeted-rewrite-v9",
                            max_output_tokens=min(960, 240 + len(pending_rewrites) * 180),
                            json_mode=True,
                            use_cache=False,
                        )
                        try:
                            candidate_rows = _parse_model_scene_rewrites(
                                candidate_result["content"]
                            )
                            received_rows: dict[int, dict] = {}
                            for row in candidate_rows:
                                if not isinstance(row, dict):
                                    raise ValueError("MiniMax 镜头专项修订包含无效分镜")
                                try:
                                    row_index = int(row.get("scene")) - 1
                                except (TypeError, ValueError) as exc:
                                    raise ValueError("MiniMax 镜头专项修订缺少分镜编号") from exc
                                if row_index in received_rows:
                                    raise ValueError("MiniMax 镜头专项修订包含重复分镜")
                                received_rows[row_index] = row
                            if set(received_rows) != pending_rewrites:
                                raise ValueError("MiniMax 镜头专项修订未完整覆盖本轮指定分镜")
                        except ValueError as exc:
                            targeted_errors = {
                                index: exc for index in pending_rewrites
                            }
                            logger.warning(
                                "MiniMax 镜头专项修订结构未通过: brief=%s attempt=%s/%s validation=%s pending=%s",
                                brief_id,
                                targeted_attempt,
                                targeted_max_attempts,
                                _planner_validation_kind(exc),
                                ",".join(str(index + 1) for index in sorted(pending_rewrites)),
                            )
                            failure_signature = _targeted_repair_failure_signature(
                                pending_rewrites, targeted_errors,
                            )
                            if failure_signature and failure_signature == previous_failure_signature:
                                logger.warning(
                                    "MiniMax 镜头专项修订连续无进展，提前切换证据脚本: brief=%s attempt=%s signature=%s",
                                    brief_id,
                                    targeted_attempt,
                                    failure_signature,
                                )
                                raise next(iter(targeted_errors.values()))
                            previous_failure_signature = failure_signature
                            continue

                        failed_this_round: dict[int, ValueError] = {}
                        for index in sorted(pending_rewrites):
                            try:
                                accepted_rewrites[index] = _normalize_model_scene_rewrite_row(
                                    received_rows[index],
                                    index=index,
                                    bridge_index=bridge_index,
                                    scenes=scenes,
                                    voiceover_minimums=voiceover_minimums,
                                    voiceover_limits=voiceover_limits,
                                )
                            except ValueError as exc:
                                failed_this_round[index] = exc
                                accepted_rewrites.pop(index, None)
                        pending_rewrites = set(failed_this_round)
                        targeted_errors = failed_this_round
                        if pending_rewrites:
                            logger.warning(
                                "MiniMax 镜头专项修订部分未通过: brief=%s attempt=%s/%s pending=%s validations=%s",
                                brief_id,
                                targeted_attempt,
                                targeted_max_attempts,
                                ",".join(str(index + 1) for index in sorted(pending_rewrites)),
                                ",".join(
                                    f"{index + 1}:{_planner_validation_kind(error)}"
                                    for index, error in sorted(targeted_errors.items())
                                ),
                            )
                            failure_signature = _targeted_repair_failure_signature(
                                pending_rewrites, targeted_errors,
                            )
                            if failure_signature and failure_signature == previous_failure_signature:
                                logger.warning(
                                    "MiniMax 镜头专项修订连续无进展，提前切换证据脚本: brief=%s attempt=%s signature=%s",
                                    brief_id,
                                    targeted_attempt,
                                    failure_signature,
                                )
                                raise next(iter(targeted_errors.values()))
                            previous_failure_signature = failure_signature
                            continue

                        combined_content = _json.dumps({
                            "scenes": [
                                accepted_rewrites[index]
                                for index in sorted(rewrite_indices)
                            ],
                        }, ensure_ascii=False)
                        try:
                            generated = _apply_model_scene_rewrites(
                                bridge_base,
                                combined_content,
                                rewrite_indices=rewrite_indices,
                                bridge_index=bridge_index,
                                scenes=scenes,
                                brief=brief,
                                event=event,
                                voiceover_minimums=voiceover_minimums,
                                voiceover_limits=voiceover_limits,
                                hotspot_count=hotspot_count,
                            )
                        except ValueError as exc:
                            bridge_retry = {
                                index for index in bridge_window
                                if index in rewrite_indices
                            } or set(rewrite_indices)
                            pending_rewrites = bridge_retry
                            targeted_errors = {
                                index: exc for index in pending_rewrites
                            }
                            for index in pending_rewrites:
                                accepted_rewrites.pop(index, None)
                            logger.warning(
                                "MiniMax 镜头专项修订聚合未通过: brief=%s attempt=%s/%s validation=%s retry_scenes=%s",
                                brief_id,
                                targeted_attempt,
                                targeted_max_attempts,
                                _planner_validation_kind(exc),
                                ",".join(str(index + 1) for index in sorted(pending_rewrites)),
                            )
                            failure_signature = _targeted_repair_failure_signature(
                                pending_rewrites, targeted_errors,
                            )
                            if failure_signature and failure_signature == previous_failure_signature:
                                logger.warning(
                                    "MiniMax 镜头专项修订连续无进展，提前切换证据脚本: brief=%s attempt=%s signature=%s",
                                    brief_id,
                                    targeted_attempt,
                                    failure_signature,
                                )
                                raise next(iter(targeted_errors.values()))
                            previous_failure_signature = failure_signature
                            continue

                        bridge_result = candidate_result
                        repair_result = {
                            **bridge_result,
                            "content": _json.dumps(generated, ensure_ascii=False),
                            "cache_hit": False,
                            "usage": {
                                **(bridge_result.get("usage") or {}),
                                "model_targeted_scene_rewrite": True,
                                "model_targeted_scene_rewrite_attempts": targeted_attempt,
                                "deterministic_fallback": False,
                            },
                        }
                        logger.info(
                            "MiniMax 镜头专项修订通过: brief=%s scenes=%s bridge=%s attempt=%s/%s",
                            brief_id,
                            ",".join(str(index + 1) for index in rewrite_indices),
                            bridge_index + 1,
                            targeted_attempt,
                            targeted_max_attempts,
                        )
                        break
                    else:
                        final_error = next(iter(targeted_errors.values()), None)
                        raise final_error or ValueError("MiniMax 镜头专项修订连续失败")
            result = {
                **repair_result,
                "cache_hit": bool(result.get("cache_hit")) and bool(repair_result.get("cache_hit")),
                "usage": {
                    "input_tokens": int((result.get("usage") or {}).get("input_tokens") or 0)
                    + int((repair_result.get("usage") or {}).get("input_tokens") or 0),
                    "output_tokens": int((result.get("usage") or {}).get("output_tokens") or 0)
                    + int((repair_result.get("usage") or {}).get("output_tokens") or 0),
                    "repair_attempted": True,
                    "deterministic_fallback": bool(
                        (repair_result.get("usage") or {}).get("deterministic_fallback")
                    ),
                },
            }
    except ValueError as exc:
        # A reachable model can still violate the immutable topic, fact or
        # duration contract.  That is a recoverable provider-output failure,
        # not a reason to strand the user's production job.  Rebuild from the
        # locked Hook fact, topic contract and reviewed Buffalo scene anchors;
        # keep the reason on every scene so the report never disguises the
        # fallback as model-authored copy.
        logger.warning(
            "MiniMax 动态文案连续校验失败，切换证据脚本: brief=%s validation=%s",
            brief_id, exc,
        )
        generated = _deterministic_formal_script(
            brief,
            scenes,
            event,
            hook_binding_mode=hook_binding_mode,
            fallback_reason=f"remote_model_output_invalid:{_planner_validation_kind(exc)}",
        )
        result = {
            "content": _json.dumps(generated, ensure_ascii=False),
            "cache_hit": False,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "deterministic_fallback": True,
                "model_output_rejected": True,
            },
            "fallback_reason": str(exc)[:240],
        }
    except Exception as exc:
        logger.warning(
            "内容规划远端链路不可用，切换确定性脚本: brief=%s error=%r",
            brief_id, exc,
        )
        generated = _deterministic_formal_script(
            brief, scenes, event, hook_binding_mode=hook_binding_mode,
        )
        result = {
            "content": _json.dumps(generated, ensure_ascii=False),
            "cache_hit": False,
            "usage": {"input_tokens": 0, "output_tokens": 0, "deterministic_fallback": True},
            "fallback_reason": repr(exc)[:240],
        }
    try:
        generated = _finalize_formal_script_candidate(
            generated,
            brief=brief,
            scenes=scenes,
            event=event,
            hook_binding_mode=hook_binding_mode,
            voiceover_minimums=voiceover_minimums,
            voiceover_limits=voiceover_limits,
            hotspot_count=hotspot_count,
            allow_fallback_bridge=bool(
                (result.get("usage") or {}).get("deterministic_fallback")
            ),
        )
    except ValueError as exc:
        if not bool((result.get("usage") or {}).get("deterministic_fallback")):
            logger.warning(
                "MiniMax 文案最终门禁失败，切换证据脚本: brief=%s validation=%s",
                brief_id, exc,
            )
        else:
            logger.warning(
                "证据脚本后处理未通过，重建确定性脚本: brief=%s validation=%s",
                brief_id, exc,
            )
        generated = _deterministic_formal_script(
            brief,
            scenes,
            event,
            hook_binding_mode=hook_binding_mode,
            fallback_reason=f"final_copy_gate_rescue:{_planner_validation_kind(exc)}",
        )
        generated = _finalize_formal_script_candidate(
            generated,
            brief=brief,
            scenes=scenes,
            event=event,
            hook_binding_mode=hook_binding_mode,
            voiceover_minimums=voiceover_minimums,
            voiceover_limits=voiceover_limits,
            hotspot_count=hotspot_count,
            allow_fallback_bridge=True,
        )
        result["cache_hit"] = False
        result.setdefault("usage", {})["deterministic_fallback"] = True
    # 清关 preparation 模式文案门禁：所有文案修复/字数校验之后的最后一道
    # 确定性拦截。非真 customs 素材在清关节点下宣称已完成受监管结果时，
    # 不放行模型那句，回退安全准备式模板，确保过度宣称无法进入渲染。
    before_overclaim = {**generated, "scenes": [dict(item) for item in generated.get("scenes") or []]}
    overclaim_guard_nodes = _immutable_topic_guard_nodes(planning_brief)
    overclaim_records = hotspot_preview_narration.apply_overclaim_guard(
        generated["scenes"], scenes, overclaim_guard_nodes,
    )
    # MiniMax and MiniMax-repair copy is not rewritten here. The remaining
    # deterministic normalization is reserved for the explicit offline path.
    is_deterministic_fallback = bool(
        (result.get("usage") or {}).get("deterministic_fallback")
    )
    if is_deterministic_fallback:
        generated = _repair_repeated_formal_voiceovers(
            generated, scenes, voiceover_minimums, voiceover_limits,
        )
        generated = _enforce_generated_topic_opening(generated, brief, scenes, event)
        generated = _repair_formal_narrative_hook(generated, scenes, event)
        generated = _repair_formal_narrative_bridge(
            generated, scenes, hook_binding_mode=hook_binding_mode,
        )
        generated = _repair_repeated_formal_voiceovers(
            generated, scenes, voiceover_minimums, voiceover_limits,
        )
        generated = _repair_dangling_formal_voiceovers(
            generated, scenes, voiceover_minimums, voiceover_limits,
        )
        generated = _repair_formal_narrative_hook(generated, scenes, event)
    generated = _annotate_copy_revisions(
        before_overclaim,
        generated,
        reason="post_model_policy_guard",
    )
    try:
        _validate_formal_copy_specificity(generated)
        _validate_complete_formal_voiceovers(generated)
        _validate_generated_topic_anchor(
            generated, brief, has_event_anchor=bool(event),
        )
        _validate_formal_narrative(generated, scenes, event)
    except ValueError as exc:
        if not is_deterministic_fallback:
            logger.warning(
                "MiniMax 文案经安全规则修订后未通过门禁，切换证据脚本: brief=%s validation=%s",
                brief_id, exc,
            )
            is_deterministic_fallback = True
        # A late policy guard can rewrite copy after the first deterministic
        # finalization. Rebuild once from immutable evidence and re-run every
        # guard in the production order. This is bounded and still ends at the
        # same hard factual/topic/narrative validation; it does not waive it.
        logger.warning(
            "最终文案门禁触发，执行一次证据脚本重建: brief=%s validation=%s",
            brief_id, exc,
        )
        generated = _deterministic_formal_script(
            brief,
            scenes,
            event,
            hook_binding_mode=hook_binding_mode,
            fallback_reason=f"post_policy_gate_rescue:{_planner_validation_kind(exc)}",
        )
        generated = _finalize_formal_script_candidate(
            generated,
            brief=brief,
            scenes=scenes,
            event=event,
            hook_binding_mode=hook_binding_mode,
            voiceover_minimums=voiceover_minimums,
            voiceover_limits=voiceover_limits,
            hotspot_count=hotspot_count,
            allow_fallback_bridge=True,
        )
        overclaim_records = hotspot_preview_narration.apply_overclaim_guard(
            generated["scenes"], scenes, overclaim_guard_nodes,
        )
        generated = _enforce_generated_topic_opening(generated, brief, scenes, event)
        generated = _repair_formal_narrative_hook(generated, scenes, event)
        generated = _repair_formal_narrative_bridge(
            generated, scenes, hook_binding_mode=hook_binding_mode,
        )
        generated = _repair_repeated_formal_voiceovers(
            generated, scenes, voiceover_minimums, voiceover_limits,
        )
        generated = _repair_dangling_formal_voiceovers(
            generated, scenes, voiceover_minimums, voiceover_limits,
        )
        generated = _repair_formal_narrative_hook(generated, scenes, event)
        try:
            _validate_formal_copy_specificity(generated)
            _validate_complete_formal_voiceovers(generated)
            _validate_generated_topic_anchor(
                generated, brief, has_event_anchor=bool(event),
            )
            _validate_formal_narrative(generated, scenes, event)
        except ValueError as final_exc:
            logger.error(
                "证据脚本重建仍未通过硬门禁: brief=%s validation=%s",
                brief_id, final_exc,
            )
            raise HTTPException(500, f"确定性内容规划失败：{final_exc}") from final_exc
        result["cache_hit"] = False
        result.setdefault("usage", {})["deterministic_fallback"] = True
    for record in overclaim_records:
        scene_index = int(record.get("scene") or 0) - 1
        if 0 <= scene_index < len(generated["scenes"]):
            final_voiceover = generated["scenes"][scene_index].get("voiceover") or ""
            record["final_voiceover"] = final_voiceover
            record["distinct_safe_repair"] = final_voiceover != record.get("replaced_voiceover")
    for scene, generated_scene in zip(scenes, generated["scenes"]):
        scene.update(generated_scene)
    for scene in scenes:
        if not scene.get("copy_source"):
            scene["copy_source"] = "fallback"
            scene["copy_repair_reason"] = "remote_model_chain_unavailable"
    copy_provenance = _copy_provenance_rows(scenes)
    duration_ms = sum(int(item.get("duration_ms") or 0) for item in scenes)
    if duration_ms < video_renderer.FORMAL_MIN_DURATION_MS:
        raise HTTPException(409, _formal_duration_insufficient_detail(
            final_duration_ms=duration_ms,
            coverage={
                "hotspot_video": hotspot_count,
                "owned_video": owned_count,
                "za_stock": za_stock_count,
                "image": image_count,
                "duration_ms": duration_ms,
            },
            adaptation=adaptation,
            chain_mode=chain_mode,
        ))
    formal_target_ms = video_renderer.resolve_formal_video_target_ms(
        snapshot={
            "target_duration_ms": body.target_duration_ms,
            **(source_snapshot or {}),
        },
        fallback=body.target_duration_ms,
    )
    title = f"{generated['title']}｜{duration_ms // 1000}秒动态视频"
    project_snapshot = {
        "topic_brief_id": brief_id, "hotspot_event_id": event["id"] if event else None, "brief": planning_brief,
        "chain_mode": chain_mode,
        "model": model_router.get_route("planner_text").get("model"), "model_cache_hit": result.get("cache_hit", False),
        "copywriting_sop": douyin_copywriting_sop.metadata(),
        "overclaim_guard": overclaim_records,
        "copy_provenance": copy_provenance,
        "adaptation": adaptation,
        "hook_binding_mode": hook_binding_mode,
        "hook_compatibility_issues": binding_assessment["issues"],
        "provenance": {
            "hotspot_video": hotspot_count,
            "owned_video": owned_count,
            "za_stock": za_stock_count,
            "chain_mode": chain_mode,
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
        "target_duration_ms": formal_target_ms,
        "formal_target_duration_ms": formal_target_ms,
        "source_type": "topic_brief_dual_library",
        "brief": {
            **planning_brief,
            "angle": generated["angle"],
            "hook_binding_mode": hook_binding_mode,
            "hook_compatibility_issues": binding_assessment["issues"],
        }, "scenes": scenes,
        "adaptation": adaptation,
        "provenance": project_snapshot["provenance"],
        "copy_provenance": copy_provenance,
    }
    tts_provider = str((source_snapshot or {}).get("tts_provider") or "")
    tts_voice = str((source_snapshot or {}).get("voice") or "")
    if target_project_id:
        existing_project = db.get_video_project(target_project_id, created_by=user["id"]) or {}
        existing_payload = ((existing_project.get("current_revision") or {}).get("payload") or {})
        existing_snapshot = _video_project_snapshot(existing_project)
        tts_provider = tts_provider or str(existing_payload.get("tts_provider") or existing_snapshot.get("tts_provider") or "")
        tts_voice = tts_voice or str(existing_payload.get("voice") or existing_snapshot.get("voice") or "")
    if tts_provider:
        revision_payload["tts_provider"] = tts_provider
        revision_payload["voice"] = tts_voice
    if target_project_id and target_revision_id:
        updated_revision = db.update_video_project_revision_payload(
            target_revision_id,
            revision_payload,
            user["id"],
            title=title,
            target_duration_ms=formal_target_ms,
        )
        if not updated_revision:
            raise HTTPException(404, "视频项目修订不存在")
        project = db.get_video_project(target_project_id, created_by=user["id"])
    else:
        project = db.create_video_project(
            created_by=user["id"], source_type="topic_brief_dual_library",
            source_snapshot=project_snapshot,
            title=title, platform=body.platform, target_duration_ms=formal_target_ms, target_orientation="portrait",
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
            "za_stock": za_stock_count,
            "image": image_count,
            "duration_ms": duration_ms,
        },
        "adaptation": adaptation,
        "overclaim_guard": overclaim_records,
        "provenance": project_snapshot["provenance"],
        "copy_provenance": copy_provenance,
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


def _formal_duration_insufficient_detail(
    *,
    final_duration_ms: int,
    coverage: dict,
    adaptation: dict,
    chain_mode: str,
) -> dict:
    if chain_mode == "owned_only":
        message = "当前 Buffalo 自有素材无法形成 50–90 秒正式成片。"
    else:
        message = "热点 Hook 已匹配，但当前素材组合无法形成 50–90 秒正式成片。"
    return {
        "message": message,
        "status": "needs_owned_media",
        "coverage": coverage,
        "required": {"duration_ms": "50000–90000"},
        "adaptation": adaptation,
        "next_action": (
            "补充至少一段未重复、每段不少于 3 秒的 Buffalo 自有视频，"
            "或重新锁定更强相关的 Hook。"
        ),
    }


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
        report = dict(job.get("quality_report") or {})
        hook_ids = [int(item) for item in snapshot.get("matched_event_clip_ids") or []]
        owned_only = str(snapshot.get("chain_mode") or "") == "owned_only" and not hook_ids
        if owned_only:
            raise RuntimeError("正式出片必须至少绑定 1 条真实、已确认、可播放的热点 Hook")
        events = [await asyncio.to_thread(db.get_hotspot_event_clip, event_id) for event_id in hook_ids]
        hook_rebound = False
        binding_mode = str(snapshot.get("hook_binding_mode") or _HOOK_BINDING_CONTEXTUAL)
        if not 1 <= len(events) <= 2 or any(
            event is None or not _is_confirmed_renderable_hotspot_hook(event)
            for event in events
        ):
            recovered = await _retrieve_confirmed_chat_hooks(
                str(snapshot.get("topic") or "南非物流"),
                int(job["created_by"]),
                str(snapshot.get("session_id") or ""),
            )
            recovered_ids = [
                int(item["event_clip_id"])
                for item in (recovered.get("hooks") or [])
                if item.get("event_clip_id")
            ][:2]
            events = [
                await asyncio.to_thread(db.get_hotspot_event_clip, event_id)
                for event_id in recovered_ids
            ]
            if not events or any(
                event is None or not _is_confirmed_renderable_hotspot_hook(event)
                for event in events
            ):
                raise RuntimeError("真实 Hook 库当前没有任何已确认且可播放的片段")
            hook_rebound = True
            binding_mode = str(recovered.get("hook_binding_mode") or _HOOK_BINDING_CONTEXTUAL)
        ordered = sorted(events, key=lambda event: int(event["start_ms"]))
        if not _is_same_confirmed_hotspot_event(ordered):
            # Keep the freshest recovered opener rather than failing because a
            # stale two-clip group partially survived retention cleanup.
            ordered = ordered[:1]
            hook_rebound = True
        if len(ordered) == 2 and int(ordered[1]["start_ms"]) < int(ordered[0]["end_ms"]):
            ordered = ordered[:1]
            hook_rebound = True
        assessed = _hook_binding_assessment(
            str(snapshot.get("topic") or "南非物流"),
            ordered,
        )
        binding_mode = str(assessed.get("mode") or binding_mode)
        report["chat_generation"] = {
            **(report.get("chat_generation") or {}),
            "locked_hook_event_ids": [int(item["id"]) for item in ordered],
            "hook_binding_mode": binding_mode,
            "hook_rebound": hook_rebound,
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
        existing_report = dict(job.get("quality_report") or {})
        chat_generation = existing_report.get("chat_generation") or {}
        hook_ids = [
            int(item) for item in (
                chat_generation.get("locked_hook_event_ids")
                or snapshot.get("matched_event_clip_ids")
                or []
            )
        ]
        chain_mode = str(snapshot.get("chain_mode") or "hotspot_owned")
        if not hook_ids or chain_mode == "owned_only":
            raise RuntimeError("正式出片必须至少绑定 1 条真实、已确认、可播放的热点 Hook")
        brief_for_chain = await asyncio.to_thread(
            db.get_topic_brief, str(snapshot.get("topic_brief_id") or ""), job["created_by"]
        )
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            progress=video_generation._STAGE_PROGRESS[video_generation.PipelineStage.SCRIPTING],
        )
        try:
            result = await _generate_topic_brief_video(
                str(snapshot["topic_brief_id"]),
                TopicBriefGenerateRequest(
                    hotspot_event_id=hook_ids[0] if hook_ids else None,
                    approved_hook_event_ids=hook_ids,
                    platform=str(project.get("platform") or snapshot.get("platform") or "douyin"),
                    target_duration_ms=video_renderer.resolve_formal_video_target_ms(
                        project=project,
                        snapshot=snapshot,
                        payload=(project.get("current_revision") or {}).get("payload") or {},
                        fallback=60_000,
                    ),
                    chain_mode=(brief_for_chain or {}).get("chain_mode") or chain_mode,
                ),
                {
                    "id": int(job["created_by"]),
                    "username": str(snapshot.get("username") or "system"),
                },
                source_snapshot={
                    **snapshot,
                    "matched_event_clip_ids": hook_ids,
                    "hook_binding_mode": str(
                        chat_generation.get("hook_binding_mode")
                        or snapshot.get("hook_binding_mode")
                        or _HOOK_BINDING_CONTEXTUAL
                    ),
                },
                target_project_id=job["project_id"],
                target_revision_id=job["revision_id"],
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                detail = detail.get("message") or str(detail)
            raise RuntimeError(str(detail)[:160]) from exc
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
    event = _with_soft_logistics_bridge(event)
    owned_segments = [
        item for item in db.list_asset_segments(limit=20_000)
        if not item.get("asset_hotspot_id")
    ]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
    ]
    related_events = [
        _with_soft_logistics_bridge(item)
        for item in db.list_hotspot_event_clips(
            asset_id=event.get("asset_id"), hotspot_id=event.get("hotspot_id")
        )
    ]
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
    if authorization_status and authorization_status not in {"authorized", "blocked"}:
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
            "authorization_status": "authorized",
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
        "authorization_status": "authorized",
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


def _hotspot_segment_has_meaningful_evidence(segment: dict) -> bool:
    """Reject title-only placeholder segments from the reusable-analysis cache."""
    if str(segment.get("transcript") or "").strip() or str(segment.get("ocr_text") or "").strip():
        return True
    if str(segment.get("primary_category_source") or "") != "model":
        return False
    description = " ".join(str(segment.get("description") or "").split()).casefold()
    asset_name = " ".join(str(segment.get("asset_name") or "").split()).casefold()
    if description and description != asset_name:
        return True
    return any(str(tag.get("source") or "") == "model" for tag in (segment.get("tags") or []))


def _has_meaningful_hotspot_analysis(segments: list[dict]) -> bool:
    return any(_hotspot_segment_has_meaningful_evidence(segment) for segment in segments)


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
            ]
        has_reusable_analysis = _has_meaningful_hotspot_analysis(reusable_segments)
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
            reusable_segments = [
                segment for segment in db.list_asset_segments(asset_id=asset["id"], limit=500)
                if segment.get("processing_version") == asset_processing.PROCESSING_VERSION
            ]
            if not _has_meaningful_hotspot_analysis(reusable_segments):
                detail = str(job.get("error") or "视觉、ASR 与 OCR 均未形成可核验证据")[:360]
                raise RuntimeError(f"热点视频分析证据为空，禁止标记 ready：{detail}")
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
                    static_root=STATIC_DIR,
                    asset_filepath=asset.get("filepath"),
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


def _validate_hotspot_source(body: dict) -> tuple[str, str, list[str], bool, str]:
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
    source_kind = str(body.get("source_kind") or "rss").strip().casefold()
    if source_kind not in {"rss", "html_index"}:
        raise HTTPException(400, "可信源类型只支持 rss 或 html_index")
    return name[:100], feed_url, domains, bool(body.get("enabled", True)), source_kind


@app.get("/api/hotspot-sources")
async def list_hotspot_sources(user=Depends(require_role(UserRole.ADMIN))):
    return db.list_hotspot_sources()


@app.post("/api/hotspot-sources", status_code=201)
async def create_hotspot_source(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    name, feed_url, domains, enabled, source_kind = _validate_hotspot_source(body)
    if enabled and len(db.list_hotspot_sources(enabled_only=True)) >= hotspot_fetcher.MAX_ENABLED_SOURCES:
        raise HTTPException(409, f"最多启用 {hotspot_fetcher.MAX_ENABLED_SOURCES} 个可信源，请先停用一个现有信源")
    try:
        source_id = db.create_hotspot_source(name, feed_url, domains, user["id"], enabled, source_kind)
    except Exception as exc:
        raise HTTPException(400, "该 Feed URL 已存在") from exc
    return {"id": source_id, "status": "ok"}


@app.put("/api/hotspot-sources/{source_id}")
async def update_hotspot_source(source_id: int, body: dict, user=Depends(require_role(UserRole.ADMIN))):
    name, feed_url, domains, enabled, source_kind = _validate_hotspot_source(body)
    current = next((item for item in db.list_hotspot_sources() if item["id"] == source_id), None)
    if not current:
        raise HTTPException(404, "可信源不存在")
    if enabled and not current["enabled"] and len(db.list_hotspot_sources(enabled_only=True)) >= hotspot_fetcher.MAX_ENABLED_SOURCES:
        raise HTTPException(409, f"最多启用 {hotspot_fetcher.MAX_ENABLED_SOURCES} 个可信源，请先停用一个现有信源")
    db.update_hotspot_source(source_id, name, feed_url, domains, enabled, source_kind)
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
    result["minimax_token_plan_key"] = bool(os.environ.get("MINIMAX_TOKEN_PLAN_KEY"))
    result["tts_provider"] = os.environ.get("TTS_PROVIDER", "mimo")
    result["mimo_tts_model"] = os.environ.get("MIMO_TTS_MODEL", "mimo-v2.5-tts")
    result["mimo_tts_voice"] = os.environ.get("MIMO_TTS_VOICE", video_renderer.MIMO_TTS_VOICE)
    result["chat_model"] = (model_router.get_route("chat_text") or {}).get("model") or "mimo-v2.5"
    result["planner_model"] = (model_router.get_route("planner_text") or {}).get("model") or "mimo-v2.5-pro"
    result["vision_model"] = (model_router.get_route("vision_tagger") or {}).get("model") or "mimo-v2.5"
    # Ready for formal video: FFmpeg + active TTS provider key.
    media_ok = bool(result.get("ffmpeg") and result.get("ffprobe"))
    active_tts = str(result["tts_provider"] or "mimo").strip().lower()
    tts_ok = bool(
        result["mimo_api_key"] if active_tts == "mimo" else result["minimax_token_plan_key"]
    )
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


def _topic_media_by_asset(events: list[dict]) -> dict[int, dict]:
    index: dict[int, dict] = {}
    for event in events:
        asset_id = int(event.get("asset_id") or 0)
        if not asset_id or asset_id in index:
            continue
        media = db.get_hotspot_media_by_asset_id(asset_id)
        if media:
            index[asset_id] = media
    return index


def _match_buckets_for_topic(topic: str) -> tuple[dict, dict]:
    normalized_topic = video_topic_contract.normalize_topic_input(topic)
    query = topic_hook_pipeline.structure_topic(normalized_topic)
    events = db.list_hotspot_event_clips()
    buckets = topic_hook_pipeline.match_topic_hooks(
        query,
        events,
        media_by_asset=_topic_media_by_asset(events),
        is_ready=_is_confirmed_renderable_hotspot_hook,
        is_audit=_is_audited_hotspot_hook,
    )
    # The score matcher may still find a generic logistics phrase through a
    # broad node. Re-check the verified event fact against the immutable topic
    # contract before exposing it as directly usable.
    for bucket_name in ("matched_ready", "matched_audit_only"):
        kept = []
        for row in buckets.get(bucket_name) or []:
            event_id = row.get("event_clip_id")
            event = db.get_hotspot_event_clip(int(event_id)) if event_id is not None else None
            issues = video_topic_contract.topic_hook_compatibility_issues(
                normalized_topic, [event] if event else [],
            )
            if issues:
                row["gaps"] = list(row.get("gaps") or []) + issues
                buckets.setdefault("unmatched", []).append(row)
            else:
                kept.append(row)
        buckets[bucket_name] = kept
    return query, buckets


_HOOK_BINDING_EXACT = "exact"
_HOOK_BINDING_ADJACENT = "logistics_adjacent"
_HOOK_BINDING_CONTEXTUAL = "contextual_attention"


def _hook_topic_direct_fit_score(topic: str, event: dict) -> int:
    """Rank grounded logistics scenes before freshness/reuse tie-breakers."""
    scenes = _grounded_chat_hook_scene_keys(event)
    if not scenes:
        return 0
    contract = video_topic_contract.build_topic_contract(topic)
    intent = str(contract.get("intent") or "")
    intent_weights = {
        "local_courier_comparison": {
            "last_mile": 100, "road": 85, "warehouse": 65,
            "disruption": 40, "border": 25, "customs": 20, "port": 10,
        },
        "same_city_delivery_sla": {
            "last_mile": 100, "road": 90, "warehouse": 60,
            "disruption": 35, "border": 20, "customs": 15, "port": 5,
        },
        "peak_overflow_response": {
            "warehouse": 100, "disruption": 85, "last_mile": 55,
            "road": 45, "port": 35, "border": 25, "customs": 20,
        },
        "peak_full_cycle_review": {
            "warehouse": 100, "last_mile": 90, "road": 80,
            "port": 65, "disruption": 55, "border": 35, "customs": 30,
        },
        "policy_change_verification": {
            "customs": 100, "border": 95, "port": 75,
            "warehouse": 45, "road": 30, "last_mile": 20, "disruption": 15,
        },
    }
    weights = intent_weights.get(intent)
    if weights is None:
        contract_nodes = {str(item).casefold() for item in (contract.get("nodes") or [])}
        node_scene_map = {
            "末端": "last_mile", "配送": "last_mile", "道路": "road",
            "运输": "road", "仓储": "warehouse", "分拣": "warehouse",
            "港口": "port", "清关": "customs", "边境": "border",
        }
        requested_scenes = {
            scene for node, scene in node_scene_map.items()
            if node.casefold() in contract_nodes
        }
        return 80 + 5 * len(scenes & requested_scenes) if scenes & requested_scenes else 20
    score = max((weights.get(scene, 0) for scene in scenes), default=0)
    # Scene labels express where a clip happened, not what business fact the
    # clip proves.  A passenger customs tutorial therefore must not outrank a
    # cargo vessel for a freight-policy topic merely because ``customs`` has a
    # nominal weight of 100.  Keep contextual Hooks available for mandatory
    # output, but cap their direct-fit score until the audited fact supports
    # the requested intent.
    if not _hook_fact_supports_exact_topic_intent(topic, event):
        contextual_caps = {
            "local_courier_comparison": 55,
            "same_city_delivery_sla": 60,
            "peak_overflow_response": 55,
            "peak_full_cycle_review": 60,
        }
        if intent == "policy_change_verification":
            evidence = event.get("evidence") or {}
            fact_text = " ".join(str(value or "") for value in (
                event.get("title_zh"), event.get("title_en"),
                evidence.get("what_happened"), evidence.get("event_identity"),
            )).casefold()
            freight_terms = (
                "货物", "货运", "货车", "卡车", "集装箱", "码头", "港口",
                "parcel", "cargo", "freight", "truck", "container", "warehouse",
            )
            # A cargo scene is a defensible contextual bridge for a policy
            # topic even when the clip itself does not prove a policy change.
            # Passenger-only customs footage is not freight evidence and must
            # rank below that bridge.
            score = min(score, 75 if any(term in fact_text for term in freight_terms) else 45)
        else:
            score = min(score, contextual_caps.get(intent, score))
    return score


def _hook_editorial_fit_score(topic: str, event: dict, parent: dict | None = None) -> int:
    """Prefer visible logistics operations over generic breaking-news footage.

    Mandatory-Hook mode may need a contextual clip when there is no exact
    event.  Freshness alone is a poor tie-breaker in that bucket: it can put a
    crime/tribute headline ahead of an older but visibly relevant freight
    operation.  This score uses only audited facts and source titles.  It does
    not claim that the clip proves the user's topic.
    """
    evidence = event.get("evidence") or {}
    audit = evidence.get("visual_audit") or {}
    fact_text = " ".join(str(value or "") for value in (
        event.get("title_zh"), event.get("title_en"),
        evidence.get("what_happened"), evidence.get("event_identity"),
        " ".join(str(item or "") for item in (audit.get("visible_objects") or [])),
        " ".join(str(item or "") for item in (audit.get("visible_actions") or [])),
    )).casefold()
    source_text = " ".join(str(value or "") for value in (
        (parent or {}).get("title_zh"), (parent or {}).get("title"),
        evidence.get("source_title"),
    )).casefold()
    intent = str(video_topic_contract.build_topic_contract(topic).get("intent") or "")
    scenes = _grounded_chat_hook_scene_keys(event)
    direct_freight_terms = (
        "快递", "配送", "派送", "末端", "包裹", "货物", "货运", "货车",
        "卡车", "厢式", "集装箱", "港口", "码头", "仓库", "仓储", "分拣",
        "courier", "delivery", "parcel", "cargo", "freight", "truck",
        "container", "warehouse",
    )
    operational_terms = (
        "行驶", "通行", "排队", "装卸", "周转", "交接", "分拣", "航行",
        "驶过", "运输", "moving", "driving", "queue", "loading", "sorting",
    )
    emergency_terms = (
        "救护车", "警车", "警员", "法医", "警戒", "担架", "葬礼", "悼念",
        "拳击", "champion", "tribute", "funeral", "forensic", "ambulance",
    )
    incident_terms = (
        "起火", "燃烧", "火焰", "浓烟", "侧翻", "碰撞", "事故", "crash", "fire",
    )
    broadcast_terms = (
        "sabcnews", "sabc news", "headlines", "south africa today",
        "what’s happening", "what's happening",
    )

    score = 0
    if any(term in fact_text for term in direct_freight_terms):
        score += 2
    if any(term in fact_text for term in operational_terms):
        score += 1
    if any(term in fact_text for term in emergency_terms):
        score -= 3
    if any(term in source_text for term in broadcast_terms):
        score -= 1

    # Comparison and service-level topics should open on an observable
    # logistics operation.  A fire/accident can illustrate resilience, but it
    # is a weaker editorial fit than ordinary freight movement or handoff.
    if intent in {"local_courier_comparison", "same_city_delivery_sla"}:
        if any(term in fact_text for term in incident_terms):
            score -= 2
        if scenes & {"border", "customs", "port"}:
            score -= 1
    return score


def _hook_fact_supports_exact_topic_intent(topic: str, event: dict) -> bool:
    """Require visible/source facts for a specific intent before calling it exact.

    A shared broad node such as ``road`` is enough for a contextual logistics
    bridge, but it does not prove a courier comparison, warehouse overflow or
    freight-policy change.  Keeping that distinction here prevents a generic
    road clip from outranking a stronger real-world fallback while preserving
    the mandatory real-Hook output path.
    """
    contract = video_topic_contract.build_topic_contract(topic)
    intent = str(contract.get("intent") or "")
    evidence = event.get("evidence") or {}
    parent = db.get_hotspot(int(event.get("hotspot_id") or 0)) or {}
    fact_text = " ".join(str(value or "") for value in (
        event.get("title_zh"), event.get("title_en"),
        evidence.get("what_happened"), evidence.get("event_identity"),
        parent.get("title_zh"), parent.get("title"),
    )).casefold()
    scenes = _grounded_chat_hook_scene_keys(event)
    courier_terms = (
        "快递", "配送", "派送", "末端", "同城", "courier", "delivery",
        "last mile", "parcel",
    )
    warehouse_terms = (
        "仓库", "仓储", "分拣", "堆积", "爆仓", "库位", "仓配", "warehouse",
        "sorting", "storage", "overflow",
    )
    freight_terms = (
        "货物", "货运", "货车", "卡车", "集装箱", "码头", "港口", "仓库",
        "分拣", "配送", "订单", "parcel", "cargo", "freight", "truck",
        "container", "warehouse", "delivery",
    )
    policy_terms = (
        "政策", "法规", "海关", "关税", "税率", "申报", "许可证", "监管",
        "policy", "regulation", "customs", "tariff", "declaration", "permit",
    )
    contains = lambda terms: any(term in fact_text for term in terms)
    if intent not in {
        "local_courier_comparison",
        "same_city_delivery_sla",
        "peak_overflow_response",
        "peak_full_cycle_review",
        "policy_change_verification",
        "custom_logistics_topic",
    }:
        return True
    if intent == "custom_logistics_topic":
        nodes = {str(item) for item in (contract.get("custom_topic_nodes") or [])}
        if "清关" in nodes:
            return contains(policy_terms)
        if "仓储" in nodes and (contains(warehouse_terms) or "warehouse" in scenes):
            return True
        if "末端" in nodes and (contains(courier_terms) or "last_mile" in scenes):
            return True
        if nodes & {"港口", "道路", "铁路"}:
            return bool(scenes)
        return False
    if intent in {"local_courier_comparison", "same_city_delivery_sla"}:
        return "last_mile" in scenes or contains(courier_terms)
    if intent == "peak_overflow_response":
        return (
            "warehouse" in scenes and contains(warehouse_terms)
        ) or "disruption" in scenes
    if intent == "peak_full_cycle_review":
        return bool(scenes & {"warehouse", "last_mile", "road", "port"}) and contains(freight_terms)
    if intent == "policy_change_verification":
        return bool(scenes & {"customs", "border", "port"}) and contains(policy_terms) and contains(freight_terms)
    return True


def _hook_binding_assessment(topic: str, events: list[dict]) -> dict:
    """Classify relevance without turning editorial fit into an availability gate."""
    # ``logistics_scenes`` on legacy rows may have been inferred from the
    # curator-authored logistics question.  That bridge is useful for copy but
    # cannot turn a border queue into warehouse footage.  Exact compatibility
    # therefore uses only source/visual facts and recomputes adjacent nodes
    # from the same grounded text.
    grounded_events = [{**event, "logistics_scenes": []} for event in events]
    issues = video_topic_contract.topic_hook_compatibility_issues(topic, grounded_events)
    if not issues and all(_hook_fact_supports_exact_topic_intent(topic, event) for event in events):
        return {
            "mode": _HOOK_BINDING_EXACT,
            "issues": [],
            "reason": "热点事实与当前主题的实体或物流节点直接匹配。",
        }
    query_nodes = {
        str(item).casefold()
        for item in (topic_hook_pipeline.structure_topic(topic).get("logistics_nodes") or [])
        if str(item).strip()
    }
    event_nodes: set[str] = set()
    for event in events:
        evidence = event.get("evidence") or {}
        fact_text = " ".join(str(value or "") for value in (
            event.get("title_zh"), event.get("title_en"),
            evidence.get("what_happened"), evidence.get("event_identity"),
        ))
        event_nodes.update(_chat_hook_event_profile(fact_text))
    if query_nodes and event_nodes and query_nodes & event_nodes:
        return {
            "mode": _HOOK_BINDING_ADJACENT,
            "issues": issues,
            "reason": "热点与主题共享真实物流节点，将作为相邻场景风险引子，不冒充主题直接证据。",
        }
    return {
        "mode": _HOOK_BINDING_CONTEXTUAL,
        "issues": issues,
        "reason": "未找到精确事件，使用最新真实物流现场作为行业注意力开场；该现场不被表述为主题直接证据。",
    }


def _force_output_hook_candidates(topic: str, *, recently_used: set[int] | None = None) -> list[dict]:
    """Return real timely Hooks ordered for exact→adjacent→latest fallback."""
    recent = {int(item) for item in (recently_used or set())}
    rows = []
    for event in db.list_hotspot_event_clips():
        if str(event.get("hook_kind") or "timely_event") != "timely_event":
            continue
        if not _is_confirmed_renderable_hotspot_hook(event):
            continue
        if not _grounded_chat_hook_scene_keys(event):
            continue
        event = _with_soft_logistics_bridge(event)
        binding = _hook_binding_assessment(topic, [event])
        parent = db.get_hotspot(int(event.get("hotspot_id") or 0)) or {}
        published_ts = _event_date_seconds(parent.get("published_at"))
        mode_rank = {
            _HOOK_BINDING_EXACT: 3,
            _HOOK_BINDING_ADJACENT: 2,
            _HOOK_BINDING_CONTEXTUAL: 1,
        }[binding["mode"]]
        topic_fit = _hook_topic_direct_fit_score(topic, event)
        editorial_fit = _hook_editorial_fit_score(topic, event, parent)
        rows.append({
            "event": event,
            "parent": parent,
            "binding": binding,
            "topic_fit": topic_fit,
            "editorial_fit": editorial_fit,
            "published_ts": published_ts,
            "sort_key": (
                mode_rank,
                topic_fit,
                0 if int(event.get("id") or 0) in recent else 1,
                editorial_fit,
                published_ts,
                int(event.get("id") or 0),
            ),
        })
    return sorted(rows, key=lambda item: item["sort_key"], reverse=True)


async def _retrieve_confirmed_chat_hooks(
    topic: str,
    user_id: int,
    session_id: str = "",
    *,
    content_mode: str = "hotspot",
    event_anchor: dict | None = None,
) -> dict:
    """Always bind the strongest real Hook available for a valid logistics topic.

    Exact semantic fit is preferred, but it is an editorial ranking signal,
    not a production-availability gate.  A contextual fallback remains a real,
    confirmed and playable external Hook and is explicitly narrated as
    industry context rather than evidence of the user's exact subject.
    """
    normalized_topic = video_topic_contract.normalize_topic_input(topic)
    if not normalized_topic:
        return {
            "status": "not_requested",
            "hooks": [],
            "failure_class": "empty_topic",
            "message": "没有提取到有效主题，无法创建视频项目。",
        }
    anchor = event_anchor or chat_intent.assess_event_anchor(normalized_topic)
    query = topic_hook_pipeline.structure_topic(normalized_topic)
    recently_used = db.recent_user_hook_event_ids(user_id)
    if session_id:
        recently_used.update(db.recent_session_hook_event_ids(session_id, user_id))

    # First preserve the established fact-grounded retrieval path.  It can
    # return two non-overlapping clips from the same verified event and carries
    # stronger exact-match evidence than a library-wide freshness sort.  The
    # force-output inventory is only the fallback when this path has no usable
    # group; otherwise a newer but unrelated clip could replace an exact Hook.
    brief = {
        "raw_input": normalized_topic,
        "subject": normalized_topic,
        "angle": normalized_topic,
        "goal": "为 Buffalo 物流内容选择真实、已确认、可播放的热点 Hook",
    }
    marketing_funnel: dict = {}
    try:
        marketing_candidates, _kb_context, _brand_evidence, marketing_funnel = _marketing_hook_candidates(
            brief,
            limit=8,
            hook_kind="timely_event",
            require_scene_overlap=False,
            allow_broad_match=True,
        )
    except Exception as exc:  # retrieval diagnostics must never break output
        logger.warning("精确 Hook 检索失败，切换真实库存兜底: %r", exc)
        marketing_candidates = []

    preferred_groups: list[dict] = []
    mode_rank = {
        _HOOK_BINDING_EXACT: 3,
        _HOOK_BINDING_ADJACENT: 2,
        _HOOK_BINDING_CONTEXTUAL: 1,
    }
    for candidate in marketing_candidates:
        selected_hooks = _select_chat_video_hook_pair(candidate.get("hook_clips") or [])
        locked_events = []
        for hook in selected_hooks:
            event_id = hook.get("event_clip_id")
            event = db.get_hotspot_event_clip(int(event_id)) if event_id is not None else None
            if (
                event
                and _is_confirmed_renderable_hotspot_hook(event)
                and _grounded_chat_hook_scene_keys(event)
            ):
                locked_events.append(_with_soft_logistics_bridge(event))
        if not locked_events:
            continue
        binding = _hook_binding_assessment(normalized_topic, locked_events)
        topic_fit = max(
            (_hook_topic_direct_fit_score(normalized_topic, event) for event in locked_events),
            default=0,
        )
        recent_hit = any(int(event.get("id") or 0) in recently_used for event in locked_events)
        preferred_groups.append({
            "events": locked_events,
            "candidate": candidate,
            "binding": binding,
            "topic_fit": topic_fit,
            "sort_key": (
                mode_rank[binding["mode"]],
                topic_fit,
                0 if recent_hit else 1,
                float(candidate.get("score") or 0),
                max(int(event.get("id") or 0) for event in locked_events),
            ),
        })
    preferred_groups.sort(key=lambda item: item["sort_key"], reverse=True)

    inventory_candidates = _force_output_hook_candidates(
        normalized_topic, recently_used=recently_used,
    )
    if not preferred_groups and not inventory_candidates:
        refresh_started = bool(topic_hook_pipeline.autofetch_enabled() and sched.request_targeted_hotspot_refresh())
        return {
            "status": "unavailable",
            "topic": normalized_topic,
            "hooks": [],
            "request_id": None,
            "failure_class": "no_real_hook_inventory",
            "event_anchor": {**anchor, "topic_query": query},
            "hook_kind": "timely_event",
            "video": {"status": "disabled", "hotspot_event_ids": []},
            "message": (
                "当前库中没有任何真实、已确认、可播放的时效 Hook，不能伪造热点。"
                + (" 已立即启动补库。" if refresh_started else " 请先恢复热点抓取任务。")
            ),
        }

    inventory_by_event_id = {
        int(item["event"]["id"]): item for item in inventory_candidates
    }
    for group in preferred_groups:
        effective_binding = group["binding"]
        for grouped_event in group["events"]:
            inventory_item = inventory_by_event_id.get(int(grouped_event["id"]))
            if (
                inventory_item
                and mode_rank[inventory_item["binding"]["mode"]]
                > mode_rank[effective_binding["mode"]]
            ):
                effective_binding = inventory_item["binding"]
        group["effective_binding"] = effective_binding
        group["effective_sort_key"] = (
            mode_rank[effective_binding["mode"]],
            group["topic_fit"],
            *group["sort_key"][2:],
        )
        group["published_ts"] = max(
            (
                _event_date_seconds(
                    (db.get_hotspot(int(item.get("hotspot_id") or 0)) or {}).get("published_at")
                )
                for item in group["events"]
            ),
            default=0.0,
        )
    preferred_groups.sort(key=lambda item: item["effective_sort_key"], reverse=True)

    chosen_group = preferred_groups[0] if preferred_groups else None
    chosen_inventory = inventory_candidates[0] if inventory_candidates else None
    # Compare both retrieval paths before locking a Hook.  A lower-quality
    # contextual marketing candidate must never hide an exact/adjacent match
    # already present in the confirmed inventory.  For contextual ties prefer
    # the inventory path because it is explicitly ordered by freshness.
    group_priority = (
        mode_rank[chosen_group["effective_binding"]["mode"]],
        int(chosen_group.get("topic_fit") or 0),
    ) if chosen_group else (-1, -1)
    inventory_priority = (
        mode_rank[chosen_inventory["binding"]["mode"]],
        int(chosen_inventory.get("topic_fit") or 0),
    ) if chosen_inventory else (-1, -1)
    prefer_group = bool(chosen_group) and (
        chosen_inventory is None
        or group_priority > inventory_priority
        or (
            group_priority == inventory_priority
            and (
                chosen_group["effective_binding"]["mode"] != _HOOK_BINDING_CONTEXTUAL
                or chosen_group["published_ts"] >= chosen_inventory["published_ts"]
            )
        )
    )
    if prefer_group:
        locked_events = chosen_group["events"]
        selected_candidate = chosen_group["candidate"]
        binding = chosen_group["effective_binding"]
        selection_source = "fact_grounded_retrieval"
    else:
        chosen = chosen_inventory
        locked_events = [chosen["event"]]
        selected_candidate = {}
        binding = chosen["binding"]
        selection_source = "force_output_inventory"
    event = locked_events[0]
    parent = db.get_hotspot(int(event.get("hotspot_id") or 0)) or {}
    readiness = _chat_video_delivery_readiness(
        normalized_topic,
        locked_events,
        hook_binding_mode=binding["mode"],
    )
    hooks = []
    for locked in locked_events:
        evidence = locked.get("evidence") or {}
        locked_parent = db.get_hotspot(int(locked.get("hotspot_id") or 0)) or parent
        source_title = str(
            locked.get("title_zh") or locked.get("title_en")
            or locked_parent.get("title_zh") or locked_parent.get("title") or ""
        ).strip()
        attention_title = str(
            selected_candidate.get("attention_title")
            or evidence.get("attention_title")
            or hotspot_hook_copy.attention_headline(
                str(evidence.get("what_happened") or ""),
                str(evidence.get("logistics_question") or ""),
                source_title,
            )
        ).strip()
        hooks.append({
            "event_clip_id": int(locked["id"]),
            "asset_id": int(locked["asset_id"]),
            "title": selected_candidate.get("title") or attention_title or source_title,
            "attention_title": attention_title,
            "source_title": source_title,
            "event_identity": evidence.get("event_identity"),
            "description": evidence.get("what_happened"),
            "marketing_question": (
                selected_candidate.get("marketing_question")
                or evidence.get("logistics_question")
            ),
            "published_at": locked_parent.get("published_at"),
        })
    mode_messages = {
        _HOOK_BINDING_EXACT: "已自动绑定与当前主题直接匹配的真实热点 Hook，可直接创建 60 秒视频项目。",
        _HOOK_BINDING_ADJACENT: "已绑定共享物流节点的真实热点 Hook；它只作为相邻风险引子，随后回到你的原主题和 Buffalo 可见动作。",
        _HOOK_BINDING_CONTEXTUAL: "未找到精确事件，已绑定最新真实物流现场作为行业注意力开场；不会把该现场冒充为你的主题证据，仍可直接出片。",
    }
    return {
        "status": "matched",
        "topic": normalized_topic,
        "hooks": hooks,
        "model": {"used": False, "fallback": selection_source},
        "failure_class": None,
        "event_anchor": {**anchor, "topic_query": query},
        "hook_kind": "timely_event",
        "hook_binding_mode": binding["mode"],
        "compatibility_issues": binding["issues"],
        "relevance": selected_candidate.get("relevance") or {
            "level": binding["mode"], "reason": binding["reason"],
        },
        "funnel": {
            **(marketing_funnel or {}),
            "inventory_scanned": len(inventory_candidates),
            "selected": len(locked_events),
            "exact": sum(item["binding"]["mode"] == _HOOK_BINDING_EXACT for item in inventory_candidates),
            "adjacent": sum(item["binding"]["mode"] == _HOOK_BINDING_ADJACENT for item in inventory_candidates),
            "contextual": sum(item["binding"]["mode"] == _HOOK_BINDING_CONTEXTUAL for item in inventory_candidates),
        },
        "match_buckets": {},
        "producible_topics": [],
        "candidates_debug": [
            {
                "event_clip_id": int(item["event"]["id"]),
                "binding_mode": item["binding"]["mode"],
                "published_at": item["parent"].get("published_at"),
            }
            for item in inventory_candidates[:8]
        ],
        "video": {
            "status": "ready" if readiness.get("delivery_ready") else "adapting",
            "chain_mode": "hotspot_owned",
            "hook_binding_mode": binding["mode"],
            "hotspot_event_ids": [int(item["id"]) for item in locked_events],
            "source_asset_id": int(event["asset_id"]),
            "delivery_readiness": readiness,
        },
        "message": mode_messages[binding["mode"]],
    }


def _chat_video_logistics_nodes(topic: str, events: list[dict]) -> list[str]:
    """Derive a conservative owned-media role from the approved Hook evidence."""
    contract = video_topic_contract.build_topic_contract(
        topic, has_event_anchor=bool(events),
    )
    contract_nodes = [
        str(node) for node in (contract.get("custom_topic_nodes") or (
            contract.get("nodes") if contract.get("is_named_contract") else []
        ) or [])
        if str(node) in {"清关", "末端", "配送", "仓储", "运输", "分拣"}
    ]
    # The original topic owns the primary logistics nodes. A retrieved event
    # may add evidenced bridge actions, but can never replace the requested
    # subject. Do not return early: a broad transport topic can legitimately
    # need warehouse/delivery actions when the approved Hook proves them.
    nodes = list(contract_nodes)
    evidence_text = " ".join(
        str(value or "")
        for event in events
        for value in (
            event.get("title_zh"), event.get("title_en"),
            (event.get("evidence") or {}).get("logistics_question"),
        )
    )
    candidates = _topic_keywords(" ".join((topic, evidence_text)))
    nodes.extend(
        node for node in candidates
        if node in {"清关", "末端", "配送", "仓储", "运输"}
    )
    # When there is no matched Hook, preserve the structured topic's logistics
    # meaning instead of reducing an operator topic to a generic delivery line.
    structured_nodes = set(
        topic_hook_pipeline.structure_topic(topic).get("logistics_nodes") or []
    )
    structured_transport_context = structured_nodes & {"港口", "铁路", "集装箱", "跨境运输"}
    if structured_transport_context:
        nodes.append("运输")
    if structured_transport_context and "仓储" in structured_nodes:
        nodes.append("仓储")
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
    return list(dict.fromkeys(nodes)) or ["仓储", "配送"]


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


def _chat_video_delivery_readiness(
    topic: str,
    locked_events: list[dict],
    *,
    chain_mode: str = "hotspot_owned",
    hook_binding_mode: str | None = None,
) -> dict:
    """Preflight the formal 50–90s plan without creating a project or calling a model.

    Formal production always requires at least one locked, confirmed, playable
    hotspot Hook. Buffalo-owned footage remains the bridge and proof layer; it
    is never a replacement for the required real Hook.
    """
    if chain_mode == "owned_only" or not locked_events:
        return {
            "status": "needs_hook", "delivery_ready": False,
            "message": "正式出片必须至少绑定 1 条真实、已确认、可播放的热点 Hook；Buffalo 自有素材不能替代热点开场。",
            "required": {"hotspot_video": 1, "duration_ms": "50000–90000"},
            "adaptation": {"adapted": False, "strategies": []},
        }
    # Callers created before the media proxy was materialized can hold a stale
    # event dict without ``clip_path``/``clip_status``.  Rehydrate every locked
    # id here so a valid database-backed Hook is not incorrectly reported as
    # unavailable merely because the chat card kept an older snapshot.
    resolved_events: list[dict] = []
    for event in locked_events:
        try:
            persisted = db.get_hotspot_event_clip(int(event.get("id") or 0))
        except (TypeError, ValueError):
            persisted = None
        resolved_events.append(persisted or event)
    locked_events = resolved_events
    binding = _hook_binding_assessment(topic, locked_events)
    binding_mode = hook_binding_mode or binding["mode"]
    compatibility_issues = binding["issues"]
    primary = locked_events[0] if locked_events else {}
    owned_segments = [
        item for item in db.list_asset_segments(limit=20_000)
        if not item.get("asset_hotspot_id")
    ]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
    ]
    source_hotspot = db.get_hotspot(int(primary["hotspot_id"])) if primary else {}
    source_hotspot = source_hotspot or {}
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
    planning_brief["hook_binding_mode"] = binding_mode
    planning_brief["hook_compatibility_issues"] = compatibility_issues
    if primary:
        planning_brief.update({
            "hotspot_id": primary["hotspot_id"],
            "source_asset_id": primary["asset_id"],
            "primary_event_id": primary["id"],
            "approved_hook_event_ids": [int(event["id"]) for event in locked_events],
        })
    related_events = []
    if primary:
        related_events = [
            _with_soft_logistics_bridge(item)
            for item in db.list_hotspot_event_clips(
                asset_id=primary["asset_id"], hotspot_id=primary["hotspot_id"],
            )
        ]
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
            chain_mode=chain_mode,
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
    owned_matching_mode = next(
        (
            str(scene.get("owned_match_mode") or "")
            for scene in scenes
            if scene.get("evidence_type") == "owned_video"
            and scene.get("owned_match_mode")
        ),
        "strict_category",
    )
    duration_ok = video_renderer.formal_duration_in_range(duration_ms)
    coverage = {
        "hotspot_video": hotspot_count,
        "owned_video": owned_count,
        "image": image_count,
        "duration_ms": duration_ms,
    }
    # A validated real Hook is the only chat-time production gate.  Sparse
    # owned footage and a short preflight plan are adaptation signals: the
    # formal planner can extend the plan with distinct Buffalo still images.
    # Expose the preflight truth for audit, but never turn it into a disabled
    # create button after a real playable Hook has already been locked.
    preflight_ready = bool(not planner_issue and hotspot_count >= 1 and duration_ok)
    delivery_ready = bool(locked_events and all(_is_confirmed_renderable_hotspot_hook(item) for item in locked_events))
    if preflight_ready and not adaptation.get("adapted"):
        message = "真实热点 Hook 与 Buffalo 自有动态素材均已就绪，可生成正式 50–90 秒成片。"
        status = "delivery_ready"
    elif preflight_ready:
        if owned_matching_mode == "broad_operational_fallback":
            message = (
                "真实热点 Hook 已锁定；同节点自有视频不足，已切换到 Buffalo 可见仓配动作桥接，"
                f"当前 {owned_count} 段，不把自有画面冒充为热点事实。"
            )
        else:
            message = (
                f"真实热点 Hook 已锁定；Buffalo 自有动态目前 {owned_count} 段"
                f"（理想 ≥4）。系统将按现有库存自适应规划并继续出片。"
            )
        status = "delivery_ready_adapted"
    elif delivery_ready and planner_issue:
        message = "真实热点 Hook 已锁定；预规划素材偏少，将使用 Buffalo 静态图与可见仓配动作补齐后继续出片。"
        status = "adaptation_queued"
    elif delivery_ready and not duration_ok:
        message = "真实热点 Hook 已锁定；预规划不足 50 秒，将以 Buffalo 静态图慢推镜头补足正式时长后继续出片。"
        status = "adaptation_queued"
    else:
        message = "真实热点 Hook 已匹配，但规划未产出可用热点镜头。"
        status = "needs_owned_media"
    result = {
        "status": status,
        "delivery_ready": delivery_ready,
        "preflight_ready": preflight_ready,
        "hook_binding_mode": binding_mode,
        "compatibility_issues": compatibility_issues,
        "coverage": coverage,
        "required": {"hotspot_video": 1, "owned_video": "adaptive", "duration_ms": "50000–90000"},
        "ideal": {"hotspot_video": 1, "owned_video": 4, "duration_ms": "50000–90000"},
        "logistics_nodes": nodes,
        "message": message,
        "planner_issue": planner_issue or None,
        "owned_matching_mode": owned_matching_mode,
        "adaptation": adaptation,
    }
    # Observation only when inventory looks thin or preflight needs adaptation.
    if owned_count < 4 or not preflight_ready:
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
        event = _with_soft_logistics_bridge(event)
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
        # Preserve the exact user topic.  Missing comparison evidence changes
        # only the evidence boundary, never the subject or its logistics nodes.
        degraded_from_comparison = True
        messages = list(messages)
        contract = video_topic_contract.build_topic_contract(latest_topic)
        messages.append({
            "role": "system",
            "content": (
                "必须保留用户原主题，不得改写成泛化物流话题。"
                + contract["safe_angle"]
                + " 标题和正文必须明确回应原主题。"
            ),
        })
        content_mode = "evergreen"

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
        # Every normal Douyin request is a video production command.  Content
        # classification may influence copy style, but it must never skip the
        # real-Hook stage for custom/evergreen topics.
        video_production_request = req.command is None and "douyin" in platforms
        if video_production_request or chat_intent.should_attempt_hook_retrieval(content_mode):
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

    platform_errors: list[dict] = []
    for item in outputs:
        if item["platform"] == "xiaohongshu" and item.get("title") != "生成失败":
            item["image_pages"], item["attachments"], render_error = _safe_xhs_carousel(
                item["title"], item.get("image_pages"), item.get("title") or "", "",
                output_id=str(item.get("platform") or "xiaohongshu"),
            )
            if render_error:
                platform_errors.append(render_error)
                item["quality_warnings"] = list(item.get("quality_warnings") or []) + [
                    render_error["message"]
                ]
    usable_outputs = [
        item for item in outputs
        if str(item.get("title") or "").strip() and str(item.get("title")) != "生成失败"
    ]
    if not usable_outputs:
        raise HTTPException(502, "所有平台内容生成失败")
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
    response_payload = {
        "content": context_content,
        "title": first["title"], "body": first["body"],
        "hashtags": first["hashtags"], "outputs": outputs,
        "platform_errors": platform_errors,
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
                    (hotspot_retrieval or {}).get("status") == "matched"
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
                readiness.get("message")
                or (hotspot_retrieval or {}).get("message")
                or "尚未匹配可用于正式成片的热点 Hook"
            ),
        },
    }
    # The chat request itself is the production command. Once a real playable
    # Hook is locked, create one durable generation job immediately; project
    # creation is local/transactional and does not wait for planning/rendering.
    workflow = response_payload["video_workflow"]
    if (
        req.command is None
        and "douyin" in platforms
        and workflow.get("status") == "ready"
        and workflow.get("hotspot_event_ids")
    ):
        try:
            # Automatic production uses the configured provider when healthy,
            # and a configured fallback when it is temporarily unavailable.
            # Audio integrity is still enforced by final technical QC.
            tts_provider, voice = video_renderer.resolve_tts_selection(
                None, None, strict=False,
            )
            queued = _queue_chat_dual_library_video_job(
                ChatDualLibraryVideoRequest(
                    topic=latest_topic or first.get("title") or "南非物流话题",
                    hotspot_event_ids=[
                        int(item) for item in workflow["hotspot_event_ids"][:2]
                    ],
                    platform="douyin",
                    target_duration_ms=60_000,
                    chain_mode=str(
                        ((hotspot_retrieval or {}).get("video") or {}).get("chain_mode")
                        or "hotspot_owned"
                    ),
                    session_id=req.session_id,
                    tts_provider=tts_provider,
                    voice=voice,
                ),
                user,
            )
            response_payload["video_task"] = {
                "status": str((queued.get("job") or {}).get("status") or "queued"),
                "stage": str((queued.get("job") or {}).get("stage") or "queued"),
                "job_id": queued["job_id"],
                "project_id": queued["project"]["id"],
                "poll_url": queued["poll_url"],
                "created": bool(queued.get("created")),
                "message": queued.get("message") or "视频已自动进入生产队列",
            }
            for item in outputs:
                if item.get("platform") == "douyin":
                    item["video_project_id"] = queued["project"]["id"]
                    item["video_generation_job_id"] = queued["job_id"]
        except HTTPException as exc:
            logger.warning(
                "AI 对话自动入队被业务门禁拒绝: topic=%s detail=%s",
                latest_topic, exc.detail,
            )
            response_payload["video_task"] = {
                "status": "blocked",
                "stage": "queueing",
                "message": str(exc.detail),
            }
        except Exception as exc:
            logger.exception("AI 对话自动入队失败: topic=%s", latest_topic)
            response_payload["video_task"] = {
                "status": "queue_failed",
                "stage": "queueing",
                "message": f"自动建立视频任务失败：{exc}",
            }
    elif "douyin" in platforms:
        response_payload["video_task"] = {
            "status": "waiting_hook",
            "stage": "hook_retrieval",
            "message": workflow.get("block_reason") or "正在等待真实热点 Hook",
        }
    return response_payload


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
                source_title = str(event.get("title_zh") or event.get("title_en") or "").strip()
                hooks.append({
                    "event_clip_id": event["id"],
                    "title": evidence.get("attention_title") or hotspot_hook_copy.attention_headline(
                        str(evidence.get("what_happened") or ""),
                        str(evidence.get("logistics_question") or ""),
                        source_title,
                    ) or source_title,
                    "attention_title": evidence.get("attention_title") or "",
                    "source_title": source_title,
                    "description": evidence.get("what_happened") or "",
                    "asset_id": event.get("asset_id"),
                })
    status = item.get("status") or "queued"
    payload = topic_hook_pipeline.discovery_payload(item, hooks=hooks)
    if status == "matched" and hooks:
        # The polling endpoint must return the same formal readiness decision
        # as the create endpoint.  A hook being found is not equivalent to a
        # 50--90s plan being renderable; the old UI used to hard-code ready
        # here and only discovered the contradiction after the button click.
        locked_events = []
        for hook in hooks:
            clip = db.get_hotspot_event_clip(int(hook["event_clip_id"]))
            if clip:
                locked_events.append(_with_soft_logistics_bridge(clip))
        readiness = _chat_video_delivery_readiness(
            str(item.get("topic") or ""), locked_events,
        )
        payload["video"] = {
            "status": "ready" if readiness.get("delivery_ready") else "blocked",
            "chain_mode": "hotspot_owned",
            "hotspot_event_ids": [int(hook["event_clip_id"]) for hook in hooks],
            "source_asset_id": int(hooks[0]["asset_id"]) if hooks and hooks[0].get("asset_id") else None,
            "delivery_readiness": readiness,
        }
        payload["message"] = (
            "定向采集完成，已确认匹配 Hook；正式成片素材门禁已通过。"
            if readiness.get("delivery_ready")
            else readiness.get("message") or "Hook 已找到，但当前素材尚不足以形成正式成片。"
        )
    if status in {"unmatched", "no_match", "failed"}:
        payload["video"] = {
            "status": "blocked",
            "chain_mode": "hotspot_owned",
            "hotspot_event_ids": [],
            "delivery_readiness": _chat_video_delivery_readiness(
                str(item.get("topic") or ""), [],
            ),
        }
    payload["recovery"] = (
        "定向采集已完成，但没有合格物流 Hook；正式出片保持阻断，不切换 Buffalo 自有素材直出"
        if status in {"unmatched", "no_match", "failed"}
        else ("该请求已归档，对比评测请补充资料后重新生成" if status == "cancelled_misrouted" else None)
    )
    return payload


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
    events = []
    for event_id in event_ids:
        event = db.get_hotspot_event_clip(event_id)
        events.append(_with_soft_logistics_bridge(event) if event else None)
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
    if body.chain_mode == "owned_only":
        raise HTTPException(409, {
            "message": "正式出片必须至少绑定 1 条真实、已确认、可播放的热点 Hook；不支持自有素材直出。",
            "required": {"hotspot_video": 1},
            "next_action": "先从热点审核台或 AI 对话结果中选择至少 1 条相关 Hook。",
        })
    topic = video_topic_contract.normalize_topic_input(body.topic)
    ordered_events = _validated_chat_video_events(body.hotspot_event_ids)
    binding = _hook_binding_assessment(topic, ordered_events)
    readiness = _chat_video_delivery_readiness(
        topic, ordered_events, chain_mode=body.chain_mode,
        hook_binding_mode=binding["mode"],
    )
    try:
        tts_provider, voice = video_renderer.resolve_tts_selection(
            body.tts_provider, body.voice, strict=True,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    locked_hook_ids = [int(item["id"]) for item in ordered_events]
    idempotency_key = body.idempotency_key or _chat_dual_library_idempotency_key(
        topic, locked_hook_ids, body.platform, body.target_duration_ms
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

    brief_input = topic if len(topic) >= 3 else f"{topic}物流热点"
    logistics_nodes = _chat_video_logistics_nodes(topic, ordered_events)
    topic_contract = video_topic_contract.build_topic_contract(
        topic, has_event_anchor=bool(ordered_events),
    )
    brief = db.create_topic_brief(
        _build_topic_brief_payload(TopicBriefCreateRequest(
            raw_input=brief_input,
            goal="基于已确认热点 Hook 生成 Buffalo 双素材库视频",
            logistics_nodes=logistics_nodes,
            platforms=[body.platform],
            content_form="video",
            chain_mode=body.chain_mode,
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
        "chain_mode": body.chain_mode,
        "fallback_mode": (
            None if binding["mode"] == _HOOK_BINDING_EXACT else binding["mode"]
        ),
        "hook_binding_mode": binding["mode"],
        "hook_compatibility_issues": binding["issues"],
        "logistics_nodes": logistics_nodes,
        "topic_contract": topic_contract,
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
        "deterministic_autopilot": True,
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
    revision_payload = {
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
            "topic_contract": topic_contract,
            "approved_hook_event_ids": locked_hook_ids,
            "hook_binding_mode": binding["mode"],
            "hook_compatibility_issues": binding["issues"],
        },
        "scenes": [],
    }
    if ordered_events:
        revision_payload["brief"]["primary_event_id"] = int(ordered_events[0]["id"])
    revision = db.create_video_project_revision(project["id"], revision_payload, user["id"])
    job, created = db.create_or_get_video_generation_job(
        project["id"], revision["id"], user["id"], idempotency_key
    )
    if created:
        db.add_video_generation_event(job["id"], "job_created", "视频生成任务已创建，等待后台规划")
        if locked_hook_ids:
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

@app.get("/api/ai/chat/dual-library-video/readiness")
async def get_chat_dual_library_video_readiness(
    topic: str,
    hotspot_event_id: int,
    user=Depends(get_current_user),
):
    """Recheck a manually selected Hook without creating a generation job."""
    event = db.get_hotspot_event_clip(int(hotspot_event_id))
    if not event or not _is_confirmed_renderable_hotspot_hook(event):
        raise HTTPException(409, "匹配的热点 Hook 已失效，请重新选择可播放 Hook。")
    event = _with_soft_logistics_bridge(event)
    normalized_topic = video_topic_contract.normalize_topic_input(topic)
    binding = _hook_binding_assessment(normalized_topic, [event])
    readiness = _chat_video_delivery_readiness(
        normalized_topic, [event], hook_binding_mode=binding["mode"],
    )
    return {
        "status": "ready" if readiness.get("delivery_ready") else "blocked",
        "chain_mode": "hotspot_owned",
        "hotspot_event_ids": [int(event["id"])],
        "source_asset_id": int(event["asset_id"]),
        "hook_binding_mode": binding["mode"],
        "compatibility_issues": binding["issues"],
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
