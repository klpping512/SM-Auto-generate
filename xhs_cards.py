"""Render BUFFALO-branded Xiaohongshu carousel pages into publishable PNGs."""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


WIDTH, HEIGHT = 1242, 1660
TEMPLATE_VERSION = "buffalo-gold-v1"
GOLD = "#B78A4A"
GOLD_LIGHT = "#D2AA6D"
GOLD_DARK = "#7C542D"
CREAM = "#F3E6D2"
WHITE = "#FFFDF8"
FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _font(size: int, bold: bool = False):
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size, index=1 if bold else 0)
            except OSError:
                return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _clean(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalize_pages(title: str, pages: list[dict] | None) -> list[dict]:
    valid = []
    for index, raw in enumerate(pages or []):
        if not isinstance(raw, dict):
            continue
        points = [_clean(item, 48) for item in raw.get("points", []) if _clean(item, 48)][:4]
        valid.append({
            "type": "cover" if index == 0 else "content",
            "headline": _clean(raw.get("headline") or (title if index == 0 else "物流提示"), 24),
            "subheadline": _clean(raw.get("subheadline"), 34),
            "points": points,
        })
    if len(valid) < 2:
        valid = [
            {"type": "cover", "headline": _clean(title, 24), "subheadline": "南非物流实用指南", "points": []},
            {"type": "content", "headline": "值得关注的重点", "subheadline": "", "points": ["及时确认最新物流动态", "提前准备可执行的应对方案"]},
        ]
    return valid[:9]


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


def _brand(draw: ImageDraw.ImageDraw, page: int, total: int, dark=False):
    color = WHITE if dark else "#FFFFFF"
    draw.text((785, 70), "BUFFALO", font=_font(34, True), fill=color)
    draw.text((980, 80), "WE DELIVER HOPE", font=_font(13, True), fill=color)
    draw.text((1144, 72), f"{page}/{total}", font=_font(24, True), fill=color)


def _draw_cover(page: dict, total: int, photo: Path | None) -> Image.Image:
    image = _gradient((WIDTH, HEIGHT), "#C39A61", "#80562E")
    draw = ImageDraw.Draw(image)
    image.paste(_photo_panel(photo, (WIDTH, 690)), (0, 970))
    # Signature curved transition used by BUFFALO's published covers.
    draw.ellipse((-240, 785, WIDTH + 260, 1125), fill="#9C6C3D")
    draw.rectangle((0, 0, WIDTH, 915), fill="#A77743")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle((0, 0, WIDTH, 980), fill=(120, 77, 38, 55))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    _brand(draw, 1, total, dark=True)

    headline_font = _font(92, True)
    lines = _wrapped(draw, page["headline"], headline_font, 1010)[:3]
    y = 300 if len(lines) <= 2 else 245
    for line in lines:
        width = draw.textbbox((0, 0), line, font=headline_font)[2]
        draw.text(((WIDTH - width) / 2, y), line, font=headline_font, fill=WHITE, stroke_width=2, stroke_fill="#875C32")
        y += 120

    subtitle = page.get("subheadline") or "拆解关键逻辑，避开常见误区"
    sub_font = _font(40)
    sub_w = min(1030, draw.textbbox((0, 0), subtitle, font=sub_font)[2] + 76)
    x = (WIDTH - sub_w) / 2
    draw.rounded_rectangle((x, y + 24, x + sub_w, y + 94), radius=8, fill="#C49A5E")
    draw.text((x + 38, y + 35), subtitle, font=sub_font, fill="#FFF6E9")
    draw.text((495, 850), "WE DELIVER HOPE", font=_font(22, True), fill="#F7E7CE")
    return image


def _draw_text_page(page: dict, index: int, total: int) -> Image.Image:
    image = _gradient((WIDTH, HEIGHT), "#C2975C", "#8B5D34")
    image = image.filter(ImageFilter.GaussianBlur(0.35))
    draw = ImageDraw.Draw(image)
    _brand(draw, index + 1, total, dark=True)

    intro = page.get("subheadline") or "真正拉开差距的，往往是这些容易忽略的细节"
    intro_font = _font(38)
    y = 190
    for line in _wrapped(draw, intro, intro_font, 1020)[:2]:
        draw.text((105, y), line, font=intro_font, fill="#F8E8D2")
        y += 54
    draw.text((105, y + 8), page["headline"], font=_font(51, True), fill=WHITE)
    y += 126

    points = page.get("points") or ["核对最新要求与真实成本", "提前规划关键节点", "保留充足执行缓冲"]
    supporting = [
        "确认计费口径与服务边界，避免后续临时追加",
        "核对费用发生节点与承担方，防止责任不清",
        "结合货量和时效，选择真正适合自己的方案",
        "报价存在有效期，执行前再次确认最新标准",
    ]
    for number, point in enumerate(points[:4], 1):
        number_text = f"{number:02d}"
        draw.text((72, y), number_text, font=_font(45, True), fill="#FFF5E6")
        lines = _wrapped(draw, point, _font(40, True), 930)[:2]
        draw.text((170, y + 2), lines[0], font=_font(40, True), fill=WHITE)
        detail = lines[1] if len(lines) > 1 else supporting[number - 1]
        for detail_line in _wrapped(draw, detail, _font(28), 920)[:2]:
            draw.text((170, y + 62), detail_line, font=_font(28), fill="#F1DDC3")
        draw.line((170, y + 142, 1120, y + 142), fill="#D2A974", width=2)
        y += 230
    draw.text((78, 1570), "* 内容仅供业务沟通参考，请以最新政策与实际报价为准", font=_font(20), fill="#E6C9A4")
    return image


def _draw_photo_page(page: dict, index: int, total: int, photo: Path | None) -> Image.Image:
    image = _gradient((WIDTH, HEIGHT), "#B68A51", "#80562F")
    image.paste(_photo_panel(photo, (WIDTH, 610)), (0, 0))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle((0, 0, WIDTH, 610), fill=(76, 48, 25, 65))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    _brand(draw, index + 1, total, dark=True)

    title_font = _font(49, True)
    title_lines = _wrapped(draw, page["headline"], title_font, 1010)[:2]
    ty = 365
    for line in title_lines:
        w = draw.textbbox((0, 0), line, font=title_font)[2]
        draw.rounded_rectangle(((WIDTH - w) / 2 - 24, ty - 8, (WIDTH + w) / 2 + 24, ty + 62), radius=7, fill="#AA793E")
        draw.text(((WIDTH - w) / 2, ty), line, font=title_font, fill=WHITE)
        ty += 72

    points = page.get("points") or ["信息透明，节点可追踪", "灵活响应，减少积压", "本地执行，提高效率"]
    supporting = [
        "逐项问清费用范围，并写入正式报价",
        "起运端、海运段与目的港分别核对",
        "确认清关、仓储与末端配送责任",
    ]
    y = 680
    icons = ["✓", "◇", "◎"]
    for number, point in enumerate(points[:3]):
        draw.ellipse((95, y, 185, y + 90), outline=WHITE, width=4)
        draw.text((121, y + 12), icons[number], font=_font(48, True), fill=WHITE)
        lines = _wrapped(draw, point, _font(39, True), 860)[:2]
        draw.text((220, y + 2), lines[0], font=_font(39, True), fill=WHITE)
        detail = lines[1] if len(lines) > 1 else supporting[number]
        draw.text((220, y + 62), detail, font=_font(27), fill="#F2DDC2")
        y += 275
    return image


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
