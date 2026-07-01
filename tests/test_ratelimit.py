from datetime import datetime
import ratelimit


def test_defaults():
    assert ratelimit.DAILY_LIMIT == 10
    assert ratelimit.MIN_INTERVAL_MIN == 30
    assert ratelimit.JITTER_MIN == 5


def test_ok_when_empty(tmp_db):
    ok, reason = ratelimit.can_publish_now("reddit")
    assert ok is True and reason == "ok"


def test_blocked_by_daily_limit(tmp_db, monkeypatch):
    monkeypatch.setattr(ratelimit, "DAILY_LIMIT", 2)
    for i in range(1, 3):
        tmp_db.add_publish_log(i, "reddit", "t", "published")
    ok, reason = ratelimit.can_publish_now("reddit")
    assert ok is False and "上限" in reason


def test_blocked_by_min_interval(tmp_db):
    tmp_db.add_publish_log(1, "reddit", "t", "published")
    ok, reason = ratelimit.can_publish_now("reddit")
    assert ok is False and "分钟" in reason


def test_next_run_time_interval_and_jitter():
    now = datetime(2026, 7, 1, 12, 0)
    got = ratelimit.next_run_time(now, jitter_fn=lambda lo, hi: 3)
    assert got == "2026-07-01 12:33"
