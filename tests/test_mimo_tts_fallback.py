"""MiMo TTS primary path with recoverable Qwen fallback."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest


def test_mimo_success_writes_cache(tmp_path, monkeypatch):
    import uuid
    import video_renderer

    monkeypatch.setenv("TTS_PROVIDER", "mimo")
    monkeypatch.setenv("TTS_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    calls = {"mimo": 0, "qwen": 0}
    unique = f"你好南非物流-{uuid.uuid4()}"

    def fake_mimo(text, voice, output, api_key=None, style_instruction=None):
        calls["mimo"] += 1
        output.write_bytes(b"RIFF" + b"0" * 128)

    def fake_qwen(text, voice, output, api_key=None):
        calls["qwen"] += 1
        output.write_bytes(b"RIFF" + b"1" * 128)

    monkeypatch.setattr(video_renderer, "synthesize_mimo_tts", fake_mimo)
    monkeypatch.setattr(video_renderer, "synthesize_qwen_tts", fake_qwen)

    out1 = tmp_path / "a.wav"
    meta1 = video_renderer.synthesize_scene_voiceover(unique, out1, tts_provider="mimo", voice="mimo_default")
    assert meta1["provider"] == "mimo"
    assert meta1["fallback_used"] is False
    assert meta1["cache_hit"] is False
    assert out1.read_bytes().startswith(b"RIFF")
    assert calls["mimo"] == 1

    out2 = tmp_path / "b.wav"
    meta2 = video_renderer.synthesize_scene_voiceover(unique, out2, tts_provider="mimo", voice="mimo_default")
    assert meta2["cache_hit"] is True
    assert calls["mimo"] == 1


def test_mimo_recoverable_timeout_falls_back_to_qwen(tmp_path, monkeypatch):
    import video_renderer

    monkeypatch.setenv("TTS_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("TTS_FALLBACK_PROVIDER", "qwen")

    def boom(text, voice, output, api_key=None, style_instruction=None):
        raise httpx.TimeoutException("timeout")

    def fake_qwen(text, voice, output, api_key=None):
        output.write_bytes(b"RIFF" + b"q" * 128)

    monkeypatch.setattr(video_renderer, "synthesize_mimo_tts", boom)
    monkeypatch.setattr(video_renderer, "synthesize_qwen_tts", fake_qwen)

    out = tmp_path / "fb.wav"
    meta = video_renderer.synthesize_scene_voiceover("回退旁白", out, tts_provider="mimo")
    assert meta["fallback_used"] is True
    assert meta["provider"] == "qwen"
    assert out.is_file()


def test_mimo_auth_failure_does_not_fallback(tmp_path, monkeypatch):
    import video_renderer

    monkeypatch.setenv("TTS_FALLBACK_ENABLED", "1")

    def boom(text, voice, output, api_key=None, style_instruction=None):
        raise RuntimeError("未配置 MIMO_API_KEY / 鉴权失败")

    monkeypatch.setattr(video_renderer, "synthesize_mimo_tts", boom)
    monkeypatch.setattr(
        video_renderer,
        "synthesize_qwen_tts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fallback")),
    )

    with pytest.raises(RuntimeError, match="鉴权|未配置"):
        video_renderer.synthesize_scene_voiceover("不可回退", tmp_path / "x.wav", tts_provider="mimo")
