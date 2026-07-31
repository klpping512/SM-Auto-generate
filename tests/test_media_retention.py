import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import auth


def _asset(db, *, retention_class="hotspot_source", filepath="assets/library/hotspot.mp4"):
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO assets
               (name,filepath,file_type,category,size,sha256,source,status,retention_class)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "热点视频",
                filepath,
                "video",
                "other",
                1024,
                f"sha-{filepath}",
                "south_africa_hotspot",
                "active",
                retention_class,
            ),
        )
        return int(cur.lastrowid)


def _hotspot(db, *, source_url="https://news.gov.za/story"):
    return db.upsert_hotspot({
        "title": "Port update",
        "summary": "Durban operations continue.",
        "source_url": source_url,
        "publisher": "SAnews",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": f"snapshot-{source_url}",
        "image_candidate_url": None,
    })[0]


def test_lifecycle_schema_is_idempotent_and_owned_assets_default_permanent(tmp_db):
    tmp_db.init_db()
    with tmp_db.get_conn() as conn:
        asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(assets)")}
        media_columns = {row[1] for row in conn.execute("PRAGMA table_info(hotspot_media)")}
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(video_generation_jobs)")}
    assert {
        "retention_class", "last_used_at", "pinned_at", "purge_after",
        "file_status", "purged_at",
    } <= asset_columns
    assert "lifecycle_status" in media_columns
    assert {"output_pinned_at", "output_purged_at"} <= job_columns

    owned_id = _asset(tmp_db, retention_class="permanent", filepath="assets/library/owned.mp4")
    assert tmp_db.get_asset(owned_id)["retention_class"] == "permanent"


def test_hotspot_media_can_filter_recent_active_and_archived_candidates(tmp_db):
    hotspot_id = _hotspot(tmp_db)
    now = datetime.now(timezone.utc)
    recent_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "image",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://news.gov.za/recent.jpg",
        "published_at": (now - timedelta(days=2)).isoformat(),
        "lifecycle_status": "active",
    })
    old_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "image",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://news.gov.za/old.jpg",
        "published_at": (now - timedelta(days=40)).isoformat(),
        "lifecycle_status": "archived",
    })

    active = tmp_db.list_hotspot_media(lifecycle_status="active", freshness_days=30)
    archived = tmp_db.list_hotspot_media(lifecycle_status="archived")

    assert [item["id"] for item in active] == [recent_id]
    assert [item["id"] for item in archived] == [old_id]


def test_asset_reference_reasons_cover_current_and_legacy_workflows(tmp_db):
    asset_id = _asset(tmp_db)
    assert tmp_db.asset_reference_reasons(asset_id) == []

    user_id = tmp_db.create_user("retention-admin", "hashed", "admin", "Retention Admin")
    project = tmp_db.create_video_project(
        created_by=user_id,
        source_type="chat",
        source_snapshot={"topic": "Durban"},
        title="Durban update",
    )
    tmp_db.create_video_project_revision(
        project["id"],
        {"scenes": [{"asset_id": asset_id}]},
        created_by=user_id,
    )
    with tmp_db.get_conn() as conn:
        conn.execute(
            "UPDATE video_projects SET status='generating' WHERE id=?",
            (project["id"],),
        )

    assert "video_project_revision" in tmp_db.asset_reference_reasons(asset_id)
    assert tmp_db.asset_is_referenced(asset_id) is True


def test_asset_reference_reasons_cover_queue_and_direct_relations(tmp_db):
    asset_id = _asset(tmp_db, filepath="assets/library/queue.mp4")
    hotspot_id = _hotspot(tmp_db, source_url="https://news.gov.za/queue-story")
    with tmp_db.get_conn() as conn:
        conn.execute(
            "UPDATE hotspots SET asset_id=? WHERE id=?",
            (asset_id, hotspot_id),
        )
        conn.execute(
            """INSERT INTO queue (title,body,platform,status,attachments)
               VALUES (?,?,?,?,?)""",
            (
                "Queued post",
                "body",
                "douyin",
                "queued",
                json.dumps([{"asset_id": asset_id, "path": "assets/library/queue.mp4"}]),
            ),
        )

    reasons = tmp_db.asset_reference_reasons(asset_id)
    assert "hotspot" in reasons
    assert "queue_attachment" in reasons


def _old_hotspot_file(db, static_dir: Path, *, name="cleanup.mp4", age_days=8):
    relative = f"assets/library/{name}"
    target = static_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"hotspot-video")
    asset_id = _asset(db, filepath=relative)
    created_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE assets SET created_at=?,size=? WHERE id=?",
            (created_at, target.stat().st_size, asset_id),
        )
    return asset_id, target


