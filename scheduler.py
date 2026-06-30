"""定时发布调度 + 通知告警 - SA-LogiFlow v2.0."""
import asyncio
import logging
import smtplib
import os
from email.mime.text import MIMEText
from datetime import datetime

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

scheduler = AsyncIOScheduler()


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

        result = await publisher.publish_via_huimei(
            platform=platform,
            title=item["title"],
            content=item["body"],
            tags=item.get("hashtags", []),
        )

        if result["success"]:
            db.update_queue_status(item_id, "published")
            db.add_publish_log(item_id, platform, item["title"], "published")
            logger.info("定时发布成功: id=%d", item_id)
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


async def send_alert(item: dict, error: str):
    """发布失败时发送告警邮件。"""
    if not SMTP_USER or not ALERT_EMAIL:
        logger.debug("未配置邮件，跳过告警")
        return

    subject = f"[SA-LogiFlow] 发布失败告警 - {item['platform']}"
    body = f"""发布失败通知

平台: {item['platform']}
标题: {item['title']}
错误: {error}
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

请登录系统查看并处理。
"""

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


def start_scheduler():
    """启动定时调度器。"""
    # 每分钟检查一次定时发布任务
    scheduler.add_job(check_scheduled_publish, "interval", minutes=1, id="scheduled_publish", replace_existing=True)
    scheduler.start()
    logger.info("定时调度器已启动（每分钟检查定时发布任务）")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("定时调度器已停止")
