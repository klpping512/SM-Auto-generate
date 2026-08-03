"""Acceptance tests for chat content-mode routing and comparison frameworks."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ai_engine
import app
import auth
import chat_intent


def test_classify_comparison_outranks_hotspot_markers():
    assert chat_intent.classify_content_mode("南非本地快递对比评测") == "comparison_research"
    assert chat_intent.classify_content_mode("最近哪家快递最好") == "comparison_research"


def test_classify_hotspot_requires_time_sensitive_markers():
    assert chat_intent.classify_content_mode("德班港拥堵最新") == "hotspot"
    assert chat_intent.classify_content_mode("昨晚道路中断影响交货") == "hotspot"
    assert chat_intent.classify_content_mode("南非海外仓介绍") == "evergreen"
    assert chat_intent.classify_content_mode("帮我写一段品牌问候") == "general_copy"


def test_assess_comparison_evidence_requires_candidates_plus_metrics_or_sources():
    thin = chat_intent.assess_comparison_evidence(
        [{"role": "user", "content": "南非本地快递对比评测"}],
        topic="南非本地快递对比评测",
    )
    assert thin["evidence_state"] == "insufficient"
    assert not thin["sufficient"]

    names_only = chat_intent.assess_comparison_evidence(
        [{"role": "user", "content": "对比 The Courier Guy 和 Fastway"}],
        topic="快递对比",
    )
    assert names_only["has_candidates"] is True
    assert names_only["sufficient"] is False

    with_source = chat_intent.assess_comparison_evidence(
        [{"role": "user", "content": "对比 The Courier Guy 和 Fastway，官网报价隔日达 R89，来源官网价目表 2026-07-01"}],
        topic="快递对比",
    )
    assert with_source["sufficient"] is True
    assert with_source["has_metrics"] and with_source["has_sources"]


def test_build_comparison_framework_has_no_fake_review_language():
    outputs = ai_engine.build_comparison_framework(
        "南非本地快递对比评测",
        ["xiaohongshu", "douyin"],
        {"has_candidates": False, "evidence_state": "insufficient"},
    )
    titles = " ".join(item["title"] for item in outputs)
    bodies = " ".join(item["body"] for item in outputs)
    assert "4家" not in titles
    assert "实测对比" not in titles
    assert "排名第一" not in titles
    assert "未生成服务商排名和推荐结论" in bodies
    assert all(item["result_kind"] == "framework" for item in outputs)
    assert "怎么选？先比较这几个关键维度" in outputs[0]["title"]


def test_framework_with_candidate_names_still_leaves_prices_blank():
    outputs = ai_engine.build_comparison_framework(
        "对比 The Courier Guy / Fastway / RAM",
        ["xiaohongshu"],
        {"has_candidates": True, "evidence_state": "insufficient"},
    )
    body = outputs[0]["body"]
    assert "价格/时效/排名仍留空" in body
    assert "R89" not in body
    assert "未生成服务商排名和推荐结论" in body


def test_enforce_comparison_authenticity_downgrades_fabricated_review():
    fabricated = [{
        "platform": "xiaohongshu",
        "title": "南非4家主流快递实测对比",
        "body": "我们测试了四家，综合评估后排名第一最稳。",
        "hashtags": ["实测", "最稳"],
        "scenes": [],
    }]
    evidence = {"sufficient": False, "evidence_state": "insufficient"}
    outputs, blocked = ai_engine.enforce_comparison_authenticity(fabricated, evidence)
    assert blocked is True
    assert outputs[0]["result_kind"] == "framework"
    assert "4家" not in outputs[0]["title"]
    assert "实测对比" not in outputs[0]["title"]
    assert "未生成服务商排名和推荐结论" in outputs[0]["body"]


def test_derive_result_state_priority():
    assert chat_intent.derive_result_state(
        content_mode="comparison_research",
        evidence_state="insufficient",
        hotspot_retrieval={"status": "queued"},
        authenticity_blocked=True,
    ) == "authenticity_blocked"
    assert chat_intent.derive_result_state(
        content_mode="comparison_research",
        evidence_state="insufficient",
        hotspot_retrieval={"status": "not_requested"},
    ) == "framework_pending_evidence"
    assert chat_intent.derive_result_state(
        content_mode="hotspot",
        evidence_state="insufficient",
        hotspot_retrieval={"status": "queued"},
    ) == "hotspot_retrieval_pending"


def _auth_client(tmp_db, username="cmp-editor"):
    tmp_db.create_user(username, auth.hash_password("pw12345"), "editor", "Cmp Editor")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": username, "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_ai_chat_comparison_without_evidence_returns_framework_and_skips_discovery(tmp_db, monkeypatch):
    client, headers = _auth_client(tmp_db)
    called = {"chat": 0, "hooks": 0}

    async def fake_chat(**_kwargs):
        called["chat"] += 1
        return [{"platform": "xiaohongshu", "title": "假评测", "body": "4家实测", "hashtags": [], "image_pages": []}]

    async def fake_hooks(*_args, **_kwargs):
        called["hooks"] += 1
        return {"status": "queued", "hooks": [], "video": {"status": "pending"}}

    monkeypatch.setattr(ai_engine, "chat_platforms", fake_chat)
    monkeypatch.setattr(app, "_retrieve_confirmed_chat_hooks", fake_hooks)

    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": "南非本地快递对比评测"}],
        "platforms": ["xiaohongshu"],
        "topic": "南非本地快递对比评测",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_mode"] == "comparison_research"
    assert payload["result_state"] == "framework_pending_evidence"
    assert payload["hotspot_retrieval"]["status"] == "not_requested"
    assert payload["evidence_state"]["evidence_state"] == "insufficient"
    assert called["chat"] == 0
    assert called["hooks"] == 0
    assert tmp_db.list_hotspot_discovery_requests() == []
    assert "4家" not in payload["outputs"][0]["title"]
    assert "实测对比" not in payload["outputs"][0]["title"]
    assert payload["outputs"][0]["result_kind"] == "framework"
    assert "未生成服务商排名和推荐结论" in payload["outputs"][0]["body"]


def test_ai_chat_comparison_with_evidence_allows_formal_path(tmp_db, monkeypatch):
    client, headers = _auth_client(tmp_db, "cmp-formal")
    topic = "对比 The Courier Guy 与 Fastway，官网报价隔日达 R89，来源官网价目表 2026-07-01"

    async def fake_chat(**_kwargs):
        return [{
            "platform": "xiaohongshu",
            "title": "两家快递资料对照",
            "body": "根据官网价目表，The Courier Guy 隔日达报价 R89（来源官网，测试日期 2026-07-01）。",
            "hashtags": ["资料对照"],
            "image_pages": [],
        }]

    monkeypatch.setattr(ai_engine, "chat_platforms", fake_chat)

    async def must_not_retrieve(*_a, **_k):
        raise AssertionError("comparison with evidence must not retrieve hotspot hooks")

    monkeypatch.setattr(app, "_retrieve_confirmed_chat_hooks", must_not_retrieve)

    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": topic}],
        "platforms": ["xiaohongshu"],
        "topic": topic,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_mode"] == "comparison_research"
    assert payload["evidence_state"]["sufficient"] is True
    assert payload["result_state"] == "formal_content"
    assert payload["hotspot_retrieval"]["status"] == "not_requested"
    assert "来源官网" in payload["outputs"][0]["body"]


def test_ai_chat_hotspot_topic_still_queues_discovery(tmp_db, monkeypatch):
    client, headers = _auth_client(tmp_db, "hot-editor")
    empty_funnel = {
        "scanned": 0, "scene_mismatch": 0, "relevance_low": 0, "not_playable": 0,
        "kind_filtered": 0, "duplicate_or_recent": 0, "passed": 0, "selected": 0,
    }
    monkeypatch.setattr(
        app, "_marketing_hook_candidates",
        lambda *_a, **_k: ([], "", [], empty_funnel),
    )
    refresh_calls = []
    monkeypatch.setattr(app.sched, "request_targeted_hotspot_refresh", lambda: refresh_calls.append(True) or True)

    async def generated(**_kwargs):
        return [{"platform": "xiaohongshu", "title": "港口提醒", "body": "请核实船期。", "hashtags": [], "image_pages": []}]

    monkeypatch.setattr(ai_engine, "chat_platforms", generated)
    topic = "德班港拥堵最新情况会影响交期吗？"
    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": topic}],
        "platforms": ["xiaohongshu"],
        "topic": topic,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_mode"] == "hotspot"
    assert payload["event_anchor"]["has_event_anchor"] is True
    assert payload["result_state"] == "hotspot_retrieval_pending"
    assert payload["hotspot_retrieval"]["status"] == "queued"
    assert payload["failure_class"] == "coverage_gap"
    assert refresh_calls == [True]
    assert tmp_db.list_hotspot_discovery_requests(status="pending")[0]["topic"] == topic


def test_ai_chat_broad_market_topic_needs_event_anchor_not_discovery(tmp_db, monkeypatch):
    client, headers = _auth_client(tmp_db, "market-editor")
    empty_funnel = {
        "scanned": 2, "scene_mismatch": 1, "relevance_low": 0, "not_playable": 0,
        "kind_filtered": 1, "duplicate_or_recent": 0, "passed": 0, "selected": 0,
    }
    monkeypatch.setattr(
        app, "_marketing_hook_candidates",
        lambda *_a, **_k: ([], "", [], empty_funnel),
    )
    refresh_calls = []
    monkeypatch.setattr(app.sched, "request_targeted_hotspot_refresh", lambda: refresh_calls.append(True) or True)

    async def generated(**_kwargs):
        return [{"platform": "xiaohongshu", "title": "市场开拓", "body": "先核实节点。", "hashtags": [], "image_pages": []}]

    monkeypatch.setattr(ai_engine, "chat_platforms", generated)
    topic = "从0到1开拓南非市场"
    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": topic}],
        "platforms": ["xiaohongshu"],
        "topic": topic,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_mode"] == "evergreen"
    assert payload["event_anchor"]["has_event_anchor"] is False
    assert payload["result_state"] == "topic_needs_event_anchor"
    assert payload["hotspot_retrieval"]["status"] == "not_requested"
    assert payload["failure_class"] == "no_event_anchor"
    assert refresh_calls == []
    assert tmp_db.list_hotspot_discovery_requests() == []
    assert "未启动" in (payload["hotspot_retrieval"].get("message") or "") or "点选" in (payload["hotspot_retrieval"].get("message") or "")
    assert payload["hotspot_retrieval"].get("funnel", {}).get("passed") == 0


def test_ai_chat_evergreen_tries_generic_hooks_but_skips_discovery(tmp_db, monkeypatch):
    client, headers = _auth_client(tmp_db, "ever-editor")
    called = {"hooks": 0}

    async def fake_hooks(*_a, **_k):
        called["hooks"] += 1
        return {
            "status": "not_requested",
            "failure_class": "no_event_anchor",
            "hooks": [],
            "video": {"status": "disabled"},
            "producible_topics": [{
                "topic": "德班港拥堵最新：港口拥堵时发货前要核对什么？",
                "scene": "port",
                "hook_event_id": 42,
                "hook_title": "德班港拥堵",
            }],
            "funnel": {"scanned": 1, "passed": 0, "selected": 0},
            "message": "当前话题没有自动命中可用 Hook。请在下方点选一个库内 Hook。",
        }

    async def generated(**_kwargs):
        return [{"platform": "xiaohongshu", "title": "介绍", "body": "先核实再写。", "hashtags": [], "image_pages": []}]

    monkeypatch.setattr(ai_engine, "chat_platforms", generated)
    monkeypatch.setattr(app, "_retrieve_confirmed_chat_hooks", fake_hooks)
    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": "帮我生成一个关于南非海外仓的介绍视频"}],
        "platforms": ["xiaohongshu"],
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_mode"] == "evergreen"
    assert called["hooks"] == 1
    assert payload["hotspot_retrieval"]["status"] == "not_requested"
    assert payload["result_state"] == "hook_selection_required"
    assert payload["producible_topics"]


def test_assess_event_anchor_and_result_states():
    broad = chat_intent.assess_event_anchor("从0到1开拓南非市场")
    assert broad["has_event_anchor"] is False
    port = chat_intent.assess_event_anchor("德班港拥堵最新")
    assert port["has_event_anchor"] is True
    assert chat_intent.derive_result_state(
        content_mode="evergreen",
        evidence_state="insufficient",
        hotspot_retrieval={"status": "not_requested", "failure_class": "no_event_anchor"},
        event_anchor=broad,
    ) == "topic_needs_event_anchor"
    assert chat_intent.derive_result_state(
        content_mode="evergreen",
        evidence_state="insufficient",
        hotspot_retrieval={
            "status": "not_requested",
            "failure_class": "no_event_anchor",
            "producible_topics": [{"hook_event_id": 1}],
        },
        event_anchor=broad,
    ) == "hook_selection_required"


def test_producible_topics_cluster_ready_hooks():
    import producible_topics

    events = [
        {
            "id": 1, "hotspot_id": 10, "review_status": "confirmed", "clip_status": "ready",
            "clip_path": "/x.mp4", "title_zh": "Beitbridge 卡车排队",
            "logistics_scenes": ["border"], "evidence": {"logistics_question": "口岸排队会影响清关吗？"},
        },
        {
            "id": 2, "hotspot_id": 11, "review_status": "confirmed", "clip_status": "ready",
            "clip_path": "/y.mp4", "title_zh": "德班港船期延误",
            "logistics_scenes": ["port"], "evidence": {},
        },
        {
            "id": 3, "hotspot_id": 12, "review_status": "confirmed", "clip_status": "ready",
            "clip_path": "/z.mp4", "title_zh": "听证会证词",
            "logistics_scenes": ["disruption"], "evidence": {"what_happened": "委员会听证会"},
        },
    ]
    recs = producible_topics.recommend_producible_topics(events, limit=5)
    assert 2 <= len(recs) <= 5
    assert any("Beitbridge" in item["topic"] or "口岸" in item["topic"] for item in recs)
    assert producible_topics.is_generic_logistics_eligible(events[0])
    assert not producible_topics.is_generic_logistics_eligible(events[2])


def test_discovery_status_api_and_misrouted_archive(tmp_db):
    admin_id = tmp_db.create_user("disc-admin", auth.hash_password("pw12345"), "admin", "Disc Admin")
    editor_id = tmp_db.create_user("disc-editor", auth.hash_password("pw12345"), "editor", "Disc Editor")
    client = TestClient(app.app)
    admin_token = client.post("/api/auth/login", json={"username": "disc-admin", "password": "pw12345"}).json()["access_token"]
    editor_token = client.post("/api/auth/login", json={"username": "disc-editor", "password": "pw12345"}).json()["access_token"]

    request = tmp_db.enqueue_hotspot_discovery_request("南非本地快递对比评测", requested_by=editor_id)
    foreign = tmp_db.enqueue_hotspot_discovery_request("德班港拥堵最新", requested_by=admin_id)

    denied = client.get(
        f"/api/hotspot-discovery-requests/{foreign['id']}",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert denied.status_code == 403

    ok = client.get(
        f"/api/hotspot-discovery-requests/{request['id']}",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "pending"

    archived = client.post(
        "/api/hotspot-discovery-requests/archive-misrouted-comparisons",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert archived.status_code == 200
    assert archived.json()["count"] == 1
    assert tmp_db.get_hotspot_discovery_request(request["id"])["status"] == "cancelled_misrouted"
    assert tmp_db.get_hotspot_discovery_request(foreign["id"])["status"] == "pending"


def test_chat_ui_uses_result_state_card_without_conflicting_peer_status():
    page = Path("static/chat.html").read_text(encoding="utf-8")
    assert "function resultStateCard" in page
    assert "assistantBubbleText" in page
    assert "framework_pending_evidence" in page
    assert "hook_selection_required" in page
    assert "bindHookToOriginalTopic" in page
    assert "producibleTopicsMarkup" in page
    assert "funnelDetailsMarkup" in page
    assert "promptComparisonEvidence" in page
    assert "/api/hotspot-discovery-requests/" in page
    assert "useProducibleTopic" not in page
    assert "已填入可生产选题" not in page
    # Editor transfer carries evidence gate.
    transfer = Path("static/editor-transfer.js").read_text(encoding="utf-8")
    assert "evidence_status" in transfer
    editor = Path("static/editor.html").read_text(encoding="utf-8")
    assert "evidenceGateBanner" in editor
    assert "证据不足的对比框架不可发布" in editor
