import asyncio

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


def test_chat_video_worker_creates_project_only_after_background_planning(tmp_db, monkeypatch):
    app, client, headers, event = _client_with_hook(tmp_db)
    created = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "南非海外仓不是仓库，而是本地团队",
        "hotspot_event_ids": [event["id"]], "platform": "douyin",
        "target_duration_ms": 60_000, "session_id": "worker-session",
    }).json()["task"]
    captured = {}

    async def fake_generate(_brief_id, _body, user, source_snapshot=None):
        captured["snapshot"] = source_snapshot
        project = tmp_db.create_video_project(
            created_by=user["id"], source_type="topic_brief_dual_library",
            source_snapshot=source_snapshot or {}, title="海外仓是本地团队",
            platform="douyin", target_duration_ms=60_000, target_orientation="portrait",
        )
        tmp_db.create_video_project_revision(project["id"], {"scenes": []}, user["id"])
        return {"project": tmp_db.get_video_project(project["id"], created_by=user["id"])}

    monkeypatch.setattr(app, "_generate_topic_brief_video", fake_generate)
    claimed = tmp_db.claim_next_chat_video_task("test-worker")
    assert claimed["id"] == created["id"]
    asyncio.run(app._run_chat_video_task(claimed, "test-worker"))
    task = tmp_db.get_chat_video_task(created["id"])

    assert task["status"] == "running"
    assert task["stage"] == "video_generation"
    assert task["project_id"] and task["video_job_id"]
    assert captured["snapshot"]["matched_event_clip_ids"] == [event["id"]]
    observed = client.get(f"/api/ai/chat/dual-library-video/tasks/{task['id']}", headers=headers)
    assert observed.status_code == 200
    assert observed.json()["next_action"]


def test_expired_chat_task_is_recoverable_after_worker_restart(tmp_db):
    _app, _client, _headers, event = _client_with_hook(tmp_db)
    user = tmp_db.get_user_by_username("task-owner")
    task, _ = tmp_db.create_or_get_chat_video_task(
        user["id"], "expired-task", "南非物流", [event["id"]], "douyin", 60_000, "restart",
    )
    claimed = tmp_db.claim_next_chat_video_task("first-worker", lease_seconds=1)
    assert claimed["id"] == task["id"]
    tmp_db.update_chat_video_task(task["id"], lease_expires_at="2000-01-01 00:00:00")

    assert tmp_db.recover_expired_chat_video_tasks() == 1
    assert tmp_db.get_chat_video_task(task["id"])["status"] == "pending"
