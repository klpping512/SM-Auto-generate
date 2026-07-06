import io
import base64
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
        video_renderer.normalize_script({"scenes": [{"duration": 5}]}, set())
        assert False, "expected validation error"
    except ValueError as exc:
        assert "4–6" in str(exc)


def test_douyin_script_removes_unknown_asset_ids():
    script = {"scenes": [
        {"duration": 5, "asset_id": 1}, {"duration": 5, "asset_id": 999},
        {"duration": 5}, {"duration": 5}, {"duration": 5},
    ]}
    normalized = video_renderer.normalize_script(script, {1})
    assert normalized["scenes"][0]["asset_id"] == 1
    assert normalized["scenes"][1]["asset_id"] is None
    assert 25 <= sum(s["duration"] for s in normalized["scenes"]) <= 35


def test_mimo_tts_uses_assistant_text_and_decodes_wav(monkeypatch, tmp_path):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"audio": {"data": base64.b64encode(b"RIFFdemo").decode()}}}]}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, headers, json): captured.update(json); return Response()
    monkeypatch.setattr(video_renderer.httpx, "Client", Client)
    output = tmp_path / "voice.wav"
    video_renderer.synthesize_mimo("测试口播", "苏打", output, api_key="secret")
    assert captured["model"] == "mimo-v2.5-tts"
    assert captured["messages"][1] == {"role": "assistant", "content": "测试口播"}
    assert output.read_bytes() == b"RIFFdemo"
