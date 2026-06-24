"""SA-LogiFlow MVP - FastAPI Backend."""
import os
import json
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

import database as db
import ai_engine
import publisher
from models import (
    GenerateRequest, GenerateResponse, GeneratedContent,
    QueueCreateRequest, QueueItem, AccountCreateRequest,
    Platform, TopicCategory,
)
from topic_library import TOPIC_CATEGORIES, TOPIC_MAP


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Try to load API key from env
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        ai_engine.set_api_key(key)
    print("✅ SA-LogiFlow MVP started")
    print(f"📊 Database: {db.DB_PATH}")
    yield


app = FastAPI(title="SA-LogiFlow", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


# ==================== Pages ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/home.html", response_class=HTMLResponse)
async def home():
    return FileResponse(STATIC_DIR / "home.html")


@app.get("/accounts.html", response_class=HTMLResponse)
async def accounts_page():
    return FileResponse(STATIC_DIR / "accounts.html")


@app.get("/editor.html", response_class=HTMLResponse)
async def editor_page():
    return FileResponse(STATIC_DIR / "editor.html")


@app.get("/queue.html", response_class=HTMLResponse)
async def queue_page():
    return FileResponse(STATIC_DIR / "queue.html")


@app.get("/config.html", response_class=HTMLResponse)
async def config_page():
    return FileResponse(STATIC_DIR / "config.html")


# ==================== API: Dashboard ====================

@app.get("/api/dashboard")
async def dashboard():
    """Get dashboard stats."""
    stats = db.get_queue_stats()
    accounts = db.get_accounts()
    recent = db.get_recent_activity(5)

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
    """List all topic categories."""
    return [cat.model_dump() for cat in TOPIC_CATEGORIES]


@app.get("/api/topics/{category_id}")
async def get_topic(category_id: str):
    """Get topics in a category."""
    cat = TOPIC_MAP.get(category_id)
    if not cat:
        raise HTTPException(404, f"Category '{category_id}' not found")
    return cat.model_dump()


# ==================== API: AI Generation ====================

@app.post("/api/generate", response_model=GenerateResponse)
async def generate_content(req: GenerateRequest):
    """Generate AI content for specified platforms."""
    if not ai_engine.DEEPSEEK_API_KEY:
        # Use fallback content
        contents = []
        for p in req.platforms:
            contents.append(ai_engine._fallback_content(p, req.topic, req.category))
        return GenerateResponse(
            topic=req.topic,
            contents=contents,
            generated_at=datetime.now().isoformat(),
        )

    contents = await ai_engine.generate_content(
        topic=req.topic,
        category=req.category,
        platforms=req.platforms,
        tone=req.tone,
        length=req.length,
    )
    return GenerateResponse(
        topic=req.topic,
        contents=contents,
        generated_at=datetime.now().isoformat(),
    )


@app.post("/api/config/apikey")
async def set_api_key(body: dict):
    """Set DeepSeek API key."""
    key = body.get("key", "")
    if key:
        ai_engine.set_api_key(key)
        return {"status": "ok", "message": "API key set"}
    raise HTTPException(400, "Missing key")


# ==================== API: Accounts ====================

@app.get("/api/accounts")
async def list_accounts(platform: str = None):
    """List all accounts, optionally filtered by platform."""
    return db.get_accounts(platform)


@app.post("/api/accounts")
async def create_account(req: AccountCreateRequest):
    """Create a new account."""
    try:
        db.create_account(req.platform.value, req.name, req.account_id, req.config_summary)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int):
    """Delete an account."""
    db.delete_account(account_id)
    return {"status": "ok"}


# ==================== API: Queue ====================

@app.get("/api/queue")
async def list_queue(status: str = None, platform: str = None):
    """List queue items."""
    return db.get_queue(status, platform)


@app.post("/api/queue")
async def add_queue(req: QueueCreateRequest):
    """Add items to the publishing queue."""
    for platform in req.platforms:
        db.add_to_queue(
            title=req.title,
            body=req.body,
            platform=platform.value,
            hashtags=req.hashtags,
            scheduled_at=req.scheduled_at,
        )
    return {"status": "ok", "added": len(req.platforms)}


@app.put("/api/queue/{item_id}/status")
async def update_status(item_id: int, body: dict):
    """Update queue item status."""
    status = body.get("status")
    error_msg = body.get("error_msg")
    db.update_queue_status(item_id, status, error_msg)
    return {"status": "ok"}


@app.delete("/api/queue/{item_id}")
async def delete_queue_item(item_id: int):
    """Delete a queue item."""
    db.delete_queue_item(item_id)
    return {"status": "ok"}



# ==================== API: Publish ====================

@app.post("/api/publish/{item_id}")
async def publish_item(item_id: int):
    """Publish a single queue item via huimei."""
    items = db.get_queue()
    item = next((i for i in items if i["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "Queue item not found")

    result = await publisher.publish_via_huimei(
        platform=item["platform"],
        title=item["title"],
        content=item["body"],
        tags=item.get("hashtags", []),
    )

    if result["success"]:
        db.update_queue_status(item_id, "published")
    else:
        db.update_queue_status(item_id, "failed", result.get("error", "Unknown error"))

    return result


@app.post("/api/publish/batch")
async def publish_batch(body: dict):
    """Publish multiple queue items."""
    item_ids = body.get("item_ids", [])
    results = []
    for item_id in item_ids:
        items = db.get_queue()
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            results.append({"item_id": item_id, "success": False, "error": "Not found"})
            continue

        result = await publisher.publish_via_huimei(
            platform=item["platform"],
            title=item["title"],
            content=item["body"],
            tags=item.get("hashtags", []),
        )

        if result["success"]:
            db.update_queue_status(item_id, "published")
        else:
            db.update_queue_status(item_id, "failed", result.get("error", "Unknown error"))

        results.append({"item_id": item_id, **result})

    return {"results": results}


@app.get("/api/publish/status")
async def publish_status():
    """Check huimei status and supported platforms."""
    status = await publisher.check_huimei_status()
    return {
        "huimei": status,
        "supported_platforms": list(publisher.PLATFORM_MAP.keys()),
        "external_platforms": list(publisher.EXTERNAL_PLATFORMS),
    }


@app.get("/api/publish/accounts")
async def publish_accounts():
    """List huimei linked accounts."""
    return await publisher.list_huimei_accounts()


# ==================== Static Assets ====================

# Serve any remaining static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
