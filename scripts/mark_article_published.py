#!/usr/bin/env python3
"""公众号图文阶段 0 — 发布标记 CLI：ready -> published（人工手动发完之后跑）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把文章标记为已发布（仅允许从 ready 流转）")
    parser.add_argument("--id", type=int, required=True, help="article id")
    args = parser.parse_args()

    article = db.get_article(args.id)
    if article is None:
        print(f"错误：文章不存在 id={args.id}")
        return 1
    if article["status"] != "ready":
        print(f"错误：当前状态是 {article['status']}，只有 ready 才能标记为 published"
              f"（不允许从 {article['status']} 直接跳过人工发布环节）")
        return 1
    db.update_article(args.id, status="published")
    print(f"已标记发布：id={args.id} slug={article['slug']} status=published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
