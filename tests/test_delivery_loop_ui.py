"""Delivery / package separation UI contract checks."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_common_nav_hides_hotspot_review_for_non_admin():
    source = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
    assert "item.id === 'hotspots' && !isAdmin" in source


def test_video_project_exposes_preview_download_quality_and_manual_review():
    page = (ROOT / "static" / "video-project.html").read_text(encoding="utf-8")
    assert "download" in page
    assert "quality_report" in page
    assert "人工验收" in page
    assert "needs_review" in page
    assert "output_url" in page


def test_config_page_is_mimo_first():
    page = (ROOT / "static" / "config.html").read_text(encoding="utf-8")
    assert "MiMo TTS" in page
    assert "saveMimoKey" in page
    assert "仅本次运行" in page
    assert "百炼 Qwen TTS 负责旁白" not in page


def test_video_project_uses_shared_tts_selector_not_qwen_only():
    page = (ROOT / "static" / "video-project.html").read_text(encoding="utf-8")
    assert "voicePickerMarkup" in page or "voiceSelectMarkup" in page
    assert "配音字幕" in page
    assert "Qwen TTS 音色" not in page


def test_common_nav_exposes_video_project_entry():
    source = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
    assert "id: 'video-project'" in source
    assert "videoProjectNavBadge" in source
    assert "ensureVideoTaskCenter" not in source


def test_chat_response_contract_fields_present_in_backend():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"hook_requirement": "optional"' in source
    assert '"delivery_readiness": readiness' in source
    assert '"funnel": (hotspot_retrieval or {}).get("funnel")' in source
    assert "voice_options" in source
    assert "tts_provider" in source
    assert "resolve_tts_selection" in source
