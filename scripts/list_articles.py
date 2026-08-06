#!/usr/bin/env python3
"""公众号图文阶段 0 — 待发清单 CLI：按状态分组列出文章，人工扫一眼现在有几篇在等发。"""
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

STATUS_LABELS = {"draft": "草稿", "ready": "待发", "published": "已发布"}


def main() -> int:
    parser = argparse.ArgumentParser(description="列出文章清单（按状态分组）")
    parser.add_argument("--status", choices=("draft", "ready", "published"), help="只显示某状态")
    args = parser.parse_args()

    statuses = [args.status] if args.status else ("draft", "ready", "published")
    for status in statuses:
        rows = db.list_articles(status=status)
        print(f"== {STATUS_LABELS.get(status, status)}（{len(rows)}） ==")
        for row in rows:
            print(f"  id={row['id']} | {row['slug']} | {row['title'][:30]} | 更新 {row['updated_at']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
