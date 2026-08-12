import asyncio
import time
from datetime import datetime, timezone

import auth
import pytest


def _hotspot_and_media(tmp_db, rights_tier="yellow", confirmed=False):
    token = "abc123def45" if rights_tier != "red" else "red123def45"
    source_url = f"https://news.gov.za/video-story-{token}"
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Port video",
        "summary": "Video report",
        "source_url": source_url,
        "publisher": "SAnews",
        "published_at": "2026-07-22T08:00:00+00:00",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": "video-snapshot",
        "image_candidate_url": None,
    })
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": token,
        "source_page_url": source_url,
        "original_media_url": f"https://www.youtube.com/watch?v={token}",
        "rights_tier": rights_tier,
        "download_status": "metadata_ready",
    })
    if confirmed:
        tmp_db.update_hotspot_media_rights(
            media_id,
            rights_tier=rights_tier,
            rights_note="已确认新闻型宣传授权",
            license_name="Publisher permission",
            attribution="SAnews",
            rights_evidence_url="https://news.gov.za/permissions/video",
            confirmed_by=None,
        )
        with tmp_db.get_conn() as conn:
            conn.execute(
                "UPDATE hotspot_media SET confirmed_by=99,confirmed_at=datetime('now') WHERE id=?",
                (media_id,),
            )
    return hotspot_id, media_id


def _login_admin(tmp_db):
    from fastapi.testclient import TestClient
    import app

    tmp_db.create_user("video-media-admin", auth.hash_password("pw12345"), "admin", "admin")
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": "video-media-admin", "password": "pw12345"}
    ).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_materialization_guard_accepts_explicit_confirmation_and_blocks_red(tmp_db):
    from hotspot_media import validate_materialization

    _, yellow_id = _hotspot_and_media(tmp_db, "yellow", confirmed=False)
    _, red_id = _hotspot_and_media(
        tmp_db, "red", confirmed=False
    )

    validate_materialization(tmp_db.get_hotspot_media(yellow_id), "admin", True)
    with pytest.raises(ValueError, match="人工确认"):
        validate_materialization(tmp_db.get_hotspot_media(yellow_id), "admin", False)
    with pytest.raises(ValueError, match="停用"):
        validate_materialization(tmp_db.get_hotspot_media(red_id), "admin", True)


def test_one_click_confirmation_records_source_and_marks_download_pending(tmp_db, monkeypatch):
    import app

    source_url, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=False)
    source_url = tmp_db.get_hotspot(source_url)["source_url"]

    async def no_op_materialization(media_id, created_by):
        return None

    monkeypatch.setattr(app, "_run_hotspot_media_materialization", no_op_materialization)
    client, headers = _login_admin(tmp_db)
    accepted = client.post(
        f"/api/hotspot-media/{media_id}/materialize",
        headers=headers,
        json={"confirmed": True},
    )

    item = tmp_db.get_hotspot_media(media_id)
    assert accepted.status_code == 202
    assert item["download_status"] == "pending"
    assert item["confirmed_at"]
    assert item["confirmed_by"] == tmp_db.get_user_by_username("video-media-admin")["id"]
    assert item["authorization_status"] == "authorized"
    assert item["rights_note"] == "管理员确认下载到热点素材库（内部使用）"
    assert item["license_name"] is None
    assert item["attribution"] == "SAnews"
    assert item["rights_evidence_url"] == source_url


def test_materialization_endpoint_requires_confirmation_and_marks_pending(tmp_db, monkeypatch):
    import app

    _, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=True)

    async def no_op_materialization(media_id, created_by):
        return None

    monkeypatch.setattr(app, "_run_hotspot_media_materialization", no_op_materialization)
    client, headers = _login_admin(tmp_db)

    blocked = client.post(
        f"/api/hotspot-media/{media_id}/materialize",
        headers=headers,
        json={"confirmed": False},
    )
    accepted = client.post(
        f"/api/hotspot-media/{media_id}/materialize",
        headers=headers,
        json={"confirmed": True},
    )

    assert blocked.status_code == 400
    assert accepted.status_code == 202
    assert tmp_db.get_hotspot_media(media_id)["download_status"] == "pending"


def test_materialization_preserves_original_duration_seconds(tmp_db, monkeypatch, tmp_path):
    """分析档只有 120s 时，不得把母片 duration_seconds 焊成 120。"""
    import json

    import app

    _, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=True)
    tmp_db.update_hotspot_media_state(media_id, duration_seconds=600)
    asset_id = tmp_db.create_asset({
        "name": "分析档短片",
        "filepath": "assets/library/video/analysis-clip.mp4",
        "file_type": "video",
        "category": "other",
        "duration": 120,
        "width": 854,
        "height": 480,
        "size": 100,
        "thumbnail": "assets/thumbnails/analysis-clip.jpg",
        "sha256": "a" * 64,
        "source": "youtube",
        "status": "active",
        "created_by": None,
    })

    def fake_download(item, static_dir, created_by, progress_callback=None):
        asset = dict(tmp_db.get_asset(asset_id))
        asset["sample_offsets"] = [(0.0, 120.0)]
        return asset

    async def fake_process(job_id):
        tmp_db.update_asset_processing_job(job_id, status="succeeded", stage="ready", progress=100)

    monkeypatch.setattr(app.hotspot_media, "download_authorized_video", fake_download)
    monkeypatch.setattr(app, "_run_asset_processing_job", fake_process)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    asyncio.run(app._run_hotspot_media_materialization(media_id, created_by=1))

    item = tmp_db.get_hotspot_media(media_id)
    assert item["duration_seconds"] == 600
    intake = json.loads(item.get("intake_decision_json") or "{}")
    assert intake["sample_offsets"] == [[0.0, 120.0]]
    assert intake["analysis_clip_seconds"] == 120


