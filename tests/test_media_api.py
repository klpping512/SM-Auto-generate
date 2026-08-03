import io
from pathlib import Path

import pytest

from PIL import Image


def _client_and_token(tmp_db):
    from fastapi.testclient import TestClient
    import app, auth
    tmp_db.create_user("mediaadmin", auth.hash_password("pw12345"), "admin", "Media Admin")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "mediaadmin", "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _png():
    buffer = io.BytesIO(); Image.new("RGB", (100, 160), "red").save(buffer, "PNG"); buffer.seek(0); return buffer


class _ChunkOnlyUpload:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.read_sizes = []

    async def read(self, size=-1):
        assert size > 0, "上传实现禁止无参数整文件读取"
        self.read_sizes.append(size)
        return next(self._chunks, b"")


@pytest.mark.asyncio
async def test_large_upload_is_streamed_in_bounded_chunks(tmp_path):
    import media_assets

    upload = _ChunkOnlyUpload([b"abcd", b"efgh", b"ij", b""])
    target = tmp_path / "large.mov"
    size = await media_assets.stream_upload_to_path(upload, target, max_size=12, chunk_size=4)

    assert size == 10
    assert target.read_bytes() == b"abcdefghij"
    assert upload.read_sizes == [4, 4, 4, 4]


@pytest.mark.asyncio
async def test_stream_upload_rejects_limit_and_removes_partial_file(tmp_path):
    import media_assets

    upload = _ChunkOnlyUpload([b"abcd", b"efgh", b""])
    target = tmp_path / "too-large.mov"
    with pytest.raises(ValueError, match="超过大小限制"):
        await media_assets.stream_upload_to_path(upload, target, max_size=6, chunk_size=4)

    assert not target.exists()


def test_assets_page_exposes_large_upload_progress_contract():
    page = (Path(__file__).parents[1] / "static" / "assets.html").read_text()
    assert "upload.onprogress" in page
    assert 'id="uploadProgress"' in page
    assert "MAX_VIDEO_UPLOAD" in page
    assert "2147483648" in page


def test_asset_upload_list_update_delete(tmp_db, monkeypatch, tmp_path):
    import app
    client, headers = _client_and_token(tmp_db)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)
    response = client.post("/api/assets/upload?category=warehouse", headers=headers, files={"file": ("warehouse.png", _png(), "image/png")})
    assert response.status_code == 200
    asset = response.json(); assert asset["category"] == "warehouse"
    listed = client.get("/api/assets?type=image&category=warehouse", headers=headers).json()
    assert [item["id"] for item in listed] == [asset["id"]]
    assert client.put(f"/api/assets/{asset['id']}", headers=headers, json={"name": "新名称", "category": "brand"}).status_code == 200
    assert client.delete(f"/api/assets/{asset['id']}", headers=headers).json()["status"] == "deleted"


def test_brand_filter_returns_delivery_asset_with_visible_buffalo_tag(tmp_db):
    client, headers = _client_and_token(tmp_db)
    asset_id = tmp_db.create_asset({
        "name": "IMG_6032", "filepath": "assets/library/image/truck.jpg", "file_type": "image",
        "category": "delivery", "duration": None, "width": 1600, "height": 900, "size": 1024,
        "thumbnail": None, "sha256": "b" * 64, "source": "upload", "status": "active", "created_by": None,
    })
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 0,
        "description": "Buffalo 配送车", "primary_category": "delivery",
        "processing_version": "semantic-v3-qwen-vl-brand",
    })
    tmp_db.replace_segment_tags(segment_id, [
        {"dimension": "brand", "value": "Buffalo", "confidence": 0.95, "source": "ocr"},
    ])

    response = client.get("/api/assets?category=brand", headers=headers)

    assert response.status_code == 200
    items = response.json()
    assert [item["id"] for item in items] == [asset_id]
    assert items[0]["category"] == "delivery"
    assert items[0]["brand_tags"] == ["Buffalo"]


