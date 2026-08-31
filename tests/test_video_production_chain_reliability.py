"""Video production chain reliability: state, copy, assets, queue, login, UI."""
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.rpa_base import cookies_indicate_login, build_credentials
import video_state


ROOT = Path(__file__).parents[1]


def test_probe_rejects_missing_and_empty_files(tmp_path):
    missing = video_state.probe_video_artifact(str(tmp_path / "nope.mp4"))
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    empty_probe = video_state.probe_video_artifact(str(empty))
    present = tmp_path / "clip.mp4"
    present.write_bytes(b"not-empty")
    present_probe = video_state.probe_video_artifact(str(present))

    assert missing["ok"] is False
    assert empty_probe["ok"] is False
    assert present_probe["exists"] is True
    assert present_probe["readable"] is True


def test_quality_hold_is_not_shown_as_succeeded():
    job = {
        "status": "succeeded",
        "output_path": "",
        "quality_report": {"publication": {"tier": "quality_hold", "publish_allowed": False}},
    }
    assert video_state.derive_quality_status(job) == "hold"
    assert video_state.result_label(job) != "succeeded"


def test_incomplete_third_scene_is_repaired_locally():
    script = {
        "title": "清关窗口变化",
        "scenes": [
            {"scene": 1, "voiceover": "先把这条物流变化说清楚。", "scene_role": "opening"},
            {"scene": 2, "voiceover": "同样一批货，节点一变等待成本就会分开。", "scene_role": "contrast"},
            {"scene": 3, "voiceover": "如果继续空等货值和", "scene_role": "risk"},
            {"scene": 4, "voiceover": "仓内人员按票核对包裹。", "scene_role": "action"},
        ],
    }
    repaired, notes = video_state.repair_incomplete_scenes(script, seed="job-3")
    assert repaired["scenes"][2]["voiceover"].endswith("。")
    assert repaired["scenes"][2]["copy_source"] == "fallback"
    assert any("第3镜" in note for note in notes)


def test_copy_source_repair_normalizes_to_model_repair():
    assert video_state.normalize_copy_source("repair") == "model_repair"
    assert video_state.normalize_copy_source("model") == "model"
    assert video_state.normalize_copy_source("minimax") == "fallback"


def test_scene_asset_signature_ignores_endcard():
    scenes = [
        {"asset_id": 11, "evidence_type": "owned_video", "asset_start_ms": 0, "asset_end_ms": 4000},
        {"asset_id": 12, "evidence_type": "owned_image"},
        {"asset_id": 99, "evidence_type": "brand_endcard"},
    ]
    other = [
        {"asset_id": 11, "evidence_type": "owned_video", "asset_start_ms": 0, "asset_end_ms": 4000},
        {"asset_id": 12, "evidence_type": "owned_image"},
    ]
    assert video_state.scene_asset_signature(scenes) == video_state.scene_asset_signature(other)


def test_queue_double_click_is_idempotent(tmp_db):
    user_id = tmp_db.create_user("queue-owner", "hashed", role="admin", display_name="Q")
    first = tmp_db.add_to_queue(
        "标题", "正文", "douyin", created_by=user_id, status="queued",
        attachments=[{"type": "video", "path": "uploads/a.mp4"}],
        video_project_id="proj-1", revision_id="rev-1",
        idempotency_key="queue:1:proj-1:rev-1:douyin:default",
    )
    second = tmp_db.add_to_queue(
        "标题", "正文", "douyin", created_by=user_id, status="queued",
        attachments=[{"type": "video", "path": "uploads/a.mp4"}],
        video_project_id="proj-1", revision_id="rev-1",
        idempotency_key="queue:1:proj-1:rev-1:douyin:default",
    )
    assert first == second


def test_editor_url_is_authoritative_and_queue_jumps_to_item():
    editor = (ROOT / "static" / "editor.html").read_text(encoding="utf-8")
    queue = (ROOT / "static" / "queue.html").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "chat.html").read_text(encoding="utf-8")
    common = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
    accounts = (ROOT / "static" / "accounts.html").read_text(encoding="utf-8")
    project = (ROOT / "static" / "video-project.html").read_text(encoding="utf-8")

    assert "function getVideoProjectId()" in editor
    assert "绝不回退到旧草稿" in editor
    assert "video_project_id" in editor
    assert "function buildQueuePayload" in editor
    assert "function enqueueContentToQueue" in editor
    assert "await enqueueContentToQueue([['douyin', c]])" in editor
    assert "/queue.html?highlight=" in editor
    assert "openPublishConfirm" in queue
    assert "filterStatus === 'pending_review'" in queue or "highlightId" in queue
    assert "idempotency_key: `chat-video-" not in chat
    assert "{ id: 'hotspots', label: '热点审核台'" not in common
    assert "{ id: 'articles', label: '公众号图文'" not in common
    assert "重新检测" in accounts
    assert "/api/accounts/${id}/session-health" in accounts
    assert "video_project_id=" in project
    assert "1 分钟内自动发布" not in project


def test_renderer_does_not_overwrite_minimax_copy_at_render():
    source = (ROOT / "video_renderer.py").read_text(encoding="utf-8")
    assert 'job["script"]["scenes"][index]["voiceover"] = shortened_voiceover' not in source
    assert "write_silent_wav" in source


def test_douyin_cookies_indicate_login_without_publish_selector():
    assert cookies_indicate_login(
        [{"name": "sessionid", "value": "abc"}, {"name": "sid_tt", "value": "x"}],
        platform="douyin",
    )
    assert not cookies_indicate_login([{"name": "ttwid", "value": "1"}], platform="douyin")
    cred = build_credentials([{"name": "sessionid", "value": "abc"}])
    assert "sessionid" in cred


