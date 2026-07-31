from pathlib import Path


def test_config_page_can_view_and_replace_each_model_role():
    page = (Path(__file__).parents[1] / "static" / "config.html").read_text(encoding="utf-8")

    assert "可替换模型角色" in page
    assert "planner_text" in page
    assert "vision_tagger" in page
    assert "critic" in page
    assert "tts" in page
    assert "/api/model-routes/" in page
    assert "密钥只读取环境变量名" in page