def test_assets_page_exposes_brand_tags_and_safe_taxonomy_rebuild_actions():
    page = (Path(__file__).parents[1] / "static" / "assets.html").read_text(encoding="utf-8")

    assert "品牌露出：" in page
    assert "补全 Buffalo 品牌标签" in page
    assert "重建视觉与物流标签" in page
    assert "/api/assets/backfill-buffalo-brand-tags" in page
    assert "limit:100" in page
    assert "最多 100 条" in page


def test_render_rejects_missing_capability(tmp_db, monkeypatch):
    import app
    client, headers = _client_and_token(tmp_db)
    monkeypatch.setattr(app.media_assets, "capabilities", lambda: {"ffmpeg": False, "ffprobe": False})
    response = client.post("/api/douyin/render", headers=headers, json={"voice": "苏打", "scenes": []})
    assert response.status_code == 503
    assert "FFmpeg" in response.json()["detail"]


def test_render_rejects_safe_fallback_and_short_production_copy(tmp_db, monkeypatch):
    import app
    client, headers = _client_and_token(tmp_db)
    monkeypatch.setattr(app.media_assets, "capabilities", lambda: {"ffmpeg": True, "ffprobe": True})
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    fallback = client.post("/api/douyin/render", headers=headers, json={
        "source": "safe_fallback", "voice": "Cherry", "duration_target": 60, "scenes": [],
    })
    assert fallback.status_code == 409
    assert "提示不能生成视频" in fallback.json()["detail"]

    short = client.post("/api/douyin/render", headers=headers, json={
        "source": "model", "voice": "Cherry", "tts_provider": "qwen", "duration_target": 60,
        "scenes": [
            {"duration": 8, "voiceover": "核对节点。", "visual": f"物流场景{i}"}
            for i in range(8)
        ],
    })
    assert short.status_code == 409
    assert "无法支撑 60 秒正式成片" in short.json()["detail"]

    too_few = client.post("/api/douyin/render", headers=headers, json={
        "source": "model", "voice": "Cherry", "tts_provider": "qwen", "duration_target": 60,
        "scenes": [
            {"duration": 10, "voiceover": "这是一段足够长的旁白用于通过字数门槛。" * 3, "visual": f"场景{i}"}
            for i in range(5)
        ],
    })
    assert too_few.status_code == 400
    assert "7–10" in too_few.json()["detail"]


def test_render_job_is_private_to_creator(tmp_db, monkeypatch):
    import app, auth
    from fastapi.testclient import TestClient
    own_id = tmp_db.create_user("owneditor", auth.hash_password("pw12345"), "editor", "Own")
    other_id = tmp_db.create_user("othereditor", auth.hash_password("pw12345"), "editor", "Other")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "owneditor", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tmp_db.create_render_job("private-job", {"scenes": []}, "苏打", other_id)
    assert client.get("/api/douyin/render/private-job", headers=headers).status_code == 404
    assert client.post("/api/douyin/render/private-job/retry", headers=headers).status_code == 404


def test_subtitles_follow_speech_timeline_and_clip_selection():
    import video_renderer
    script = video_renderer.normalize_script({
        "duration_target_ms": 30_000,
        "output_mode": "full_and_clips", "selected_clip_scenes": [1, "2", 9, 2],
        "scenes": [{"duration": 5, "voiceover": f"第{i}句。", "visual": "仓库"} for i in range(1, 5)],
    }, set())
    assert script["selected_clip_scenes"] == [1, 2]
    cues = video_renderer.build_subtitle_cues("先说事实。再说结论！", 6.5)
    assert cues[0]["start"] == 0
    assert cues[-1]["end"] == 6.5
    assert cues[0]["end"] == cues[1]["start"]


