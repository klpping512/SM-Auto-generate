from fastapi.testclient import TestClient


def _client(tmp_db, username):
    import app, auth

    tmp_db.create_user(username, auth.hash_password("pw12345"), "editor", username)
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pw12345"}
    ).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _create_project(client, headers):
    response = client.post(
        "/api/video-projects",
        headers=headers,
        json={
            "source_type": "chat",
            "source_snapshot": {"topic": "南非海外仓入库"},
            "title": "海外仓入库",
            "target_orientation": "portrait",
            "revision": {
                "title": "海外仓入库",
                "voice": "冰糖",
                "scenes": [{"scene": 1, "voiceover": "货物抵达仓库", "visual": "仓库卸货"}],
            },
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_read_and_update_project_revision(tmp_db):
    client, headers = _client(tmp_db, "project-owner")
    project = _create_project(client, headers)

    loaded = client.get(f"/api/video-projects/{project['id']}", headers=headers)
    updated = client.put(
        f"/api/video-projects/{project['id']}/revision",
        headers=headers,
        json={"payload": {"title": "修改后", "voice": "茉莉", "scenes": []}},
    )

    assert loaded.status_code == 200
    assert loaded.json()["current_revision"]["revision_no"] == 1
    assert updated.status_code == 200
    assert updated.json()["revision_no"] == 2


def test_legacy_landscape_project_request_is_stored_as_portrait(tmp_db):
    client, headers = _client(tmp_db, "portrait-only-owner")
    response = client.post(
        "/api/video-projects",
        headers=headers,
        json={
            "source_type": "chat",
            "title": "旧客户端视频",
            "platform": "douyin",
            "target_duration_ms": 30_000,
            "target_orientation": "landscape",
        },
    )

    assert response.status_code == 201
    assert response.json()["target_orientation"] == "portrait"


def test_generate_is_idempotent_and_active_job_recovers(tmp_db):
    client, headers = _client(tmp_db, "generation-owner")
    project = _create_project(client, headers)

    first = client.post(f"/api/video-projects/{project['id']}/generate", headers=headers)
    second = client.post(f"/api/video-projects/{project['id']}/generate", headers=headers)
    active = client.get("/api/video-generation/jobs/active", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert [item["id"] for item in active.json()] == [first.json()["job"]["id"]]


def test_project_exposes_active_job_preview_for_editor_handoff(tmp_db):
    client, headers = _client(tmp_db, "preview-handoff-owner")
    project = _create_project(client, headers)
    job = client.post(f"/api/video-projects/{project['id']}/generate", headers=headers).json()["job"]
    tmp_db.update_video_generation_job(job["id"], preview_path="uploads/video/preview.mp4")

    loaded = client.get(f"/api/video-projects/{project['id']}", headers=headers)

    assert loaded.status_code == 200
    assert loaded.json()["active_job"]["id"] == job["id"]
    assert loaded.json()["active_job"]["preview_url"] == "/static/uploads/video/preview.mp4"


def test_cancel_pending_job_is_idempotent(tmp_db):
    client, headers = _client(tmp_db, "cancel-owner")
    project = _create_project(client, headers)
    job = client.post(f"/api/video-projects/{project['id']}/generate", headers=headers).json()["job"]

    canceled = client.post(f"/api/video-generation/jobs/{job['id']}/cancel", headers=headers)
    repeated = client.post(f"/api/video-generation/jobs/{job['id']}/cancel", headers=headers)

    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert repeated.json()["status"] == "canceled"


def test_project_and_job_are_private_to_owner(tmp_db):
    owner_client, owner_headers = _client(tmp_db, "private-owner")
    project = _create_project(owner_client, owner_headers)
    job = owner_client.post(
        f"/api/video-projects/{project['id']}/generate", headers=owner_headers
    ).json()["job"]
    other_client, other_headers = _client(tmp_db, "private-other")

    assert other_client.get(
        f"/api/video-projects/{project['id']}", headers=other_headers
    ).status_code == 404
    assert other_client.get(
        f"/api/video-generation/jobs/{job['id']}", headers=other_headers
    ).status_code == 404
    assert other_client.post(
        f"/api/video-generation/jobs/{job['id']}/cancel", headers=other_headers
    ).status_code == 404


def _manual_preview_job(tmp_db, client, headers):
    project = _create_project(client, headers)
    job = client.post(f"/api/video-projects/{project['id']}/generate", headers=headers).json()["job"]
    checks = {
        "expected_resolution": True,
        "has_audio": True,
        "duration_aligned": True,
        "has_timed_subtitles": True,
        "no_repeated_source_or_range": True,
        "final_subtitle_timeline_aligned": True,
        "transition_audio_video_sync": True,
    }
    tmp_db.update_video_generation_job(
        job["id"],
        status="succeeded",
        stage="succeeded",
        progress=100,
        output_path="uploads/video/internal-preview.mp4",
        quality_report={
            "preview_quality": {"status": "passed", "checks": checks},
            "publication": {
                "tier": "internal_preview",
                "publish_allowed": False,
                "review_mode": "manual_preview",
            },
        },
    )
    return job


def _semantic_review_job(tmp_db, client, headers):
    job = _manual_preview_job(tmp_db, client, headers)
    loaded = tmp_db.get_video_generation_job(job["id"])
    report = loaded["quality_report"]
    report.pop("publication", None)
    report["video_evaluation"] = {
        "evaluation_status": "completed", "overall_score": 72,
        "passed": False, "issues": [{"severity": "low", "description": "需人工判断"}],
    }
    tmp_db.update_video_generation_job(
        job["id"], status="needs_review", stage="preview_quality_check",
        preview_path="uploads/video/semantic-review-preview.mp4", output_path=None,
        quality_report=report,
    )
    return job


def test_manual_preview_acceptance_records_checklist_without_publishing(tmp_db):
    client, headers = _client(tmp_db, "manual-preview-owner")
    job = _manual_preview_job(tmp_db, client, headers)
    checklist = {
        "hook_authentic": True,
        "no_repeat": True,
        "image_transitions": True,
        "subtitle_visibility": True,
        "audio_video_sync": True,
        "cta_natural": True,
    }

    response = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-review",
        headers=headers,
        json={"action": "accept", "checklist": checklist, "note": "完整观看后通过"},
    )
    loaded = client.get(f"/api/video-generation/jobs/{job['id']}", headers=headers)

    assert response.status_code == 200
    assert response.json()["job"]["status"] == "succeeded"
    assert response.json()["job"]["quality_report"]["publication"]["publish_allowed"] is False
    assert loaded.json()["quality_report"]["manual_review"]["status"] == "accepted"
    assert loaded.json()["quality_report"]["manual_review"]["reviewer"]["username"] == "manual-preview-owner"
    assert loaded.json()["events"][-1]["event_type"] == "manual_preview_accepted"


def test_semantic_review_preview_can_be_human_accepted_without_publishing(tmp_db):
    client, headers = _client(tmp_db, "semantic-review-owner")
    job = _semantic_review_job(tmp_db, client, headers)
    checklist = {
        "hook_authentic": True,
        "no_repeat": True,
        "image_transitions": True,
        "subtitle_visibility": True,
        "audio_video_sync": True,
        "cta_natural": True,
    }

    response = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-review",
        headers=headers,
        json={"action": "accept", "checklist": checklist, "note": "语义建议经人工复核不成立"},
    )

    accepted = response.json()["job"]
    assert response.status_code == 200
    assert accepted["status"] == "needs_review"
    assert accepted["stage"] == "manual_accepted"
    assert accepted["quality_report"]["manual_review"]["status"] == "accepted"
    assert accepted["quality_report"]["publication"]["publish_allowed"] is False


def test_manual_acceptance_can_explicitly_queue_final_render_without_publishing(tmp_db):
    client, headers = _client(tmp_db, "manual-final-owner")
    job = _semantic_review_job(tmp_db, client, headers)
    checklist = {
        "hook_authentic": True,
        "no_repeat": True,
        "image_transitions": True,
        "subtitle_visibility": True,
        "audio_video_sync": True,
        "cta_natural": True,
    }
    accepted = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-review",
        headers=headers,
        json={"action": "accept", "checklist": checklist},
    )
    finalized = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-finalize", headers=headers
    )

    assert accepted.status_code == 200
    assert finalized.status_code == 202
    assert finalized.json()["job"]["status"] == "pending"
    assert finalized.json()["job"]["stage"] == "final_rendering"
    assert finalized.json()["job"]["quality_report"]["publication"]["publish_allowed"] is False
    events = client.get(f"/api/video-generation/jobs/{job['id']}", headers=headers).json()["events"]
    assert events[-1]["event_type"] == "manual_final_render_requested"


