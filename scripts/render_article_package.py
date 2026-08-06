#!/usr/bin/env python3
"""公众号图文阶段 0 — 渲染 CLI：结构化正文 + 配图 -> per-article 素材包。

- 事实核查兜底（R3）：正则扫正文里的数字类片段，未登记进 evidence_footnotes 的
  一律原地打【待核实】标记并入 unresolved_claims_json，不静默通过。
- 落盘保护（R4）：目录已存在且非空时必须显式 --force 才允许覆盖。
- 固定物料机制化：把 banner + 文末三件套复制进素材包，并在 article.md 末尾固定
  追加"文末信息图服务描述发布前需业务核实"提示（占位文案不受生成链路事实锚定约束）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402

FIXED_MATERIALS_DIR = PROJECT_ROOT / "docs" / "总指挥指令-2026-08-06" / "公众号固定物料-占位稿"
FIXED_MATERIAL_FILES = ("banner.png", "closing-1-info.png", "closing-2-brand.png", "closing-3-grid.png")

# 文末信息图（closing-1-info.png）里的服务描述是占位文案，不经生成链路事实锚定，发布前必须人工核实
FIXED_MATERIALS_REVIEW_NOTICE = (
    "【发布前必核】文末信息图（closing-1-info.png）中「仓储/清关/干线/配送」四条服务描述为占位文案，"
    "发布前请业务同事核实具体表述与资质范围（如 PoE/PoA/NRCS 全流程代办），与正文事实核查标准保持一致。"
)

# assets.filepath 是相对 static/ 的挂载路径，真实文件在 static/ 下
STATIC_DIR = PROJECT_ROOT / "static"

_NUMBER_UNIT = r"(?:\d[\d,，.]*\s*)(?:%|％|元|美元|兰特|人民币|天|年|周|月|个工作日|个自然日|R\s?\d|\$\s?\d|ZAR|USD|CNY)"
_DATE_PATTERNS = (
    r"\d{4}年(?:\s*\d{1,2}月(?:\s*\d{1,2}日)?)?",
    r"\d{1,2}月\d{1,2}日",
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
)
CLAIM_PATTERN = re.compile("|".join((_NUMBER_UNIT,) + _DATE_PATTERNS))


def scan_unresolved_claims(texts: list[str], footnotes: list[dict]) -> list[str]:
    """扫出未登记进 evidence_footnotes 的数字类片段；返回去重后的片段列表。"""
    registered = [str(note.get("claim_text") or "") for note in footnotes]
    hits: set[str] = set()
    for text in texts:
        for match in CLAIM_PATTERN.finditer(text):
            fragment = match.group(0).strip()
            if not fragment:
                continue
            if any(fragment in reg or reg in fragment for reg in registered if reg):
                continue
            hits.add(fragment)
    return sorted(hits)


def mark_unresolved(text: str, unresolved: list[str]) -> str:
    for fragment in unresolved:
        if fragment in text:
            text = text.replace(fragment, f"【待核实：』{fragment}『】")
    return text


def render_markdown(article: dict, content: dict, footnotes: list[dict],
                    selections: dict, materials: list[dict], unresolved: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"# {article['title']}")
    lines.append("")
    lines.append(mark_unresolved(content.get("intro") or "", unresolved))
    lines.append("")

    for i, section in enumerate(content.get("sections") or [], start=1):
        lines.append(f"## {section['heading']}")
        lines.append("")
        lines.append(mark_unresolved(section.get("body") or "", unresolved))
        lines.append("")
        comparison = section.get("comparison_card")
        if isinstance(comparison, dict) and comparison:
            lines.append("| 字段 | 说明 |")
            lines.append("|---|---|")
            for field, value in comparison.items():
                lines.append(f"| {field} | {value} |")
            lines.append("")
        image_rel = (selections.get(f"section-{i:02d}") or {}).get("target_rel")
        if image_rel:
            lines.append(f"![](images/{image_rel})")
            lines.append("")

    lines.append("## 最后的话")
    lines.append("")
    lines.append(mark_unresolved(content.get("conclusion") or "", unresolved))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**资料来源**")
    for note in footnotes:
        index = int(note.get("material_index") or 0)
        material = materials[index - 1] if 0 < index <= len(materials) else None
        if not material:
            continue
        source_line = f"{index}. {material.get('source_note') or '未知来源'}"
        if material.get("source_url"):
            source_line += f"（{material['source_url']}）"
        lines.append(source_line)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"> {FIXED_MATERIALS_REVIEW_NOTICE}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 per-article 素材包（Markdown + 图片 + meta.json）")
    parser.add_argument("--id", type=int, required=True, help="article id")
    parser.add_argument("--force", action="store_true", help="目标目录已存在且非空时强制覆盖")
    args = parser.parse_args()

    article = db.get_article(args.id)
    if article is None:
        print(f"错误：文章不存在 id={args.id}")
        return 1

    content = json.loads(article.get("generated_content_json") or "{}")
    selections = json.loads(article.get("image_selections_json") or "{}")
    if not content or not content.get("sections"):
        print("错误：还没有生成结果，先跑 scripts/generate_article.py --id", args.id)
        return 1
    if not selections or "cover" not in selections:
        print("错误：还没有封面选择记录，先跑 scripts/select_article_images.py --id", args.id)
        return 1

    materials = json.loads(article.get("materials_json") or "[]")
    footnotes = json.loads(article.get("evidence_footnotes_json") or "[]")

    # ---- R3：事实核查正则扫描（硬约束最后一道闸，不静默通过） ----
    texts = [content.get("intro") or ""]
    for section in content.get("sections") or []:
        texts.append(str(section.get("body") or ""))
        card = section.get("comparison_card")
        if isinstance(card, dict):
            texts.extend(str(value) for value in card.values())
    texts.append(content.get("conclusion") or "")
    unresolved = scan_unresolved_claims(texts, footnotes)
    if unresolved:
        print(f"⚠ 发现 {len(unresolved)} 处数字类片段未登记证据来源，将打【待核实】标记：")
        for fragment in unresolved:
            print(f"  - {fragment}")

    output_dir = PROJECT_ROOT / "data" / "articles" / article["slug"]
    if output_dir.exists() and any(output_dir.iterdir()) and not args.force:
        print(f"错误：目标目录已存在且非空，加 --force 才允许覆盖：{output_dir}")
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # ---- 已选配图文件就位（Step 4 已复制；这里只做缺文件提示） ----
    missing_images = []
    for slot in ("cover", *[f"section-{i:02d}" for i in range(1, len(content["sections"]) + 1)]):
        picked = selections.get(slot) or {}
        if not picked:
            continue
        source_file = STATIC_DIR / picked.get("filepath", "")
        if not source_file.is_file():
            missing_images.append(f"{slot}（{picked.get('filepath')}）")
    if missing_images:
        print(f"⚠ 以下已选配图的源文件在库里不存在（可能是文件被移动），发布前需人工处理：{missing_images}")

    # ---- 固定物料复制 + 机制化提示 ----
    fixed_copied = []
    if FIXED_MATERIALS_DIR.is_dir():
        for name in FIXED_MATERIAL_FILES:
            source = FIXED_MATERIALS_DIR / name
            if source.is_file():
                shutil.copy2(source, output_dir / name)
                fixed_copied.append(name)
        if fixed_copied:
            print(f"固定物料已复制进素材包：{', '.join(fixed_copied)}")
    else:
        print("⚠ 固定物料目录不存在（公众号固定物料-占位稿），素材包不含 banner/文末三件套，发布前需人工补图")
        print(f"  查找位置：{FIXED_MATERIALS_DIR}")

    # ---- 渲染 article.md ----
    markdown = render_markdown(article, content, footnotes, selections, materials, unresolved)
    article_md = output_dir / "article.md"
    article_md.write_text(markdown, encoding="utf-8")
    print(f"已写入：{article_md}")

    # ---- meta.json ----
    meta = {
        "article_id": article["id"],
        "slug": article["slug"],
        "title": article["title"],
        "materials_sources": [m.get("source_note") or "" for m in materials],
        "unresolved_claims": unresolved,
        "fixed_materials_review_notice": FIXED_MATERIALS_REVIEW_NOTICE,
        "fixed_materials": fixed_copied,
        "rendered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (output_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入：{output_dir / 'meta.json'}")

    db.update_article(args.id, output_dir=str(output_dir), status="ready",
                      unresolved_claims_json=json.dumps(unresolved, ensure_ascii=False))
    print(f"状态已更新：id={args.id} -> ready")
    if unresolved:
        print(f"⚠⚠ 有 {len(unresolved)} 处待核实，发布前必须人工确认（见 article.md 中【待核实】标记）")
    print(f"产出目录：{output_dir}（封面：{selections['cover'].get('target_rel')}）")
    print("下一步：人工检查 article.md 与配图，用秀米/135/Doocs-md 二次排版后手动发布；")
    print("发完后跑 scripts/mark_article_published.py --id", args.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
