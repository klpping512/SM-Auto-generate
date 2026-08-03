"""Render BUFFALO-branded Xiaohongshu carousel pages into publishable PNGs."""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


WIDTH, HEIGHT = 1242, 1660
TEMPLATE_VERSION = "buffalo-reference-v4"
GOLD = "#B78A4A"
GOLD_LIGHT = "#D2AA6D"
GOLD_DARK = "#7C542D"
CREAM = "#F3E6D2"
WHITE = "#FFFDF8"
FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
LOGO_FILES = {
    "header": Path(__file__).parent / "static" / "icons" / "buffalo_logo_header.png",
    "large": Path(__file__).parent / "static" / "icons" / "buffalo_logo_large.png",
    "white": Path(__file__).parent / "static" / "icons" / "buffalo_logo_white.png",
    "mark_white": Path(__file__).parent / "static" / "icons" / "buffalo_mark_white.png",
}


def _font(size: int, bold: bool = False):
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                if "Hiragino Sans GB" in candidate:
                    return ImageFont.truetype(candidate, size=size, index=2 if bold else 0)
                return ImageFont.truetype(candidate, size=size, index=1 if bold else 0)
            except OSError:
                return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _clean(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalize_pages(title: str, pages: list[dict] | None) -> list[dict]:
    valid = []
    for raw in pages or []:
        if not isinstance(raw, dict):
            continue
        points = [_clean(item, 48) for item in raw.get("points", []) if _clean(item, 48)][:4]
        valid.append({
            "type": "cover" if not valid else "content",
            "headline": _clean(raw.get("headline") or (title if not valid else "物流提示"), 24),
            "subheadline": _clean(raw.get("subheadline"), 34),
            "points": points,
            "show_logo": raw.get("show_logo", True) is not False,
            "logo_variant": "large" if raw.get("logo_variant") == "large" else "header",
        })
    if not valid:
        valid.append({"type": "cover", "headline": _clean(title, 24), "subheadline": "南非物流实用指南", "points": [], "show_logo": True, "logo_variant": "header"})

    # AI 偶尔只返回 2-3 页。轮播产品约定为 5-7 页，因此用不依赖外部事实的
    # 核对/执行清单补足，而不是编造业务数据或热点细节。
    fallback_pages = [
        ("核心拆解", ["确认真实情况与适用范围", "梳理关键流程与费用节点"]),
        ("常见风险", ["信息不完整可能导致判断偏差", "核对时效要求与责任边界"]),
        ("执行建议", ["提前准备资料与时间缓冲", "执行前再次核对最新要求"]),
        ("行动清单", ["明确负责人和下一步动作", "保留记录并持续跟进"]),
        ("最后提醒", ["根据实际情况选择合适方案", "必要时咨询专业服务人员"]),
    ]
    logo_variant = valid[0]["logo_variant"]
    for headline, points in fallback_pages:
        if len(valid) >= 5:
            break
        valid.append({
            "type": "content", "headline": headline, "subheadline": "", "points": points,
            "show_logo": True, "logo_variant": logo_variant,
        })
    return valid[:7]


def pages_from_content(title: str, body: str) -> list[dict]:
    """Create useful carousel pages from legacy text-only content."""
    clean_body = re.sub(r"\*+", "", body or "")
    sections = [part.strip(" \n：:") for part in re.split(r"\n{2,}|(?=第[一二三四五六七八九十]+[步点])", clean_body) if part.strip()]
    pages = [{"type": "cover", "headline": _clean(title, 24), "subheadline": "南非物流实用指南", "points": []}]
    for section in sections[:7]:
        lines = [line.strip(" -•\t") for line in section.splitlines() if line.strip()]
        headline = _clean(lines[0], 18)
        detail = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
        sentences = [s.strip() for s in re.split(r"[。！？!?；;]", detail) if s.strip()]
        pages.append({"type": "content", "headline": headline, "subheadline": "", "points": sentences[:4] or [detail[:48]]})
        if len(pages) >= 7:
            break
    if len(pages) < 3:
        pages.extend([
            {"type": "content", "headline": "核心信息", "points": [_clean(clean_body, 48)]},
            {"type": "content", "headline": "行动建议", "points": ["核对最新要求与资料", "提前咨询专业服务人员"]},
        ])
    return normalize_pages(title, pages)


def _wrapped(draw, text: str, font, max_width: int) -> list[str]:
    lines, current = [], ""
    for char in text:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _cover_headline_lines(text: str) -> list[str]:
    """参考物料偏好均衡的两行大标题，避免问号被单独挤到第三行。"""
    clean = str(text or "").strip()
    if 8 <= len(clean) <= 18:
        split_at = len(clean) // 2
        return [clean[:split_at], clean[split_at:]]
    return []


def _gradient(size: tuple[int, int], top=GOLD_LIGHT, bottom=GOLD_DARK) -> Image.Image:
    image = Image.new("RGB", size, top)
    top_rgb = Image.new("RGB", (1, 1), top).getpixel((0, 0))
    bottom_rgb = Image.new("RGB", (1, 1), bottom).getpixel((0, 0))
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        ratio = y / max(1, size[1] - 1)
        color = tuple(int(a + (b - a) * ratio) for a, b in zip(top_rgb, bottom_rgb))
        draw.line((0, y, size[0], y), fill=color)
    return image


def _photo_sources(static_dir: Path) -> list[Path]:
    roots = [static_dir / "assets" / "thumbnails", static_dir / "assets" / "library" / "image"]
    candidates = []
    for root in roots:
        if root.exists():
            candidates.extend(sorted(path for path in root.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}))
    return candidates


