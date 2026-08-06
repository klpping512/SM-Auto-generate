#!/usr/bin/env python3
"""列出 Hook 策展 JSON 失败诊断行，按启发式分类并打印统计。

示例：
  python3 scripts/dump_hook_curation_diagnostics.py --limit 30
  python3 scripts/dump_hook_curation_diagnostics.py --asset-id 815
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402


def classify(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "空返回"
    if "{" not in text and "[" not in text:
        return "纯错误或拒绝文本(无括号)"
    bal = 0
    for ch in text:
        if ch == "{":
            bal += 1
        elif ch == "}":
            bal -= 1
    return "截断(括号不平衡)" if bal > 0 else "其它(有括号但解析失败)"


def _media_for_asset(asset_id: int) -> dict | None:
    for media in db.list_hotspot_media(lifecycle_status="active", limit=500):
        if int(media.get("asset_id") or 0) == int(asset_id):
            return {
                "id": media.get("id"),
                "publisher": media.get("publisher"),
                "intake_title": media.get("intake_title"),
            }
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, publisher, intake_title FROM hotspot_media WHERE asset_id=? "
            "ORDER BY id DESC LIMIT 1",
            (int(asset_id),),
        ).fetchone()
    return dict(row) if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump hook curation JSON-failure diagnostics")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--asset-id", type=int, default=None)
    args = parser.parse_args()
    db.init_db()
    rows = db.list_hook_curation_diagnostics(limit=args.limit, asset_id=args.asset_id)
    counts: Counter[str] = Counter()
    details = []
    for row in rows:
        raw = row.get("raw_content") or ""
        label = classify(raw)
        counts[label] += 1
        media = _media_for_asset(int(row["asset_id"])) or {}
        details.append({
            "id": row.get("id"),
            "asset_id": row.get("asset_id"),
            "media_id": media.get("id"),
            "publisher": media.get("publisher"),
            "intake_title": (media.get("intake_title") or "")[:80],
            "attempt_number": row.get("attempt_number"),
            "model": row.get("model"),
            "cache_hit": row.get("cache_hit"),
            "error": row.get("error"),
            "classification": label,
            "raw_len": len(raw),
            "raw_preview": raw[:300],
        })
    print(json.dumps({
        "total": len(rows),
        "classification_counts": dict(counts),
        "details": details,
        "next_step_hint": (
            "截断为主 → 需评估调大 max_output_tokens/压缩证据；"
            "空返回/纯错误为主 → 重试即可，重试后再统计失败率。"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
