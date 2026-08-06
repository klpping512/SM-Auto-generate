"""第 3 批：小红书发布台账 + 周复盘导出。"""
from __future__ import annotations

import auth
from fastapi.testclient import TestClient


def _seed_published_xhs(tmp_db, *, title, body="正文", account_name="号甲", created_by=None, seo_meta=None, target_account_id=None):
    if target_account_id is None and account_name:
        tmp_db.create_account("xiaohongshu", account_name, f"xhs-{account_name}", owner_id=created_by)
        with tmp_db.get_conn() as conn:
            target_account_id = conn.execute(
                "SELECT id FROM accounts WHERE name=? ORDER BY id DESC LIMIT 1", (account_name,),
            ).fetchone()["id"]
    if seo_meta is None:
        seo_meta = {"main": "南非清关", "longtail": ["清关费用"]}
    qid = tmp_db.add_to_queue(
        title=title, body=body, platform="xiaohongshu",
        status="published", created_by=created_by,
        target_account_id=target_account_id,
        seo_meta=seo_meta,
    )
    tmp_db.add_publish_log(qid, "xiaohongshu", title, "published")
    return qid, target_account_id


def test_ensure_xhs_ledger_prefills_and_idempotent(tmp_db):
    uid = tmp_db.create_user("ledger-u", "hash", "editor", "U")
    qid, _ = _seed_published_xhs(tmp_db, title="清关攻略", created_by=uid, account_name="矩阵1号")
    lid1 = tmp_db.ensure_xhs_ledger(qid)
    lid2 = tmp_db.ensure_xhs_ledger(qid)
    assert lid1 is not None and lid1 == lid2
    row = tmp_db.get_xhs_ledger(lid1)
    assert row["title"] == "清关攻略"
    assert row["account_name"] == "矩阵1号"
    assert row["published_on"]  # UTC date from publish_log
    assert row["seo_meta"]["main"] == "南非清关"
    assert row["created_by"] == uid
    assert row["reads"] == 0
    assert row["topic_level"] == ""


def test_ensure_rejects_non_xhs_or_unpublished(tmp_db):
    qid = tmp_db.add_to_queue(title="抖音", body="b", platform="douyin", status="published")
    tmp_db.add_publish_log(qid, "douyin", "抖音", "published")
    assert tmp_db.ensure_xhs_ledger(qid) is None

    qid2 = tmp_db.add_to_queue(title="草稿", body="b", platform="xiaohongshu", status="draft")
    assert tmp_db.ensure_xhs_ledger(qid2) is None


def test_list_date_bounds_inclusive(tmp_db):
    q1, _ = _seed_published_xhs(tmp_db, title="A", account_name="a1")
    q2, _ = _seed_published_xhs(tmp_db, title="B", account_name="a2")
    l1 = tmp_db.ensure_xhs_ledger(q1)
    l2 = tmp_db.ensure_xhs_ledger(q2)
    with tmp_db.get_conn() as conn:
        conn.execute("UPDATE xhs_ledger SET published_on='2026-08-01' WHERE id=?", (l1,))
        conn.execute("UPDATE xhs_ledger SET published_on='2026-08-07' WHERE id=?", (l2,))
    mid = tmp_db.list_xhs_ledger(from_date="2026-08-01", to_date="2026-08-07")
    assert {r["id"] for r in mid} == {l1, l2}
    only = tmp_db.list_xhs_ledger(from_date="2026-08-01", to_date="2026-08-01")
    assert [r["id"] for r in only] == [l1]


def test_update_whitelist_and_updated_at(tmp_db):
    qid, _ = _seed_published_xhs(tmp_db, title="U", account_name="u1")
    lid = tmp_db.ensure_xhs_ledger(qid)
    before = tmp_db.get_xhs_ledger(lid)["updated_at"]
    tmp_db.update_xhs_ledger(lid, {"reads": 100, "verdict_48h": "达标", "topic_level": "S"})
    row = tmp_db.get_xhs_ledger(lid)
    assert row["reads"] == 100
    assert row["verdict_48h"] == "达标"
    assert row["updated_at"] >= before
    try:
        tmp_db.update_xhs_ledger(lid, {"title": "hack"})
        assert False, "should reject"
    except ValueError as exc:
        assert "不允许" in str(exc)


def test_candidates_excludes_archived(tmp_db):
    q_open, _ = _seed_published_xhs(tmp_db, title="待建", account_name="c1")
    q_done, _ = _seed_published_xhs(tmp_db, title="已建", account_name="c2")
    tmp_db.ensure_xhs_ledger(q_done)
    cands = tmp_db.list_xhs_ledger_candidates()
    ids = {c["queue_id"] for c in cands}
    assert q_open in ids
    assert q_done not in ids


