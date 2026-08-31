import io
import uuid
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


def test_tts_voice_options_expose_mimo_and_minimax_defaults(monkeypatch):
    monkeypatch.delenv("MINIMAX_TOKEN_PLAN_KEY", raising=False)
    options = video_renderer.tts_voice_options(mimo_available=True)
    assert len(options) == 2
    mimo = options[0]
    assert mimo["provider"] == "mimo" and mimo["id"] == "mimo_default"
    assert mimo["label"] == "MiMo 默认"
    assert mimo["available"] is True
    assert mimo["preview_supported"] is True
    minimax = options[1]
    assert minimax["provider"] == "minimax"
    assert minimax["available"] is False
    assert "MINIMAX_TOKEN_PLAN_KEY" in minimax["disabled_reason"]
    disabled = video_renderer.tts_voice_options(mimo_available=False)
    assert disabled[0]["available"] is False
    assert "MIMO_API_KEY" in disabled[0]["disabled_reason"]


def test_minimax_tts_selection_uses_configured_voice(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_TTS_VOICE", "male-qn-qingse")
    provider, voice = video_renderer.resolve_tts_selection(None, None, strict=True)
    assert provider == "minimax"
    assert voice == "male-qn-qingse"


def test_tts_selection_inherits_minimax_environment_default(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "minimax")
    provider, voice = video_renderer.resolve_tts_selection(None, None, strict=True)
    assert provider == "minimax"
    assert voice == video_renderer.MINIMAX_TTS_VOICE


def test_subtitle_alignment_covers_trailing_audio_silence():
    cues = video_renderer._align_cues_to_silence(
        ["现场核对完成。"], 4.0, [(2.0, 4.0)],
    )
    assert cues[-1]["end"] == 4.0
    assert video_renderer.subtitle_sync_report(cues, 4.0)["passed"] is True


def test_resolve_tts_selection_normalizes_retired_providers_to_mimo():
    # 历史项目存了已下线的 provider/音色，重渲染必须静默归一到 MiMo，不抛错。
    for provider, voice in (("qwen", "Cherry"), ("qwen", ""), ("mimo", "Cherry"), ("dashscope", "Cherry")):
        resolved_provider, resolved_voice = video_renderer.resolve_tts_selection(provider, voice, strict=True)
        assert resolved_provider == "mimo"
        assert resolved_voice == video_renderer.MIMO_TTS_VOICE


def test_resolve_tts_selection_keeps_mimo_default_in_strict_mode():
    provider, voice = video_renderer.resolve_tts_selection("mimo", "mimo_default", strict=True)
    assert provider == "mimo" and voice == "mimo_default"


def test_scene_voiceover_falls_back_to_muted_preview_when_tts_fails(monkeypatch, tmp_path):
    calls = {"mimo": 0}

    def failing_mimo(*_args, **_kwargs):
        calls["mimo"] += 1
        raise RuntimeError("MiMo TTS 暂时不可用")

    monkeypatch.delenv("MINIMAX_TOKEN_PLAN_KEY", raising=False)
    monkeypatch.setattr(video_renderer, "synthesize_mimo_tts", failing_mimo)
    output = tmp_path / "fail.wav"
    meta = video_renderer.synthesize_scene_voiceover(
        f"单轨失败冒泡-{uuid.uuid4().hex}", output, tts_provider="mimo",
    )
    assert calls["mimo"] == 1
    assert meta["muted"] is True
    assert meta["subtitle_only"] is True
    assert output.is_file() and output.stat().st_size > 0


def test_scene_voiceover_meta_has_no_fallback_fields(monkeypatch, tmp_path):
    import pathlib

    def fake_mimo(text, voice, output, api_key=None, style_instruction=None):
        pathlib.Path(output).write_bytes(b"RIFFsingletrack")

    monkeypatch.setattr(video_renderer, "synthesize_mimo_tts", fake_mimo)
    meta = video_renderer.synthesize_scene_voiceover(
        f"单轨元数据-{uuid.uuid4().hex}", tmp_path / "ok.wav", tts_provider="mimo",
    )
    assert meta["provider"] == "mimo"
    assert "fallback_used" not in meta
    assert "fallback_reason" not in meta


def test_scene_voiceover_normalizes_legacy_provider_to_mimo(monkeypatch, tmp_path):
    import pathlib

    def fake_mimo(text, voice, output, api_key=None, style_instruction=None):
        pathlib.Path(output).write_bytes(b"RIFFlegacy")

    monkeypatch.setattr(video_renderer, "synthesize_mimo_tts", fake_mimo)
    meta = video_renderer.synthesize_scene_voiceover(
        f"历史兼容-{uuid.uuid4().hex}", tmp_path / "legacy.wav", tts_provider="qwen", voice="Cherry",
    )
    assert meta["provider"] == "mimo"