def test_manual_final_render_requires_a_recorded_acceptance(tmp_db):
    client, headers = _client(tmp_db, "manual-final-guard")
    job = _semantic_review_job(tmp_db, client, headers)

    response = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-finalize", headers=headers
    )

    assert response.status_code == 409
    assert "人工验收" in response.json()["detail"]


def test_manual_preview_acceptance_requires_all_visual_checks(tmp_db):
    client, headers = _client(tmp_db, "manual-preview-checks")
    job = _manual_preview_job(tmp_db, client, headers)

    response = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-review",
        headers=headers,
        json={"action": "accept", "checklist": {"hook_authentic": True}},
    )

    assert response.status_code == 422
    assert "逐项确认" in response.json()["detail"]


def test_manual_preview_acceptance_requires_passed_technical_checks(tmp_db):
    client, headers = _client(tmp_db, "manual-preview-technical")
    job = _manual_preview_job(tmp_db, client, headers)
    loaded = tmp_db.get_video_generation_job(job["id"])
    loaded["quality_report"]["preview_quality"]["checks"]["has_audio"] = False
    tmp_db.update_video_generation_job(job["id"], quality_report=loaded["quality_report"])
    checklist = {
        "hook_authentic": True,
        "no_repeat": True,
        "image_transitions": True,
        "subtitle_visibility": True,
        "audio_video_sync": True,
        "cta_natural": True,
    }

    response = client.post(
        f"/api/video-generation/jobs/{job['id']}/manual-review",
        headers=headers,
        json={"action": "accept", "checklist": checklist},
    )

    assert response.status_code == 409
    assert "has_audio" in response.json()["detail"]


def test_project_revision_rejects_hotspot_mother_without_event_ref(tmp_db):
    client, headers = _client(tmp_db, "hotspot-project-owner")
    project = _create_project(client, headers)
    asset_id = tmp_db.create_asset({
        "name": "热点母片", "filepath": "assets/hotspot-mother.mp4", "file_type": "video",
        "category": "other", "duration": 120, "size": 1,
        "source": "youtube", "status": "active", "sha256": "m" * 64,
    })
    tmp_db.update_asset_provenance(asset_id, "https://example.com/mother", "", "SA Today", 31)
    response = client.put(
        f"/api/video-projects/{project['id']}/revision",
        headers=headers,
        json={"payload": {"scenes": [{"duration": 5, "asset_id": asset_id, "voiceover": "事件"}]}},
    )
    assert response.status_code == 400
    assert "热点事件片段" in response.json()["detail"]
