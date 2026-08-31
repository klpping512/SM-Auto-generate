from pathlib import Path


def test_followup_page_is_default_event_workflow_and_precision_is_secondary():
    page = Path("static/video-followup.html").read_text(encoding="utf-8")
    assert "视频跟进" in page
    assert "生成视频草稿" in page
    assert "视频精确调整" in page
    assert "/api/hotspot-events/${eventId}" in page


def test_event_cards_use_previewable_virtual_asset_fields():
    page = Path("static/assets.html").read_text(encoding="utf-8")
    assert "preview_url" in page
    assert 'controls preload="none"' in page or 'controls preload="metadata"' in page
    assert "加入视频跟进" in page
