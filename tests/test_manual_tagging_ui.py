from pathlib import Path


PAGE = Path(__file__).parents[1] / "static" / "assets.html"


def test_assets_page_exposes_manual_segment_tagging_for_admins():
    page = PAGE.read_text(encoding="utf-8")

    assert "人工打标" in page
    assert "saveSegmentClassification" in page
    assert "/api/asset-segments/${segmentId}/classification" in page
    assert "TAG_PRESETS" in page
    assert "CATEGORY_MATCH_HINT" in page
    assert "reviewFilter" in page
    assert "待复核" in page
    assert "分类为其他" in page
    assert "保存此镜头标签" in page
    assert "主场景（粗分类，只做节点门禁；细匹配看下方标签）" in page
    assert "后续自动重建不会覆盖" in page or "批量只改主场景" in page
    assert "港口码头" in page
    assert "集装箱堆场" in page
    assert "吊机" in page
    assert "堆放" in page
    assert "粗分类，只做物流节点门禁" in page


def test_assets_page_exposes_multi_shot_tagging_workbench():
    page = PAGE.read_text(encoding="utf-8")

    assert "renderSegmentTaggingWorkbench" in page
    assert "shot-tag-layout" in page
    assert "shot-nav" in page
    assert "focusSegmentShot" in page
    assert "保存并下一镜" in page
    assert "batchApplyPrimaryCategory" in page
    assert "应用到未确认" in page
    assert "应用到全部" in page
    assert "patchAssetCardCategory" in page
    assert "goNext:true" in page
    assert "skipAssetReload" in page
    assert "进度 ${confirmed}/${total}" in page
