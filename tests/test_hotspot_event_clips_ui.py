from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_assets_page_exposes_event_clips_and_source_labels():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    assert "事件片段" in page
    assert "title_zh" in page
    assert "title_en" in page
    assert "热点素材" in page


def test_hotspot_media_task_requires_hook_choice_before_video_use():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    assert 'onclick="viewHotspotEvents(${item.asset_id})">选择 Hook 后成片</button>' in page
    assert 'onclick="useAsset(${item.asset_id})">应用到成片</button>' not in page


def test_video_workbench_exposes_event_match_endpoint():
    page = (ROOT / "static/video-workbench.html").read_text(encoding="utf-8")
    assert "/api/hotspot-events" in page
    assert "Buffalo 原有素材" in page
