from pathlib import Path


PAGE = Path(__file__).parents[1] / "static" / "assets.html"


def test_assets_page_has_two_primary_libraries_and_hotspot_media_filters():
    page = PAGE.read_text(encoding="utf-8")

    assert "原本素材库" in page
    assert "热点素材库" in page
    assert "热点媒体" in page
    assert "灵感链接" in page
    assert "/api/hotspot-media" in page
    assert "hotspotMediaKind" in page
    assert "全部热点" in page
    assert "全部媒体" in page
    assert "hotspotRightsTier" not in page
    assert "全部权利状态" not in page


def test_hotspot_media_cards_show_dates_freshness_and_return_link():
    page = PAGE.read_text(encoding="utf-8")

    for text in (
        "发布时间",
        "抓取时间",
        "入库时间",
        "24 小时",
        "3 天",
        "7 天",
        "30 天",
        "已归档",
        "返回当前热点",
    ):
        assert text in page
    assert "formatHotspotDate" in page
    assert "hotspotFreshness" in page


def test_hotspot_video_card_exposes_one_click_download_and_processing_states():
    page = PAGE.read_text(encoding="utf-8")

    for text in ("仅链接", "待确认授权", "不可处理", "下载中", "分析中", "可匹配"):
        assert text in page
    assert "duration_seconds" in page
    assert "processing_status" in page
    assert "original_media_url" in page
    assert "materializeHotspotMedia" in page
    assert "hotspotMediaBusy" in page
    assert "确认并下载到库" in page
    assert "confirmHotspotMediaRights" not in page
    assert "权利状态：green / yellow / red" not in page
    assert "确认已核实授权，并下载该单条视频进行本地分析？" not in page


def test_hotspot_media_ui_uses_explicit_authorization_status_not_traffic_light_labels():
    page = PAGE.read_text(encoding="utf-8")

    assert "authorization_status" in page
    assert "rights_tier==='red'" not in page


def test_hotspot_library_hides_compose_button_when_no_hooks_curated():
    page = PAGE.read_text(encoding="utf-8")

    assert "function hotspotHasCuratedHooks(item)" in page
    assert "筛出\\s*\\d+\\s*条" in page
    assert "已分析无 Hook" in page
    assert "镜头已入库，暂无策展 Hook" in page
    assert "hotspotHasCuratedHooks(item)&&item.asset_id" in page
    assert "item.processing_status==='ready'&&item.asset_id?`<button class=\"btn btn-primary btn-sm\" onclick=\"viewHotspotEvents(${item.asset_id})\">选择 Hook 后成片</button>`" not in page


def test_hotspot_download_shows_stage_progress_and_keeps_polling():
    page = PAGE.read_text(encoding="utf-8")

    assert "hotspotMediaProgress" in page
    assert "scheduleHotspotMediaPoll" in page
    assert "pollHotspotMedia" in page
    assert "正在连接来源" in page
    assert "正在下载媒体" in page
    assert "正在分析镜头" in page
    assert "已完成" in page
    assert "hotspot-progress-track" in page
    assert "download_progress" in page
    assert "progress_detail" in page
    assert "cache:'no-store'" in page
    assert "立即刷新状态" in page
    assert "setInterval" in page


def test_all_pages_use_latest_common_navigation_bundle():
    static_dir = PAGE.parent
    pages = list(static_dir.glob("*.html"))

    stale = [page.name for page in pages if "common.js?v=" in page.read_text(encoding="utf-8")
             and "common.js?v=8" not in page.read_text(encoding="utf-8")]

    assert stale == []


def test_confirmed_hotspot_image_can_use_same_download_and_analysis_action():
    page = PAGE.read_text(encoding="utf-8")

    assert "item.media_kind==='image'" in page
    assert "确认并下载到库" in page
    assert "已提交图片下载与分析" in page


def test_hotspot_library_exposes_admin_single_and_bulk_delete_actions():
    page = PAGE.read_text(encoding="utf-8")

    assert "清空热点素材库" in page
    assert "clearHotspotLibrary" in page
    assert "deleteHotspotMedia" in page
    assert "/api/hotspot-library/cleanup-preview" in page
    assert "/api/hotspot-library" in page


def test_hotspot_events_are_owned_by_hotspot_library_and_can_delete_source_asset():
    page = PAGE.read_text(encoding="utf-8")

    assert "function filteredHotspotEvents()" in page
    assert "const visibleEvents=filteredHotspotEvents()" in page
    assert "const eventBody=visibleEvents.length" in page
    assert "assets=allAssets.filter(asset=>asset.library_origin!=='hotspot'&&!asset.hotspot_id)" in page
    assert "deleteHotspotEvent(eventId)" in page
    assert "deleteHotspotEventAsset" in page
    assert "/api/hotspot-event-assets/" in page


def test_hotspot_library_defaults_to_all_hotspots_without_auto_select():
    page = PAGE.read_text(encoding="utf-8")

    assert "if(!selectedHotspotId&&hotspots.length)selectedHotspotId=String(hotspots[0].id);" not in page
    assert "async function clearHotspotFilter()" in page
    assert "当前正在查看：" in page
    assert "清除筛选 / 返回全部热点" in page
    assert "最新入库" in page
    assert "全部素材" in page
    assert "groupHotspotLibraryItems" in page
    assert "hotspotIntakeAt" in page


def test_hotspot_library_mixed_cards_share_top_media_layout():
    """Hook 与媒体混排时共用上图下文，避免一列横屏一列竖屏。"""
    page = PAGE.read_text(encoding="utf-8")

    assert "grid-template-columns:150px 1fr" not in page
    assert ".hotspot-media-thumb{position:relative;width:100%;height:180px" in page
    assert ".hotspot-media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))" in page
    assert ".asset-media{position:relative;width:100%;height:180px" in page


def test_hotspot_library_can_bind_video_to_specific_hotspot():
    page = PAGE.read_text(encoding="utf-8")

    assert "添加视频链接" in page
    assert "添加素材" in page
    assert "/media/attach" in page
    assert "selectedHotspotId" in page
    assert "频道或播放列表不能直接加入" in page


def test_hotspot_image_card_opens_article_source_page():
    page = PAGE.read_text(encoding="utf-8")

    assert "item.source_page_url||item.original_media_url" in page
    assert ">查看原文</a>" in page
    assert ">打开来源</a>" not in page


def test_hotspot_image_preview_has_explicit_failure_fallback():
    page = PAGE.read_text(encoding="utf-8")

    assert "handleHotspotImageError" in page
    assert "预览暂不可用" in page


def test_hotspot_media_rescan_reports_unavailable_images():
    page = PAGE.read_text(encoding="utf-8")

    assert "skipped_unavailable" in page
    assert "不可用图片" in page


def test_hotspot_library_exposes_cleanup_preview_without_direct_delete_action():
    page = PAGE.read_text(encoding="utf-8")

    assert "/api/media-retention/preview" in page
    assert "存储清理预览" in page
    assert "预计释放" in page
    assert "执行清理" not in page