def _photo_panel(source: Path | None, size: tuple[int, int]) -> Image.Image:
    if source and source.exists():
        with Image.open(source) as original:
            panel = ImageOps.fit(original.convert("RGB"), size, method=Image.Resampling.LANCZOS)
        panel = ImageEnhance.Color(panel).enhance(0.86)
        panel = ImageEnhance.Contrast(panel).enhance(1.08)
        return panel
    return _gradient(size)


def _brand(image: Image.Image, draw: ImageDraw.ImageDraw, page_data: dict, page: int, total: int, dark=False):
    """参考 BUFFALO 既有物料：白色 Logo 居右，页码使用深金色胶囊。"""
    if page_data.get("show_logo", True):
        variant = "large" if page_data.get("logo_variant") == "large" else "header"
        # 水牛轮廓来自用户提供的官方成品物料；字标和口号来自官网原始 PNG。
        # 分开组合可避免手绘失真，也避免放大截图中的小号口号造成锯齿。
        mark_path, wordmark_path = LOGO_FILES["mark_white"], LOGO_FILES["header"]
        if mark_path.exists() and wordmark_path.exists():
            with Image.open(mark_path) as original:
                mark = original.convert("RGBA")
            with Image.open(wordmark_path) as original:
                wordmark = original.convert("RGBA").crop((52, 0, original.width, original.height))
            mark_width = 82 if variant == "large" else 74
            wordmark_width = 310 if variant == "large" else 285
            mark = mark.resize((mark_width, round(mark.height * mark_width / mark.width)), Image.Resampling.LANCZOS)
            wordmark = wordmark.resize((wordmark_width, round(wordmark.height * wordmark_width / wordmark.width)), Image.Resampling.LANCZOS)
            alpha = wordmark.getchannel("A")
            white_wordmark = Image.new("RGBA", wordmark.size, (255, 255, 255, 255))
            white_wordmark.putalpha(alpha)
            total_width = mark_width + 16 + wordmark_width
            logo_x = WIDTH - total_width - 145
            image.paste(mark, (logo_x, 63), mark)
            image.paste(white_wordmark, (logo_x + mark_width + 16, 58), white_wordmark)
        else:
            draw.text((760, 68), "BUFFALO · WE DELIVER HOPE", font=_font(28, True), fill=WHITE)
    count = f"{page}/{total}"
    bbox = draw.textbbox((0, 0), count, font=_font(28, True))
    pill_w = bbox[2] - bbox[0] + 42
    draw.rounded_rectangle((WIDTH - pill_w - 28, 40, WIDTH - 28, 112), radius=34, fill="#9A642D")
    draw.text((WIDTH - pill_w - 7, 57), count, font=_font(28, True), fill=WHITE)


def _dots(draw: ImageDraw.ImageDraw, page: int, total: int):
    count = min(total, 9)
    gap, radius = 30, 7
    start = WIDTH / 2 - (count - 1) * gap / 2
    for index in range(count):
        color = WHITE if index == page - 1 else "#D9C2A6"
        draw.ellipse((start + index * gap - radius, 1605 - radius,
                      start + index * gap + radius, 1605 + radius), fill=color)


def _draw_cover(page: dict, total: int, photo: Path | None) -> Image.Image:
    image = _gradient((WIDTH, HEIGHT), "#C8944C", "#7E512F")
    image.paste(_photo_panel(photo, (WIDTH, 720)), (0, 940))
    # 大块金棕信息区 + 参考图中的柔和弧形落边。
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle((0, 0, WIDTH, 900), fill=(151, 98, 52, 72))
    odraw.ellipse((-250, 735, WIDTH + 250, 1085), fill=(139, 87, 45, 245))
    odraw.rectangle((0, 0, WIDTH, 850), fill=(170, 116, 61, 120))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    _brand(image, draw, page, 1, total, dark=True)

    headline_font = _font(124, True)
    lines = _cover_headline_lines(page["headline"]) or _wrapped(draw, page["headline"], headline_font, 1040)[:3]
    y = 225 if len(lines) <= 2 else 175
    for line in lines:
        width = draw.textbbox((0, 0), line, font=headline_font)[2]
        draw.text(((WIDTH - width) / 2, y), line, font=headline_font, fill=WHITE)
        y += 142

    subtitle = page.get("subheadline") or "拆解关键逻辑，避开常见误区"
    sub_font = _font(49)
    sub_w = min(1110, draw.textbbox((0, 0), subtitle, font=sub_font)[2] + 72)
    x = (WIDTH - sub_w) / 2
    draw.rectangle((x, y + 20, x + sub_w, y + 112), fill="#CC9340")
    draw.text((x + 36, y + 34), subtitle, font=sub_font, fill=WHITE)
    slogan = "WE DELIVER HOPE"
    sw = draw.textbbox((0, 0), slogan, font=_font(25, True))[2]
    draw.text(((WIDTH - sw) / 2, 800), slogan, font=_font(25, True), fill=WHITE)
    _dots(draw, 1, total)
    return image


