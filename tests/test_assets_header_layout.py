from pathlib import Path


PAGE = Path(__file__).parents[1] / "static" / "assets.html"


def test_asset_header_actions_wrap_without_collapsing_title():
    page = PAGE.read_text(encoding="utf-8")

    assert 'class="page-header asset-page-header"' in page
    assert 'class="asset-page-heading"' in page
    assert 'class="asset-page-actions"' in page
    assert ".asset-page-heading{min-width:240px" in page
    assert ".asset-page-actions{display:flex;gap:8px;flex-wrap:wrap" in page
    assert "overflow-x:hidden" in page
