import asyncio
import io
from datetime import datetime, timezone

import httpx
import pytest
from PIL import Image


def _image_media(tmp_db, *, rights_tier="yellow", confirmed=True):
    token = f"{rights_tier}-{int(confirmed)}"
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Port image",
        "summary": "Photo report",
        "source_url": f"https://news.gov.za/port-image-{token}",
        "publisher": "Official News",
        "published_at": "2026-07-22T08:00:00+00:00",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": "image-snapshot",
        "image_candidate_url": f"https://news.gov.za/uploads/port-{token}.jpg",
    })
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "image",
        "platform": "direct",
        "source_page_url": f"https://news.gov.za/port-image-{token}",
        "original_media_url": f"https://news.gov.za/uploads/port-{token}.jpg",
        "publisher": "Official News",
        "author": "Official News",
        "rights_tier": rights_tier,
        "download_status": "metadata_ready",
    })
    if confirmed:
        tmp_db.update_hotspot_media_rights(
            media_id,
            rights_tier=rights_tier,
            rights_note="宣传使用已获许可",
            license_name="Publisher permission",
            attribution="Official News",
            rights_evidence_url="https://news.gov.za/media-policy",
            confirmed_by=None,
        )
        with tmp_db.get_conn() as conn:
            conn.execute(
                "UPDATE hotspot_media SET confirmed_by=99,confirmed_at=datetime('now') WHERE id=?",
                (media_id,),
            )
    return hotspot_id, media_id


def _jpeg_bytes():
    image = Image.new("RGB", (640, 360), "blue")
    output = io.BytesIO()
    image.save(output, "JPEG")
    return output.getvalue()


def test_image_materialization_guard_accepts_one_click_confirmation_and_rejects_red(tmp_db):
    from hotspot_media import validate_materialization

    _, allowed_id = _image_media(tmp_db, confirmed=False)
    _, red_id = _image_media(tmp_db, rights_tier="red", confirmed=False)

    validate_materialization(tmp_db.get_hotspot_media(allowed_id), "admin", True)
    with pytest.raises(ValueError, match="禁止"):
        validate_materialization(tmp_db.get_hotspot_media(red_id), "admin", True)


def test_download_authorized_image_validates_mime_and_ingests_asset(tmp_db, tmp_path):
    import hotspot_media

    hotspot_id, media_id = _image_media(tmp_db)
    item = tmp_db.get_hotspot_media(media_id)

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            content=_jpeg_bytes(),
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        asset = hotspot_media.download_authorized_image(
            item, tmp_path, created_by=None, client=client
        )

    stored = tmp_db.get_asset(asset["id"])
    assert stored["file_type"] == "image"
    assert stored["hotspot_id"] == hotspot_id
    assert stored["source_url"] == item["original_media_url"]


def test_download_authorized_image_rejects_non_image_and_oversize_response(tmp_db, tmp_path):
    import hotspot_media

    _, media_id = _image_media(tmp_db)
    item = tmp_db.get_hotspot_media(media_id)

    responses = [
        httpx.Response(200, content=b"html", headers={"content-type": "text/html"}),
        httpx.Response(
            200,
            content=b"",
            headers={"content-type": "image/jpeg", "content-length": str(10 * 1024 * 1024 + 1)},
        ),
    ]
    for response in responses:
        def handler(request: httpx.Request, response=response):
            response.request = request
            return response

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError):
                hotspot_media.download_authorized_image(
                    item, tmp_path, created_by=None, client=client
                )


def test_image_materialization_reuses_processing_and_marks_ready(tmp_db, monkeypatch, tmp_path):
    import app

    _, media_id = _image_media(tmp_db)
    asset_id = tmp_db.create_asset({
        "name": "热点图片",
        "filepath": "assets/library/image/hotspot.jpg",
        "file_type": "image",
        "category": "other",
        "duration": 0,
        "width": 640,
        "height": 360,
        "size": 100,
        "thumbnail": "assets/library/image/hotspot.jpg",
        "sha256": "8" * 64,
        "source": "official_news",
        "status": "active",
        "created_by": None,
    })

    def fake_download(item, static_dir, created_by):
        assert item["id"] == media_id
        return tmp_db.get_asset(asset_id)

    async def fake_process(job_id):
        tmp_db.update_asset_processing_job(
            job_id, status="succeeded", stage="ready", progress=100
        )

    monkeypatch.setattr(app.hotspot_media, "download_authorized_image", fake_download)
    monkeypatch.setattr(app, "_run_asset_processing_job", fake_process)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    asyncio.run(app._run_hotspot_media_materialization(media_id, created_by=1))

    item = tmp_db.get_hotspot_media(media_id)
    assert item["asset_id"] == asset_id
    assert item["media_kind"] == "image"
    assert item["download_status"] == "downloaded"
    assert item["processing_status"] == "ready"
