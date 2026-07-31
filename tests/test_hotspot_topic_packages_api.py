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


def test_topic_package_detail_contains_signals_media_and_actions(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    client, headers = _login(tmp_db, "topic-editor", "editor")

    response = client.get(f"/api/hotspot-packages/{hotspot_id}", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["signals"]
    assert payload["event_clips"] == []
    assert "media_groups" in payload
    assert payload["actions"]["can_confirm"] is True


def test_admin_can_confirm_package_and_no_media_package_is_listed_as_facts_only(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    facts_only_id, _ = tmp_db.upsert_hotspot({
        "title": "Port notice", "summary": "Official notice without media.",
        "source_url": "https://sabc.example/notice", "publisher": "SABC",
        "published_at": "2026-07-24T08:00:00+00:00", "retrieved_at": "2026-07-24T08:05:00+00:00",
        "snapshot_sha256": "b" * 64,
    })
    tmp_db.upsert_hotspot_signal({"hotspot_id": facts_only_id, "source_name": "SABC", "source_type": "news", "external_id": "sabc-2", "title": "Port notice", "summary": "Official notice without media.", "source_url": "https://sabc.example/notice", "retrieved_at": "2026-07-24T08:05:00+00:00"})
    client, headers = _login(tmp_db, "topic-admin", "admin")

    confirmed = client.post(f"/api/hotspot-packages/{hotspot_id}/confirm", headers=headers)
    facts_only = client.get("/api/hotspot-packages?media_form=none", headers=headers)

    assert confirmed.status_code == 200
    assert confirmed.json()["package_status"] == "confirmed"
    assert [item["id"] for item in facts_only.json()] == [facts_only_id]