def _draw_text_page(page: dict, index: int, total: int) -> Image.Image:
    image = _gradient((WIDTH, HEIGHT), "#C78C3E", "#80512D")
    draw = ImageDraw.Draw(image)
    _brand(image, draw, page, index + 1, total, dark=True)

    intro = page.get("subheadline") or page["headline"]
    intro_font = _font(55, True)
    y = 190
    intro_lines = _wrapped(draw, intro, intro_font, 980)[:2]
    intro_width = max(draw.textbbox((0, 0), line, font=intro_font)[2] for line in intro_lines)
    draw.rectangle((100, y - 12, 100 + intro_width + 34, y + len(intro_lines) * 68), fill="#D29A48")
    for line in intro_lines:
        draw.text((118, y), line, font=intro_font, fill=WHITE)
        y += 68
    y += 68

    points = page.get("points") or ["核对最新要求与真实成本", "提前规划关键节点", "保留充足执行缓冲"]
    supporting = [
        "先说明它是什么，再解释执行口径与注意事项",
        "结合真实业务节点，避免只给抽象结论",
        "核对费用、责任和时效，形成可执行方案",
        "执行前再次确认最新政策与实际报价",
    ]
    for number, point in enumerate(points[:4], 1):
        point_count = min(len(points), 4)
        block_h = 430 if point_count <= 2 else (320 if point_count == 3 else 245)
        fill = "#A96E32" if number % 2 else "#B97C36"
        draw.rectangle((0, y - 22, WIDTH, y + block_h - 22), fill=fill)
        number_text = f"{number:02d}"
        draw.ellipse((44, y - 10, 116, y + 62), fill="#C58A3E")
        draw.text((43, y - 4), number_text, font=_font(48, True), fill=WHITE)
        lines = _wrapped(draw, point, _font(55, True), 1020)[:2]
        draw.text((135, y - 4), lines[0], font=_font(55, True), fill=WHITE, stroke_width=3, stroke_fill="#704425")
        detail = lines[1] if len(lines) > 1 else supporting[number - 1]
        detail_y = y + 74
        draw.text((145, detail_y), "核心说明：", font=_font(35, True), fill=WHITE)
        detail_y += 50
        for detail_line in _wrapped(draw, detail, _font(36), 970)[:3]:
            draw.text((145, detail_y), detail_line, font=_font(36), fill="#FFF4E4")
            detail_y += 50
        if block_h >= 320:
            reminder = "执行前核对最新口径、责任边界与实际费用"
            draw.text((145, y + block_h - 105), "执行提醒：", font=_font(34, True), fill=WHITE)
            draw.text((345, y + block_h - 105), reminder, font=_font(32), fill="#FFF4E4")
        y += block_h + 22
    _dots(draw, index + 1, total)
    return image


def _draw_photo_page(page: dict, index: int, total: int, photo: Path | None) -> Image.Image:
    return _draw_text_page(page, index, total)


def _render_page(page: dict, index: int, total: int, output: Path, photo: Path | None = None):
    if index == 0:
        image = _draw_cover(page, total, photo)
    elif index % 2:
        image = _draw_text_page(page, index, total)
    else:
        image = _draw_photo_page(page, index, total, photo)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)


def render_carousel(title: str, pages: list[dict] | None, static_dir: Path) -> tuple[list[dict], list[dict]]:
    normalized = normalize_pages(title, pages)
    batch = uuid4().hex
    attachments = []
    photos = _photo_sources(static_dir)
    for index, page in enumerate(normalized):
        relative = Path("uploads") / "image" / f"xhs-{batch}-{index + 1:02d}.png"
        photo = photos[index % len(photos)] if photos else None
        _render_page(page, index, len(normalized), static_dir / relative, photo)
        attachments.append({
            "type": "image", "path": relative.as_posix(),
            "url": f"/static/{relative.as_posix()}",
            "filename": relative.name, "generated": True,
            "template_version": TEMPLATE_VERSION,
        })
    return normalized, attachments
