import scheduler
import ratelimit
import publisher


async def _noop(*a, **k):
    return None


async def test_defers_when_rate_limited(tmp_db, monkeypatch):
    tmp_db.add_to_queue("标题", "正文", "reddit", scheduled_at="2020-01-01 00:00", status="queued")
    item_id = tmp_db.get_queue("queued", "reddit")[0]["id"]
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (False, "今日已达上限 10/10"))
    monkeypatch.setattr(ratelimit, "next_run_time", lambda now, **k: "2099-01-01 00:00")

    async def boom(**kwargs):
        raise AssertionError("频控命中时不应发布")
    monkeypatch.setattr(publisher, "dispatch", boom)

    await scheduler.check_scheduled_publish()
    row = tmp_db.get_queue_item_by_id(item_id)
    assert row["status"] == "queued"
    assert row["scheduled_at"] == "2099-01-01 00:00"
    assert "顺延" in (row["error_msg"] or "")


async def test_marks_account_expired_on_login_error(tmp_db, monkeypatch):
    tmp_db.create_account("reddit", "主号", "rd_001")
    tmp_db.add_to_queue("标题", "正文", "reddit", scheduled_at="2020-01-01 00:00", status="queued")
    item_id = tmp_db.get_queue("queued", "reddit")[0]["id"]
    for _ in range(3):
        tmp_db.increment_retry_count(item_id)
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (True, "ok"))

    async def token_fail(**kwargs):
        return {"success": False, "platform": "reddit", "error": "token 过期，请重新登录"}
    monkeypatch.setattr(publisher, "dispatch", token_fail)
    monkeypatch.setattr(scheduler, "send_alert", _noop)

    await scheduler.check_scheduled_publish()
    assert tmp_db.get_accounts("reddit")[0]["status"] == "expired"
