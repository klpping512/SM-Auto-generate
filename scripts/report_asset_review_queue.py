"""导出 primary_category=other / processing_status=review_required 抽检清单。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import database as db


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        other = [dict(row) for row in conn.execute(
            """SELECT id, name, file_type, coalesce(primary_category, category) AS category,
                      processing_status, processing_version, primary_category_source
               FROM assets
               WHERE status='active' AND coalesce(primary_category, category)='other'
               ORDER BY id LIMIT 200"""
        ).fetchall()]
        review = [dict(row) for row in conn.execute(
            """SELECT id, name, file_type, coalesce(primary_category, category) AS category,
                      processing_status, processing_version, primary_category_source
               FROM assets
               WHERE status='active' AND processing_status='review_required'
               ORDER BY id LIMIT 200"""
        ).fetchall()]
        tagless = conn.execute(
            """SELECT count(*) FROM asset_segments s
               WHERE coalesce(s.status,'active')='active'
                 AND NOT EXISTS (SELECT 1 FROM segment_tags t WHERE t.segment_id=s.id)"""
        ).fetchone()[0]
        segments = conn.execute(
            "SELECT count(*) FROM asset_segments WHERE coalesce(status,'active')='active'"
        ).fetchone()[0]
    print(json.dumps({
        "other_count": len(other),
        "review_required_count": len(review),
        "active_segments": segments,
        "tagless_segments": tagless,
        "other_sample": other[:40],
        "review_required_sample": review[:40],
        "guidance": [
            "优先抽检 other / review_required，不必手打全部 259。",
            "重建完成后再次运行本脚本，对比 tagless_segments 是否下降。",
            "旧 45% 视频任务不会自动重匹配；请新建任务复测。",
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
