import pytest

import video_renderer


def test_formal_video_target_ms_restores_from_snapshot_when_project_corrupted():
    project = {"target_duration_ms": 14_933}
    snapshot = {"target_duration_ms": 60_000}
    payload = {"target_duration_ms": 14_933, "duration_target_ms": 14_933}

    assert video_renderer.resolve_formal_video_target_ms(
        project=project, snapshot=snapshot, payload=payload,
    ) == 60_000


def test_normalize_revision_formal_target_restores_60000_from_14933():
    project = {"target_duration_ms": 14_933}
    snapshot = {"target_duration_ms": 60_000}
    payload = {"target_duration_ms": 14_933, "duration_target_ms": 14_933, "scenes": []}

    normalized = video_renderer.normalize_revision_formal_target(
        payload, project=project, snapshot=snapshot,
    )

    assert normalized["target_duration_ms"] == 60_000
    assert normalized["formal_target_duration_ms"] == 60_000
    assert normalized["duration_target_ms"] == 14_933


def test_delivery_readiness_false_when_duration_below_50s(tmp_db, monkeypatch):
    import asyncio
    import app

    hotspot_id, asset_id, events = _create_ready_chat_hook_pair(tmp_db)
    event = events[0]

    short_scenes = [
        {"evidence_type": "hotspot_video", "duration_ms": 7_000},
        {"evidence_type": "image", "duration_ms": 3_000},
        {"evidence_type": "image", "duration_ms": 3_000},
        {"evidence_type": "image", "duration_ms": 3_000},
    ]
    monkeypatch.setattr(app.hotspot_video_planner, "plan_followup_scenes", lambda *_a, **_k: short_scenes)
    monkeypatch.setattr(
        app.hotspot_video_planner,
        "describe_plan_adaptation",
        lambda *_a, **_k: {"adapted": True, "strategies": ["image_fill"]},
    )

    readiness = app._chat_video_delivery_readiness(
        "南非港口拥堵会怎样影响跨境订单",
        [event],
    )

    assert readiness["delivery_ready"] is False
    assert readiness["status"] == "needs_owned_media"
    assert readiness["coverage"]["duration_ms"] < 50_000
    assert "50–90" in readiness["message"] or "50-90" in readiness["message"]


def test_generate_topic_brief_video_rejects_short_plan_without_writing(tmp_db, monkeypatch):
    import asyncio
    import app

    client, headers, event = _topic_brief_client(tmp_db, event_title="货车排队等待筛查")
    brief = client.post("/api/topic-briefs", headers=headers, json={
        "raw_input": "南非港口拥堵会怎样影响跨境订单",
        "platforms": ["douyin"],
    }).json()["brief"]

    short_scenes = [
        {"evidence_type": "hotspot_video", "duration_ms": 7_000, "voiceover_max_chars": 40, "voiceover_min_chars": 10},
        {"evidence_type": "image", "duration_ms": 3_000, "voiceover_max_chars": 40, "voiceover_min_chars": 10},
        {"evidence_type": "image", "duration_ms": 3_000, "voiceover_max_chars": 40, "voiceover_min_chars": 10},
        {"evidence_type": "image", "duration_ms": 3_000, "voiceover_max_chars": 40, "voiceover_min_chars": 10},
    ]
    monkeypatch.setattr(app.hotspot_video_planner, "plan_followup_scenes", lambda *_a, **_k: short_scenes)
    monkeypatch.setattr(
        app.hotspot_video_planner,
        "describe_plan_adaptation",
        lambda *_a, **_k: {"adapted": True, "strategies": ["image_fill"]},
    )
    monkeypatch.setattr(app.hotspot_video_planner, "append_brand_endcard_scenes", lambda scenes: scenes)
    monkeypatch.setattr(app.model_router, "key_is_available", lambda role: role == "planner_text")

    async def must_not_call_model(*_args, **_kwargs):
        raise AssertionError("短计划不得调用内容规划模型")

    monkeypatch.setattr(app.model_router, "call_text", must_not_call_model)

    with pytest.raises(app.HTTPException) as exc:
        asyncio.run(app._generate_topic_brief_video(
            brief["id"],
            app.TopicBriefGenerateRequest(
                hotspot_event_id=event["id"],
                platform="douyin",
                target_duration_ms=60_000,
            ),
            {"id": 1, "username": "formal-target-owner"},
        ))

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["status"] == "needs_owned_media"
    assert detail["coverage"]["duration_ms"] < 50_000


def _create_ready_chat_hook_pair(tmp_db, *, title="Beitbridge 边境卡车排队"):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": title, "summary": "货车在筛查入口等待",
        "source_url": "https://example.com/formal-target", "publisher": "SA Today",
        "published_at": "2026-07-31T00:00:00Z", "retrieved_at": "2026-07-31T00:00:00Z",
        "snapshot_sha256": "formal-target-hook",
    })
    asset_id = tmp_db.create_asset({
        "name": "边境排队母片", "filepath": "assets/formal-target.mp4", "file_type": "video",
        "category": "other", "duration": 20, "size": 1, "source": "youtube",
        "status": "active", "sha256": "b" * 64,
    })
    events = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 7_000,
        "title_zh": title, "title_en": "Truck queue",
        "segments": [], "confidence": 0.9, "review_status": "confirmed",
        "evidence": {
            "what_happened": "货车在边境筛查入口排队等待",
            "hook_reason": "排队现场清晰可见",
            "logistics_question": "等待会先影响哪个履约节点？",
            "event_identity": "边境入口货车排队等待筛查",
        },
    }])
    for event in events:
        tmp_db.update_hotspot_event_clip_media(event["id"], "assets/events/formal-target.mp4", None, "ready")
    return hotspot_id, asset_id, events


def _topic_brief_client(tmp_db, *, event_title="货车排队等待筛查"):
    import app
    import auth
    from fastapi.testclient import TestClient

    tmp_db.create_user("formal-target-owner", auth.hash_password("pw12345"), "editor", "Formal Target Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "formal-target-owner", "password": "pw12345",
    }).json()["access_token"]
    _hotspot_id, _asset_id, events = _create_ready_chat_hook_pair(tmp_db, title=event_title)
    return client, {"Authorization": f"Bearer {token}"}, events[0]
