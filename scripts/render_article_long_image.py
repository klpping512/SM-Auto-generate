#!/usr/bin/env python3
"""公众号图文长图渲染模块（阶段 0 扩展 · 可选格式 · 宽度可配）。

把同一份 generated_content_json + image_selections_json 排成竖长图 PNG。
R3 事实核查复用 scripts.render_article_package 的 scan_unresolved_claims / mark_unresolved，
与 Markdown 素材包的【待核实】标记集合完全一致，两格式永不漂移。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
from scripts.render_article_package import (  # noqa: E402
    FIXED_MATERIALS_DIR,
    FIXED_MATERIALS_REVIEW_NOTICE,
    FIXED_MATERIAL_FILES,
    STATIC_DIR,
    mark_unresolved,
    scan_unresolved_claims,
)

MARGIN = 24
BG_COLOR = (255, 255, 255)
TITLE_COLOR = (26, 26, 26)
BODY_COLOR = (51, 51, 51)
MUTED_COLOR = (120, 120, 120)
WARN_COLOR = (220, 38, 38)        # #DC2626
ACCENT_COLOR = (201, 162, 39)     # 品牌金 #C9A227
TABLE_HEADER_BG = (245, 245, 245)
PLACEHOLDER_BG = (238, 238, 238)
BANNER_FALLBACK = (24, 32, 44)

FONT_CANDIDATES = {
    "regular": [
        os.environ.get("ARTICLE_FONT_PATH", ""),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ],
    "bold": [
        os.environ.get("ARTICLE_FONT_PATH_BOLD", ""),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
    ],
}

# 未登记数字标记（与 render_article_package.mark_unresolved 的标记同源）
_UNRESOLVED_RE = re.compile(r"【待核实：』(.*?)『】")


class FontNotFoundError(Exception):
    pass


def resolve_font_paths() -> tuple[str, str]:
    """返回 (regular, bold) 第一个存在路径；找不到抛 FontNotFoundError（信息含设置方法）。"""
    def first(paths) -> str | None:
        for p in paths:
            if p and os.path.isfile(p):
                return p
        return None

    reg = first(FONT_CANDIDATES["regular"])
    bold = first(FONT_CANDIDATES["bold"]) or reg
    if not reg:
        raise FontNotFoundError(
            "未找到可用中文字体。请把字体文件路径写入环境变量 ARTICLE_FONT_PATH "
            "（如 /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc 或 macOS 的 PingFang.ttc）后重试。"
        )
    return reg, bold


_FONT_CACHE: dict = {}


def get_font(size: int, bold: bool = False):
    """返回缓存字体；.ttc 默认取第一个 face（CJK 统一码覆盖简体可读，A6 接受）。"""
    key = (size, bold)
    if key not in _FONT_CACHE:
        from PIL import ImageFont
        reg, bd = resolve_font_paths()
        _FONT_CACHE[key] = ImageFont.truetype(bd if bold else reg, size)
    return _FONT_CACHE[key]


# ==================== 排版工具 ====================

def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """按字符断行（不破坏单词为代价的中文优先策略），返回行列表。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def _draw_para(draw, x: int, y: int, text: str, font, max_width: int,
               line_height: int, color=BODY_COLOR) -> int:
    """包行绘制段落；段内【待核实：』…『】覆盖重画为红字（方案 A）。返回下一行 y。"""
    lines = _wrap_text(draw, text, font, max_width)
    for line in lines:
        draw.text((x, y), line, font=font, fill=color)
        for m in _UNRESOLVED_RE.finditer(line):
            prefix_w = draw.textlength(line[:m.start()], font=font)
            draw.text((x + prefix_w, y), m.group(0), font=font, fill=WARN_COLOR)
        y += line_height
    return y


def _load_scaled(path: Path, target_w: int):
    """打开图片并按 target_w 等比缩放；失败返回 None。"""
    try:
        from PIL import Image
        img = Image.open(path)
        img = img.convert("RGB")
        scale = target_w / img.width
        return img.resize((int(target_w), int(img.height * scale)), Image.LANCZOS)
    except Exception:  # noqa: BLE001 损坏/缺失图片统一走占位
        return None


