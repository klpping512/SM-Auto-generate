#!/usr/bin/env python3
"""公众号图文阶段 0 — 配图选择 CLI（人工确认制，无全自动参数）。

封面 + 每个正文小节各选一张：脚本按关键词查库、分【免版权通用素材】/【自有实拍】两组展示，
人工输入 asset id 或 skip，脚本才复制文件落位。选错图无法在发布前被机器发现，因此绝不自动落地。
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


def ask_one(label: str, default_keyword: str, target_rel: str) -> dict | None:
    """一次人工选图：返回 {asset_id, source, filepath, target_rel} 或 None(skip)。"""
    keyword = input(f"[{label}] 搜索关键词（回车用默认：{default_keyword}）：").strip()
    if not keyword:
        keyword = default_keyword

    assets = db.list_assets(category=None, query=keyword, status="active")
    za_group = [a for a in assets if str(a.get("source") or "") == ZA_STOCK_SOURCE]
    own_group = [a for a in assets if str(a.get("source") or "") != ZA_STOCK_SOURCE]

    def _render(item: dict) -> str:
        return (f"  id={item['id']} | {item.get('name') or ''} | {item.get('filepath') or ''} "
                f"| category={item.get('category') or ''}")

    print(f"[{label}] 候选（关键词 {keyword!r}，共 {len(assets)} 条，各最多显示 {MAX_PER_GROUP} 条）：")
    print("【自有实拍】可用来证明 Buffalo 自己的能力：")
    for item in own_group[:MAX_PER_GROUP]:
        print(_render(item))
    if not own_group:
        print("  （无候选）")
    print("【免版权通用素材】不能当 Buffalo 自己的实拍用：")
    for item in za_group[:MAX_PER_GROUP]:
        print(_render(item))
    if not za_group:
        print("  （无候选）")

    choice = input(f"[{label}] 输入选中的 asset id（或输入 skip 跳过这张）：").strip().lower()
    if choice == "skip":
        return None

    try:
        asset_id = int(choice)
    except ValueError:
        print(f"[{label}] 输入无效，跳过这张（发布前记得补图）")
        return None
    matched = next((a for a in assets if a["id"] == asset_id), None)
    if matched is None:
        print(f"[{label}] 无效 asset id，跳过这张（发布前记得补图）")
        return None
    return {
        "asset_id": matched["id"],
        "source": str(matched.get("source") or ""),
        "filepath": str(matched.get("filepath") or ""),
        "target_rel": target_rel,
    }


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

    selections: dict = {}
    print(f"文章：{article['slug']}，需要选 {len(sections) + 1} 张图（封面 + {len(sections)} 节正文）")

    cover = ask_one("封面", heading_keywords(article["title"]), "cover.jpg")
    if cover is not None:
        target = output_dir / cover["target_rel"]
        suffix = Path(cover["filepath"]).suffix
        final_target = target.with_suffix(suffix) if target.suffix == ".jpg" else target
        shutil.copy2(STATIC_DIR / cover["filepath"], final_target)
        cover["target_rel"] = final_target.name
        selections["cover"] = cover
        print(f"  封面已复制：{final_target}")

    for i, section in enumerate(sections, start=1):
        label = f"section-{i:02d}"
        heading = str(section.get("heading") or "")
        picked = ask_one(f"{label} {heading}", heading_keywords(heading), f"section-{i:02d}.jpg")
        if picked is None:
            print(f"  {label} 跳过（该节无图）")
            continue
        target = images_dir / picked["target_rel"]
        suffix = Path(picked["filepath"]).suffix
        final_target = target.with_suffix(suffix) if target.suffix == ".jpg" else target
        shutil.copy2(STATIC_DIR / picked["filepath"], final_target)
        picked["target_rel"] = final_target.name
        selections[label] = picked
        print(f"  {label} 已复制：{final_target}")

    if "cover" not in selections:
        print("提示：封面未选择，发布前必须补封面图（公众号要求 900×383）")
    db.update_article(args.id, image_selections_json=json.dumps(selections, ensure_ascii=False))
    print(f"选择记录已保存：{len(selections)} 张")
    print("下一步：跑 scripts/render_article_package.py --id", args.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
