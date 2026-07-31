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


def _hotspot(tmp_db) -> int:
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Johannesburg delivery disruption",
        "summary": "A local delivery disruption.",
        "source_url": "https://news.example.com/delivery",
        "publisher": "Example News",
        "published_at": "2026-07-28T08:00:00+00:00",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": "hotspot-library-cleanup",
        "image_candidate_url": None,
    })
    return hotspot_id


def _asset(tmp_db, *, name: str, filepath: str, sha256: str) -> int:
    return tmp_db.create_asset({
        "name": name, "filepath": filepath, "thumbnail": "assets/thumbs/" + filepath.rsplit("/", 1)[-1] + ".jpg",
        "file_type": "video", "category": "other", "duration": 30, "width": 1280, "height": 720,
        "size": 100, "sha256": sha256, "source": "official_news", "status": "active", "created_by": None,
    })


def test_admin_can_preview_and_clear_only_hotspot_library(tmp_db, tmp_path, monkeypatch):
    import app

    static_root = tmp_path / "static"
    monkeypatch.setattr(app, "STATIC_DIR", static_root)
    hotspot_id = _hotspot(tmp_db)
    hotspot_asset = _asset(
        tmp_db, name="热点母片", filepath="assets/library/video/hotspot.mp4", sha256="a" * 64
    )
    tmp_db.update_asset_provenance(
        hotspot_asset, "https://news.example.com/video", "授权", "Example News", hotspot_id
    )
    event = tmp_db.replace_hotspot_event_clips(hotspot_asset, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 8_000,
        "title_zh": "约翰内斯堡配送受阻", "title_en": "Johannesburg delivery disruption",
        "segments": [], "confidence": 0.9, "review_status": "confirmed",
    }])[0]
    with tmp_db.get_conn() as conn:
        conn.execute(
            "UPDATE hotspot_event_clips SET clip_path=?,thumbnail_path=? WHERE id=?",
            ("assets/event-clips/hook.mp4", "assets/event-clips/hook.jpg", event["id"]),
        )
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id, "media_kind": "video_file", "platform": "direct",
        "source_page_url": "https://news.example.com/delivery", "original_media_url": "https://news.example.com/video",
        "local_path": "assets/library/video/hotspot.mp4", "asset_id": hotspot_asset,
        "rights_tier": "yellow", "download_status": "downloaded", "processing_status": "ready",
    })
    owned_asset = _asset(
        tmp_db, name="Buffalo 自有素材", filepath="assets/library/video/owned.mp4", sha256="b" * 64
    )
    for relative in (
        "assets/library/video/hotspot.mp4", "assets/thumbs/hotspot.mp4.jpg",
        "assets/event-clips/hook.mp4", "assets/event-clips/hook.jpg",
        "assets/library/video/owned.mp4",
    ):
        target = static_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test")

    editor, editor_headers = _login(tmp_db, "cleanup-editor", "editor")
    assert editor.get("/api/hotspot-library/cleanup-preview", headers=editor_headers).status_code == 403
    assert editor.request(
        "DELETE", "/api/hotspot-library", headers=editor_headers, json={"confirmation": "清空热点素材库"}
    ).status_code == 403

    admin, admin_headers = _login(tmp_db, "cleanup-admin", "admin")
    preview = admin.get("/api/hotspot-library/cleanup-preview", headers=admin_headers)
    rejected = admin.request(
        "DELETE", "/api/hotspot-library", headers=admin_headers, json={"confirmation": "delete"}
    )
    cleared = admin.request(
        "DELETE", "/api/hotspot-library", headers=admin_headers, json={"confirmation": "清空热点素材库"}
    )

    assert preview.status_code == 200
    assert preview.json()["media_count"] == 1
    assert preview.json()["asset_count"] == 1
    assert preview.json()["event_clip_count"] == 1
    assert rejected.status_code == 422
    assert cleared.status_code == 200
    assert cleared.json()["status"] == "deleted"
    assert cleared.json()["media_count"] == 1
    assert cleared.json()["asset_count"] == 1
    assert cleared.json()["event_clip_count"] == 1
    assert tmp_db.get_hotspot_media(media_id) is None
    assert tmp_db.get_asset(hotspot_asset) is None
    assert tmp_db.list_hotspot_event_clips(asset_id=hotspot_asset) == []
    assert tmp_db.get_asset(owned_asset) is not None
    assert tmp_db.get_hotspot(hotspot_id) is not None
    assert not (static_root / "assets/library/video/hotspot.mp4").exists()
    assert not (static_root / "assets/event-clips/hook.mp4").exists()
    assert (static_root / "assets/library/video/owned.mp4").exists()