def _draw_placeholder(draw, canvas, x: int, y: int, w: int, h: int, label: str) -> None:
    draw.rectangle([x, y, x + w, y + h], fill=PLACEHOLDER_BG)
    font = get_font(14)
    draw.text((x + 10, y + h // 2 - 10), label, font=font, fill=MUTED_COLOR)


def _draw_table(draw, x: int, y: int, rows: list, left_w: int, table_w: int,
                body_font, body_lh: int) -> None:
    draw.rectangle([x, y, x + table_w, y + len(rows) * body_lh], outline=(222, 222, 222))
    for row_idx, (k, v, is_header) in enumerate(rows):
        row_y = y + row_idx * body_lh
        if is_header:
            draw.rectangle([x, row_y, x + table_w, row_y + body_lh], fill=TABLE_HEADER_BG)
        draw.text((x + 8, row_y + 4), k, font=body_font, fill=TITLE_COLOR)
        draw.line([x + left_w, row_y, x + left_w, row_y + body_lh], fill=(222, 222, 222))
        _draw_para(draw, x + left_w + 8, row_y + 4, v, body_font, table_w - left_w - 16,
                   body_lh, BODY_COLOR)
        draw.line([x, row_y + body_lh, x + table_w, row_y + body_lh], fill=(222, 222, 222))


def _measure_para(draw, text: str, font, max_width: int, line_height: int) -> int:
    return len(_wrap_text(draw, text, font, max_width)) * line_height


# ==================== 渲染主函数 ====================

def render_long_image(article_id: int, width: int = 750, force: bool = False) -> dict:
    """把结构化内容排成竖长图 PNG。失败返回 {"status":"error","error":str}，不抛异常。"""
    try:
        return _render_impl(article_id, width, force)
    except FontNotFoundError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 任何绘制异常都不裸抛
        return {"status": "error", "error": f"长图渲染失败：{exc}"}


def _render_impl(article_id: int, width: int, force: bool) -> dict:
    from PIL import Image, ImageDraw

    article = db.get_article(article_id)
    if article is None:
        return {"status": "error", "error": f"文章不存在 id={article_id}"}
    content = json.loads(article.get("generated_content_json") or "{}")
    selections = json.loads(article.get("image_selections_json") or "{}")
    if not isinstance(selections, dict):
        selections = {}
    if not content or not content.get("sections"):
        return {"status": "error", "error": "还没有生成结果，先跑 scripts/generate_article.py"}
    if "cover" not in selections:
        return {"status": "error", "error": "还没有封面选择记录，先跑 scripts/select_article_images.py"}

    materials = json.loads(article.get("materials_json") or "[]")
    footnotes = json.loads(article.get("evidence_footnotes_json") or "[]")
    output_dir = PROJECT_ROOT / "data" / "articles" / article["slug"]

    # ---- R3：与 MD 完全同一集合（texts 组装含 comparison_card 值） ----
    texts = [content.get("intro") or ""]
    for section in content.get("sections") or []:
        texts.append(str(section.get("body") or ""))
        card = section.get("comparison_card")
        if isinstance(card, dict):
            texts.extend(str(v) for v in card.values())
    texts.append(content.get("conclusion") or "")
    unresolved = scan_unresolved_claims(texts, footnotes)

    # ---- 覆盖保护（只守卫自身文件，D5） ----
    target_file = output_dir / f"long-image-{width}.png"
    if target_file.exists() and not force:
        return {"status": "error", "error": f"目标长图已存在，加 --force 才允许覆盖：{target_file}"}

    warnings: list[str] = []
    scale = width / 750.0

    # ---- 测高阶段：构建块列表 (height, draw_fn) ----
    probe = Image.new("RGB", (1, 1))
    probe_draw = ImageDraw.Draw(probe)
    content_w = width - 2 * MARGIN
    blocks: list[tuple[int, object]] = []
    title_font = get_font(max(16, int(34 * scale)), bold=True)
    body_font = get_font(max(12, int(18 * scale)))
    heading_font = get_font(max(14, int(26 * scale)), bold=True)
    sub_font = get_font(max(13, int(22 * scale)), bold=True)
    small_font = get_font(max(10, int(13 * scale)))
    source_font = get_font(max(10, int(12 * scale)))
    body_lh = int(body_font.size * 1.7)
    heading_lh = int(heading_font.size * 1.6)
    title_lh = int(title_font.size * 1.5)
    small_lh = int(small_font.size * 1.6)

    # 1) banner
    banner_img = None
    if FIXED_MATERIALS_DIR.is_dir():
        banner_img = _load_scaled(FIXED_MATERIALS_DIR / "banner.png", content_w)
    else:
        warnings.append(f"固定物料目录不存在（公众号固定物料-占位稿），素材包不含 banner/文末三件套，发布前需人工补图（{FIXED_MATERIALS_DIR}）")
    if banner_img is not None:
        blocks.append((banner_img.height, ("image", banner_img)))
    else:
        blocks.append((int(content_w * 383 / 900), ("placeholder", "缺图：banner.png（固定物料未就位）")))

    # 2) 标题
    title_h = len(_wrap_text(probe_draw, article["title"], title_font, content_w)) * title_lh
    blocks.append((title_h + 12, ("title", article["title"])))

    # 3) 导语
    intro_h = _measure_para(probe_draw, mark_unresolved(content.get("intro") or "", unresolved),
                            body_font, content_w, body_lh)
    blocks.append((intro_h, ("para", content.get("intro") or "", body_font, body_lh, body_lh + 4)))

    # 4) 分节
    for i, section in enumerate(content.get("sections") or [], start=1):
        heading = str(section.get("heading") or "")
        body = str(section.get("body") or "")
        card = section.get("comparison_card") if isinstance(section.get("comparison_card"), dict) else None
        item_h = 0
        item_draws = []
        item_h += 10  # 节前留白
        heading_lines = _wrap_text(probe_draw, heading, heading_font, content_w)
        item_h += len(heading_lines) * heading_lh + 6
        item_draws.append(("heading", heading, heading_font, heading_lh))
        item_h += _measure_para(probe_draw, mark_unresolved(body, unresolved), body_font, content_w, body_lh) + 6
        item_draws.append(("para", body, body_font, body_lh, body_lh + 4))
        if card:
            table_w = content_w
            left_w = max([probe_draw.textlength(k, font=body_font) for k in card.keys()] + [probe_draw.textlength("字段", font=body_font)]) + 20
            rows = [("字段", "说明", True)] + [(k, str(v), False) for k, v in card.items()]
            for _, value, _is_header in rows:
                lines = _wrap_text(probe_draw, value, body_font, table_w - left_w - 16)
                item_h += max(1, len(lines)) * body_lh
            item_h += 8
            item_draws.append(("table", rows, left_w, table_w))
        image_rel = (selections.get(f"section-{i:02d}") or {}).get("target_rel")
        if image_rel:
            image_path = output_dir / "images" / image_rel
            img = _load_scaled(image_path, content_w) if image_path.is_file() else None
            if img is not None:
                item_h += img.height + 6
                item_draws.append(("image", img))
            else:
                item_h += 150 + 6
                item_draws.append(("placeholder", f"缺图：{image_rel}（源文件不在）"))
        item_h += 14  # 节后留白
        blocks.append((item_h, ("section", item_draws)))

    # 5) 最后的话
    blocks.append((small_lh + 4, ("sub", "最后的话", sub_font, small_lh)))
    concl_h = _measure_para(probe_draw, mark_unresolved(content.get("conclusion") or "", unresolved),
                            body_font, content_w, body_lh)
    blocks.append((concl_h, ("para", content.get("conclusion") or "", body_font, body_lh, body_lh + 4)))

    # 6) 资料来源
    source_lines = []
    for note in footnotes:
        index = int(note.get("material_index") or 0)
        material = materials[index - 1] if 0 < index <= len(materials) else None
        if not material:
            continue
        line = f"{index}. {material.get('source_note') or '未知来源'}"
        if material.get("source_url"):
            line += f"（{material['source_url']}）"
        source_lines.append(line)
    if source_lines:
        blocks.append((len(source_lines) * (source_font.size + 4) + 6,
                       ("source", source_lines, source_font)))

    # 7) 文末三件套
    closing_sizes = {
        "closing-1-info.png": 500 / 900,
        "closing-2-brand.png": 500 / 900,
        "closing-3-grid.png": 1.0,
    }
    closing_imgs = {}
    if FIXED_MATERIALS_DIR.is_dir():
        for name in FIXED_MATERIAL_FILES:
            if name == "banner.png":
                continue
            closing_imgs[name] = _load_scaled(FIXED_MATERIALS_DIR / name, content_w)
    for name, ratio in closing_sizes.items():
        img = closing_imgs.get(name)
        if img is not None:
            blocks.append((img.height, ("image", img)))
        else:
            blocks.append((int(content_w * ratio), ("placeholder", f"缺图：{name}（固定物料未就位）")))

    # 8) 固定提示块
    notice_h = _measure_para(probe_draw, FIXED_MATERIALS_REVIEW_NOTICE, small_font, content_w - 24, small_lh) + 16
    blocks.append((notice_h, ("notice", FIXED_MATERIALS_REVIEW_NOTICE, small_font, small_lh)))

    # ---- 创建画布逐块绘制（y 按测高布局推进，杜绝漂移） ----
    total_h = sum(h for h, _ in blocks) + MARGIN * 2
    canvas = Image.new("RGB", (width, total_h), BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    y = MARGIN
    x = MARGIN
    for h, payload in blocks:
        y0 = y
        kind = payload[0]
        if kind == "image":
            canvas.paste(payload[1], (x, y))
        elif kind == "placeholder":
            _draw_placeholder(draw, canvas, x, y, content_w, h, payload[1])
        elif kind == "title":
            lines = _wrap_text(draw, payload[1], title_font, content_w)
            for line in lines:
                draw.text((x, y + 6), line, font=title_font, fill=TITLE_COLOR)
                y += title_lh
        elif kind == "para":
            _draw_para(draw, x, y, payload[1], payload[2], content_w, payload[3], BODY_COLOR)
        elif kind == "sub":
            draw.text((x, y + 2), payload[1], font=payload[2], fill=TITLE_COLOR)
        elif kind == "heading":
            lines = _wrap_text(draw, payload[1], payload[2], content_w)
            for line in lines:
                draw.rectangle([x, y, x + 4, y + payload[3] - 6], fill=ACCENT_COLOR)
                draw.text((x + 12, y), line, font=payload[2], fill=TITLE_COLOR)
        elif kind == "table":
            _draw_table(draw, x, y, payload[1], payload[2], payload[3], body_font, body_lh)
        elif kind == "section":
            inner_y = y
            for sub in payload[1]:
                sub_kind = sub[0]
                if sub_kind == "heading":
                    for line in _wrap_text(draw, sub[1], sub[2], content_w):
                        draw.rectangle([x, inner_y, x + 4, inner_y + sub[3] - 6], fill=ACCENT_COLOR)
                        draw.text((x + 12, inner_y), line, font=sub[2], fill=TITLE_COLOR)
                        inner_y += sub[3]
                elif sub_kind == "para":
                    inner_y = _draw_para(draw, x, inner_y, sub[1], sub[2], content_w, sub[3], BODY_COLOR)
                elif sub_kind == "table":
                    _draw_table(draw, x, inner_y, sub[1], sub[2], sub[3], body_font, body_lh)
                    inner_y += len(sub[1]) * body_lh + 8
                elif sub_kind == "image":
                    canvas.paste(sub[1], (x, inner_y))
                    inner_y += sub[1].height
                elif sub_kind == "placeholder":
                    _draw_placeholder(draw, canvas, x, inner_y, content_w, 150, sub[1])
                    inner_y += 150
        elif kind == "source":
            for line in payload[1]:
                draw.text((x, y), line, font=payload[2], fill=MUTED_COLOR)
        elif kind == "notice":
            draw.rectangle([x, y, x + content_w, y + h], fill=(254, 242, 242))
            _draw_para(draw, x + 12, y + 8, payload[1], payload[2], content_w - 24,
                       payload[3], WARN_COLOR)
        y = y0 + h

    output_dir.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target_file, "PNG")

    # ---- meta.json 合并追加（不清掉 MD 渲染的字段） ----
    meta_path = output_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (TypeError, ValueError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta["unresolved_claims"] = unresolved
    meta["fixed_materials_review_notice"] = FIXED_MATERIALS_REVIEW_NOTICE
    long_images = list(meta.get("long_images") or [])
    if width not in long_images:
        long_images.append(width)
    meta["long_images"] = long_images
    formats = list(dict.fromkeys((meta.get("formats") or []) + ["longimg"]))
    meta["formats"] = formats
    meta["rendered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    db.update_article(article_id, output_dir=str(output_dir), status="ready",
                      unresolved_claims_json=json.dumps(unresolved, ensure_ascii=False))

    return {
        "status": "ok",
        "output_dir": str(output_dir),
        "long_image_file": f"long-image-{width}.png",
        "long_image_path": f"data/articles/{article['slug']}/long-image-{width}.png",
        "width": width,
        "unresolved": unresolved,
        "warnings": warnings,
        "meta": meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染公众号图文长图 PNG（可选格式 · 宽度可配）")
    parser.add_argument("--id", type=int, required=True, help="article id")
    parser.add_argument("--width", type=int, default=750, help="长图宽度（600–1200，默认 750）")
    parser.add_argument("--force", action="store_true", help="目标长图已存在时强制覆盖")
    args = parser.parse_args()

    result = render_long_image(args.id, width=args.width, force=args.force)
    if result["status"] == "error":
        print(f"错误：{result['error']}")
        return 1
    if result["unresolved"]:
        print(f"⚠ 发现 {len(result['unresolved'])} 处数字类片段未登记证据来源，图上已标红：")
        for fragment in result["unresolved"]:
            print(f"  - {fragment}")
    for warning in result["warnings"]:
        print(f"⚠ {warning}")
    print(f"长图已输出：{Path(result['output_dir']) / result['long_image_file']}")
    print(f"状态已更新：id={args.id} -> ready（meta.json 已合并 long_images/formats）")
    print("下一步：人工检查长图，确认后下载发布（公众号图片消息建议 750 宽；朋友圈/小红书建议 1080 宽）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
