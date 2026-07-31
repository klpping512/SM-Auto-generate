from pathlib import Path


def test_assets_page_has_persistent_local_import_controls():
    page = (Path(__file__).parents[1] / "static" / "assets.html").read_text(encoding="utf-8")

    assert "/api/assets/local-imports" in page
    assert "取消导入" in page
    assert "current_file" in page
    assert "本地服务已断开" in page
    assert "已入库" in page
    assert "已分析" in page
    assert "setTimeout(pollLocalImport,1500)" in page


def test_assets_page_stops_polling_after_repeated_connection_failures():
    page = (Path(__file__).parents[1] / "static" / "assets.html").read_text(encoding="utf-8")

    assert "localImportFailures>=3" in page
    assert "clearTimeout(localImportTimer)" in page
