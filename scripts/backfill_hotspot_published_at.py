#!/usr/bin/env python3
"""批17：为已入库但发布时间缺失的 YouTube 热点回填 published_at。

只读元数据（yt-dlp --skip-download --dump-single-json），幂等，可反复执行。
用法：
  python3 scripts/backfill_hotspot_published_at.py             # 全量回填
  python3 scripts/backfill_hotspot_published_at.py --dry-run   # 只报告将回填数量
  python3 scripts/backfill_hotspot_published_at.py --limit 20  # 只回填前 N 条
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
from hotspot_video_sources import _metadata_command, _published_at  # noqa: E402


def _pending_youtube_hotspots() -> list[dict]:
    with db.get_conn() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, source_url, title FROM hotspots "
                "WHERE (published_at IS NULL OR published_at='' OR published_at LIKE '1970-%') "
                "AND source_url LIKE '%youtube.com%' ORDER BY id"
            ).fetchall()
        ]


def backfill(dry_run: bool = False, limit: int = 0) -> dict:
    rows = _pending_youtube_hotspots()
    if limit:
        rows = rows[:limit]
    filled = failed = skipped = 0
    total = len(rows)
    for idx, hotspot in enumerate(rows, start=1):
        url = str(hotspot.get("source_url") or "").strip()
        if not url:
            skipped += 1
            continue
        try:
            completed = subprocess.run(
                _metadata_command(url), capture_output=True, text=True, timeout=35, check=False
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "元数据读取失败").strip()[:200])
            entry = json.loads(completed.stdout or "{}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[{idx}/{total}] FAIL #{hotspot['id']} {url} -> {str(exc)[:120]}")
            continue
        iso = _published_at(entry)
        if not iso:
            skipped += 1
            print(f"[{idx}/{total}] NO-DATE #{hotspot['id']} {url}")
            continue
        if dry_run:
            filled += 1
            print(f"[{idx}/{total}] WOULD #{hotspot['id']} {iso} {url}")
        else:
            db.update_hotspot_published_at_if_empty(int(hotspot["id"]), iso)
            filled += 1
            if filled % 25 == 0 or filled == total:
                print(f"  ... 已回填 {filled}/{total}")
    return {"scanned": total, "filled": filled, "failed": failed, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(backfill(dry_run=args.dry_run, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
