"""SA-LogiFlow v3.0 - FastAPI Backend."""
import asyncio
import json as _json
import logging
import os
import secrets
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4 as _uuid4
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

import database as db
import ai_engine
import publisher
import scheduler as sched
from xhs_cards import pages_from_content, render_carousel
import media_assets
import video_renderer
from auth import (
    create_access_token, verify_password, hash_password,
    get_current_user, require_role,
)
from models import (
    GenerateRequest, GenerateResponse,
    QueueCreateRequest, AccountCreateRequest, AccountCredentialsRequest, ReviewRequest, ChatRequest,
    LoginRequest, RegisterRequest, TokenResponse,
    UserRole, LocalScanCompleteRequest,
)
import publish_readiness
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _clear_stale_remote_desktop_artifacts()
    # 确保默认管理员存在
    if not db.get_user_by_username("admin"):
        db.create_user("admin", hash_password("admin123"), "admin", "系统管理员")
        logger.info("已创建默认管理员: admin / admin123")
    # 从环境变量加载 API key
    key = os.environ.get("MIMO_API_KEY", "")
    if key:
        ai_engine.set_api_key(key)
        logger.info("MiMo API key 已加载")
    # 清理卡住的渲染任务（启动时自动清理超时任务）
    video_renderer.cleanup_stale_jobs()
    # 启动定时调度器
    sched.start_scheduler()
    logger.info("SA-LogiFlow v3.0 启动完成 | 数据库: %s", db.DB_PATH)
    yield
    sched.stop_scheduler()
    logger.info("SA-LogiFlow v3.0 关闭")


