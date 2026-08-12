from datetime import datetime, timezone

import auth


def _login(tmp_db, username: str, role: str):
    from fastapi.testclient import TestClient
    import app

    tmp_db.create_user(username, auth.hash_password("pw12345"), role, username)
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pw12345"}
    ).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _create_hotspot(tmp_db) -> int:
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Durban port operations update",
        "summary": "Freight operations continue.",
        "source_url": "https://news.gov.za/story",
        "publisher": "SAnews",
        "published_at": "2026-07-22T08:00:00+00:00",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": "translation-snapshot",
        "image_candidate_url": None,
    })
    return hotspot_id


def test_hotspot_translation_calls_model_once_then_uses_snapshot_cache(tmp_db, monkeypatch):
    import app

    hotspot_id = _create_hotspot(tmp_db)
    calls = []

    async def fake_call_text(job_id, role, messages, *, prompt_version, client=None):
        calls.append((job_id, role, prompt_version, messages))
        return {
            "content": '{"title_zh":"德班港运营动态","summary_zh":"货运作业继续进行。"}',
            "cache_hit": False,
            "usage": {"input_tokens": 12, "output_tokens": 8},
        }

    monkeypatch.setattr(app.model_router, "call_text", fake_call_text)
    client, headers = _login(tmp_db, "translate-editor", "editor")

    first = client.post(f"/api/hotspots/{hotspot_id}/translate", headers=headers)
    second = client.post(f"/api/hotspots/{hotspot_id}/translate", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["title_zh"] == "德班港运营动态"
    assert second.json()["translation_cache_hit"] is True
    assert len(calls) == 1


def test_discover_and_filter_hotspot_video_candidates(tmp_db, monkeypatch):
    import app

    hotspot_id = _create_hotspot(tmp_db)

    async def fake_fetch_source_page(url):
        assert url == "https://news.gov.za/story"
        return """
        <meta property="og:image" content="https://news.gov.za/lead.jpg">
        <meta property="og:video" content="https://news.gov.za/clip.mp4">
        <iframe src="https://www.youtube.com/embed/abc123def45"></iframe>
        """, url

    monkeypatch.setattr(app.hotspot_media, "fetch_source_page", fake_fetch_source_page)

    async def fake_filter_reachable(candidates):
        assert len(candidates) == 3
        return [item for item in candidates if item["media_kind"] != "image"], 1

    monkeypatch.setattr(
        app.hotspot_media,
        "filter_reachable_image_candidates",
        fake_filter_reachable,
    )
    client, headers = _login(tmp_db, "media-admin", "admin")

    discovered = client.post(
        f"/api/hotspots/{hotspot_id}/media/discover", headers=headers
    )
    listed = client.get(
        f"/api/hotspot-media?hotspot_id={hotspot_id}&media_kind=video_link",
        headers=headers,
    )

    assert discovered.status_code == 200
    assert discovered.json()["created"] == 2
    assert discovered.json()["skipped_unavailable"] == 1
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    assert {item["platform"] for item in listed.json()} == {"direct", "youtube"}
    assert all(item["authorization_status"] == "authorized" for item in listed.json())


def test_attach_single_youtube_video_and_reject_channel(tmp_db, monkeypatch):
    import app

    hotspot_id = _create_hotspot(tmp_db)

    async def fake_oembed(url):
        return {
            "title": "Durban logistics report",
            "author": "SABC Digital News",
            "thumbnail_url": "https://i.ytimg.com/vi/abc123def45/hqdefault.jpg",
        }

    monkeypatch.setattr(app.inspiration_assets, "fetch_oembed", fake_oembed)
    client, headers = _login(tmp_db, "attach-admin", "admin")

    attached = client.post(
        f"/api/hotspots/{hotspot_id}/media/attach",
        headers=headers,
        json={"url": "https://youtu.be/abc123def45"},
    )
    rejected = client.post(
        f"/api/hotspots/{hotspot_id}/media/attach",
        headers=headers,
        json={"url": "https://www.youtube.com/@SAtoday"},
    )

    assert attached.status_code == 201
    assert attached.json()["platform"] == "youtube"
    assert attached.json()["hotspot_id"] == hotspot_id
    assert attached.json()["media_kind"] == "video_link"
    assert rejected.status_code == 400
    assert "频道" in rejected.json()["detail"]


def test_hotspot_media_rights_are_admin_only_and_require_evidence(tmp_db):
    hotspot_id = _create_hotspot(tmp_db)
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "direct",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://news.gov.za/clip.mp4",
        "rights_tier": "yellow",
    })
    editor, editor_headers = _login(tmp_db, "media-editor", "editor")
    payload = {
        "rights_tier": "yellow",
        "rights_note": "已获得新闻型宣传授权",
        "license_name": "Publisher permission",
        "attribution": "SAnews",
        "rights_evidence_url": "https://news.gov.za/permissions/story",
    }
    assert editor.put(
        f"/api/hotspot-media/{media_id}/rights", headers=editor_headers, json=payload
    ).status_code == 403

    admin, admin_headers = _login(tmp_db, "rights-media-admin", "admin")
    missing = admin.put(
        f"/api/hotspot-media/{media_id}/rights",
        headers=admin_headers,
        json={**payload, "rights_evidence_url": ""},
    )
    confirmed = admin.put(
        f"/api/hotspot-media/{media_id}/rights",
        headers=admin_headers,
        json=payload,
    )

    assert missing.status_code == 422
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_by"] == tmp_db.get_user_by_username("rights-media-admin")["id"]


