"""第 2 批 2C：差异化守卫（零表结构新增）。"""
import json

import xhs_diff_guard as guard


def _publish_xhs(tmp_db, *, account_id, title, body, attachments):
    """写入一条今日 published 记录（publish_log JOIN queue）。"""
    queue_id = tmp_db.add_to_queue(
        title=title, body=body, platform="xiaohongshu",
        status="published", created_by=None,
        attachments=attachments, target_account_id=account_id,
    )
    tmp_db.add_publish_log(queue_id, "xiaohongshu", title, "published")
    return tmp_db.get_queue_item_by_id(queue_id)


def test_content_fingerprint_normalizes_whitespace_and_fullwidth():
    a = guard.content_fingerprint("南非清关", "今天 提醒一下")
    b = guard.content_fingerprint("南非清关", "今天提醒一下")
    c = guard.content_fingerprint("南非清关", "今天　提醒一下")  # 全角空格
    d = guard.content_fingerprint("南非清关", "今天提醒一下\n")
    e = guard.content_fingerprint("南非清关#", "今天提醒一下")  # 仅去 #
    assert a == b == c == d == e


def test_account_daily_count_and_check_blocks_third(tmp_db):
    _publish_xhs(tmp_db, account_id=101, title="A1", body="b1", attachments=[])
    _publish_xhs(tmp_db, account_id=101, title="A2", body="b2", attachments=[])
    assert guard.account_daily_count(tmp_db, 101) == 2
    ok, reason = guard.check(
        {"title": "A3", "body": "b3", "attachments": []},
        tmp_db, 101,
    )
    assert ok is False
    assert "单号今日已达上限" in reason
    assert guard.account_daily_count(tmp_db, 102) == 0


def test_fingerprint_blocks_same_copy_today(tmp_db):
    _publish_xhs(tmp_db, account_id=201, title="同文案", body="正文内容", attachments=[])
    ok, reason = guard.check(
        {"title": "同文案", "body": "正文内容", "attachments": []},
        tmp_db, 202,
    )
    assert ok is False
    assert "同文案" in reason


def test_asset_matrix_same_account_counts_once(tmp_db):
    att = [{"type": "image", "path": "x.png", "asset_id": 9}]
    _publish_xhs(tmp_db, account_id=301, title="t1", body="b1", attachments=att)
    _publish_xhs(tmp_db, account_id=301, title="t2", body="b2", attachments=att)
    counts = guard.asset_matrix_count(tmp_db, [9])
    assert counts[9] == 1


def test_asset_matrix_blocks_fourth_account(tmp_db):
    att = [{"type": "image", "path": "x.png", "asset_id": 77}]
    for i, account_id in enumerate((401, 402, 403), start=1):
        _publish_xhs(
            tmp_db, account_id=account_id,
            title=f"t{i}", body=f"b{i}", attachments=att,
        )
    assert guard.asset_matrix_count(tmp_db, [77])[77] == 3
    ok, reason = guard.check(
        {"title": "t4", "body": "b4", "attachments": att},
        tmp_db, 404,
    )
    assert ok is False
    assert "3 号上限" in reason
    assert "77" in reason


def test_check_allows_when_under_limits(tmp_db):
    ok, reason = guard.check(
        {"title": "新文案", "body": "全新内容", "attachments": [{"asset_id": 1}]},
        tmp_db, 501,
    )
    assert ok is True
    assert reason == ""


def test_legacy_attachments_without_asset_id_skip_asset_rule(tmp_db):
    """旧条目无 asset_id 不误拦素材维度。"""
    _publish_xhs(
        tmp_db, account_id=601, title="旧图1", body="b1",
        attachments=[{"type": "image", "path": "a.png"}],
    )
    _publish_xhs(
        tmp_db, account_id=602, title="旧图2", body="b2",
        attachments=[{"type": "image", "path": "b.png"}],
    )
    _publish_xhs(
        tmp_db, account_id=603, title="旧图3", body="b3",
        attachments=[{"type": "image", "path": "c.png"}],
    )
    ok, reason = guard.check(
        {
            "title": "新文案无 id",
            "body": "新正文",
            "attachments": [{"type": "image", "path": "d.png"}],
        },
        tmp_db, 604,
    )
    assert ok is True


