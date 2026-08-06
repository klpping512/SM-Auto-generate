#!/usr/bin/env python3
"""列出 Hook 入库选片 JSON 失败诊断行，按 stage 分组 + 启发式分类并打印统计。

示例：
  python3 scripts/dump_hook_intake_diagnostics.py --limit 30
  python3 scripts/dump_hook_intake_diagnostics.py --stage selection
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump hook intake JSON-failure diagnostics")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--stage", choices=["selection", "audit"], default=None)
    args = parser.parse_args()
    db.init_db()
    rows = db.list_hook_intake_diagnostics(limit=args.limit, stage=args.stage)

    stage_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    details = []
    for row in rows:
        raw = row.get("raw_content") or ""
        label = classify(raw)
        stage_counts[str(row.get("stage"))] += 1
        classification_counts[label] += 1
        details.append({
            "id": row.get("id"),
            "stage": row.get("stage"),
            "job_id": row.get("job_id"),
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
        "stage_counts": dict(stage_counts),
        "classification_counts": dict(classification_counts),
        "details": details,
        "next_step_hint": (
            "截断为主 → 需评估调大该段 max_output_tokens/压缩证据；"
            "空返回/纯错误为主 → 重试即可，重试后再统计失败率。"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
