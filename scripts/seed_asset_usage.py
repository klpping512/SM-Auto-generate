#!/usr/bin/env python3
"""素材使用历史播种：从历史 video_render_jobs 统计回灌 assets.usage_count（批15 块G）。

批13 块D 的冷却排序读 assets.usage_count / assets.last_used_at，但列是新增的，
历史使用（clip_refs 已记录在 quality_report 里）从未回灌，导致冷却「失忆」、
61/63 等霸榜素材继续霸榜。本脚本：

1. 读全部 status='succeeded' 且 clip_refs 非空的 video_render_jobs.quality_report；
2. 每个 job 的 clip_refs 按 asset_id 去重（同片重复镜头只计一次）；
3. 覆盖（非累加）写入 assets.usage_count = 历史使用次数、
   assets.last_used_at = 该资产最近一次被用时间（job 的 updated_at，UTC）；
4. 幂等：每次运行都是「重新统计后覆盖」，可任意重跑，结果不变。

用法：python scripts/seed_asset_usage.py [--dry-run]
"""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "logiflow.db"
TOP_N = 10


def collect_history(conn: sqlite3.Connection):
    """返回 (counter, last_used, jobs_scanned)：按 asset_id 统计历史使用。"""
    rows = conn.execute(
        """SELECT id, updated_at, quality_report FROM video_render_jobs
           WHERE status='succeeded' AND quality_report LIKE '%clip_refs%'"""
    ).fetchall()
    counter: Counter[int] = Counter()
    last_used: dict[int, str] = {}
    jobs_scanned = 0
    for row in rows:
        try:
            report = json.loads(row["quality_report"] or "{}")
        except (TypeError, ValueError):
            continue
        refs = report.get("clip_refs")
        if not isinstance(refs, list) or not refs:
            continue
        used_at = str(row["updated_at"] or "")
        # 同片内一个 asset 多个镜头只计一次（与 bump_asset_usage 的语义一致）
        asset_ids = set()
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            try:
                asset_ids.add(int(ref.get("asset_id") or 0))
            except (TypeError, ValueError):
                continue
        asset_ids.discard(0)
        if not asset_ids:
            continue
        jobs_scanned += 1
        for asset_id in asset_ids:
            counter[asset_id] += 1
            # updated_at 为 UTC 'YYYY-MM-DD HH:MM:SS'，字典序即时间序
            if used_at and used_at > last_used.get(asset_id, ""):
                last_used[asset_id] = used_at
    return counter, last_used, jobs_scanned


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        counter, last_used, jobs_scanned = collect_history(conn)
        if not counter:
            print("未找到任何含 clip_refs 的成功渲染记录，无需播种。")
            return
        # 只覆盖历史中出现过的资产；资产若已被删除则自动跳过（WHERE id=?）
        existing = {
            row["id"]
            for row in conn.execute(
                "SELECT id FROM assets WHERE id IN (%s)"
                % ",".join("?" * len(counter)),
                list(counter),
            )
        }
        missing = sorted(set(counter) - existing)
        todo = sorted(set(counter) & existing)
        print(f"扫描成功渲染 job：{jobs_scanned} 个（含非空 clip_refs）")
        print(f"命中资产：{len(todo)} 个（历史中出现但已删除的资产 {len(missing)} 个，跳过：{missing}）")
        print(f"Top {TOP_N} 使用榜：")
        for asset_id, used in counter.most_common(TOP_N):
            print(f"  asset {asset_id}: {used} 次，last_used_at={last_used[asset_id]}")
        if args.dry_run:
            print("--dry-run：未写库。")
            return
        conn.executemany(
            "UPDATE assets SET usage_count=?, last_used_at=? WHERE id=?",
            [(counter[asset_id], last_used[asset_id], asset_id) for asset_id in todo],
        )
        conn.commit()
        print(f"已覆盖写入 {len(todo)} 个资产的 usage_count / last_used_at（幂等，可重跑）。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
