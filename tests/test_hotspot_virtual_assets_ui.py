from pathlib import Path


def test_hotspot_library_labels_virtual_event_cards():
    html = Path("static/assets.html").read_text(encoding="utf-8")
    assert "热点原始母片" in html
    assert "查看事件素材" in html
    assert "event_clip_id" in html
    assert "加入视频跟进" in html
    assert "video-followup.html?event_clip_id=" in html


def test_video_project_shows_duration_budget():
    html = Path("static/video-project.html").read_text(encoding="utf-8")
    assert "时长预算" in html


def test_video_workbench_writes_event_clip_reference():
    html = Path("static/video-workbench.html").read_text(encoding="utf-8")
    assert "视频精确调整已合并" in html or "selectedHotspotEventClip" in html