def test_weekly_summary_top_bottom_cover_keyword(tmp_db):
    rows_spec = [
        ("高阅读", 300, "大字报", "南非清关", "达标"),
        ("中阅读", 100, "清单体", "南非清关", "未达标"),
        ("低阅读", 10, "大字报", "", "待判定"),
        ("更低", 5, "对比图", "德班港", "达标"),
    ]
    for i, (title, reads, cover, main, verdict) in enumerate(rows_spec):
        qid, _ = _seed_published_xhs(
            tmp_db, title=title, account_name=f"n{i}",
            seo_meta={"main": main} if main else {"main": ""},
        )
        lid = tmp_db.ensure_xhs_ledger(qid)
        tmp_db.update_xhs_ledger(lid, {
            "reads": reads, "likes_saves": 10, "comments": 5,
            "cover_type": cover, "verdict_48h": verdict,
        })
        with tmp_db.get_conn() as conn:
            conn.execute("UPDATE xhs_ledger SET published_on='2026-08-05' WHERE id=?", (lid,))

    # 预建零阅读行：互动率应为 0
    q0, _ = _seed_published_xhs(tmp_db, title="零阅读", account_name="z0")
    l0 = tmp_db.ensure_xhs_ledger(q0)
    with tmp_db.get_conn() as conn:
        conn.execute("UPDATE xhs_ledger SET published_on='2026-08-05' WHERE id=?", (l0,))

    summary = tmp_db.weekly_xhs_ledger_summary("2026-08-01", "2026-08-10")
    assert summary["overview"]["count"] == 5
    assert summary["overview"]["passed"] == 2
    assert summary["top"][0]["title"] == "高阅读"
    assert summary["bottom"][0]["title"] == "零阅读" or summary["bottom"][0]["reads"] == 0
    assert any(c["cover_type"] == "大字报" and c["count"] == 2 for c in summary["cover_dist"])
    kw = {k["main"]: k for k in summary["keyword_perf"]}
    assert "南非清关" in kw
    assert "—" in kw  # 无主词
    # 含零阅读：平均互动率有限
    assert summary["overview"]["avg_interaction_rate"] >= 0


def test_interaction_rate_zero_reads():
    import database as db
    assert db._interaction_rate(0, 10, 5) == 0.0
    assert abs(db._interaction_rate(100, 10, 5) - 0.15) < 1e-9


def test_export_api_empty_and_with_data(tmp_db, monkeypatch):
    import app as app_module

    tmp_db.create_user("adminx", auth.hash_password("pw12345"), "admin", "Admin")
    client = TestClient(app_module.app)
    token = client.post("/api/auth/login", json={"username": "adminx", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    empty = client.get("/api/xhs/ledger/export", headers=headers)
    assert empty.status_code == 200
    text = empty.content.decode("utf-8-sig")
    assert "# 概览" in text
    assert "# Top 选题（按阅读）" in text
    assert "# Bottom 选题（按阅读）" in text
    assert "# 封面类型分布" in text
    assert "# 关键词表现" in text

    qid, _ = _seed_published_xhs(tmp_db, title="导出行", account_name="ex1")
    lid = tmp_db.ensure_xhs_ledger(qid)
    tmp_db.update_xhs_ledger(lid, {"reads": 50, "cover_type": "问答体", "verdict_48h": "达标"})
    filled = client.get("/api/xhs/ledger/export?from=2020-01-01&to=2099-12-31", headers=headers)
    assert filled.status_code == 200
    body = filled.content.decode("utf-8-sig")
    assert "导出行" in body
    assert filled.headers["content-type"].startswith("text/csv")


async def test_scheduler_success_calls_ensure(tmp_db, monkeypatch):
    """scheduler 成功分支必须预建台账（本批显式触点）。"""
    import scheduler as sched
    import publisher
    import ratelimit

    uid = tmp_db.create_user("sch", "hash", "editor", "S")
    qid = tmp_db.add_to_queue(
        title="定时稿", body="b", platform="xiaohongshu",
        status="queued", created_by=uid, scheduled_at="2020-01-01 00:00",
        attachments=[{"type": "image", "path": "x.png", "asset_id": 1}],
    )

    async def fake_dispatch(**kwargs):
        return {"success": True, "platform": "xiaohongshu"}

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(publisher, "dispatch", fake_dispatch)
    monkeypatch.setattr(sched, "send_success_notify", _noop)
    monkeypatch.setattr(sched.truth_guard, "publish_error", lambda item: None)
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (True, "ok"))

    await sched.check_scheduled_publish()
    row = tmp_db.get_xhs_ledger_by_queue(qid)
    assert row is not None
    assert row["title"] == "定时稿"
    assert row["created_by"] == uid
