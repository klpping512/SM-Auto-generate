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
    assert pages[0]["show_logo"] is True
    assert pages[0]["logo_variant"] == "header"
    assert len(pages) == 5


def test_normalize_pages_preserves_logo_editing_controls():
    pages = normalize_pages("标题", [
        {"headline": "封面", "show_logo": False, "logo_variant": "large"},
        {"headline": "正文", "points": ["要点"], "show_logo": True, "logo_variant": "large"},
    ])
    assert pages[0]["show_logo"] is False
    assert pages[0]["logo_variant"] == "large"
    assert pages[1]["show_logo"] is True


def test_render_carousel_creates_publishable_pngs(tmp_path: Path):
    pages, attachments = render_carousel("南非物流提醒", [
        {"headline": "南非物流提醒", "subheadline": "建议收藏"},
        {"headline": "确认最新动态", "points": ["核对船期", "提前同步客户"]},
    ], tmp_path)

    assert len(pages) == len(attachments) == 5
    first = tmp_path / attachments[0]["path"]
    assert first.exists()
    with Image.open(first) as image:
        assert image.format == "PNG"
        assert image.size == (1242, 1660)
    assert attachments[0]["generated"] is True
    assert attachments[0]["template_version"] == "buffalo-reference-v5"

    # Both the Buffalo icon and wordmark must be visible; neither a text-only
    # fallback nor a hand-drawn approximation is acceptable.
    with Image.open(first).convert("RGB") as image:
        mark_region = image.crop((730, 50, 815, 145))
        wordmark_region = image.crop((815, 50, 1100, 155))
        assert any(r > 235 and g > 235 and b > 235 for r, g, b in mark_region.getdata())
        assert any(r > 235 and g > 235 and b > 235 for r, g, b in wordmark_region.getdata())


def test_pages_from_legacy_content_builds_carousel():
    pages = pages_from_content("PAT 注册三步走", "第一步：准备资料\n确认产品与企业资料。\n\n第二步：提交申请\n保存申请编号。")
    assert pages[0]["type"] == "cover"
    assert 5 <= len(pages) <= 7


def test_render_carousel_uses_available_brand_photos(tmp_path: Path):
    thumbnails = tmp_path / "assets" / "thumbnails"
    thumbnails.mkdir(parents=True)
    Image.new("RGB", (640, 1138), "#2f6f4f").save(thumbnails / "warehouse.jpg")

    _, attachments = render_carousel("跨境仓储怎么做", [
        {"headline": "跨境仓储怎么做", "subheadline": "仓储能力决定履约效率"},
        {"headline": "为什么容易亏钱", "points": ["库存跟不上", "退货成本高", "促销越猛越积压"]},
        {"headline": "海外仓能力", "points": ["库位精细化管理", "灵活补货", "本地退货处理"]},
    ], tmp_path)

    assert len(attachments) == 5
    with Image.open(tmp_path / attachments[2]["path"]) as image:
        assert image.getpixel((100, 100)) != image.getpixel((100, 1000))


def test_normalize_pages_caps_carousel_at_seven_pages():
    pages = normalize_pages("标题", [
        {"headline": f"第 {index + 1} 页", "points": ["真实要点"]}
        for index in range(10)
    ])
    assert len(pages) == 7


def _mean_rgb(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    region = image.crop(box)
    pixels = list(region.getdata())
    n = len(pixels)
    return (
        sum(p[0] for p in pixels) / n,
        sum(p[1] for p in pixels) / n,
        sum(p[2] for p in pixels) / n,
    )


def test_inner_pages_alternate_annotation_position(tmp_path: Path):
    """图2 标注在下、图3 标注在上；全图打底且保留品牌与圆点。"""
    thumbnails = tmp_path / "assets" / "thumbnails"
    thumbnails.mkdir(parents=True)
    # 高饱和纯色便于区分照片区与暖棕标注叠层
    Image.new("RGB", (640, 1138), "#00CC44").save(thumbnails / "green.jpg")

    pages_spec = [
        {"headline": "封面标题", "subheadline": "副标题"},
        {"headline": "第二页要点", "points": ["核对船期", "同步客户"]},
        {"headline": "第三页要点", "points": ["海外仓能力", "本地退货"]},
        {"headline": "第四页要点", "points": ["费用边界"]},
        {"headline": "第五页要点", "points": ["行动清单"]},
    ]
    _, attachments = render_carousel("交替标注", pages_spec, tmp_path)
    assert all(item["template_version"] == "buffalo-reference-v5" for item in attachments)

    def assert_brand_and_dots(path: Path):
        with Image.open(path).convert("RGB") as image:
            mark_region = image.crop((730, 50, 815, 145))
            assert any(r > 235 and g > 235 and b > 235 for r, g, b in mark_region.getdata())
            # 底部圆点带：当前页白色点
            dots = image.crop((580, 1590, 660, 1620))
            assert any(r > 230 and g > 230 and b > 230 for r, g, b in dots.getdata())

    # 图2 (index=1)：标注在下 → 上区偏绿，下区绿通道显著压低（暖棕叠层）
    page2 = tmp_path / attachments[1]["path"]
    with Image.open(page2).convert("RGB") as image:
        top = _mean_rgb(image, (200, 280, 500, 420))
        bottom = _mean_rgb(image, (200, 1380, 500, 1520))
        assert top[1] > top[0] + 30  # 绿主导（照片区）
        assert top[1] - bottom[1] > 40
        assert bottom[0] / max(bottom[1], 1) > top[0] / max(top[1], 1)
    assert_brand_and_dots(page2)

    # 图3 (index=2)：标注在上 → 上区绿被压，下区偏绿
    page3 = tmp_path / attachments[2]["path"]
    with Image.open(page3).convert("RGB") as image:
        top = _mean_rgb(image, (200, 280, 500, 420))
        bottom = _mean_rgb(image, (200, 1380, 500, 1520))
        assert bottom[1] > bottom[0] + 30  # 绿主导（照片区）
        assert bottom[1] - top[1] > 40
        assert top[0] / max(top[1], 1) > bottom[0] / max(bottom[1], 1)
    assert_brand_and_dots(page3)


def test_inner_page_falls_back_to_text_without_photo(tmp_path: Path):
    """无可用照片时内页走纯文字页，不崩。"""
    pages, attachments = render_carousel("无图兜底", [
        {"headline": "无图封面", "subheadline": "副标题"},
        {"headline": "无图内页", "points": ["要点一", "要点二"]},
    ], tmp_path)
    assert len(pages) == len(attachments) == 5
    inner = tmp_path / attachments[1]["path"]
    assert inner.exists()
    with Image.open(inner) as image:
        assert image.size == (1242, 1660)
    # 纯文字页是金棕渐变，上下采样色相近且无高饱和绿
    with Image.open(inner).convert("RGB") as image:
        top = _mean_rgb(image, (200, 280, 500, 420))
        bottom = _mean_rgb(image, (200, 1380, 500, 1520))
        assert abs(top[0] - bottom[0]) < 80
        assert top[0] > 100 and top[1] > 60
