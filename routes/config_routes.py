"""Runtime configuration routes."""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

import ai_engine
import database as db
import scheduler as sched
from auth import require_role
from models import UserRole

router = APIRouter()


@router.post("/api/config/apikey")
async def set_api_key_endpoint(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    key = body.get("key", "")
    if key:
        ai_engine.set_api_key(key)
        db.add_audit_log(user["id"], user["username"], "set_api_key")
        os.environ["DASHSCOPE_API_KEY"] = key
        return {"status": "ok", "message": "百炼 API key 已加载"}
    raise HTTPException(400, "Missing key")


@router.post("/api/config/dashscope-key")
async def set_dashscope_key(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    key = str(body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "Missing key")
    os.environ["DASHSCOPE_API_KEY"] = key
    ai_engine.set_api_key(key)
    db.add_audit_log(user["id"], user["username"], "set_dashscope_key")
    return {"status": "ok", "persistence": "runtime_only", "message": "仅本次运行生效（环境变量）"}


@router.post("/api/config/mimo-key")
async def set_mimo_key(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    key = str(body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "Missing key")
    os.environ["MIMO_API_KEY"] = key
    db.add_audit_log(user["id"], user["username"], "set_mimo_key")
    return {"status": "ok", "persistence": "runtime_only", "message": "仅本次运行生效（环境变量）"}


@router.post("/api/config/notification")
async def save_notification_config(body: dict, user=Depends(require_role(UserRole.ADMIN))):
    """保存通知告警配置（写入环境变量，运行时生效）。"""
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
    if "feishu_webhook" in body:
        os.environ["FEISHU_WEBHOOK"] = body["feishu_webhook"]
    if "wecom_webhook" in body:
        os.environ["WECOM_WEBHOOK"] = body["wecom_webhook"]
    sched.SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.163.com")
    sched.SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
    sched.SMTP_USER = os.environ.get("SMTP_USER", "")
    sched.SMTP_PASS = os.environ.get("SMTP_PASS", "")
    sched.ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
    sched.FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
    sched.WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")
    db.add_audit_log(user["id"], user["username"], "save_notification_config")
    return {"status": "ok"}


@router.post("/api/config/notification/test")
async def test_notification(body: dict = None, user=Depends(require_role(UserRole.ADMIN))):
    """发送测试通知。body.channel: all/email/feishu/wecom（默认 all）。"""
    channel = (body or {}).get("channel", "all")
    subject = "[SA-LogiFlow] 测试通知"
    text = "这是一条测试通知，收到说明该渠道配置成功。\n时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

