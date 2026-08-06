#!/usr/bin/env python3
"""公众号图文阶段 0 — 选题投喂 CLI。

人工把资料包整理成一个 JSON 文件喂进来，校验通过后建一条 draft 文章。
校验不合格直接拒绝建草稿并打印具体哪条不合格（R1），绝不静默通过。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_package(package: dict) -> list[str]:
    """返回错误清单；空列表 = 通过。"""
    errors = []
    slug = str(package.get("slug") or "").strip()
    if not slug:
        errors.append("slug 不能为空")
    elif not SLUG_RE.match(slug):
        errors.append(f"slug 格式非法（只允许小写字母/数字/连字符）：{slug!r}")
    elif db.get_article_by_slug(slug):
        errors.append(f"slug 已存在，请换一个或先处理已有文章：{slug}")

    if not str(package.get("title") or "").strip():
        errors.append("title 不能为空")

    materials = package.get("materials")
    if not isinstance(materials, list) or not materials:
        errors.append("materials 必须是至少 1 条的数组")
    else:
        for i, item in enumerate(materials):
            if not isinstance(item, dict):
                errors.append(f"materials[{i}] 不是对象")
                continue
            if not str(item.get("excerpt") or "").strip():
                errors.append(f"materials[{i}] 缺少非空 excerpt（原文片段）")
            if not str(item.get("source_note") or "").strip():
                errors.append(f"materials[{i}] 缺少非空 source_note（来源说明）")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="选题投喂：资料包 JSON -> articles 表 draft 草稿")
    parser.add_argument("--materials-file", required=True, help="人工整理的资料包 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印将插入的内容，不写库")
    args = parser.parse_args()

    materials_path = Path(args.materials_file)
    if not materials_path.is_file():
        print(f"错误：文件不存在 {materials_path}")
        return 1
    try:
        package = json.loads(materials_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"错误：JSON 解析失败 {materials_path}: {exc}")
        return 1
    if not isinstance(package, dict):
        print("错误：资料包根节点必须是 JSON 对象")
        return 1

    errors = validate_package(package)
    if errors:
        print("校验未通过，拒绝建草稿：")
        for err in errors:
            print(f"  - {err}")
        return 1

    slug = str(package["slug"]).strip()
    title = str(package["title"]).strip()
    topic_brief = str(package.get("topic_brief") or "").strip()
    reference_style = str(package.get("reference_style") or "").strip()
    materials = package["materials"]

    print(f"校验通过：slug={slug} title={title!r} 资料 {len(materials)} 条")
    if args.dry_run:
        print("[dry-run] 将插入 articles：")
        print(json.dumps({
            "slug": slug, "title": title, "topic_brief": topic_brief,
            "reference_style": reference_style,
            "materials": materials,
        }, ensure_ascii=False, indent=2))
        return 0

    article_id = db.create_article(
        slug=slug,
        title=title,
        topic_brief=topic_brief,
        materials_json=json.dumps(materials, ensure_ascii=False),
        reference_style=reference_style,
    )
    print(f"已建草稿：id={article_id} slug={slug} status=draft")
    print("下一步：跑 scripts/generate_article.py --id", article_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
