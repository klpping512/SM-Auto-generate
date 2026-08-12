"""批22：12 天 Hook 周期清理门禁与幂等删除测试。"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hotspot_hook_cycle_cleanup as cycle


ANCHOR = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class HookCycleCleanupTests(unittest.TestCase):
    def setUp(self):
        self._old = {
            k: os.environ.get(k)
            for k in (
                "HOTSPOT_HOOK_CYCLE_DAYS",
                "HOTSPOT_HOOK_MIN_CYCLE_QUALIFIED",
                "HOTSPOT_HOOK_CYCLE_ANCHOR",
                "HOTSPOT_HOOK_CLEANUP_ENABLED",
            )
        }
        os.environ["HOTSPOT_HOOK_CYCLE_DAYS"] = "12"
        os.environ["HOTSPOT_HOOK_MIN_CYCLE_QUALIFIED"] = "40"
        os.environ["HOTSPOT_HOOK_CYCLE_ANCHOR"] = "2026-08-01T00:00:00+00:00"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.static = self.root / "static"
        self.static.mkdir()
        self.db_path = self.root / "test.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _init_schema(self, conn: sqlite3.Connection):
        conn.executescript(
            """
            CREATE TABLE hotspot_event_clips(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER,
                hotspot_id INTEGER,
                clip_path TEXT,
                thumbnail_path TEXT,
                hook_kind TEXT,
                review_status TEXT,
                clip_status TEXT,
                created_at TEXT,
                locked INTEGER DEFAULT 0
            );
            CREATE TABLE hotspot_event_segment_links(
                event_clip_id INTEGER,
                segment_id INTEGER
            );
            CREATE TABLE hotspot_media(
                id INTEGER PRIMARY KEY,
                download_status TEXT,
                processing_status TEXT,
                created_at TEXT
            );
            CREATE TABLE video_projects(
                id INTEGER PRIMARY KEY,
                status TEXT,
                source_snapshot TEXT
            );
            CREATE TABLE assets(
                id INTEGER PRIMARY KEY,
                filepath TEXT,
                name TEXT
            );
            """
        )
        conn.commit()

    def _add_hook(
        self,
        *,
        created_at: datetime,
        hook_kind: str = "timely_event",
        review_status: str = "confirmed",
        clip_status: str = "ready",
        asset_id: int = 1,
        locked: int = 0,
        with_files: bool = True,
    ) -> int:
        clip_rel = None
        thumb_rel = None
        if with_files:
            clips = self.static / "assets" / "event-clips"
            clips.mkdir(parents=True, exist_ok=True)
            clip_file = clips / f"hook-{created_at.timestamp()}.mp4"
            thumb_file = clips / f"hook-{created_at.timestamp()}.jpg"
            clip_file.write_bytes(b"clip-bytes-123456")
            thumb_file.write_bytes(b"jpg")
            clip_rel = str(clip_file.relative_to(self.static))
            thumb_rel = str(thumb_file.relative_to(self.static))
        cur = self.conn.execute(
            """
            INSERT INTO hotspot_event_clips(
                asset_id,hotspot_id,clip_path,thumbnail_path,hook_kind,
                review_status,clip_status,created_at,locked
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                asset_id,
                10,
                clip_rel,
                thumb_rel,
                hook_kind,
                review_status,
                clip_status,
                _iso(created_at),
                locked,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _seed_qualified(self, cycle_start: datetime, n: int = 40):
        for i in range(n):
            self._add_hook(created_at=cycle_start + timedelta(hours=i + 1), with_files=False)

    def test_cycle_id_from_created_at(self):
        c0 = cycle.cycle_for(ANCHOR + timedelta(days=0), anchor=ANCHOR)
        c1 = cycle.cycle_for(ANCHOR + timedelta(days=12), anchor=ANCHOR)
        c1b = cycle.cycle_for(ANCHOR + timedelta(days=23, hours=23), anchor=ANCHOR)
        self.assertEqual(c0.cycle_id, 0)
        self.assertEqual(c1.cycle_id, 1)
        self.assertEqual(c1b.cycle_id, 1)
        self.assertEqual(c0.end_at, ANCHOR + timedelta(days=12))

    def test_old_cycle_can_delete_when_gates_pass(self):
        # 完成周期 0（40 条），当前位于周期 1；应允许删除周期 -1 的空集，或删除周期 0 的上一周期
        # 按实现：completed=cycle0, delete=previous(cycle0)=cycle -1
        # 为测「旧周期可删」，把待删素材放进 cycle -1，并把 now 放在 cycle1，completed=cycle0 有 40 条
        cycle_minus1_start = ANCHOR + timedelta(days=-12)
        old_id = self._add_hook(created_at=cycle_minus1_start + timedelta(days=1))
        mother = self.static / "assets" / "library" / "video" / "mother.mp4"
        mother.parent.mkdir(parents=True, exist_ok=True)
        mother.write_bytes(b"mother-source")
        self.conn.execute("INSERT INTO assets(id,filepath,name) VALUES (1,?,?)", (str(mother), "mother"))
        self.conn.commit()

        self._seed_qualified(ANCHOR, 40)  # cycle 0
        now = ANCHOR + timedelta(days=12, hours=1)  # cycle 1 started; cycle 0 completed
        report = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=False, now=now
        )
        self.assertTrue(report["gates"]["allowed"])
        self.assertEqual(report["deleted_count"], 1)
        self.assertEqual(report["deleted"][0]["event_clip_id"], old_id)
        left = self.conn.execute("SELECT COUNT(*) FROM hotspot_event_clips WHERE id=?", (old_id,)).fetchone()[0]
        self.assertEqual(left, 0)
        self.assertTrue(mother.exists(), "母片必须保留")

    def test_current_cycle_not_deleted(self):
        self._seed_qualified(ANCHOR, 40)
        current_hook = self._add_hook(created_at=ANCHOR + timedelta(days=12, hours=2))
        now = ANCHOR + timedelta(days=12, hours=3)
        # completed=cycle0 (>=40), delete target=cycle -1；当前周期 hook 不应进入候选
        report = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=False, now=now
        )
        ids = {row["event_clip_id"] for row in report["deleted"]}
        self.assertNotIn(current_hook, ids)
        still = self.conn.execute(
            "SELECT COUNT(*) FROM hotspot_event_clips WHERE id=?", (current_hook,)
        ).fetchone()[0]
        self.assertEqual(still, 1)

    def test_active_project_reference_not_deleted(self):
        cycle_minus1_start = ANCHOR + timedelta(days=-12)
        hook_id = self._add_hook(created_at=cycle_minus1_start + timedelta(days=2))
        self.conn.execute(
            "INSERT INTO video_projects(status,source_snapshot) VALUES ('active', ?)",
            (f'{{"event_clip_id": {hook_id}}}',),
        )
        self.conn.commit()
        self._seed_qualified(ANCHOR, 40)
        now = ANCHOR + timedelta(days=12, hours=1)
        report = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=False, now=now
        )
        self.assertEqual(report["deleted_count"], 0)
        self.assertTrue(any(s["reason"] == "active_reference" for s in report["skipped"]))
        still = self.conn.execute(
            "SELECT COUNT(*) FROM hotspot_event_clips WHERE id=?", (hook_id,)
        ).fetchone()[0]
        self.assertEqual(still, 1)

    def test_under_40_qualified_blocks_delete(self):
        cycle_minus1_start = ANCHOR + timedelta(days=-12)
        self._add_hook(created_at=cycle_minus1_start + timedelta(days=1))
        self._seed_qualified(ANCHOR, 39)  # 不足 40
        now = ANCHOR + timedelta(days=12, hours=1)
        report = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=False, now=now
        )
        self.assertFalse(report["gates"]["allowed"])
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["deleted_count"], 0)
        total = self.conn.execute("SELECT COUNT(*) FROM hotspot_event_clips").fetchone()[0]
        self.assertEqual(total, 40)

    def test_idempotent_rerun(self):
        cycle_minus1_start = ANCHOR + timedelta(days=-12)
        self._add_hook(created_at=cycle_minus1_start + timedelta(days=1))
        self._seed_qualified(ANCHOR, 40)
        now = ANCHOR + timedelta(days=12, hours=1)
        r1 = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=False, now=now
        )
        self.assertEqual(r1["deleted_count"], 1)
        r2 = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=False, now=now
        )
        self.assertEqual(r2["deleted_count"], 0)
        self.assertEqual(r2["status"], "deleted")  # 仍允许执行，只是无可删

    def test_dry_run_preview_lists_without_deleting(self):
        cycle_minus1_start = ANCHOR + timedelta(days=-12)
        hook_id = self._add_hook(created_at=cycle_minus1_start + timedelta(days=1))
        self._seed_qualified(ANCHOR, 40)
        now = ANCHOR + timedelta(days=12, hours=1)
        report = cycle.preview_or_run_cycle_cleanup(
            self.conn, static_dir=self.static, dry_run=True, now=now
        )
        self.assertEqual(report["status"], "preview")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["deleted_count"], 0)
        still = self.conn.execute(
            "SELECT COUNT(*) FROM hotspot_event_clips WHERE id=?", (hook_id,)
        ).fetchone()[0]
        self.assertEqual(still, 1)
        self.assertTrue(any(c["event_clip_id"] == hook_id for c in report["candidates"]))
        self.assertTrue(all(c.get("mother_preserved") for c in report["candidates"]))


if __name__ == "__main__":
    unittest.main()