def test_materialization_reuses_asset_processing_and_marks_ready(tmp_db, monkeypatch, tmp_path):
    import app

    _, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=True)
    asset_id = tmp_db.create_asset({
        "name": "热点视频",
        "filepath": "assets/library/video/hotspot.mp4",
        "file_type": "video",
        "category": "other",
        "duration": 8,
        "width": 1080,
        "height": 1920,
        "size": 100,
        "thumbnail": "assets/thumbnails/hotspot.jpg",
        "sha256": "9" * 64,
        "source": "youtube",
        "status": "active",
        "created_by": None,
    })

    def fake_download(item, static_dir, created_by, progress_callback=None):
        assert item["id"] == media_id
        assert callable(progress_callback)
        return tmp_db.get_asset(asset_id)

    async def fake_process(job_id):
        tmp_db.update_asset_processing_job(job_id, status="succeeded", stage="ready", progress=100)

    monkeypatch.setattr(app.hotspot_media, "download_authorized_video", fake_download)
    monkeypatch.setattr(app, "_run_asset_processing_job", fake_process)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    asyncio.run(app._run_hotspot_media_materialization(media_id, created_by=1))

    item = tmp_db.get_hotspot_media(media_id)
    assert item["asset_id"] == asset_id
    assert item["media_kind"] == "video_file"
    assert item["download_status"] == "downloaded"
    assert item["processing_status"] == "ready"
    assert item["error_message"] is None


def test_materialization_resumes_a_downloaded_source_without_fetching_it_again(tmp_db, monkeypatch, tmp_path):
    import app

    _, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=True)
    asset_id = tmp_db.create_asset({
        "name": "已下载热点视频",
        "filepath": "assets/library/video/resume-hotspot.mp4",
        "file_type": "video",
        "category": "other",
        "duration": 8,
        "width": 1080,
        "height": 1920,
        "size": 100,
        "thumbnail": "assets/thumbnails/resume-hotspot.jpg",
        "sha256": "8" * 64,
        "source": "youtube",
        "status": "active",
        "created_by": None,
    })
    tmp_db.update_hotspot_media_state(
        media_id, asset_id=asset_id, download_status="downloaded", processing_status="processing"
    )

    def downloader_must_not_run(*_args, **_kwargs):
        raise AssertionError("已有母片时不应再次下载")

    async def fake_process(job_id):
        tmp_db.update_asset_processing_job(job_id, status="succeeded", stage="ready", progress=100)

    monkeypatch.setattr(app.hotspot_media, "download_authorized_video", downloader_must_not_run)
    monkeypatch.setattr(app, "_run_asset_processing_job", fake_process)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    asyncio.run(app._run_hotspot_media_materialization(media_id, created_by=1))

    item = tmp_db.get_hotspot_media(media_id)
    assert item["asset_id"] == asset_id
    assert item["download_status"] == "downloaded"
    assert item["processing_status"] == "ready"


def test_materialization_times_out_one_stuck_source_and_marks_it_skipped(tmp_db, monkeypatch, tmp_path):
    import app

    _, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=True)

    def slow_download(*_args, **_kwargs):
        time.sleep(1.1)
        return None

    monkeypatch.setenv("HOTSPOT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(app.hotspot_media, "download_authorized_video", slow_download)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    asyncio.run(app._run_hotspot_media_materialization(media_id, created_by=1))

    item = tmp_db.get_hotspot_media(media_id)
    assert item["download_status"] == "download_failed"
    assert "下载超时" in item["progress_detail"]


def test_timed_out_download_cannot_write_late_progress_over_terminal_state(tmp_db, monkeypatch, tmp_path):
    import app

    _, media_id = _hotspot_and_media(tmp_db, "yellow", confirmed=True)

    def late_callback_download(_item, _static_dir, _created_by, progress_callback=None):
        time.sleep(1.1)
        progress_callback({"status": "finished"})
        return None

    monkeypatch.setenv("HOTSPOT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(app.hotspot_media, "download_authorized_video", late_callback_download)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    asyncio.run(app._run_hotspot_media_materialization(media_id, created_by=1))
    time.sleep(0.2)

    item = tmp_db.get_hotspot_media(media_id)
    assert item["download_status"] == "download_failed"
    assert "下载超时" in item["progress_detail"]
