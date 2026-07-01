"""定时发布调度 + 通知告警 - SA-LogiFlow v2.0."""
import asyncio
import logging
import smtplib
import os
from email.mime.text import MIMEText
from datetime import datetime

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
import publisher

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

        result = await publisher.dispatch(
            platform=platform,
            title=item["title"],
            content=item["body"],
            tags=item.get("hashtags", []),
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
    scheduler.start()
    logger.info("定时调度器已启动（每分钟检查定时发布任务）")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("定时调度器已停止")
