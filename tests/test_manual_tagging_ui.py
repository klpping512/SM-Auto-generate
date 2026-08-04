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
    assert "主场景（影响物流节点匹配）" in page
    assert "后续自动重建不会覆盖" in page