def test_cleanup_dry_run_reports_without_deleting(tmp_db, tmp_path):
    import media_retention

    static_dir = tmp_path / "static"
    asset_id, target = _old_hotspot_file(tmp_db, static_dir)

    report = media_retention.run_cleanup(static_dir, dry_run=True)

    assert target.exists()
    assert report["candidate_count"] == 1
    assert report["estimated_bytes"] == len(b"hotspot-video")
    assert report["deleted_count"] == 0
    assert tmp_db.get_asset(asset_id)["file_status"] == "available"


def test_cleanup_deletes_eligible_hotspot_file_but_keeps_metadata(tmp_db, tmp_path):
    import media_retention

    static_dir = tmp_path / "static"
    asset_id, target = _old_hotspot_file(tmp_db, static_dir, name="delete.mp4")

    report = media_retention.run_cleanup(static_dir, dry_run=False)

    assert not target.exists()
    assert report["deleted_count"] == 1
    asset = tmp_db.get_asset(asset_id)
    assert asset is not None
    assert asset["file_status"] == "purged"
    assert asset["purged_at"]


def test_cleanup_never_deletes_permanent_pinned_or_outside_files(tmp_db, tmp_path):
    import media_retention

    static_dir = tmp_path / "static"
    permanent_id, permanent = _old_hotspot_file(tmp_db, static_dir, name="permanent.mp4")
    pinned_id, pinned = _old_hotspot_file(tmp_db, static_dir, name="pinned.mp4")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    outside_id = _asset(tmp_db, filepath=str(outside))
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    with tmp_db.get_conn() as conn:
        conn.execute(
            "UPDATE assets SET retention_class='permanent',created_at=? WHERE id=?",
            (old, permanent_id),
        )
        conn.execute(
            "UPDATE assets SET pinned_at=datetime('now') WHERE id=?",
            (pinned_id,),
        )
        conn.execute(
            "UPDATE assets SET created_at=? WHERE id=?",
            (old, outside_id),
        )

    report = media_retention.run_cleanup(static_dir, dry_run=False)

    assert permanent.exists()
    assert pinned.exists()
    assert outside.exists()
    assert report["deleted_count"] == 0
    assert report["skipped_count"] >= 1


def test_cleanup_archives_old_unconfirmed_hotspot_candidates(tmp_db, tmp_path):
    import media_retention

    hotspot_id = _hotspot(tmp_db, source_url="https://news.gov.za/archive-story")
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "image",
        "source_page_url": "https://news.gov.za/archive-story",
        "original_media_url": "https://news.gov.za/archive.jpg",
    })
    with tmp_db.get_conn() as conn:
        conn.execute(
            "UPDATE hotspot_media SET created_at=datetime('now','-31 days') WHERE id=?",
            (media_id,),
        )

    report = media_retention.run_cleanup(tmp_path / "static", dry_run=True)

    assert report["archived_count"] == 1
    assert tmp_db.get_hotspot_media(media_id)["lifecycle_status"] == "archived"


def _finished_video_job(db, static_dir: Path, *, name="final.mp4", age_days=31):
    user_id = db.create_user(f"video-{name}", "hashed", "admin", "Video Owner")
    project = db.create_video_project(
        created_by=user_id,
        source_type="chat",
        source_snapshot={"topic": "hotspot"},
        title="Final video",
    )
    revision = db.create_video_project_revision(
        project["id"], {"scenes": []}, created_by=user_id
    )
    job, _ = db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, f"key-{name}"
    )
    relative = f"uploads/video/{name}"
    target = static_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"final-video")
    finished_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    db.update_video_generation_job(
        job["id"],
        status="succeeded",
        stage="succeeded",
        progress=100,
        output_path=relative,
        finished_at=finished_at,
    )
    return job["id"], target


def test_cleanup_purges_final_output_after_thirty_days(tmp_db, tmp_path):
    import media_retention

    static_dir = tmp_path / "static"
    job_id, target = _finished_video_job(tmp_db, static_dir)

    report = media_retention.run_cleanup(static_dir, dry_run=False)

    assert not target.exists()
    assert report["output_deleted_count"] == 1
    assert tmp_db.get_video_generation_job(job_id)["output_purged_at"]


def test_cleanup_keeps_pinned_final_output(tmp_db, tmp_path):
    import media_retention

    static_dir = tmp_path / "static"
    job_id, target = _finished_video_job(tmp_db, static_dir, name="pinned-final.mp4")
    tmp_db.set_video_output_pinned(job_id, True)

    report = media_retention.run_cleanup(static_dir, dry_run=False)

    assert target.exists()
    assert report["output_deleted_count"] == 0


