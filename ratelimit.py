"""发布频控：每日上限 / 最小间隔 / 随机抖动。第一期用常量 + 环境变量覆盖。"""
import os
import random
from datetime import datetime, timedelta

import database as db

DAILY_LIMIT = int(os.environ.get("PUBLISH_DAILY_LIMIT", "10"))
MIN_INTERVAL_MIN = int(os.environ.get("PUBLISH_MIN_INTERVAL_MIN", "30"))
JITTER_MIN = int(os.environ.get("PUBLISH_JITTER_MIN", "5"))


def can_publish_now(platform: str) -> tuple[bool, str]:
    count = db.count_published_today(platform)
    if count >= DAILY_LIMIT:
        return False, f"今日已达上限 {count}/{DAILY_LIMIT}"
    mins = db.minutes_since_last_publish(platform)
    if mins is not None and mins < MIN_INTERVAL_MIN:
        return False, f"距上次发布仅 {mins:.0f} 分钟（需 ≥{MIN_INTERVAL_MIN}）"
    return True, "ok"


def next_run_time(now: datetime, jitter_fn=random.randint) -> str:
    """顺延时间 = now + 最小间隔 + [-抖动,+抖动]，格式对齐 queue.scheduled_at。"""
    delay = MIN_INTERVAL_MIN + jitter_fn(-JITTER_MIN, JITTER_MIN)
    return (now + timedelta(minutes=delay)).strftime("%Y-%m-%d %H:%M")