def test_hotspot_media_accepts_explicit_authorization_status(tmp_db):
    hotspot_id = _create_hotspot(tmp_db)
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "direct",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://news.gov.za/authorized-clip.mp4",
        "authorization_status": "pending_review",
    })
    admin, headers = _login(tmp_db, "authorization-admin", "admin")
    response = admin.put(f"/api/hotspot-media/{media_id}/rights", headers=headers, json={
        "authorization_status": "authorized",
        "rights_note": "已登记为允许自动处理的授权范围",
        "license_name": "Publisher permission",
        "attribution": "SAnews",
        "rights_evidence_url": "https://news.gov.za/permissions/story",
    })

    assert response.status_code == 200
    assert response.json()["authorization_status"] == "authorized"
    assert response.json()["confirmed_by"]


def test_hotspot_media_list_prefers_local_preview_after_materialization(tmp_db):
    hotspot_id = _create_hotspot(tmp_db)
    external_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "image",
        "platform": "direct",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://news.gov.za/external.jpg",
        "thumbnail_url": "https://news.gov.za/external-thumb.jpg",
        "rights_tier": "yellow",
    })
    local_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "image",
        "platform": "direct",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://news.gov.za/local.jpg",
        "thumbnail_url": "https://news.gov.za/local-thumb.jpg",
        "rights_tier": "yellow",
    })
    asset_id = tmp_db.create_asset({
        "name": "Local hotspot image",
        "filepath": "assets/library/image/hotspot.jpg",
        "file_type": "image",
        "category": "other",
        "duration": 0,
        "width": 640,
        "height": 360,
        "size": 100,
        "thumbnail": "assets/thumbnails/hotspot.jpg",
        "sha256": "a" * 64,
        "source": "official_news",
        "status": "active",
        "created_by": None,
    })
    tmp_db.update_hotspot_media_state(
        local_id,
        asset_id=asset_id,
        download_status="downloaded",
        processing_status="ready",
    )
    client, headers = _login(tmp_db, "preview-admin", "admin")

    response = client.get(
        f"/api/hotspot-media?hotspot_id={hotspot_id}", headers=headers
    )
    items = {item["id"]: item for item in response.json()}

    assert response.status_code == 200
    assert items[external_id]["preview_url"] == "https://news.gov.za/external-thumb.jpg"
    assert items[local_id]["preview_url"] == "/static/assets/thumbnails/hotspot.jpg"