def _api_client(tmp_db, username: str, role: str):
    from fastapi.testclient import TestClient
    import app

    tmp_db.create_user(username, auth.hash_password("pw12345"), role, username)
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pw12345"}
    ).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_retention_preview_is_admin_only_and_asset_pin_is_persisted(tmp_db, tmp_path, monkeypatch):
    import app

    static_dir = tmp_path / "static"
    asset_id, _ = _old_hotspot_file(tmp_db, static_dir, name="api-preview.mp4")
    monkeypatch.setattr(app, "STATIC_DIR", static_dir)
    editor, editor_headers = _api_client(tmp_db, "retention-editor", "editor")
    admin, admin_headers = _api_client(tmp_db, "retention-api-admin", "admin")

    assert editor.get("/api/media-retention/preview", headers=editor_headers).status_code == 403
    preview = admin.get("/api/media-retention/preview", headers=admin_headers)
    pinned = admin.post(f"/api/assets/{asset_id}/pin", headers=admin_headers)
    unpinned = admin.post(f"/api/assets/{asset_id}/unpin", headers=admin_headers)

    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["candidate_count"] == 1
    assert pinned.status_code == 200
    assert pinned.json()["pinned_at"]
    assert unpinned.status_code == 200
    assert unpinned.json()["pinned_at"] is None


def test_video_output_pin_is_private_to_owner(tmp_db, tmp_path):
    static_dir = tmp_path / "static"
    job_id, _ = _finished_video_job(tmp_db, static_dir, name="api-final.mp4")
    with tmp_db.get_conn() as conn:
        owner_id = conn.execute(
            "SELECT created_by FROM video_generation_jobs WHERE id=?", (job_id,)
        ).fetchone()[0]
        owner_name = conn.execute(
            "SELECT username FROM users WHERE id=?", (owner_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (auth.hash_password("pw12345"), owner_id),
        )
    from fastapi.testclient import TestClient
    import app
    client = TestClient(app.app)
    owner_token = client.post(
        "/api/auth/login", json={"username": owner_name, "password": "pw12345"}
    ).json()["access_token"]
    _, other_headers = _api_client(tmp_db, "other-video-owner", "admin")

    denied = client.post(
        f"/api/video-generation/jobs/{job_id}/output-pin", headers=other_headers
    )
    allowed = client.post(
        f"/api/video-generation/jobs/{job_id}/output-pin",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert denied.status_code == 404
    assert allowed.status_code == 200
    assert allowed.json()["output_pinned_at"]


def test_disk_guard_uses_configured_capacity_thresholds(tmp_path, monkeypatch):
    import media_retention

    class Usage:
        total = 1000
        used = 960
        free = 40

    monkeypatch.setattr(media_retention.shutil, "disk_usage", lambda _path: Usage())
    monkeypatch.setenv("MEDIA_DISK_STOP_PERCENT", "5")
    monkeypatch.setenv("MEDIA_DISK_WARN_PERCENT", "15")

    state = media_retention.disk_guard(tmp_path)

    assert state["free_percent"] == 4.0
    assert state["warning"] is True
    assert state["blocked"] is True


def test_downloaded_hotspot_asset_gets_expiring_retention_class(tmp_db):
    asset_id = _asset(tmp_db, retention_class="permanent")

    tmp_db.set_asset_retention(asset_id, "hotspot_source", retention_days=7)
    asset = tmp_db.get_asset(asset_id)

    assert asset["retention_class"] == "hotspot_source"
    assert asset["purge_after"]
    assert asset["file_status"] == "available"


def test_scheduler_registers_daily_retention_dry_run():
    source = (Path(__file__).parents[1] / "scheduler.py").read_text(encoding="utf-8")
    assert 'id="media_retention_cleanup"' in source
    assert "MEDIA_CLEANUP_ENABLED" in source
    assert "MEDIA_CLEANUP_DRY_RUN" in source


def test_hook_library_rolling_cleanup_keeps_recent_three_days_and_removes_old_hooks(tmp_db, tmp_path):
    import media_retention

    static_dir = tmp_path / "static"
    hotspot_id = _hotspot(tmp_db, source_url="https://news.gov.za/hook-cleanup")

    def create_media(name: str, age_days: int):
        asset_id, target = _old_hotspot_file(tmp_db, static_dir, name=name, age_days=age_days)
        tmp_db.update_asset_provenance(asset_id, "https://news.gov.za/hook-video", "授权", "SAnews", hotspot_id)
        event = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
            "event_index": 1, "start_ms": 0, "end_ms": 7_000,
            "title_zh": "港口入口排队", "title_en": "Port gate queue", "segments": [],
            "confidence": 0.9, "review_status": "confirmed",
            "evidence": {"what_happened": "卡车在入口排队", "hook_reason": "现场画面", "logistics_question": "如何安排到仓？"},
        }])[0]
        clip = static_dir / f"assets/event-clips/{name}"
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"hook")
        with tmp_db.get_conn() as conn:
            conn.execute(
                "UPDATE hotspot_event_clips SET clip_path=? WHERE id=?",
                (f"assets/event-clips/{name}", event["id"]),
            )
        media_id, _ = tmp_db.upsert_hotspot_media({
            "hotspot_id": hotspot_id, "media_kind": "video_file", "platform": "direct",
            "source_page_url": f"https://news.gov.za/{name}", "original_media_url": f"https://news.gov.za/{name}.mp4",
            "local_path": f"assets/library/{name}", "asset_id": asset_id, "rights_tier": "green",
            "download_status": "downloaded", "processing_status": "ready",
        })
        with tmp_db.get_conn() as conn:
            conn.execute(
                "UPDATE hotspot_media SET confirmed_at=datetime('now', ?),created_at=datetime('now', ?) WHERE id=?",
                (f"-{age_days} days", f"-{age_days} days", media_id),
            )
        return media_id, asset_id, target, clip

    old_media, old_asset, old_source, old_clip = create_media("old-hook.mp4", 11)
    recent_media, recent_asset, recent_source, recent_clip = create_media("recent-hook.mp4", 2)

    preview = media_retention.cleanup_hotspot_hook_library(static_dir, retention_days=10, protect_days=3, dry_run=True)
    report = media_retention.cleanup_hotspot_hook_library(static_dir, retention_days=10, protect_days=3, dry_run=False)

    assert preview["candidate_count"] == 1
    assert report["candidate_count"] == 1
    assert report["deleted_count"] == 1
    assert tmp_db.get_hotspot_media(old_media) is None
    assert tmp_db.get_asset(old_asset) is None
    assert not old_source.exists()
    assert not old_clip.exists()
    assert tmp_db.get_hotspot_media(recent_media) is not None
    assert tmp_db.get_asset(recent_asset) is not None
    assert recent_source.exists()
    assert recent_clip.exists()


