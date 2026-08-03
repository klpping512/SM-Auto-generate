from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_sidebar_places_assets_next_to_hotspot_workbench():
    common = (ROOT / "static" / "common.js").read_text(encoding="utf-8")

    assert "{ id: 'hotspots', label: '热点审核台', href: '/hotspots.html' }" in common
    core = common.index("{ section: '核心' }")
    analysis = common.index("{ section: '分析' }")
    hotspots = common.index("id: 'hotspots'")
    assets = common.index("id: 'assets'")
    editor = common.index("id: 'editor'")
    assert core < hotspots < assets < editor < analysis


def test_assets_page_contains_only_asset_and_inspiration_actions():
    page = (ROOT / "static" / "assets.html").read_text(encoding="utf-8")

    for forbidden in (
        "南非热点",
        "添加可信源",
        "立即抓取",
        "品牌证据",
        "生成三份内部样本",
    ):
        assert forbidden not in page
    assert "上传素材" in page
    assert "接入桌面素材" in page
    assert "更多操作" in page


def test_hotspot_workbench_explains_collection_and_has_clear_primary_action():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "const PAGE_ID='hotspots'" in page
    assert "抓取最新热点" in page
    assert "系统如何采集？" in page
    assert "抓取不调用文本大模型" in page
    assert "版权不明确的媒体不会自动下载" in page
    assert "信源状态" in page
    assert "本次结果" in page
    assert "source_health" in page
    assert "公开视频频道" in page
    assert "video_new" in page


def test_hotspot_workbench_has_topic_package_master_detail_flow():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "hotspot-list-panel" in page
    assert "hotspot-workspace" in page
    assert "/api/hotspot-packages" in page
    assert "来源信号" in page
    assert "视频素材" in page
    assert "图片素材" in page
    assert "确认专题事实" in page
    assert "生成视频跟进" in page or "先准备视频素材" in page
    assert "can_follow_up_video" in page
    assert "确认并入热点库" not in page


def test_hotspot_workbench_restores_evidence_and_guards_async_selection():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "restoredBundle.evidence_package_id" in page
    assert "/api/evidence-packages/" in page
    assert "requestedHotspotId!==selectedHotspotId" in page
    assert "sample-bundles?limit=10" in page


def test_hotspot_workbench_exposes_material_evidence_preview_and_source_errors():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "查看 Top ${candidates.length} 匹配依据" in page
    assert "formatSegmentRange" in page
    assert "candidate.reasons" in page
    assert "sampleBundle?.preview_path" in page
    assert "本地 MP4 预览尚未生成" in page
    assert "失败原因" in page
    assert "受限说明" in page
    assert "status==='blocked'?'受限说明':'失败原因'" in page
    assert "重新抓取全部启用信源" in page
    assert "wechat.source_refs" in page
    assert "输出目录：" in page
    assert "Claim IDs：" in page
    assert "模型预算：" in page


def test_hotspot_workbench_uses_bilingual_cache_and_links_to_hotspot_library():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "/translate" in page
    assert "title_zh" in page
    assert "summary_zh" in page
    assert "translation_status" in page
    assert "/api/hotspot-media?hotspot_id=" in page
    assert "热点视频候选" in page
    assert "热点图片候选" in page
    assert "绿色" in page and "黄色" in page and "红色" in page
    assert "assets.html?library=hotspot&hotspot_id=" in page


def test_hotspot_workbench_restores_selected_hotspot_from_url():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "new URLSearchParams(location.search)" in page
    assert "params.get('hotspot_id')" in page


def test_hotspot_workbench_blocks_video_preview_when_materials_are_not_ready():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "video.material_status==='blocked'" in page
    assert "热点素材未就绪，暂不可成片" in page
    assert "video.material_gaps" in page


def test_hotspot_workbench_labels_and_filters_content_form():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert "/api/hotspot-media?limit=1000" in page
    assert "function hotspotContentForm(item)" in page
    assert "mediaTypeFilter" in page
    assert 'aria-label="筛选内容形式"' in page
    assert "content-form-tag" in page
    assert "视频候选" in page
    assert "图片候选" in page
    assert "纯文本事实来源" in page
    for label in ("全部形式", "视频", "图片", "纯文本"):
        assert label in page


def test_hotspot_filter_controls_wrap_without_overflowing_the_list_panel():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")

    assert ".filter-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in page
    assert ".filter-row .input{grid-column:1/-1}" in page
