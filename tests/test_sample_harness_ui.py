from pathlib import Path


PAGE = Path(__file__).parents[1] / "static" / "hotspots.html"


def test_hotspot_page_exposes_sample_first_harness_actions():
    page = PAGE.read_text(encoding="utf-8")

    assert "生成视频、图文和公众号样本" in page
    assert "/evidence-package" in page
    assert "/sample-bundle" in page
    assert "内部测试，不可发布" in page


def test_hotspot_page_exposes_brand_evidence_confirmation():
    page = PAGE.read_text(encoding="utf-8")

    assert "管理品牌证据" in page
    assert "/api/brand-evidence" in page
    assert "/confirm" in page