def test_admin_can_delete_one_hotspot_media_card_without_touching_other_cards(tmp_db, tmp_path, monkeypatch):
    import app

    static_root = tmp_path / "static"
    monkeypatch.setattr(app, "STATIC_DIR", static_root)
    hotspot_id = _hotspot(tmp_db)
    shared_asset = _asset(
        tmp_db, name="共享热点母片", filepath="assets/library/video/shared.mp4", sha256="c" * 64
    )
    tmp_db.update_asset_provenance(
        shared_asset, "https://news.example.com/shared", "授权", "Example News", hotspot_id
    )
    shared_file = static_root / "assets/library/video/shared.mp4"
    shared_file.parent.mkdir(parents=True, exist_ok=True)
    shared_file.write_bytes(b"shared")
    first, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id, "media_kind": "video_link", "platform": "direct",
        "source_page_url": "https://news.example.com/one", "original_media_url": "https://news.example.com/one.mp4",
        "asset_id": shared_asset, "local_path": "assets/library/video/shared.mp4", "rights_tier": "yellow",
    })
    second, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id, "media_kind": "video_link", "platform": "direct",
        "source_page_url": "https://news.example.com/two", "original_media_url": "https://news.example.com/two.mp4",
        "asset_id": shared_asset, "local_path": "assets/library/video/shared.mp4", "rights_tier": "yellow",
    })
    admin, headers = _login(tmp_db, "single-cleanup-admin", "admin")

    response = admin.delete(f"/api/hotspot-media/{first}", headers=headers)

    assert response.status_code == 200
    assert tmp_db.get_hotspot_media(first) is None
    assert tmp_db.get_hotspot_media(second) is not None
    assert tmp_db.get_asset(shared_asset) is not None
    assert shared_file.exists()


def test_admin_cannot_clear_a_hotspot_media_item_while_its_download_is_running(tmp_db):
    hotspot_id = _hotspot(tmp_db)
    busy_media, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id, "media_kind": "video_link", "platform": "direct",
        "source_page_url": "https://news.example.com/busy", "original_media_url": "https://news.example.com/busy.mp4",
        "rights_tier": "yellow", "download_status": "downloading", "processing_status": "not_started",
    })
    admin, headers = _login(tmp_db, "busy-cleanup-admin", "admin")

    response = admin.delete(f"/api/hotspot-media/{busy_media}", headers=headers)

    assert response.status_code == 409
    assert tmp_db.get_hotspot_media(busy_media) is not None


def test_admin_can_delete_hotspot_event_source_without_a_media_card(tmp_db):
    hotspot_id = _hotspot(tmp_db)
    source_asset = _asset(
        tmp_db, name="旧热点母片", filepath="assets/library/video/legacy-hotspot.mp4", sha256="d" * 64
    )
    tmp_db.update_asset_provenance(
        source_asset, "https://news.example.com/legacy", "授权", "Example News", hotspot_id
    )
    event = tmp_db.replace_hotspot_event_clips(source_asset, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 8_000,
        "title_zh": "旧热点事件", "title_en": "Legacy hotspot event",
        "segments": [], "confidence": 0.8, "review_status": "confirmed",
    }])[0]
    admin, headers = _login(tmp_db, "event-source-cleanup-admin", "admin")

    response = admin.delete(f"/api/hotspot-event-assets/{source_asset}", headers=headers)

    assert response.status_code == 200
    assert response.json()["event_clip_count"] == 1
    assert tmp_db.get_asset(source_asset) is None
    assert tmp_db.get_hotspot_event_clip(event["id"]) is None