def test_scan_login_status_falls_back_to_persisted_account(tmp_db):
    import app
    import auth
    from adapters.rpa_base import build_credentials

    user_id = tmp_db.create_user("scanpersist", auth.hash_password("pw12345"), "admin", "A")
    tmp_db.create_account("douyin", "抖音号", "dy-persist", owner_id=user_id)
    account = tmp_db.get_accounts(owner_id=user_id)[0]
    tmp_db.update_account_credentials(
        account["account_id"],
        build_credentials([{"name": "sessionid", "value": "ok"}, {"name": "sid_guard", "value": "g"}]),
    )
    tmp_db.update_account_status(account["id"], "active")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "scanpersist", "password": "pw12345",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get(f"/api/accounts/{account['id']}/scan-login/missing-session", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json().get("persisted") is True


def test_session_health_reports_agent_and_db_session(tmp_db):
    import app
    import auth
    from adapters.rpa_base import build_credentials

    user_id = tmp_db.create_user("healthuser", auth.hash_password("pw12345"), "admin", "A")
    tmp_db.create_account("douyin", "抖音号", "dy-health", owner_id=user_id)
    account = tmp_db.get_accounts(owner_id=user_id)[0]
    tmp_db.update_account_credentials(
        account["account_id"],
        build_credentials([{"name": "sessionid", "value": "ok"}]),
    )
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "healthuser", "password": "pw12345",
    }).json()["access_token"]
    response = client.post(
        f"/api/accounts/{account['id']}/session-health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["session"] in {"connected", "expired"}
    assert "agent" in payload


def test_diagnostics_endpoint_answers_stage_file_and_quality(tmp_db, monkeypatch):
    import app
    import auth

    user_id = tmp_db.create_user("diaguser", auth.hash_password("pw12345"), "admin", "A")
    project = tmp_db.create_video_project(
        created_by=user_id, source_type="chat", source_snapshot={"topic": "清关"},
        title="清关窗口",
    )
    revision = tmp_db.create_video_project_revision(
        project["id"], {"title": "清关窗口", "scenes": []}, created_by=user_id,
    )
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "diag-key",
    )
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "diaguser", "password": "pw12345",
    }).json()["access_token"]
    response = client.get(
        f"/api/video-projects/{project['id']}/diagnostics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == job["id"]
    assert payload["artifact_status"] == "absent"
    assert "stuck_stage" in payload
    assert "quality_blockers" in payload


def test_custom_topic_without_hook_still_creates_job(tmp_db, monkeypatch):
    import app
    import auth

    tmp_db.create_user("anytopic", auth.hash_password("pw12345"), "editor", "Any")
    monkeypatch.setattr(
        app,
        "_chat_video_delivery_readiness",
        lambda *_args, **_kwargs: {"status": "owned_only_ready", "delivery_ready": False},
    )
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "anytopic", "password": "pw12345",
    }).json()["access_token"]
    response = client.post("/api/ai/chat/dual-library-video", headers={
        "Authorization": f"Bearer {token}",
    }, json={
        "topic": "约翰内斯堡仓内分拣怎么做",
        "hotspot_event_ids": [],
        "platform": "douyin",
        "target_duration_ms": 60_000,
        "session_id": "any-topic-session",
    })
    assert response.status_code == 202
    payload = response.json()
    assert payload["project"]["id"]
    assert payload["job"]["id"]
    assert payload.get("poll_url") or payload.get("job_id")


def test_no_hook_readiness_is_owned_only_ready(tmp_db):
    import app

    readiness = app._chat_video_delivery_readiness(
        "约翰内斯堡仓内分拣怎么做", [], chain_mode="owned_only",
    )
    assert readiness["delivery_ready"] is True
    assert readiness["status"] == "owned_only_ready"
    assert "不阻断" in readiness["message"] or "直出" in readiness["message"]


def test_duplicate_sequence_rematch_changes_signature():
    import video_generation
    import video_state

    scenes = [
        {"evidence_type": "owned_video", "asset_id": 1, "asset_segment_id": 10, "asset_start_ms": 0, "asset_end_ms": 5000},
        {"evidence_type": "owned_video", "asset_id": 2, "asset_segment_id": 20, "asset_start_ms": 0, "asset_end_ms": 5000},
        {"evidence_type": "brand_endcard", "scene_role": "brand_endcard"},
    ]
    original = video_state.scene_asset_signature(scenes)
    rematch = video_generation.diversify_repeated_asset_sequence(
        scenes,
        [original],
        alternate_segments={
            0: [{"asset_id": 3, "asset_segment_id": 30, "asset_start_ms": 0, "asset_end_ms": 4000}],
        },
    )
    assert rematch["rematch_applied"] is True
    assert rematch["strategy"] == "alternate_segment"
    assert rematch["signature"] != original
    assert scenes[0]["asset_id"] == 3
    assert rematch["quality_hold"] is False


def test_duplicate_sequence_degrades_to_text_card_when_no_alternate():
    import video_generation
    import video_state

    scenes = [
        {
            "evidence_type": "owned_video", "asset_id": 1, "asset_segment_id": 10,
            "asset_start_ms": 0, "asset_end_ms": 5000, "scene": 1, "duration_ms": 5000,
        },
        {"evidence_type": "brand_endcard", "scene_role": "brand_endcard"},
    ]
    original = video_state.scene_asset_signature(scenes)
    rematch = video_generation.diversify_repeated_asset_sequence(scenes, [original])
    assert rematch["rematch_applied"] is True
    assert rematch["strategy"] == "text_card_degrade"
    assert rematch["signature"] != original
    assert scenes[0]["asset_source"] == "diversity_text_card"
    assert rematch["inventory_limited"] is True
    assert rematch["quality_hold"] is False
