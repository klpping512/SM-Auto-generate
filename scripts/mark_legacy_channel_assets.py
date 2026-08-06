#!/usr/bin/env python3
"""将已砍频道的旧 youtube 素材标记 deprecated=1（降权，不删文件）。可回滚。"""
import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "logiflow.db"
# 已砍频道前缀：信源整合前的历史垃圾（匹配闸不认 other，纯占池子）
LEGACY_PREFIXES = ("SABC", "SA Today", "South Africa Now")

def _conn():
    return sqlite3.connect(DB)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不修改")
    args = ap.parse_args()
    conn = _conn(); conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    like = " OR ".join(["name LIKE ? || '%'" for _ in LEGACY_PREFIXES])
    rows = cur.execute(
        f"SELECT id, name, source, category, deprecated FROM assets "
        f"WHERE source='youtube' AND category='other' AND ({like})",
        list(LEGACY_PREFIXES),
    ).fetchall()
    already = [r for r in rows if r["deprecated"]]
    todo = [r for r in rows if not r["deprecated"]]
    print(f"命中 {len(rows)} 条（已降权 {len(already)}，待降权 {len(todo)}）")
    for r in todo[:20]:
        print(f"  id={r['id']} {r['name']!r}")
    if not args.dry_run and todo:
        cur.executemany(
            "UPDATE assets SET deprecated=1 WHERE id=?", [(r["id"],) for r in todo]
        )
        conn.commit()
        print(f"已降权 {len(todo)} 条")
    conn.close()

if __name__ == "__main__":
    main()
