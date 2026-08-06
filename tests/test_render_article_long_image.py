"""长图渲染模块单元测试（纯单元，不依赖模型/网络/机器字体）。

覆盖：出图与 meta 合并 / force 冲突 / 缺字体明确错误 / unresolved 与 MD 同集合。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db  # noqa: E402

CONTENT = {
    "intro": "南非市场 PoE 登记办理周期通常 4–8 周，费用约 0.5%–1.5% 货值。",
    "sections": [
        {
            "heading": "周期与费用",
            "body": "资料齐全时周期约 4–8 周；材料不齐可能拖到 7 天以上。",
            "comparison_card": {"材料齐全": "约 4–8 周", "材料不齐": "视补料速度，可能超 7 天"},
        },
        {"heading": "新变化", "body": "2025 年 12 月起加严查验。"},
    ],
    "conclusion": "建议提前规划。",
}

FOOTNOTES = [
    {"claim_text": "PoE 办理周期通常为 4–8 周，费用约 0.5%–1.5% 货值。", "material_index": 1},
    {"claim_text": "2025 年 12 月起，南非对鞋类、纺织等品类加严了进口合规查验。", "material_index": 2},
]


@pytest.fixture
def article_ctx(tmp_db, tmp_path, monkeypatch):
    """造文章（含生成内容/脚注/选图）并 patch 长图模块的产出目录与字体。"""
    import scripts.render_article_long_image as mod

    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    from PIL import ImageFont
    monkeypatch.setattr(mod, "get_font", lambda size, bold=False: ImageFont.load_default())

    # 假 asset 文件（封面选择校验用）
    fake_static = tmp_path / "static" / "test-assets"
    fake_static.mkdir(parents=True)
    (fake_static / "img-1.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO assets (name, filepath, file_type, category, size, sha256, source, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("测试图", "test-assets/img-1.jpg", "image", "warehouse", 10,
             "sha256-longimg-unit-1", "local_directory", "active"),
        )
        asset_id = cur.lastrowid

    article_id = db.create_article(slug="unit-longimg-test", title="单元测试文章",
                                   materials_json=json.dumps(
                                       [{"excerpt": "PoE 办理周期通常为 4–8 周。", "source_note": "SARS"},
                                        {"excerpt": "2025 年 12 月起加严查验。", "source_note": "ITAC"}],
                                       ensure_ascii=False))
    db.update_article(article_id,
                      generated_content_json=json.dumps(CONTENT, ensure_ascii=False),
                      evidence_footnotes_json=json.dumps(FOOTNOTES, ensure_ascii=False))
    with db.get_conn() as conn:
        conn.execute("INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u', 'x', 'admin')")
    db.update_article(article_id, image_selections_json=json.dumps({
        "cover": {"asset_id": asset_id, "source": "local_directory",
                  "filepath": "test-assets/img-1.jpg", "target_rel": "cover.jpg"},
        "section-01": {"asset_id": asset_id, "source": "local_directory",
                       "filepath": "test-assets/img-1.jpg", "target_rel": "section-01.jpg"},
    }, ensure_ascii=False))
    return article_id, tmp_path


def test_render_png_file_and_meta(article_ctx):
    import scripts.render_article_long_image as mod

    article_id, tmp_path = article_ctx
    result = mod.render_long_image(article_id, width=750, force=True)
    assert result["status"] == "ok", result

    target = tmp_path / "data" / "articles" / "unit-longimg-test" / "long-image-750.png"
    assert target.is_file()
    data = target.read_bytes()
    assert data[:4] == b"\x89PNG"

    from PIL import Image
    im = Image.open(target)
    assert im.width == 750

    meta = json.loads((tmp_path / "data" / "articles" / "unit-longimg-test" / "meta.json").read_text(encoding="utf-8"))
    assert 750 in meta["long_images"]
    assert "longimg" in meta["formats"]

    article = db.get_article(article_id)
    assert article["status"] == "ready"


def test_render_force_conflict(article_ctx):
    import scripts.render_article_long_image as mod

    article_id, _ = article_ctx
    first = mod.render_long_image(article_id, width=750, force=True)
    assert first["status"] == "ok", first

    second = mod.render_long_image(article_id, width=750, force=False)
    assert second["status"] == "error"
    assert "--force" in second["error"]

    # 不同宽度不冲突（文件名带宽度后缀）
    other = mod.render_long_image(article_id, width=1080, force=False)
    assert other["status"] == "ok", other


def test_render_missing_font_clear_error(article_ctx, monkeypatch):
    import scripts.render_article_long_image as mod

    def _boom():
        raise mod.FontNotFoundError("未找到可用中文字体。请把字体文件路径写入环境变量 ARTICLE_FONT_PATH 后重试。")
    monkeypatch.setattr(mod, "resolve_font_paths", _boom)

    def _calling_font(size, bold=False):
        # 模拟 get_font 的真实行为：先解析字体路径（此时抛 FontNotFoundError）
        mod.resolve_font_paths()
        raise AssertionError("unreachable: 字体缺失应已抛错")
    monkeypatch.setattr(mod, "get_font", _calling_font)

    article_id, _ = article_ctx
    result = mod.render_long_image(article_id, width=750, force=True)
    assert result["status"] == "error"
    assert "字体" in result["error"]


def test_render_unresolved_matches_md(article_ctx):
    """长图 unresolved 与 render_article_package 对同一内容扫出的集合完全一致。"""
    import scripts.render_article_long_image as mod
    from scripts.render_article_package import scan_unresolved_claims

    article_id, _ = article_ctx
    result = mod.render_long_image(article_id, width=750, force=True)
    assert result["status"] == "ok", result

    texts = [CONTENT["intro"]]
    for section in CONTENT["sections"]:
        texts.append(section["body"])
        texts.extend(str(v) for v in section.get("comparison_card", {}).values())
    texts.append(CONTENT["conclusion"])
    expected = scan_unresolved_claims(texts, FOOTNOTES)

    assert result["unresolved"] == expected
    # 未登记数字「7 天」在集合里；已登记数字（4–8 周/2025 年 12 月）不在
    assert "7 天" in result["unresolved"]
    assert all(fragment not in result["unresolved"]
               for fragment in ("4–8 周", "0.5%–1.5%", "2025 年 12 月"))