def test_scene_subtitles_do_not_require_optional_libass(tmp_path, monkeypatch):
    import video_renderer
    source = tmp_path / "source.mp4"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    segment = tmp_path / "segment.mp4"
    monkeypatch.setattr(video_renderer, "_has_audio", lambda *_: False)
    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, True, wav,
        [{"start": 0.0, "end": 1.5, "text": "字幕兼容性测试。"}],
        2.0, segment, tmp_path, 0,
    )
    joined = " ".join(command)
    assert "overlay=" in joined
    assert "ass=" not in joined
    assert (tmp_path / "subtitle-0-0.png").exists()


def test_normalized_script_rejects_reusing_one_buffalo_source_for_multiple_segments():
    import video_renderer

    with pytest.raises(ValueError, match="Buffalo 原始视频 7"):
        video_renderer.normalize_script({"duration_target_ms": 30_000, "scenes": [
            {"duration": 5, "voiceover": f"旁白{i}", "asset_id": 7,
             "asset_segment_id": 20 + i, "asset_start_ms": 2_000, "asset_end_ms": 7_000}
            for i in range(4)
        ]}, {7})


def test_scene_command_seeks_to_matched_shot_start(tmp_path, monkeypatch):
    import video_renderer

    source = tmp_path / "source.mp4"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    monkeypatch.setattr(video_renderer, "_has_audio", lambda *_: False)
    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, True, wav, [], 4.0,
        tmp_path / "out.mp4", tmp_path, 0, source_start=2.5,
    )

    assert command[command.index("-ss") + 1] == "2.5"


def test_media_capabilities_expose_voice_availability_and_preview_flag(tmp_db, monkeypatch):
    import app
    client, headers = _client_and_token(tmp_db)
    monkeypatch.setenv("MIMO_API_KEY", "mimo-test")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    response = client.get("/api/media/capabilities", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["tts_preview_supported"] is True
    assert payload["voice_options"][0]["provider"] == "mimo"
    assert "available" in payload["voice_options"][0]
    assert "disabled_reason" in payload["voice_options"][0]
    assert "preview_supported" in payload["voice_options"][0]


def test_tts_preview_does_not_create_video_jobs(tmp_db, monkeypatch, tmp_path):
    import database as db
    import video_renderer

    client, headers = _client_and_token(tmp_db)
    monkeypatch.setenv("MIMO_API_KEY", "mimo-test")

    def _fake_preview(text, *, tts_provider=None, voice=None, output_dir=None):
        root = Path(output_dir or tmp_path)
        root.mkdir(parents=True, exist_ok=True)
        out = root / "preview-test.wav"
        out.write_bytes(b"RIFFpreview")
        return {
            "audio_path": f"uploads/tts-previews/{out.name}",
            "audio_url": f"/static/uploads/tts-previews/{out.name}",
            "tts_provider": tts_provider or "mimo",
            "voice": voice or "mimo_default",
            "text": text,
            "fallback_used": False,
        }

    monkeypatch.setattr(video_renderer, "synthesize_tts_preview", _fake_preview)
    user = db.get_user_by_username("mediaadmin")
    before = len(db.list_active_video_generation_jobs(user["id"]))
    response = client.post("/api/media/tts-preview", headers=headers, json={
        "text": "西开普发货前先确认路况。",
        "tts_provider": "mimo",
        "voice": "mimo_default",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["audio_url"].startswith("/static/")
    assert body["tts_provider"] == "mimo"
    after = len(db.list_active_video_generation_jobs(user["id"]))
    assert after == before


def test_list_video_projects_endpoint(tmp_db):
    import database as db
    client, headers = _client_and_token(tmp_db)
    user = db.get_user_by_username("mediaadmin")
    created = db.create_video_project(
        created_by=user["id"],
        source_type="manual",
        source_snapshot={},
        title="列表测试项目",
        platform="douyin",
        target_duration_ms=60000,
        target_orientation="portrait",
    )
    response = client.get("/api/video-projects", headers=headers)
    assert response.status_code == 200
    items = response.json()
    assert any(item["id"] == created["id"] for item in items)
