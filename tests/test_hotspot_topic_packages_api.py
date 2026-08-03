from fastapi.testclient import TestClient

import auth
import app


def _login(tmp_db, username: str, role: str):
    tmp_db.create_user(username, auth.hash_password("pw12345"), role, username)
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": username, "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _create_hotspot_with_signal_and_media(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Johannesburg driver strike", "summary": "Drivers announce a shutdown.",
        "source_url": "https://sabc.example/strike", "publisher": "SABC",
        "published_at": "2026-07-24T07:00:00+00:00", "retrieved_at": "2026-07-24T07:05:00+00:00",
        "snapshot_sha256": "a" * 64,
    })
    tmp_db.update_hotspot_package_metrics(hotspot_id, heat_score=82, heat_state="rising", event_type="strike", logistics_relevance=91, locations=["Johannesburg"], entities=["driver"], package_status="new")
    tmp_db.upsert_hotspot_signal({"hotspot_id": hotspot_id, "source_name": "SABC", "source_type": "news", "external_id": "sabc-1", "title": "Drivers announce a shutdown", "summary": "Johannesburg drivers announce a national shutdown.", "source_url": "https://sabc.example/strike", "published_at": "2026-07-24T07:00:00+00:00", "retrieved_at": "2026-07-24T07:05:00+00:00"})
    tmp_db.upsert_hotspot_media({"hotspot_id": hotspot_id, "media_kind": "video_link", "platform": "youtube", "source_page_url": "https://sabc.example/strike", "original_media_url": "https://www.youtube.com/watch?v=abc123def45", "rights_tier": "yellow"})
    return hotspot_id


def test_editor_cannot_access_topic_packages(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    client, headers = _login(tmp_db, "topic-editor", "editor")
    response = client.get(f"/api/hotspot-packages/{hotspot_id}", headers=headers)
    assert response.status_code == 403


def test_topic_package_detail_contains_hook_readiness_and_actions(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    client, headers = _login(tmp_db, "topic-admin", "admin")

    response = client.get(f"/api/hotspot-packages/{hotspot_id}", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["signals"]
    assert payload["event_clips"] == []
    assert "media_groups" in payload
    assert payload["actions"]["can_confirm_facts"] is True
    assert payload["actions"]["can_follow_up_video"] is False
    assert payload["hook_readiness"]["state"] == "not_prepared"
    assert payload["hook_readiness"]["candidate_media_count"] == 1
    assert payload["hook_readiness"]["ready_hook_count"] == 0


def test_confirm_facts_does_not_mark_hooks_ready(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    client, headers = _login(tmp_db, "topic-admin2", "admin")

    confirmed = client.post(f"/api/hotspot-packages/{hotspot_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    payload = confirmed.json()
    assert payload["package_status"] == "confirmed"
    assert payload["hook_readiness"]["state"] == "not_prepared"
    assert payload["actions"]["can_follow_up_video"] is False
    assert payload["actions"]["can_prepare_media"] is True


def test_admin_can_confirm_package_and_no_media_package_is_listed_as_facts_only(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    facts_only_id, _ = tmp_db.upsert_hotspot({
        "title": "Port notice", "summary": "Official notice without media.",
        "source_url": "https://sabc.example/notice", "publisher": "SABC",
        "published_at": "2026-07-24T08:00:00+00:00", "retrieved_at": "2026-07-24T08:05:00+00:00",
        "snapshot_sha256": "b" * 64,
    })
    tmp_db.upsert_hotspot_signal({"hotspot_id": facts_only_id, "source_name": "SABC", "source_type": "news", "external_id": "sabc-2", "title": "Port notice", "summary": "Official notice without media.", "source_url": "https://sabc.example/notice", "retrieved_at": "2026-07-24T08:05:00+00:00"})
    client, headers = _login(tmp_db, "topic-admin3", "admin")

    confirmed = client.post(f"/api/hotspot-packages/{hotspot_id}/confirm", headers=headers)
    facts_only = client.get("/api/hotspot-packages?media_form=none", headers=headers)

    assert confirmed.status_code == 200
    assert confirmed.json()["package_status"] == "confirmed"
    assert [item["id"] for item in facts_only.json()] == [facts_only_id]


def test_follow_up_requires_ready_hook(tmp_db, tmp_path):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    asset_id = tmp_db.create_asset({
        "name": "hook-mother", "filepath": "assets/library/video/hook.mp4",
        "file_type": "video", "category": "delivery", "duration": 12,
        "width": 1080, "height": 1920, "size": 12, "thumbnail": "",
        "sha256": "hook-ready-sha", "source": "official_news", "status": "active",
    })
    events = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 4000,
        "title_zh": "港口罢工现场", "title_en": "strike",
        "confidence": 0.9, "review_status": "confirmed", "segments": [],
        "evidence": {
            "what_happened": "司机罢工", "hook_reason": "时效",
            "logistics_question": "口岸会不会堵",
        },
        "hook_kind": "timely_event", "logistics_scenes": ["port"],
    }])
    tmp_db.update_hotspot_event_clip_media(events[0]["id"], "assets/hotspot-events/1/event.mp4", None, "ready")
    client, headers = _login(tmp_db, "topic-admin4", "admin")
    confirmed = client.post(f"/api/hotspot-packages/{hotspot_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    payload = confirmed.json()
    assert payload["hook_readiness"]["ready_hook_count"] >= 1
    assert payload["actions"]["can_follow_up_video"] is True
