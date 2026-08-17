from fastapi.testclient import TestClient


def _client_with_hook(tmp_db):
    import app
    import auth

    tmp_db.create_user("task-owner", auth.hash_password("pw12345"), "editor", "Task Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "task-owner", "password": "pw12345",
    }).json()["access_token"]
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "南非边境货车排队", "summary": "货车在筛查入口等待",
        "source_url": "https://example.com/chat-task", "publisher": "SA Today",
        "published_at": "2026-07-31T00:00:00Z", "retrieved_at": "2026-07-31T00:00:00Z",
        "snapshot_sha256": "chat-task-hook",
    })
    asset_id = tmp_db.create_asset({
        "name": "边境排队母片", "filepath": "assets/chat-task.mp4", "file_type": "video",
        "category": "other", "duration": 20, "size": 1, "source": "youtube",
        "status": "active", "sha256": "a" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 7_000,
        "title_zh": "货车排队等待筛查", "title_en": "Truck queue",
        "segments": [], "confidence": 0.9, "review_status": "confirmed",
        "evidence": {
            "what_happened": "货车在边境筛查入口排队等待",
            "hook_reason": "排队现场清晰可见",
            "logistics_question": "等待会先影响哪个履约节点？",
            "event_identity": "边境入口货车排队等待筛查",
        },
    }])[0]
    tmp_db.update_hotspot_event_clip_media(event["id"], "assets/events/chat-task.mp4", None, "ready")
    return app, client, {"Authorization": f"Bearer {token}"}, event


def test_chat_video_request_returns_job_id_and_does_not_call_the_model(tmp_db, monkeypatch):
    app, client, headers, event = _client_with_hook(tmp_db)

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("HTTP 请求不能等待模型规划")

    monkeypatch.setattr(app.model_router, "call_text", must_not_run)
    # This test is about job/idempotency orchestration, not delivery-readiness
    # scoring; the fixture only has one hotspot Hook and no owned assets, so it
    # would otherwise trip the delivery_ready hard gate added in app.py.
    monkeypatch.setattr(
        app, "_chat_video_delivery_readiness",
        lambda *_args, **_kwargs: {"status": "delivery_ready", "delivery_ready": True},
    )
    body = {
        "topic": "帮我生成一个关乎南非海外仓的介绍视频",
        "hotspot_event_ids": [event["id"]], "platform": "douyin",
        "target_duration_ms": 60_000, "session_id": "task-session",
        "idempotency_key": "chat-task-idempotency",
    }
    first = client.post("/api/ai/chat/dual-library-video", headers=headers, json=body)
    second = client.post("/api/ai/chat/dual-library-video", headers=headers, json=body)

    assert first.status_code == 202
    assert first.json()["created"] is True
    assert first.json()["job_id"] == first.json()["job"]["id"]
    assert first.json()["poll_url"] == f"/api/video-generation/jobs/{first.json()['job_id']}"
    assert first.json()["job"]["stage"] == "queued"
    assert first.json()["project"]["active_job_id"] == first.json()["job_id"]
    assert second.status_code == 202
    assert second.json()["created"] is False
    assert second.json()["job_id"] == first.json()["job_id"]


def test_legacy_chat_video_task_endpoint_is_gone(tmp_db):
    _app, client, headers, _event = _client_with_hook(tmp_db)
    observed = client.get(
        "/api/ai/chat/dual-library-video/tasks/not-a-real-task",
        headers=headers,
    )
    assert observed.status_code == 404


def test_owned_only_fallback_can_create_project_without_hotspot_event(tmp_db, monkeypatch):
    import json

    app, client, headers, _event = _client_with_hook(tmp_db)
    monkeypatch.setattr(
        app,
        "_chat_video_delivery_readiness",
        lambda *_args, **_kwargs: {"status": "owned_only_ready", "delivery_ready": True},
    )
    response = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "Transnet 罢工影响评估",
        "hotspot_event_ids": [], "chain_mode": "owned_only", "platform": "douyin",
        "target_duration_ms": 60_000, "session_id": "owned-fallback-session",
        "idempotency_key": "owned-fallback-idempotency",
    })

    assert response.status_code == 202
    payload = response.json()
    assert payload["created"] is True
    snapshot = json.loads(payload["project"]["source_snapshot"])
    assert snapshot["fallback_mode"] == "owned_only_no_matching_hook"
    assert snapshot["matched_event_clip_ids"] == []


def test_chat_video_request_persists_minimax_tts_on_snapshot_and_revision(tmp_db, monkeypatch):
    import json

    app, client, headers, event = _client_with_hook(tmp_db)
    monkeypatch.setattr(
        app, "_chat_video_delivery_readiness",
        lambda *_args, **_kwargs: {"status": "delivery_ready", "delivery_ready": True},
    )
    response = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "帮我生成一个关乎南非海外仓的介绍视频",
        "hotspot_event_ids": [event["id"]], "platform": "douyin",
        "target_duration_ms": 60_000, "session_id": "tts-session",
        "idempotency_key": "tts-minimax-idempotency",
        "tts_provider": "minimax", "voice": "male-qn-qingse",
    })

    assert response.status_code == 202
    payload = response.json()
    snapshot = json.loads(payload["project"]["source_snapshot"])
    revision = payload["project"]["current_revision"]["payload"]
    assert snapshot["tts_provider"] == "minimax"
    assert snapshot["voice"] == "male-qn-qingse"
    assert revision["tts_provider"] == "minimax"
    assert revision["voice"] == "male-qn-qingse"

    saved = client.put(
        f"/api/video-projects/{payload['project']['id']}/revision",
        headers=headers,
        json={"payload": {**revision, "title": "重试修订"}},
    )
    assert saved.status_code == 200
    assert saved.json()["payload"]["tts_provider"] == "minimax"
    assert saved.json()["payload"]["voice"] == "male-qn-qingse"
