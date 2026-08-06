#!/usr/bin/env python3
"""公众号图文阶段 0 — 生成 CLI：调 wechat_article_generator 重跑/首跑结构化生成。"""
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
import wechat_article_generator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成公众号图文长文结构化正文")
    parser.add_argument("--id", type=int, required=True, help="article id")
    args = parser.parse_args()

    article = db.get_article(args.id)
    if article is None:
        print(f"错误：文章不存在 id={args.id}")
        return 1
    print(f"生成中：id={args.id} slug={article['slug']} …")
    result = wechat_article_generator.generate_article(args.id)
    if result["status"] != "ok":
        print(f"生成失败：{result.get('error') or result['status']}")
        if result.get("raw_content"):
            print("原始返回：")
            print(result["raw_content"][:2000])
        return 1
    print(f"生成成功：{result['section_count']} 节，约 {result['word_count']} 字，"
          f"证据脚注 {result['footnote_count']} 条（模型 {result['model']}）")
    print("下一步：跑 scripts/select_article_images.py --id", args.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