def test_seo_meta_roundtrip_on_queue(tmp_db):
    meta = {"main": "南非清关", "longtail": ["清关费用"], "positions": ["title"]}
    qid = tmp_db.add_to_queue(
        title="t", body="b", platform="xiaohongshu",
        seo_meta=meta, attachments=[],
    )
    item = tmp_db.get_queue_item_by_id(qid)
    assert item["seo_meta"] == meta


def test_seo_lexicon_seeded(tmp_db):
    rows = tmp_db.list_xhs_seo_lexicon()
    assert len(rows) >= 10
    assert any(r["keyword"] == "南非清关" for r in rows)
    hits = tmp_db.match_xhs_seo_lexicon("关于清关的选题", limit=3)
    assert hits
    assert hits[0]["kind"] in {"main", "longtail", "scene"}


def test_utc_yesterday_not_counted_in_daily(tmp_db):
    """R2：昨天 UTC 的 published 不计今日；今日正常计入。"""
    q_old = tmp_db.add_to_queue(
        title="昨文", body="b", platform="xiaohongshu",
        status="published", target_account_id=701, attachments=[],
    )
    with tmp_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO publish_log (queue_id, platform, title, status, published_at) "
            "VALUES (?,?,?,?, datetime('now','-1 day'))",
            (q_old, "xiaohongshu", "昨文", "published"),
        )
    assert guard.account_daily_count(tmp_db, 701) == 0

    _publish_xhs(tmp_db, account_id=701, title="今文", body="b2", attachments=[])
    assert guard.account_daily_count(tmp_db, 701) == 1


async def test_scheduler_blocks_third_xhs_without_dispatch(tmp_db, monkeypatch):
    """R1：同账号今日已发 2 篇 → 第 3 篇定时不进 dispatch，status 保持 queued。"""
    import scheduler as sched
    import publisher
    import ratelimit

    _publish_xhs(tmp_db, account_id=801, title="S1", body="b1", attachments=[])
    _publish_xhs(tmp_db, account_id=801, title="S2", body="b2", attachments=[])
    qid = tmp_db.add_to_queue(
        title="S3", body="b3", platform="xiaohongshu",
        status="queued", scheduled_at="2020-01-01 00:00",
        target_account_id=801,
        attachments=[{"type": "image", "path": "x.png", "asset_id": 1}],
    )

    called = {"n": 0}

    async def boom(**kwargs):
        called["n"] += 1
        raise AssertionError("守卫命中时不应 dispatch")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(publisher, "dispatch", boom)
    monkeypatch.setattr(sched, "send_success_notify", _noop)
    monkeypatch.setattr(sched.truth_guard, "publish_error", lambda item: None)
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (True, "ok"))

    await sched.check_scheduled_publish()
    assert called["n"] == 0
    row = tmp_db.get_queue_item_by_id(qid)
    assert row["status"] == "queued"
    assert "单号今日已达上限" in (row.get("error_msg") or "")


async def test_scheduler_non_xhs_skips_diff_guard(tmp_db, monkeypatch):
    """R1：非小红书定时条目不受 diff 守卫影响。"""
    import scheduler as sched
    import publisher
    import ratelimit

    qid = tmp_db.add_to_queue(
        title="抖音定时", body="b", platform="douyin",
        status="queued", scheduled_at="2020-01-01 00:00",
        target_account_id=901,
        attachments=[{"type": "video", "path": "v.mp4"}],
    )
    called = {"n": 0}

    async def fake_dispatch(**kwargs):
        called["n"] += 1
        return {"success": True, "platform": "douyin"}

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(publisher, "dispatch", fake_dispatch)
    monkeypatch.setattr(sched, "send_success_notify", _noop)
    monkeypatch.setattr(sched.truth_guard, "publish_error", lambda item: None)
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (True, "ok"))

    await sched.check_scheduled_publish()
    assert called["n"] == 1
    assert tmp_db.get_queue_item_by_id(qid)["status"] == "published"
