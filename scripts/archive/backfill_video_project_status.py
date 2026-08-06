"""Backfill video_projects.status from active_job video_generation_jobs.status.

Fixes projects stuck at ``generating`` after the active job already reached
``needs_review`` / terminal states. Safe to re-run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db


def _count_mismatches(conn) -> int:
    return int(
        conn.execute(
            """SELECT COUNT(*) FROM video_projects p
               JOIN video_generation_jobs j ON j.id = p.active_job_id
               WHERE p.status != CASE
                   WHEN j.status IN ('pending','running','cancel_requested') THEN 'generating'
                   WHEN j.status = 'needs_review' THEN 'needs_review'
                   WHEN j.status IN ('succeeded','failed','canceled') THEN 'ready'
                   ELSE p.status
               END"""
        ).fetchone()[0]
    )


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        before = _count_mismatches(conn)
        stuck_generating = conn.execute(
            """SELECT COUNT(*) FROM video_projects p
               JOIN video_generation_jobs j ON j.id = p.active_job_id
               WHERE p.status = 'generating'
                 AND j.status IN ('succeeded','failed','canceled','needs_review')"""
        ).fetchone()[0]
        # Terminal / review jobs: align project status; keep active_job_id.
        conn.execute(
            """UPDATE video_projects
               SET status = CASE
                     WHEN (
                       SELECT j.status FROM video_generation_jobs j
                       WHERE j.id = video_projects.active_job_id
                     ) = 'needs_review' THEN 'needs_review'
                     ELSE 'ready'
                   END,
                   updated_at = datetime('now')
               WHERE status = 'generating'
                 AND active_job_id IS NOT NULL
                 AND active_job_id IN (
                   SELECT id FROM video_generation_jobs
                   WHERE status IN ('succeeded','failed','canceled','needs_review')
                 )"""
        )
        # Active generating jobs should keep projects generating.
        conn.execute(
            """UPDATE video_projects
               SET status = 'generating', updated_at = datetime('now')
               WHERE active_job_id IS NOT NULL
                 AND active_job_id IN (
                   SELECT id FROM video_generation_jobs
                   WHERE status IN ('pending','running','cancel_requested')
                 )
                 AND status != 'generating'"""
        )
        # Orphan generating projects with missing active job.
        conn.execute(
            """UPDATE video_projects
               SET status = 'ready', updated_at = datetime('now')
               WHERE status = 'generating'
                 AND (
                   active_job_id IS NULL OR active_job_id = ''
                   OR active_job_id NOT IN (SELECT id FROM video_generation_jobs)
                 )"""
        )
        after = _count_mismatches(conn)
        remaining_stuck = conn.execute(
            """SELECT COUNT(*) FROM video_projects p
               JOIN video_generation_jobs j ON j.id = p.active_job_id
               WHERE p.status = 'generating'
                 AND j.status IN ('succeeded','failed','canceled','needs_review')"""
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "mismatches_before": before,
                "stuck_generating_before": stuck_generating,
                "mismatches_after": after,
                "stuck_generating_after": remaining_stuck,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if remaining_stuck:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
