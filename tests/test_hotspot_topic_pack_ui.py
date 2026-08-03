from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hotspot_page_uses_topic_pack_structure():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "/api/hotspot-packages" in page
    assert "热点审核台" in page
    assert "来源信号" in page
    assert "视频素材" in page and "图片素材" in page
    assert "确认专题事实" in page
    assert "先准备视频素材" in page
    assert "Hook：" in page
    assert "抓取最新热点" in page
    assert "选择热点" not in page
    assert "仅事实信号" in page
    assert "有素材" in page
    assert "尚未形成可入库媒体候选" in page
    assert "heat_state:'unconfirmed'" in page
    assert "can_follow_up_video" in page
    assert "确认并入热点库" not in page
    assert "role==='admin'" in page


def test_video_followup_accepts_a_topic_package_id():
    page = (ROOT / "static" / "video-followup.html").read_text(encoding="utf-8")

    assert "hotspot_id" in page
    assert "/api/hotspot-packages/" in page


def test_topic_package_selection_preserves_list_position_and_ignores_stale_requests():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "topicPackageListScrollTop" in page
    assert "restoreTopicPackageListScroll" in page
    assert "packageSelectionRequest" in page
