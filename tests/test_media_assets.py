import io
from pathlib import Path

from PIL import Image

import media_assets
import video_renderer


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (320, 480), "navy").save(buffer, "PNG")
    return buffer.getvalue()


def test_ingest_image_validates_deduplicates_and_generates_thumbnail(tmp_db, tmp_path):
    source = tmp_path / "warehouse.png"
    source.write_bytes(_png_bytes())
    first = media_assets.ingest_file(source, tmp_path / "static", "warehouse")
    second = media_assets.ingest_file(source, tmp_path / "static", "warehouse")
    assert first["id"] == second["id"]
    assert first["width"] == 320 and first["height"] == 480
    assert (tmp_path / "static" / first["thumbnail"]).exists()


def test_ingest_rejects_fake_image(tmp_db, tmp_path):
    source = tmp_path / "fake.png"
    source.write_bytes(b"not an image")
    try:
        media_assets.ingest_file(source, tmp_path / "static")
        assert False, "expected invalid image"
    except Exception:
        pass


def test_douyin_script_normalization_rejects_bad_scene_count():
    try:
        video_renderer.normalize_script({"duration_target_ms": 30_000, "scenes": [{"duration": 5}]}, set())
        assert False, "expected validation error"
    except ValueError as exc:
        assert "4–8" in str(exc)


def test_formal_production_requires_seven_to_ten_scenes():
    try:
        video_renderer.normalize_script({
            "duration_target_ms": 60_000,
            "scenes": [{"duration": 8, "voiceover": f"口播{i}"} for i in range(5)],
        }, set())
        assert False, "expected validation error"
    except ValueError as exc:
        assert "7–10" in str(exc)


def test_douyin_script_normalization_rejects_legacy_infographic_scene():
    import pytest

    with pytest.raises(ValueError, match="信息图、流程图和 PPT 卡片已禁用"):
        video_renderer.normalize_script({"scenes": [
            {"duration": 5, "scene_role": "logistics_explainer", "evidence_type": "explanation_card"},
            {"duration": 5}, {"duration": 5}, {"duration": 5},
            {"duration": 5}, {"duration": 5}, {"duration": 5},
        ]}, set())


def test_douyin_script_removes_unknown_asset_ids():
    script = {"duration_target_ms": 30_000, "scenes": [
        {"duration": 5, "asset_id": 1}, {"duration": 5, "asset_id": 999},
        {"duration": 5}, {"duration": 5}, {"duration": 5},
    ]}
    normalized = video_renderer.normalize_script(script, {1})
    assert normalized["scenes"][0]["asset_id"] == 1
    assert normalized["scenes"][1]["asset_id"] is None
    assert 25 <= sum(s["duration"] for s in normalized["scenes"]) <= 35


def test_tts_voice_options_include_mimo_and_qwen():
    options = video_renderer.tts_voice_options()
    assert {"provider": "mimo", "id": "mimo_default", "label": "MiMo 默认"} in options
    assert any(item["provider"] == "qwen" and item["id"] == "Cherry" for item in options)


def test_resolve_tts_selection_rejects_unknown_voice_in_strict_mode():
    import pytest

    provider, voice = video_renderer.resolve_tts_selection("mimo", "mimo_default", strict=True)
    assert provider == "mimo" and voice == "mimo_default"
    with pytest.raises(ValueError, match="有效的 Qwen"):
        video_renderer.resolve_tts_selection("qwen", "不存在的音色", strict=True)
    with pytest.raises(ValueError, match="有效的 MiMo"):
        video_renderer.resolve_tts_selection("mimo", "Cherry", strict=True)


def test_qwen_tts_downloads_returned_wav(monkeypatch, tmp_path):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"output": {"audio": {"url": "https://audio.example/voice.wav"}}}
    class AudioResponse:
        content = b"RIFFdemo"
        def raise_for_status(self): pass
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, headers, json): captured.update({"url":url,"headers":headers,"json":json}); return Response()
        def get(self, url): return AudioResponse()
    monkeypatch.setattr(video_renderer.httpx, "Client", Client)
    output = tmp_path / "voice.wav"
    video_renderer.synthesize_qwen_tts("测试口播", "Cherry", output, api_key="secret")
    assert captured["json"]["model"] == "qwen3-tts-flash"
    assert captured["json"]["input"]["text"] == "测试口播"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert output.read_bytes() == b"RIFFdemo"


def test_qwen_tts_retries_a_transient_audio_download_disconnect(monkeypatch, tmp_path):
    import httpx

    calls = {"get": 0}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"output": {"audio": {"url": "https://audio.example/voice.wav"}}}
    class AudioResponse:
        content = b"RIFFretried"
        def raise_for_status(self): pass
    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *_args, **_kwargs): return Response()
        def get(self, *_args):
            calls["get"] += 1
            if calls["get"] == 1:
                raise httpx.RemoteProtocolError("Server disconnected")
            return AudioResponse()

    monkeypatch.setattr(video_renderer.httpx, "Client", Client)
    monkeypatch.setattr(video_renderer.time, "sleep", lambda _seconds: None)
    output = tmp_path / "voice.wav"

    video_renderer.synthesize_qwen_tts("测试口播", "Cherry", output, api_key="secret")

    assert calls["get"] == 2
    assert output.read_bytes() == b"RIFFretried"


def test_qwen_tts_normalizes_legacy_voice_to_current_default():
    assert video_renderer.normalize_tts_voice("冰糖") == "Cherry"
    assert video_renderer.normalize_tts_voice("") == "Cherry"
