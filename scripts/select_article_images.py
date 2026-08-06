#!/usr/bin/env python3
"""公众号图文阶段 0 — 配图选择 CLI（人工确认制，无全自动参数）。

封面 + 每个正文小节各选一张：脚本按关键词查库、分【免版权通用素材】/【自有实拍】两组展示，
人工输入 asset id 或 skip，脚本才复制文件落位。选错图无法在发布前被机器发现，因此绝不自动落地。
search_candidates / apply_selections 为模块级可导入函数，CLI 与 API 共用同一实现。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402

# assets.filepath 是相对 static/ 的挂载路径（如 assets/library/image/xxx.jpg），真实文件在 static/ 下
STATIC_DIR = PROJECT_ROOT / "static"

MAX_PER_GROUP = 8
ZA_STOCK_SOURCE = "za_stock_license"


def heading_keywords(heading: str) -> str:
    tokens = re.findall(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z]{2,}", heading or "")
    return " ".join(tokens[:4]) or "物流"


def search_candidates(keyword: str) -> dict:
    """按关键词查库并分两组返回候选（每组最多 MAX_PER_GROUP 条，仅展示用）。"""
    assets = db.list_assets(category=None, query=keyword or "", status="active")

    def _candidate(item: dict) -> dict:
        return {
            "id": item["id"],
            "name": item.get("name") or "",
            "filepath": item.get("filepath") or "",
            "category": item.get("category") or "",
            "source": str(item.get("source") or ""),
            "file_type": item.get("file_type") or "",
        }

    own = []
    za_stock = []
    for item in assets:
        candidate = _candidate(item)
        (za_stock if candidate["source"] == ZA_STOCK_SOURCE else own).append(candidate)
    return {"own": own[:MAX_PER_GROUP], "za_stock": za_stock[:MAX_PER_GROUP]}


def apply_selections(article_id: int, selections: dict) -> dict:
    """把人工确认的配图选择复制落位并写库。

    selections 形如 {"cover": {"asset_id": 73}, "section-01": {"asset_id": 147}, "section-02": None}；
    None 表示该槽跳过（保留已有选择不动）。
    返回 {"selections": 合并后的完整选择, "errors": [...]}，错误不静默。
    """
    article = db.get_article(article_id)
    if article is None:
        return {"selections": {}, "errors": [f"文章不存在 id={article_id}"]}
    output_dir = PROJECT_ROOT / "data" / "articles" / article["slug"]
    images_dir = output_dir / "images"

    existing = json.loads(article.get("image_selections_json") or "{}")
    if not isinstance(existing, dict):
        existing = {}
    errors: list[str] = []
    saved: dict = {}

    for slot, pick in (selections or {}).items():
        if pick is None:
            continue
        if not isinstance(pick, dict):
            errors.append(f"{slot}: 选择项必须是对象")
            continue
        try:
            asset_id = int(pick.get("asset_id"))
        except (TypeError, ValueError):
            errors.append(f"{slot}: 缺少有效 asset_id")
            continue
        asset = db.get_asset(asset_id)
        if asset is None:
            errors.append(f"{slot}: asset_id={asset_id} 不存在")
            continue
        filepath = str(asset.get("filepath") or "")
        source_file = STATIC_DIR / filepath
        if not source_file.is_file():
            errors.append(f"{slot}: 源文件不存在（{source_file}）")
            continue
        if slot == "cover":
            target = output_dir / "cover.jpg"
        else:
            target = images_dir / f"{slot}.jpg"
        suffix = Path(filepath).suffix
        final_target = target.with_suffix(suffix) if target.suffix == ".jpg" else target
        final_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, final_target)
        saved[slot] = {
            "asset_id": asset_id,
            "source": str(asset.get("source") or ""),
            "filepath": filepath,
            "target_rel": final_target.name,
        }

    merged = {**existing, **saved}
    db.update_article(article_id, image_selections_json=json.dumps(merged, ensure_ascii=False))
    return {"selections": merged, "errors": errors}


def ask_one(label: str, default_keyword: str) -> dict | None:
    """一次人工选图：返回 {"asset_id": int} 或 None(skip)。展示用 search_candidates，校验用全量查询。"""
    keyword = input(f"[{label}] 搜索关键词（回车用默认：{default_keyword}）：").strip()
    if not keyword:
        keyword = default_keyword

    groups = search_candidates(keyword)

    def _render(item: dict) -> str:
        return (f"  id={item['id']} | {item['name']} | {item['filepath']} "
                f"| category={item['category']}")

    total = len(groups["own"]) + len(groups["za_stock"])
    print(f"[{label}] 候选（关键词 {keyword!r}，共 {total} 条，各最多显示 {MAX_PER_GROUP} 条）：")
    print("【自有实拍】可用来证明 Buffalo 自己的能力：")
    for item in groups["own"]:
        print(_render(item))
    if not groups["own"]:
        print("  （无候选）")
    print("【免版权通用素材】不能当 Buffalo 自己的实拍用：")
    for item in groups["za_stock"]:
        print(_render(item))
    if not groups["za_stock"]:
        print("  （无候选）")

    choice = input(f"[{label}] 输入选中的 asset id（或输入 skip 跳过这张）：").strip().lower()
    if choice == "skip":
        return None
    try:
        asset_id = int(choice)
    except ValueError:
        print(f"[{label}] 输入无效，跳过这张（发布前记得补图）")
        return None
    all_assets = db.list_assets(category=None, query=keyword, status="active")
    if not any(item["id"] == asset_id for item in all_assets):
        print(f"[{label}] 无效 asset id，跳过这张（发布前记得补图）")
        return None
    return {"asset_id": asset_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="人工选择封面与正文配图并复制到位")
    parser.add_argument("--id", type=int, required=True, help="article id")
    args = parser.parse_args()

    article = db.get_article(args.id)
    if article is None:
        print(f"错误：文章不存在 id={args.id}")
        return 1
    content = json.loads(article.get("generated_content_json") or "{}")
    sections = content.get("sections") or []
    if not content or not sections:
        print("错误：该文章还没有生成结果，先跑 scripts/generate_article.py --id", args.id)
        return 1

    output_dir = PROJECT_ROOT / "data" / "articles" / article["slug"]
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    picks: dict = {}
    print(f"文章：{article['slug']}，需要选 {len(sections) + 1} 张图（封面 + {len(sections)} 节正文）")

    cover = ask_one("封面", heading_keywords(article["title"]))
    picks["cover"] = cover
    if cover is None:
        print("  封面跳过（发布前必须补封面图，公众号要求 900×383）")

    for i, section in enumerate(sections, start=1):
        label = f"section-{i:02d}"
        heading = str(section.get("heading") or "")
        picked = ask_one(f"{label} {heading}", heading_keywords(heading))
        picks[label] = picked
        if picked is None:
            print(f"  {label} 跳过（该节无图）")

    result = apply_selections(args.id, picks)
    for error in result["errors"]:
        print(f"错误：{error}")
    for slot, picked in picks.items():
        if picked is None:
            continue
        saved = result["selections"].get(slot)
        if not saved:
            continue
        if slot == "cover":
            path = output_dir / saved["target_rel"]
            print(f"  封面已复制：{path}")
        else:
            path = images_dir / saved["target_rel"]
            print(f"  {slot} 已复制：{path}")

    saved_count = sum(1 for slot in picks if picks.get(slot) is not None and slot in result["selections"])
    if "cover" not in result["selections"]:
        print("提示：封面未选择，发布前必须补封面图（公众号要求 900×383）")
    print(f"选择记录已保存：{saved_count} 张")
    print("下一步：跑 scripts/render_article_package.py --id", args.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
