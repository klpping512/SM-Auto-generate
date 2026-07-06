from pathlib import Path

from PIL import Image

from xhs_cards import normalize_pages, pages_from_content, render_carousel


def test_normalize_pages_sanitizes_and_limits_content():
    pages = normalize_pages("标题", [
        {"headline": "封面", "subheadline": "副标题", "points": []},
        {"headline": "重点", "points": ["A", "B", "C", "D"]},
    ])
    assert pages[0]["type"] == "cover"
    assert pages[1]["type"] == "content"
    assert pages[1]["points"] == ["A", "B", "C", "D"]


def test_render_carousel_creates_publishable_pngs(tmp_path: Path):
    pages, attachments = render_carousel("南非物流提醒", [
        {"headline": "南非物流提醒", "subheadline": "建议收藏"},
        {"headline": "确认最新动态", "points": ["核对船期", "提前同步客户"]},
    ], tmp_path)

    assert len(pages) == len(attachments) == 2
    first = tmp_path / attachments[0]["path"]
    assert first.exists()
    with Image.open(first) as image:
        assert image.format == "PNG"
        assert image.size == (1242, 1660)
    assert attachments[0]["generated"] is True
    assert attachments[0]["template_version"] == "buffalo-gold-v1"


def test_pages_from_legacy_content_builds_carousel():
    pages = pages_from_content("PAT 注册三步走", "第一步：准备资料\n确认产品与企业资料。\n\n第二步：提交申请\n保存申请编号。")
    assert pages[0]["type"] == "cover"
    assert len(pages) >= 3


def test_render_carousel_uses_available_brand_photos(tmp_path: Path):
    thumbnails = tmp_path / "assets" / "thumbnails"
    thumbnails.mkdir(parents=True)
    Image.new("RGB", (640, 1138), "#2f6f4f").save(thumbnails / "warehouse.jpg")

    _, attachments = render_carousel("跨境仓储怎么做", [
        {"headline": "跨境仓储怎么做", "subheadline": "仓储能力决定履约效率"},
        {"headline": "为什么容易亏钱", "points": ["库存跟不上", "退货成本高", "促销越猛越积压"]},
        {"headline": "海外仓能力", "points": ["库位精细化管理", "灵活补货", "本地退货处理"]},
    ], tmp_path)

    assert len(attachments) == 3
    with Image.open(tmp_path / attachments[2]["path"]) as image:
        assert image.getpixel((100, 100)) != image.getpixel((100, 1000))