app = FastAPI(title="SA-LogiFlow", version="3.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"

# 扫码登录会话仅用于本机单进程部署；前端据此获得明确的成功/超时/错误状态。
scan_login_sessions: dict[str, dict] = {}
# 本机原生 Chrome 扫码会话：令牌只允许本次指定账号回传一次 Cookie。
local_scan_handoffs: dict[str, dict] = {}
# 每个扫码任务使用独立的 Xvfb + x11vnc 桌面；noVNC 只通过一次性 token 路由到对应端口。
remote_desktop_sessions: dict[str, dict] = {}
remote_desktop_lock = asyncio.Lock()
REMOTE_DESKTOP_ROOT = Path(os.environ.get("SALOGIFLOW_REMOTE_DESKTOP_ROOT", "/opt/distribution-manager/data/remote-desktops"))
REMOTE_NOVNC_TOKEN_DIR = Path(os.environ.get("SALOGIFLOW_NOVNC_TOKEN_DIR", "/opt/distribution-manager/data/novnc-tokens"))
REMOTE_DISPLAY_START = int(os.environ.get("SALOGIFLOW_REMOTE_DISPLAY_START", "100"))
REMOTE_DISPLAY_COUNT = int(os.environ.get("SALOGIFLOW_REMOTE_DISPLAY_COUNT", "50"))
REMOTE_VNC_PORT_START = int(os.environ.get("SALOGIFLOW_REMOTE_VNC_PORT_START", "6100"))


def _clear_stale_remote_desktop_artifacts():
    """服务重启后清理上次任务留下的无效 token/密码文件。"""
    for directory, pattern in ((REMOTE_NOVNC_TOKEN_DIR, "*.token"), (REMOTE_DESKTOP_ROOT, "*.pass")):
        if not directory.exists():
            continue
        for path in directory.glob(pattern):
            try:
                path.unlink()
            except OSError:
                logger.warning("无法清理扫码桌面残留文件: %s", path)
# 手动发布会话：有头浏览器自动填好内容停在发布页，等用户人工点「发布」。
manual_publish_sessions: dict[str, dict] = {}
# 手动发布会话：弹有头浏览器自动填好内容、停在发布页，等用户人工点「发布」。
manual_publish_sessions: dict[str, dict] = {}
video_render_semaphore = asyncio.Semaphore(1)


# ==================== Auth API ====================

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    user = db.get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    if user["status"] != "active":
        raise HTTPException(403, "账号已被禁用")
    db.update_user_last_login(user["id"])
    db.add_audit_log(user["id"], user["username"], "login", ip=request.client.host)
    token = create_access_token({"sub": str(user["id"]), "username": user["username"], "role": user["role"]})
    return TokenResponse(
        access_token=token,
        role=UserRole(user["role"]),
        username=user["username"],
        display_name=user.get("display_name", ""),
    )


@app.post("/api/auth/register")
async def register(req: RegisterRequest, user=Depends(require_role(UserRole.ADMIN))):
    if db.get_user_by_username(req.username):
        raise HTTPException(400, "用户名已存在")
    uid = db.create_user(req.username, hash_password(req.password), req.role.value, req.display_name)
    db.add_audit_log(user["id"], user["username"], "create_user", target=req.username)
    return {"status": "ok", "user_id": uid}


@app.get("/api/auth/me")
async def get_me(user=Depends(get_current_user)):
    return user


@app.get("/api/users")
async def list_users(user=Depends(require_role(UserRole.ADMIN))):
    return db.get_users()


@app.put("/api/users/{user_id}/status")
async def update_user_status(user_id: int, body: dict, user=Depends(require_role(UserRole.ADMIN))):
    db.update_user_status(user_id, body.get("status", "active"))
    db.add_audit_log(user["id"], user["username"], "update_user_status", target=str(user_id), detail=body.get("status"))
    return {"status": "ok"}


# ==================== Pages ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "chat.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/{page_name}.html", response_class=HTMLResponse)
async def page(page_name: str):
    file_path = STATIC_DIR / f"{page_name}.html"
    if not file_path.exists():
        raise HTTPException(404, f"Page '{page_name}' not found")
    return FileResponse(file_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ==================== API: Dashboard ====================

@app.get("/api/dashboard")
async def dashboard():
    stats = db.get_queue_stats()
    accounts = db.get_accounts()
    recent = db.get_recent_activity(5)
    weekly = db.get_weekly_stats()

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
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


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

    if not ai_engine.MIMO_API_KEY:
        logger.warning("MiMo API key 未配置，使用 fallback 模板")
        contents = [ai_engine._fallback_content(p, req.topic, req.category) for p in req.platforms]
        for content in contents:
            if content.platform.value == "xiaohongshu":
                content.image_pages, content.attachments = render_carousel(content.title, content.image_pages, STATIC_DIR)
        return GenerateResponse(topic=req.topic, contents=contents, generated_at=datetime.now().isoformat(), source="fallback")

    contents = await ai_engine.generate_content(
        topic=req.topic, category=req.category, platforms=req.platforms,
        tone=req.tone, length=req.length, instruction=req.instruction,
        kb_context=kb_context,
        assets=db.list_assets(status="active"),
    )
    for content in contents:
        if content.platform.value == "xiaohongshu":
            content.image_pages, content.attachments = render_carousel(content.title, content.image_pages, STATIC_DIR)
    db.add_audit_log(user["id"], user["username"], "generate_content", target=req.topic)
    return GenerateResponse(topic=req.topic, contents=contents, generated_at=datetime.now().isoformat(), source="ai")


@app.post("/api/config/apikey")
async def set_api_key_endpoint(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    key = body.get("key", "")
    if key:
        ai_engine.set_api_key(key)
        db.add_audit_log(user["id"], user["username"], "set_api_key")
        os.environ["MIMO_API_KEY"] = key
        return {"status": "ok", "message": "MiMo API key set"}
    raise HTTPException(400, "Missing key")


@app.post("/api/config/mimo-key")
async def set_mimo_key(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    key = str(body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "Missing key")
    os.environ["MIMO_API_KEY"] = key
    ai_engine.set_api_key(key)
    db.add_audit_log(user["id"], user["username"], "set_mimo_key")
    return {"status": "ok"}


@app.post("/api/config/notification")
async def save_notification_config(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    """保存通知告警配置（写入环境变量，运行时生效）。"""
    import scheduler as sched
    if body.get("smtp_host"):
        os.environ["SMTP_HOST"] = body["smtp_host"]
    if body.get("smtp_port"):
        os.environ["SMTP_PORT"] = body["smtp_port"]
    if body.get("smtp_user"):
        os.environ["SMTP_USER"] = body["smtp_user"]
    if body.get("smtp_pass"):
        os.environ["SMTP_PASS"] = body["smtp_pass"]
    if body.get("alert_email"):
        os.environ["ALERT_EMAIL"] = body["alert_email"]
    # IM 机器人 webhook（允许置空以清除）
    if "feishu_webhook" in body:
        os.environ["FEISHU_WEBHOOK"] = body["feishu_webhook"]
    if "wecom_webhook" in body:
        os.environ["WECOM_WEBHOOK"] = body["wecom_webhook"]
    # 同步到 scheduler 模块
    sched.SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.163.com")
    sched.SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
    sched.SMTP_USER = os.environ.get("SMTP_USER", "")
    sched.SMTP_PASS = os.environ.get("SMTP_PASS", "")
    sched.ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
    sched.FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
    sched.WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")
    db.add_audit_log(user["id"], user["username"], "save_notification_config")
    return {"status": "ok"}


@app.post("/api/config/notification/test")
async def test_notification(body: dict = None, user=Depends(require_role(UserRole.ADMIN))):
    """发送测试通知。body.channel: all/email/feishu/wecom（默认 all）。"""
    channel = (body or {}).get("channel", "all")
    subject = "[SA-LogiFlow] 测试通知"
    text = "🔔 这是一条测试通知，收到说明该渠道配置成功。\n时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if channel == "email":
        await sched.send_email(subject, text)
    elif channel == "feishu":
        await sched.send_feishu(text)
    elif channel == "wecom":
        await sched.send_wecom(text)
    else:
        await sched.notify_all(subject, text)
    db.add_audit_log(user["id"], user["username"], "test_notification", target=channel)
    return {"status": "ok", "message": f"测试通知已发送（{channel}）"}


# ==================== API: Accounts ====================

@app.get("/api/accounts")
async def list_accounts(platform: str = None, user=Depends(get_current_user)):
    result = []
    for a in db.get_accounts(platform):
        r = publish_readiness.readiness(a["platform"], a.get("credentials"))
        a.pop("credentials", None)  # 脱敏：不把凭据明文返回前端
        a["ready"] = r["ready"]
        a["missing"] = r["missing"]
        a["credential_kind"] = r["kind"]
        result.append(a)
    return result


@app.post("/api/accounts")
async def create_account(req: AccountCreateRequest, user=Depends(require_role(UserRole.ADMIN))):
    try:
        db.create_account(req.platform.value, req.name, req.account_id, req.config_summary)
        db.add_audit_log(user["id"], user["username"], "create_account", target=f"{req.platform.value}:{req.name}")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, user=Depends(require_role(UserRole.ADMIN))):
    db.delete_account(account_id)
    db.add_audit_log(user["id"], user["username"], "delete_account", target=str(account_id))
    return {"status": "ok"}


@app.put("/api/accounts/{account_id}/credentials")
async def set_account_credentials(
    account_id: int, req: AccountCredentialsRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        raise HTTPException(404, "Account not found")
    acc = accounts[0]
    db.update_account_credentials(acc["account_id"], _json.dumps(req.credentials, ensure_ascii=False))
    db.update_account_status(account_id, "active")  # 填了凭据即恢复可用
    db.add_audit_log(user["id"], user["username"], "set_credentials", target=f"{acc['platform']}:{acc['account_id']}")
    r = publish_readiness.readiness(acc["platform"], _json.dumps(req.credentials))
    return {"ok": True, "ready": r["ready"], "missing": r["missing"]}


@app.post("/api/accounts/{account_id}/local-scan-login")
async def start_local_scan_login(account_id: int, user=Depends(require_role(UserRole.ADMIN))):
    """创建本机 Chrome 扫码会话；浏览器助手用一次性 handoff token 回传 Cookie。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter

    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        raise HTTPException(404, "Account not found")
    acc = accounts[0]
    adapter = get_adapter(acc["platform"])
    if not isinstance(adapter, RpaAdapter):
        raise HTTPException(400, "该平台不使用扫码登录，请填写凭据")
    session_id = str(_uuid4())
    handoff_token = secrets.token_urlsafe(32)
    scan_login_sessions[session_id] = {
        "status": "waiting", "mode": "local", "account_id": acc["id"],
        "platform": acc["platform"], "owner_id": user["id"],
    }
    local_scan_handoffs[session_id] = {
        "token": handoff_token, "account_id": acc["id"], "owner_id": user["id"],
        "expires_at": time.time() + 300,
    }
    db.add_audit_log(user["id"], user["username"], "local_scan_login",
                     target=f"{acc['platform']}:{acc['account_id']}")
    return {
        "started": True,
        "session_id": session_id,
        "mode": "local",
        "handoff_token": handoff_token,
        "login_url": adapter.login_url,
        "logged_in_selector": adapter._logged_in_selector(),
        "complete_path": f"/api/accounts/{account_id}/local-scan-login/{session_id}/complete",
        "status": "waiting",
    }


@app.post("/api/accounts/{account_id}/local-scan-login/{session_id}/complete")
async def complete_local_scan_login(
    account_id: int,
    session_id: str,
    req: LocalScanCompleteRequest,
):
    """接收本机助手回传的 Cookie；不要求 JWT，仅接受短时一次性 handoff token。"""
    handoff = local_scan_handoffs.get(session_id)
    session = scan_login_sessions.get(session_id)
    if not handoff or not session or session.get("mode") != "local":
        raise HTTPException(404, "本机扫码会话不存在或已结束")
    if handoff["account_id"] != account_id or handoff["expires_at"] < time.time():
        local_scan_handoffs.pop(session_id, None)
        session.update({"status": "timeout", "error": "本机扫码会话已过期"})
        raise HTTPException(410, "本机扫码会话已过期")
    if not secrets.compare_digest(req.handoff_token, handoff["token"]):
        raise HTTPException(403, "无效的本机扫码令牌")
    local_scan_handoffs.pop(session_id, None)
    if req.error:
        session.update({"status": "error", "error": req.error})
        return {"ok": False, "status": "error"}
    if not req.cookies:
        session.update({"status": "error", "error": "本机浏览器未返回 Cookie"})
        return {"ok": False, "status": "error"}
    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        session.update({"status": "error", "error": "Account not found"})
        return {"ok": False, "status": "error"}
    from adapters.rpa_base import build_credentials
    acc = accounts[0]
    db.update_account_credentials(acc["account_id"], build_credentials(req.cookies))
    db.update_account_status(account_id, "active")
    session.update({"status": "success"})
    db.add_audit_log(handoff["owner_id"], "local-agent", "local_scan_complete",
                     target=f"{acc['platform']}:{acc['account_id']}")
    return {"ok": True, "status": "success"}


def _remote_port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


async def _stop_remote_process(process: subprocess.Popen | None):
    if not process or process.poll() is not None:
        return
    process.terminate()
    for _ in range(20):
        if process.poll() is not None:
            return
        await asyncio.sleep(0.05)
    process.kill()


def _remote_browser_url(token: str, password: str) -> str:
    # 密码放在 URL fragment，不会随 HTTP 请求发送到 nginx/websockify 日志。
    return (
        "/novnc/vnc.html?autoconnect=1&resize=scale"
        f"&path=novnc/websockify%3Ftoken%3D{token}#password={password}"
    )


async def _start_remote_desktop(session_id: str) -> dict:
    """为一次扫码任务创建独立桌面，并返回前端可用的 noVNC 会话 URL。"""
    async with remote_desktop_lock:
        REMOTE_DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
        REMOTE_NOVNC_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        used = {(item["display"], item["port"]) for item in remote_desktop_sessions.values()}
        selected = None
        for offset in range(REMOTE_DISPLAY_COUNT):
            display = REMOTE_DISPLAY_START + offset
            port = REMOTE_VNC_PORT_START + offset
            if (display, port) not in used:
                selected = display, port
                break
        if not selected:
            raise RuntimeError("扫码桌面资源已用尽，请稍后重试")
        display, port = selected
        token = secrets.token_urlsafe(24)
        password = secrets.token_hex(4)  # x11vnc 密码最多 8 个字符
        passfile = REMOTE_DESKTOP_ROOT / f"{session_id}.pass"
        tokenfile = REMOTE_NOVNC_TOKEN_DIR / f"{session_id}.token"
        xvfb = None
        vnc = None
        try:
            passfile.write_text("", encoding="utf-8")
            os.chmod(passfile, 0o600)
            subprocess.run(
                ["/usr/bin/x11vnc", "-storepasswd", password, str(passfile)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            xvfb = subprocess.Popen(
                ["/usr/bin/Xvfb", f":{display}", "-screen", "0", "1600x1000x24",
                 "-nolisten", "tcp", "-ac", "-noreset"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            socket_path = Path(f"/tmp/.X11-unix/X{display}")
            for _ in range(100):
                if xvfb.poll() is not None:
                    raise RuntimeError(f"Xvfb 启动失败，退出码 {xvfb.returncode}")
                if socket_path.exists():
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError(f"Xvfb 显示器 :{display} 未就绪")
            vnc = subprocess.Popen(
                ["/usr/bin/x11vnc", "-display", f":{display}", "-rfbport", str(port),
                 "-rfbauth", str(passfile), "-localhost", "-forever", "-shared",
                 "-noxdamage", "-repeat", "-wait", "5", "-quiet"],
                env={**os.environ, "DISPLAY": f":{display}"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            for _ in range(100):
                if vnc.poll() is not None:
                    raise RuntimeError(f"x11vnc 启动失败，退出码 {vnc.returncode}")
                if _remote_port_is_listening(port):
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError(f"VNC 端口 {port} 未就绪")
            tokenfile.write_text(f"{token}: 127.0.0.1:{port}\n", encoding="utf-8")
            os.chmod(tokenfile, 0o600)
            desktop = {
                "display": display, "port": port, "token": token, "password": password,
                "passfile": passfile, "tokenfile": tokenfile, "xvfb": xvfb, "vnc": vnc,
                "remote_browser_url": _remote_browser_url(token, password),
            }
            remote_desktop_sessions[session_id] = desktop
            return desktop
        except Exception:
            await _stop_remote_process(vnc)
            await _stop_remote_process(xvfb)
            passfile.unlink(missing_ok=True)
            tokenfile.unlink(missing_ok=True)
            raise


async def _cleanup_remote_desktop(session_id: str):
    desktop = remote_desktop_sessions.pop(session_id, None)
    if not desktop:
        return
    await _stop_remote_process(desktop.get("vnc"))
    await _stop_remote_process(desktop.get("xvfb"))
    Path(desktop["tokenfile"]).unlink(missing_ok=True)
    Path(desktop["passfile"]).unlink(missing_ok=True)


async def _run_scan_login(account: dict, session_id: str, desktop: dict):
    """后台：在本次任务的独立有头浏览器中扫码，轮询登录态 → 存 cookie。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter, browser_launch_options, build_credentials
    adapter = get_adapter(account["platform"])
    if not isinstance(adapter, RpaAdapter):
        scan_login_sessions[session_id] = {
            "status": "error", "error": "该平台不支持扫码登录",
            "account_id": account["id"], "platform": account["platform"],
        }
        await _cleanup_remote_desktop(session_id)
        return
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            launch_options = browser_launch_options(
                headless=False, use_proxy=getattr(adapter, "use_proxy", False),
            )
            launch_options["env"] = {**os.environ, "DISPLAY": f":{desktop['display']}"}
            browser = await p.chromium.launch(**launch_options)
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
    finally:
        await _cleanup_remote_desktop(session_id)


@app.post("/api/accounts/{account_id}/scan-login")
async def scan_login(account_id: int, user=Depends(require_role(UserRole.ADMIN))):
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        raise HTTPException(404, "Account not found")
    acc = accounts[0]
    adapter = get_adapter(acc["platform"])
    if not isinstance(adapter, RpaAdapter):
        raise HTTPException(400, "该平台不使用扫码登录，请填写凭据")
    session_id = str(_uuid4())
    # 限制内存中的历史会话数量。
    if len(scan_login_sessions) >= 100:
        for old_id in list(scan_login_sessions)[:20]:
            if old_id in remote_desktop_sessions:
                continue
            scan_login_sessions.pop(old_id, None)
    scan_login_sessions[session_id] = {
        "status": "waiting", "account_id": acc["id"], "platform": acc["platform"],
    }
    try:
        desktop = await _start_remote_desktop(session_id)
    except Exception as exc:
        scan_login_sessions.pop(session_id, None)
        logger.exception("扫码桌面创建失败: %s", acc["account_id"])
        raise HTTPException(503, f"无法创建扫码桌面：{exc}") from exc
    scan_login_sessions[session_id]["remote_browser_url"] = desktop["remote_browser_url"]
    asyncio.create_task(_run_scan_login(acc, session_id, desktop))
    db.add_audit_log(user["id"], user["username"], "scan_login", target=f"{acc['platform']}:{acc['account_id']}")
    return {"started": True, "session_id": session_id, "status": "waiting",
            "message": "已启动扫码登录，请在远程浏览器中完成扫码"}


@app.get("/api/accounts/{account_id}/scan-login/{session_id}")
async def scan_login_status(account_id: int, session_id: str, user=Depends(require_role(UserRole.ADMIN))):
    session = scan_login_sessions.get(session_id)
    if not session or session.get("account_id") != account_id:
        raise HTTPException(404, "扫码登录会话不存在")
    result = {"status": session["status"], "error": session.get("error")}
    if session.get("remote_browser_url"):
        result["remote_browser_url"] = session["remote_browser_url"]
    return result


@app.post("/api/accounts/{account_id}/test-connection")
async def test_account_connection(account_id: int, user=Depends(require_role(UserRole.ADMIN))):
    """测试账号可用性：cookie 平台实际打开浏览器校验登录态；token 平台校验字段完整。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        raise HTTPException(404, "Account not found")
    acc = accounts[0]
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

@app.get("/api/queue")
async def list_queue(status: str = None, platform: str = None, user=Depends(get_current_user)):
    return db.get_queue(status, platform)


@app.post("/api/queue")
async def add_queue(req: QueueCreateRequest, user=Depends(get_current_user)):
    # editor 提交后自动进入 pending_review
    initial_status = req.status.value if req.status else "draft"
    if user["role"] == "editor" and initial_status == "draft":
        initial_status = "pending_review"

    for platform in req.platforms:
        platform_attachments = req.attachments
        if platform.value == "xiaohongshu" and not any(a.get("type") == "image" for a in platform_attachments):
            _, platform_attachments = render_carousel(
                req.title, pages_from_content(req.title, req.body), STATIC_DIR,
            )
        if platform.value == "douyin" and not any(a.get("type") == "video" for a in platform_attachments):
            raise HTTPException(400, "抖音内容必须先生成或上传 MP4 视频")
        db.add_to_queue(
            title=req.title, body=req.body, platform=platform.value,
            hashtags=req.hashtags, scheduled_at=req.scheduled_at,
            status=initial_status, created_by=user["id"],
            attachments=platform_attachments,
        )
    db.add_audit_log(user["id"], user["username"], "add_to_queue", target=req.title, detail=f"{len(req.platforms)} platforms")
    return {"status": "ok", "added": len(req.platforms)}


@app.put("/api/queue/{item_id}/status")
async def update_status(item_id: int, body: dict, user=Depends(get_current_user)):
    scheduled_at = body.get("scheduled_at")
    if scheduled_at is not None:
        db.update_queue_status(item_id, body.get("status"), body.get("error_msg"), scheduled_at=scheduled_at)
    else:
        db.update_queue_status(item_id, body.get("status"), body.get("error_msg"))
    return {"status": "ok"}


@app.delete("/api/queue/{item_id}")
async def delete_queue_item(item_id: int, user=Depends(get_current_user)):
    db.delete_queue_item(item_id)
    db.add_audit_log(user["id"], user["username"], "delete_queue_item", target=str(item_id))
    return {"status": "ok"}


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
    if not pages:
        pages = pages_from_content(title, content)
    normalized, attachments = render_carousel(title, pages, STATIC_DIR)
    return {"image_pages": normalized, "attachments": attachments}


def _repair_xhs_queue_media(item: dict, attachments: list[dict]) -> list[dict]:
    if item["platform"] != "xiaohongshu" or any(a.get("type") == "image" for a in attachments):
        return attachments
    _, generated = render_carousel(
        item["title"], pages_from_content(item["title"], item["body"]), STATIC_DIR,
    )
    db.update_queue_attachments(item["id"], generated)
    return generated

@app.post("/api/publish/{item_id}")
async def publish_item(item_id: int, user=Depends(get_current_user)):
    item = db.get_queue_item_by_id(item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")

    attachments = _json.loads(item.get('attachments') or '[]')
    attachments = _repair_xhs_queue_media(item, attachments)
    images = [a['path'] for a in attachments if a.get('type') == 'image']
    video = next((a['path'] for a in attachments if a.get('type') == 'video'), None)
    result = await publisher.dispatch(
        platform=item["platform"], title=item["title"],
        content=item["body"], tags=item.get("hashtags", []),
        images=images if images else None, video=video,
    )

    if result["success"]:
        db.update_queue_status(item_id, "published")
        db.add_publish_log(item_id, item["platform"], item["title"], "published")
    else:
        retry_count = db.get_retry_count(item_id)
        if retry_count < 3:
            db.increment_retry_count(item_id)
            db.update_queue_status(item_id, "queued", f"重试中 ({retry_count + 1}/3): {result.get('error', '')}")
            db.add_publish_log(item_id, item["platform"], item["title"], "retry", result.get("error"))
        else:
            db.update_queue_status(item_id, "failed", result.get("error", "Unknown error"))
            db.add_publish_log(item_id, item["platform"], item["title"], "failed", result.get("error"))

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

        attachments = _json.loads(item.get('attachments') or '[]')
        attachments = _repair_xhs_queue_media(item, attachments)
        images = [a['path'] for a in attachments if a.get('type') == 'image']
        video = next((a['path'] for a in attachments if a.get('type') == 'video'), None)
        result = await publisher.dispatch(
            platform=item["platform"], title=item["title"],
            content=item["body"], tags=item.get("hashtags", []),
            images=images if images else None, video=video,
        )

        if result["success"]:
            db.update_queue_status(item_id, "published")
            db.add_publish_log(item_id, item["platform"], item["title"], "published")
        else:
            retry_count = db.get_retry_count(item_id)
            if retry_count < 3:
                db.increment_retry_count(item_id)
                db.update_queue_status(item_id, "queued", f"重试中 ({retry_count + 1}/3)")
            else:
                db.update_queue_status(item_id, "failed", result.get("error"))
                db.add_publish_log(item_id, item["platform"], item["title"], "failed", result.get("error"))

        results.append({"item_id": item_id, **result})

    logger.info("批量发布完成: %d 条", len(item_ids))
    return {"results": results}


async def _run_manual_publish(item: dict, session_id: str):
    """弹有头浏览器，自动填好内容并停在发布页，等用户人工点「发布」。检测跳转后标记已发布。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter, browser_launch_options, parse_cookies
    adapter = get_adapter(item["platform"])
    if not isinstance(adapter, RpaAdapter) or not hasattr(adapter, "fill_publish_form"):
        manual_publish_sessions[session_id].update({"status": "error", "error": "该平台不支持手动发布"})
        return
    account = next((a for a in db.get_accounts(item["platform"])
                    if publish_readiness.readiness(item["platform"], a.get("credentials"))["ready"]), None)
    if not account:
        manual_publish_sessions[session_id].update({"status": "error", "error": "无可用账号，请先到「账号管理」登录"})
        return
    attachments = _json.loads(item.get("attachments") or "[]")
    attachments = _repair_xhs_queue_media(item, attachments)
    images = publisher._resolve_uploaded_media([a["path"] for a in attachments if a.get("type") == "image"])
    video_path = next((a["path"] for a in attachments if a.get("type") == "video"), None)
    resolved_video = (publisher._resolve_uploaded_media([video_path]) or [None])[0] if video_path else None
    if item["platform"] == "xiaohongshu" and not images:
        manual_publish_sessions[session_id].update({"status": "error", "error": "小红书必须配图"})
        return
    if item["platform"] == "douyin" and not resolved_video:
        manual_publish_sessions[session_id].update({"status": "error", "error": "抖音必须有视频素材"})
        return
    pw = None
    browser = None
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(**browser_launch_options(headless=False))
        context = await browser.new_context()
        cookies = parse_cookies(account.get("credentials"))
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        await adapter.fill_publish_form(
            page, title=item["title"], content=item["body"],
            tags=item.get("hashtags") or [], images=images, video=resolved_video,
        )
        manual_publish_sessions[session_id].update({"status": "ready"})
        logger.info("手动发布：已填好内容，等待人工发布 item=%s", item["id"])
        # 轮询等待用户点「发布」：URL 离开发布页即视为已提交（最多 ~15 分钟）
        # 用适配器的 publish_url 判断当前是否仍在发布页
        from urllib.parse import urlparse
        pub_path = urlparse(adapter.publish_url).path  # e.g. /publish/publish or /creator-micro/content/upload
        published = False
        for _ in range(180):
            await asyncio.sleep(5)
            try:
                url = page.url or ""
            except Exception:
                break  # 浏览器/页面被用户关闭
            if pub_path not in url and "login" not in url.lower():
                published = True
                break
        if published:
            db.update_queue_status(item["id"], "published")
            db.add_publish_log(item["id"], item["platform"], item["title"], "published", "manual")
            manual_publish_sessions[session_id].update({"status": "published"})
            logger.info("手动发布完成 item=%s", item["id"])
        else:
            manual_publish_sessions[session_id].update({"status": "closed"})
    except Exception as exc:
        logger.exception("手动发布异常")
        manual_publish_sessions[session_id].update({"status": "error", "error": str(exc)})
    finally:
        try:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
        except Exception:
            pass


@app.post("/api/publish/{item_id}/manual")
async def manual_publish(item_id: int, user=Depends(get_current_user)):
    item = db.get_queue_item_by_id(item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    adapter = get_adapter(item["platform"])
    if not isinstance(adapter, RpaAdapter) or not hasattr(adapter, "fill_publish_form"):
        raise HTTPException(400, "该平台不支持手动发布（仅小红书/抖音等浏览器发布平台）")
    session_id = str(_uuid4())
    if len(manual_publish_sessions) >= 50:
        for old_id in list(manual_publish_sessions)[:20]:
            manual_publish_sessions.pop(old_id, None)
    manual_publish_sessions[session_id] = {"status": "starting", "item_id": item_id}
    asyncio.create_task(_run_manual_publish(item, session_id))
    db.add_audit_log(user["id"], user["username"], "manual_publish", target=str(item_id))
    return {"started": True, "session_id": session_id}


@app.get("/api/publish/manual/{session_id}")
async def manual_publish_status(session_id: str, user=Depends(get_current_user)):
    s = manual_publish_sessions.get(session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    return {"status": s["status"], "error": s.get("error")}


async def _run_manual_publish(item: dict, session_id: str):
    """有头浏览器打开发布页、自动填好内容但不点发布；等用户手动发布后（页面跳离发布页）标记已发布。"""
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter, browser_launch_options, parse_cookies
    adapter = get_adapter(item["platform"])
    if not isinstance(adapter, RpaAdapter) or not hasattr(adapter, "fill_publish_form"):
        manual_publish_sessions[session_id].update({"status": "error", "error": "该平台不支持手动发布"})
        return
    account = next((a for a in db.get_accounts(item["platform"])
                    if publish_readiness.readiness(item["platform"], a.get("credentials"))["ready"]), None)
    if not account:
        manual_publish_sessions[session_id].update({"status": "error", "error": "无可用账号，请先在「账号管理」登录"})
        return
    attachments = _json.loads(item.get("attachments") or "[]")
    attachments = _repair_xhs_queue_media(item, attachments)
    images = publisher._resolve_uploaded_media([a["path"] for a in attachments if a.get("type") == "image"])
    video_path = next((a["path"] for a in attachments if a.get("type") == "video"), None)
    resolved_video = (publisher._resolve_uploaded_media([video_path]) or [None])[0] if video_path else None
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
        try:
            context = await browser.new_context()
            cookies = parse_cookies(account.get("credentials"))
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()
            await adapter.fill_publish_form(
                page, title=item["title"], content=item["body"],
                tags=item.get("hashtags") or [], images=images, video=resolved_video,
            )
            manual_publish_sessions[session_id].update({"status": "ready"})
            # 等用户手动点「发布」：轮询 URL 离开发布页视为已发布，最多 ~15 分钟。
            from urllib.parse import urlparse
            pub_path = urlparse(adapter.publish_url).path
            published = False
            for _ in range(180):
                await asyncio.sleep(5)
                try:
                    url = page.url or ""
                except Exception:
                    break  # 浏览器/页面被关闭
                if pub_path not in url and "login" not in url.lower():
                    published = True
                    break
            if published:
                db.update_queue_status(item["id"], "published")
                db.add_publish_log(item["id"], item["platform"], item["title"], "published", "manual")
                manual_publish_sessions[session_id].update({"status": "published"})
            else:
                manual_publish_sessions[session_id].update({"status": "closed"})
        finally:
            await browser.close()
            await pw.stop()
    except Exception as exc:
        logger.exception("手动发布异常")
        manual_publish_sessions[session_id].update({"status": "error", "error": str(exc)})


@app.post("/api/publish/{item_id}/manual")
async def manual_publish(item_id: int, user=Depends(get_current_user)):
    item = db.get_queue_item_by_id(item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    from adapters import get_adapter
    from adapters.rpa_base import RpaAdapter
    if not isinstance(get_adapter(item["platform"]), RpaAdapter):
        raise HTTPException(400, "该平台不支持手动发布（仅小红书/抖音等浏览器发布平台）")
    if len(manual_publish_sessions) >= 50:
        for old_id in list(manual_publish_sessions)[:20]:
            manual_publish_sessions.pop(old_id, None)
    session_id = str(_uuid4())
    manual_publish_sessions[session_id] = {"status": "starting", "item_id": item_id}
    asyncio.create_task(_run_manual_publish(item, session_id))
    db.add_audit_log(user["id"], user["username"], "manual_publish", target=str(item_id))
    return {"started": True, "session_id": session_id}


@app.get("/api/publish/manual/{session_id}")
async def manual_publish_status(session_id: str, user=Depends(get_current_user)):
    s = manual_publish_sessions.get(session_id)
    if not s:
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
    return db.get_publish_logs(limit)


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
    return [media_assets.public_asset(item) for item in db.list_assets(type, category, query, status)]


@app.post("/api/assets/upload")
async def upload_media_asset(
    file: UploadFile = File(...), category: str = "other",
    user=Depends(require_role(UserRole.ADMIN)),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in media_assets.IMAGE_EXTS | media_assets.VIDEO_EXTS:
        raise HTTPException(400, "不支持的素材格式")
    content = await file.read()
    max_size = media_assets.MAX_VIDEO if suffix in media_assets.VIDEO_EXTS else media_assets.MAX_IMAGE
    if not content or len(content) > max_size:
        raise HTTPException(400, "素材为空或超过大小限制")
    import tempfile
    temp_path = Path(tempfile.gettempdir()) / f"asset-{_uuid4().hex}{suffix}"
    try:
        temp_path.write_bytes(content)
        # auto 时按原始文件名猜分类（temp 文件名是 uuid，不含关键词，不能用来猜）
        original_name = Path(file.filename or "").stem
        if category == "auto":
            category = media_assets.guess_category(Path(file.filename or ""))
        asset = media_assets.ingest_file(temp_path, STATIC_DIR, category, "upload", user["id"], name=original_name or None)
        is_dedup = bool(asset.get("_dedup"))
        reactivated = bool(asset.get("_reactivated"))
        db.add_audit_log(user["id"], user["username"], "upload_asset", target=str(asset["id"]))
        return {**media_assets.public_asset(asset), "duplicated": is_dedup, "reactivated": reactivated}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)


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
            results.append(media_assets.public_asset(asset))
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)[:200]})
    db.add_audit_log(user["id"], user["username"], "import_assets", detail=f"success={len(results)}, errors={len(errors)}")
    return {"imported": results, "errors": errors}


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
    result["mimo_key"] = bool(os.environ.get("MIMO_API_KEY"))
    result["ready"] = all(result.values())
    result["voices"] = sorted(video_renderer.VOICES)
    return result


async def _run_video_job(job_id: str):
    async with video_render_semaphore:
        await asyncio.to_thread(video_renderer.render_job, job_id, STATIC_DIR)


@app.post("/api/douyin/render")
async def create_douyin_render(body: dict, user=Depends(get_current_user)):
    caps = media_assets.capabilities()
    if not caps["ffmpeg"] or not caps["ffprobe"]:
        raise HTTPException(503, "未安装 FFmpeg/ffprobe")
    if not os.environ.get("MIMO_API_KEY"):
        raise HTTPException(503, "未配置 MiMo API Key")
    voice = str(body.get("voice") or "")
    asset_ids = {asset["id"] for asset in db.list_assets(status="active")}
    try:
        script = video_renderer.normalize_script(body, asset_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if voice not in video_renderer.VOICES:
        raise HTTPException(400, "请选择有效的 MiMo 中文音色")
    job_id = _uuid4().hex
    db.create_render_job(job_id, script, voice, user["id"])
    asyncio.create_task(_run_video_job(job_id))
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/douyin/render/{job_id}")
async def get_douyin_render(job_id: str, user=Depends(get_current_user)):
    job = db.get_render_job(job_id)
    if not job:
        raise HTTPException(404, "渲染任务不存在")
    if job.get("output_path"):
        job["rendered_video"] = {
            "type": "video", "path": job["output_path"],
            "url": "/static/" + job["output_path"], "filename": Path(job["output_path"]).name,
        }
    return job


@app.post("/api/douyin/render/{job_id}/retry")
async def retry_douyin_render(job_id: str, user=Depends(get_current_user)):
    job = db.get_render_job(job_id)
    if not job:
        raise HTTPException(404, "渲染任务不存在")
    if job["status"] == "running":
        raise HTTPException(409, "任务正在运行")
    db.update_render_job(job_id, status="pending", stage="等待重试", progress=0, error=None, output_path=None)
    asyncio.create_task(_run_video_job(job_id))
    return {"job_id": job_id, "status": "pending"}


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
    if not db.get_prompt_template(tpl_id):
        raise HTTPException(404, "模板不存在")
    name = (body.get("name") or "").strip()
    content = (body.get("content") or "").strip()
    if not name or not content:
        raise HTTPException(400, "名称和内容不能为空")
    db.update_prompt_template(tpl_id, name, body.get("category", ""), content)
    db.add_audit_log(user["id"], user["username"], "update_prompt_template", target=name)
    return {"status": "ok"}


@app.delete("/api/prompt-templates/{tpl_id}")
async def delete_prompt_template(tpl_id: int, user=Depends(get_current_user)):
    db.delete_prompt_template(tpl_id)
    db.add_audit_log(user["id"], user["username"], "delete_prompt_template", target=str(tpl_id))
    return {"status": "ok"}


# ==================== AI Chat ====================

@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest, user=Depends(get_current_user)):
    """多轮 AI 对话 + 快捷指令。"""
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    outputs = await ai_engine.chat_platforms(
        messages=messages,
        context=req.context or "",
        command=req.command,
        tone=req.tone,
        length=req.length,
        platforms=[p.value for p in req.platforms],
        topic=req.topic,
        assets=db.list_assets(status="active"),
    )
    for item in outputs:
        if item["platform"] == "xiaohongshu" and item.get("title") != "生成失败":
            item["image_pages"], item["attachments"] = render_carousel(
                item["title"], item.get("image_pages"), STATIC_DIR,
            )
    first = outputs[0]
    context_content = "\n\n".join(
        f"[{item['platform']}]\n{item['title']}\n{item['body']}"
        for item in outputs
    )
    return {
        "content": context_content,
        "title": first["title"], "body": first["body"],
        "hashtags": first["hashtags"], "outputs": outputs,
    }


# ==================== Static Assets ====================

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