def test_scheduler_uses_model_gate_for_three_day_hook_intake_and_ten_day_rotation():
    source = (Path(__file__).parents[1] / "scheduler.py").read_text(encoding="utf-8")

    assert "db.list_active_authorized_hotspot_media_for_full_intake()" in source
    assert 'model_router.key_is_available("planner_text")' in source
    assert 'model_router.key_is_available("critic")' in source
    assert '"admission_mode": "all_authorized_video_analysis"' in source
    assert "hotspot_hook_intake.select_for_hook_ingestion" not in source
    assert '"interval",\n        days=3' in source
    assert 'id="hotspot_hook_library_sync"' in source
    assert 'id="hotspot_hook_library_cleanup"' in source
    assert "HOTSPOT_HOOK_RETENTION_DAYS" in source
    assert "HOTSPOT_HOOK_PROTECT_DAYS" in source


def test_start_script_uses_project_directory_instead_of_desktop_alias():
    source = (Path(__file__).parents[1] / "start.sh").read_text(encoding="utf-8")
    assert 'dirname -- "$0"' in source
    assert "~/Desktop/distribution-manager" not in source


def test_video_generation_is_blocked_when_disk_is_critically_low(tmp_db, monkeypatch):
    import app

    client, headers = _api_client(tmp_db, "disk-guard-user", "admin")
    with tmp_db.get_conn() as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username='disk-guard-user'"
        ).fetchone()[0]
    project = tmp_db.create_video_project(
        created_by=user_id,
        source_type="chat",
        source_snapshot={"topic": "capacity"},
        title="Capacity guard",
    )
    tmp_db.create_video_project_revision(project["id"], {"scenes": []}, user_id)
    monkeypatch.setattr(
        app.media_retention,
        "disk_guard",
        lambda _path: {"blocked": True, "free_percent": 4.0},
    )

    response = client.post(
        f"/api/video-projects/{project['id']}/generate",
        json={},
        headers=headers,
    )

    assert response.status_code == 507
    assert "磁盘" in response.json()["detail"]
