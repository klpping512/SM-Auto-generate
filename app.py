"""SA-LogiFlow v2.0 - FastAPI Backend."""
import logging
import os
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

import database as db
import ai_engine
import publisher
import scheduler as sched
from auth import (
    create_access_token, verify_password, hash_password,
    get_current_user, require_role,
)
from models import (
    GenerateRequest, GenerateResponse,
    QueueCreateRequest, AccountCreateRequest, ReviewRequest,
    LoginRequest, RegisterRequest, TokenResponse,
    UserRole,
)
from topic_library import TOPIC_CATEGORIES, TOPIC_MAP

# ==================== Logging ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # 确保默认管理员存在
    if not db.get_user_by_username("admin"):
        db.create_user("admin", hash_password("admin123"), "admin", "系统管理员")
        logger.info("已创建默认管理员: admin / admin123")
    # 从环境变量加载 API key
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        ai_engine.set_api_key(key)
        logger.info("DeepSeek API key 已加载")
    # 启动定时调度器
    sched.start_scheduler()
    logger.info("SA-LogiFlow v2.0 启动完成 | 数据库: %s", db.DB_PATH)
    yield
    sched.stop_scheduler()
    logger.info("SA-LogiFlow v2.0 关闭")


app = FastAPI(title="SA-LogiFlow", version="2.0.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


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
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/{page_name}.html", response_class=HTMLResponse)
async def page(page_name: str):
    file_path = STATIC_DIR / f"{page_name}.html"
    if not file_path.exists():
        raise HTTPException(404, f"Page '{page_name}' not found")
    return FileResponse(file_path)


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

    if not ai_engine.DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API key 未配置，使用 fallback 模板")
        contents = [ai_engine._fallback_content(p, req.topic, req.category) for p in req.platforms]
        return GenerateResponse(topic=req.topic, contents=contents, generated_at=datetime.now().isoformat(), source="fallback")

    contents = await ai_engine.generate_content(
        topic=req.topic, category=req.category, platforms=req.platforms,
        tone=req.tone, length=req.length, instruction=req.instruction,
        kb_context=kb_context,
    )
    db.add_audit_log(user["id"], user["username"], "generate_content", target=req.topic)
    return GenerateResponse(topic=req.topic, contents=contents, generated_at=datetime.now().isoformat(), source="ai")


@app.post("/api/config/apikey")
async def set_api_key_endpoint(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    key = body.get("key", "")
    if key:
        ai_engine.set_api_key(key)
        db.add_audit_log(user["id"], user["username"], "set_api_key")
        return {"status": "ok", "message": "API key set"}
    raise HTTPException(400, "Missing key")


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
    return db.get_accounts(platform)


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
        db.add_to_queue(
            title=req.title, body=req.body, platform=platform.value,
            hashtags=req.hashtags, scheduled_at=req.scheduled_at,
            status=initial_status, created_by=user["id"],
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

@app.post("/api/publish/{item_id}")
async def publish_item(item_id: int, user=Depends(get_current_user)):
    item = db.get_queue_item_by_id(item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")

    result = await publisher.dispatch(
        platform=item["platform"], title=item["title"],
        content=item["body"], tags=item.get("hashtags", []),
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

        result = await publisher.dispatch(
            platform=item["platform"], title=item["title"],
            content=item["body"], tags=item.get("hashtags", []),
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


@app.get("/api/publish/status")
async def publish_status():
    status = await publisher.check_huimei_status()
    return {
        "huimei": status,
        "supported_platforms": list(publisher.PLATFORM_MAP.keys()),
        "external_platforms": list(publisher.EXTERNAL_PLATFORMS),
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


# ==================== Static Assets ====================

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
