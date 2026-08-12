"""12 天滚动周期的热点 Hook 派生素材清理。

与旧版按年龄（HOTSPOT_HOOK_RETENTION_DAYS=10）粗删不同：
- 以固定锚点 + CYCLE_DAYS 计算 cycle_id / [start,end)
- 只清理「上一个完整周期」的 Hook 派生（event clip / 缩略图），不删母片与来源
- 当前周期合格 timely Hook < MIN_CYCLE_QUALIFIED 时只报警、不删除
- 支持 dry-run 预览；删除幂等
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_CYCLE_DAYS = 12
DEFAULT_MIN_QUALIFIED = 40
DEFAULT_ANCHOR = "2026-08-01T00:00:00+00:00"


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def cycle_days() -> int:
    return _env_int("HOTSPOT_HOOK_CYCLE_DAYS", DEFAULT_CYCLE_DAYS, minimum=1, maximum=365)


def min_cycle_qualified() -> int:
    return _env_int("HOTSPOT_HOOK_MIN_CYCLE_QUALIFIED", DEFAULT_MIN_QUALIFIED, minimum=1)


def cycle_anchor() -> datetime:
    raw = (os.environ.get("HOTSPOT_HOOK_CYCLE_ANCHOR") or DEFAULT_ANCHOR).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.fromisoformat(DEFAULT_ANCHOR)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class HookCycle:
    cycle_id: int
    start_at: datetime
    end_at: datetime

    @property
    def label(self) -> str:
        return f"cycle-{self.cycle_id}:{self.start_at.date()}..{self.end_at.date()}"


def cycle_for(moment: datetime | str | None = None, *, days: int | None = None, anchor: datetime | None = None) -> HookCycle:
    """由时间戳计算所属周期（左闭右开）。"""
    now = as_utc(moment) or datetime.now(timezone.utc)
    days = max(1, int(days or cycle_days()))
    base = anchor or cycle_anchor()
    delta_days = (now - base).total_seconds() / 86400.0
    idx = int(delta_days // days)
    if delta_days < 0:
        # 锚点之前：仍映射到负周期，便于测试；清理时会跳过
        idx = int(delta_days // days)
    start = base + timedelta(days=idx * days)
    end = start + timedelta(days=days)
    return HookCycle(cycle_id=idx, start_at=start, end_at=end)


def previous_cycle(moment: datetime | str | None = None, *, days: int | None = None, anchor: datetime | None = None) -> HookCycle:
    current = cycle_for(moment, days=days, anchor=anchor)
    prev_id = current.cycle_id - 1
    days = max(1, int(days or cycle_days()))
    base = anchor or cycle_anchor()
    start = base + timedelta(days=prev_id * days)
    end = start + timedelta(days=days)
    return HookCycle(cycle_id=prev_id, start_at=start, end_at=end)


def latest_completed_cycle(moment: datetime | str | None = None, *, days: int | None = None, anchor: datetime | None = None) -> HookCycle | None:
    """返回 end_at <= now 的最近完整周期。"""
    now = as_utc(moment) or datetime.now(timezone.utc)
    current = cycle_for(now, days=days, anchor=anchor)
    if now >= current.end_at:
        return current
    return previous_cycle(now, days=days, anchor=anchor)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def count_qualified_timely_hooks(
    conn: sqlite3.Connection,
    cycle: HookCycle,
) -> int:
    """合格时效 Hook：timely_event + confirmed + ready，且创建落在周期内。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM hotspot_event_clips
        WHERE hook_kind='timely_event'
          AND review_status='confirmed'
          AND clip_status='ready'
          AND datetime(created_at) >= datetime(?)
          AND datetime(created_at) < datetime(?)
        """,
        (_iso(cycle.start_at), _iso(cycle.end_at)),
    ).fetchone()
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["c"])


def list_cycle_hook_derivatives(conn: sqlite3.Connection, cycle: HookCycle) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, asset_id, hotspot_id, clip_path, thumbnail_path, hook_kind,
               review_status, clip_status, created_at, locked
        FROM hotspot_event_clips
        WHERE datetime(created_at) >= datetime(?)
          AND datetime(created_at) < datetime(?)
        ORDER BY id
        """,
        (_iso(cycle.start_at), _iso(cycle.end_at)),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "asset_id": row[1], "hotspot_id": row[2],
            "clip_path": row[3], "thumbnail_path": row[4], "hook_kind": row[5],
            "review_status": row[6], "clip_status": row[7], "created_at": row[8],
            "locked": row[9] if len(row) > 9 else 0,
        }
        out.append(item)
    return out


def _project_reference_reasons(conn: sqlite3.Connection, event_clip_id: int, asset_id: int | None) -> list[str]:
    reasons: list[str] = []
    # 活动项目 source_snapshot 引用 event clip
    try:
        marker = f'"event_clip_id": {int(event_clip_id)}'
        marker2 = f'"event_clip_id":{int(event_clip_id)}'
        hit = conn.execute(
            """
            SELECT 1 FROM video_projects
            WHERE status IN ('draft','active','rendering','queued','needs_review')
              AND (source_snapshot LIKE ? OR source_snapshot LIKE ?)
            LIMIT 1
            """,
            (f"%{marker}%", f"%{marker2}%"),
        ).fetchone()
        if hit:
            reasons.append("active_video_project")
    except sqlite3.Error:
        pass
    if asset_id:
        try:
            spaced = f'%"asset_id": {int(asset_id)}%'
            compact = f'%"asset_id":{int(asset_id)}%'
            hit = conn.execute(
                """
                SELECT 1 FROM video_generation_jobs j
                JOIN video_project_revisions r ON r.id=j.revision_id
                WHERE j.status IN ('pending','running','needs_review','cancel_requested')
                  AND (r.payload LIKE ? OR r.payload LIKE ?)
                LIMIT 1
                """,
                (spaced, compact),
            ).fetchone()
            if hit:
                reasons.append("active_video_generation")
        except sqlite3.Error:
            pass
    return reasons


def _pending_cycle_work(conn: sqlite3.Connection, cycle: HookCycle) -> list[str]:
    pending: list[str] = []
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM hotspot_media
            WHERE download_status IN ('downloading','queued')
               OR processing_status IN ('processing','analyzing','curating','visual_review')
            """
        ).fetchone()
        count = int(row[0] if not isinstance(row, sqlite3.Row) else row["c"])
        if count:
            pending.append(f"unfinished_media_tasks:{count}")
    except sqlite3.Error:
        pass
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM hotspot_event_clips
            WHERE review_status IN ('pending','visual_pending','text_pending')
              AND datetime(created_at) >= datetime(?)
              AND datetime(created_at) < datetime(?)
            """,
            (_iso(cycle.start_at), _iso(cycle.end_at)),
        ).fetchone()
        count = int(row[0] if not isinstance(row, sqlite3.Row) else row["c"])
        if count:
            pending.append(f"unfinished_hook_reviews:{count}")
    except sqlite3.Error:
        pass
    return pending


def _batch_records_ok(conn: sqlite3.Connection, cycle: HookCycle) -> tuple[bool, str]:
    """若存在 hook_production_batches 表则要求 4 条；否则记为 skipped_optional。"""
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM hook_production_batches
            WHERE datetime(started_at) >= datetime(?)
              AND datetime(started_at) < datetime(?)
            """,
            (_iso(cycle.start_at), _iso(cycle.end_at)),
        ).fetchone()
        count = int(row[0] if not isinstance(row, sqlite3.Row) else row["c"])
        if count < 4:
            return False, f"batch_records={count}<4"
        return True, f"batch_records={count}"
    except sqlite3.Error:
        return True, "batch_records=optional_missing"


def _evidence_report_ok(conn: sqlite3.Connection, cycle: HookCycle) -> tuple[bool, str]:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM hook_cycle_evidence_reports
            WHERE cycle_id=? AND status='ready'
            """,
            (cycle.cycle_id,),
        ).fetchone()
        count = int(row[0] if not isinstance(row, sqlite3.Row) else row["c"])
        if count < 1:
            return False, "evidence_report_missing"
        return True, "evidence_report=ready"
    except sqlite3.Error:
        return True, "evidence_report=optional_missing"


def evaluate_cleanup_gates(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    require_optional_ops_records: bool = False,
) -> dict[str, Any]:
    """评估是否允许删除「已完成周期的上一个周期」。"""
    moment = as_utc(now) or datetime.now(timezone.utc)
    completed = latest_completed_cycle(moment)
    alerts: list[str] = []
    if completed is None:
        return {
            "allowed": False,
            "reason": "no_completed_cycle",
            "now": _iso(moment),
            "completed_cycle": None,
            "delete_cycle": None,
            "qualified_count": 0,
            "min_qualified": min_cycle_qualified(),
            "alerts": ["no_completed_cycle"],
        }

    delete_cycle = previous_cycle(completed.start_at)
    qualified = count_qualified_timely_hooks(conn, completed)
    min_q = min_cycle_qualified()
    pending = _pending_cycle_work(conn, completed)
    batches_ok, batches_detail = _batch_records_ok(conn, completed)
    evidence_ok, evidence_detail = _evidence_report_ok(conn, completed)

    if require_optional_ops_records:
        if not batches_ok:
            alerts.append(batches_detail)
        if not evidence_ok:
            alerts.append(evidence_detail)
    if pending:
        alerts.extend(pending)
    if qualified < min_q:
        alerts.append(f"qualified_timely={qualified}<{min_q}")

    allowed = (
        moment >= completed.end_at
        and qualified >= min_q
        and not pending
        and (batches_ok if require_optional_ops_records else True)
        and (evidence_ok if require_optional_ops_records else True)
        and delete_cycle is not None
    )
    if not allowed and not alerts:
        alerts.append("gate_blocked")

    return {
        "allowed": allowed,
        "reason": "ok" if allowed else "gate_blocked",
        "now": _iso(moment),
        "completed_cycle": {
            "cycle_id": completed.cycle_id,
            "start_at": _iso(completed.start_at),
            "end_at": _iso(completed.end_at),
            "label": completed.label,
        },
        "delete_cycle": None
        if delete_cycle is None
        else {
            "cycle_id": delete_cycle.cycle_id,
            "start_at": _iso(delete_cycle.start_at),
            "end_at": _iso(delete_cycle.end_at),
            "label": delete_cycle.label,
        },
        "qualified_count": qualified,
        "min_qualified": min_q,
        "batches": batches_detail,
        "evidence": evidence_detail,
        "pending": pending,
        "alerts": alerts,
    }


def _safe_unlink(static_root: Path, rel: str | None) -> int:
    if not rel:
        return 0
    raw = str(rel).strip()
    if not raw:
        return 0
    if raw.startswith("/static/"):
        raw = raw[len("/static/") :]
    candidate = (static_root / raw).resolve()
    try:
        candidate.relative_to(static_root.resolve())
    except ValueError:
        return 0
    if not candidate.is_file():
        return 0
    size = candidate.stat().st_size
    candidate.unlink()
    return size


def preview_or_run_cycle_cleanup(
    conn: sqlite3.Connection,
    *,
    static_dir: str | Path,
    dry_run: bool = True,
    now: datetime | None = None,
    require_optional_ops_records: bool = False,
    delete_event_clip: Callable[[sqlite3.Connection, int], dict | None] | None = None,
) -> dict[str, Any]:
    """预览或执行：删除已完成周期的「上一周期」Hook 派生。

    门禁失败时不删除，返回 alerts。
    """
    static_root = Path(static_dir).resolve()
    gates = evaluate_cleanup_gates(
        conn, now=now, require_optional_ops_records=require_optional_ops_records
    )
    report: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "gates": gates,
        "candidates": [],
        "deleted": [],
        "skipped": [],
        "released_bytes": 0,
        "candidate_count": 0,
        "deleted_count": 0,
        "skipped_count": 0,
    }
    if not gates["allowed"] or not gates.get("delete_cycle"):
        report["status"] = "blocked"
        logger.warning("Hook 周期清理被门禁拦截: %s", gates.get("alerts"))
        return report

    delete_meta = gates["delete_cycle"]
    target = HookCycle(
        cycle_id=int(delete_meta["cycle_id"]),
        start_at=as_utc(delete_meta["start_at"]),
        end_at=as_utc(delete_meta["end_at"]),
    )
    current = cycle_for(now)
    hooks = list_cycle_hook_derivatives(conn, target)
    for hook in hooks:
        hook_id = int(hook["id"])
        asset_id = hook.get("asset_id")
        asset_id_i = int(asset_id) if asset_id not in (None, "") else None
        # 永不删除当前周期（双保险）
        created = as_utc(hook.get("created_at"))
        if created and current.start_at <= created < current.end_at:
            report["skipped"].append({"event_clip_id": hook_id, "reason": "current_cycle"})
            continue
        if int(hook.get("locked") or 0) == 1:
            report["skipped"].append({"event_clip_id": hook_id, "reason": "manual_lock"})
            continue
        refs = _project_reference_reasons(conn, hook_id, asset_id_i)
        if refs:
            report["skipped"].append(
                {"event_clip_id": hook_id, "reason": "active_reference", "references": refs}
            )
            continue
        candidate = {
            "event_clip_id": hook_id,
            "asset_id": asset_id_i,
            "hotspot_id": hook.get("hotspot_id"),
            "clip_path": hook.get("clip_path"),
            "thumbnail_path": hook.get("thumbnail_path"),
            "cycle_id": target.cycle_id,
            "mother_preserved": True,
        }
        report["candidates"].append(candidate)
        if dry_run:
            continue
        # 删除派生文件 + DB 行；母片保留
        released = 0
        released += _safe_unlink(static_root, hook.get("clip_path"))
        released += _safe_unlink(static_root, hook.get("thumbnail_path"))
        if delete_event_clip is not None:
            delete_event_clip(conn, hook_id)
        else:
            conn.execute("DELETE FROM hotspot_event_segment_links WHERE event_clip_id=?", (hook_id,))
            conn.execute("DELETE FROM hotspot_event_clips WHERE id=?", (hook_id,))
        report["deleted"].append({**candidate, "released_bytes": released})
        report["released_bytes"] += released

    report["candidate_count"] = len(report["candidates"])
    report["deleted_count"] = len(report["deleted"])
    report["skipped_count"] = len(report["skipped"])
    report["status"] = "preview" if dry_run else "deleted"
    report["delete_cycle"] = delete_meta
    return report


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }


def cleanup_enabled() -> bool:
    return _truthy_env("HOTSPOT_HOOK_CLEANUP_ENABLED", "0")


def require_ops_records() -> bool:
    """生产应开启：强制 4 批次记录 + 证据报告门禁。"""
    return _truthy_env("HOTSPOT_HOOK_REQUIRE_OPS_RECORDS", "0")


def run_scheduled_hook_cycle_cleanup(static_dir: str | Path, *, dry_run: bool | None = None) -> dict[str, Any]:
    """供调度器调用：默认尊重 HOTSPOT_HOOK_CLEANUP_ENABLED；门禁失败只报警。"""
    import database as db

    if dry_run is None:
        dry_run = not cleanup_enabled()
    with db.get_conn() as conn:
        report = preview_or_run_cycle_cleanup(
            conn,
            static_dir=static_dir,
            dry_run=dry_run,
            require_optional_ops_records=require_ops_records(),
        )
        # 审计：预览与实删都记，但不把大列表塞进 detail
        summary = {
            k: report.get(k)
            for k in (
                "status",
                "dry_run",
                "candidate_count",
                "deleted_count",
                "skipped_count",
                "released_bytes",
                "delete_cycle",
            )
        }
        summary["alerts"] = report.get("gates", {}).get("alerts", [])
        summary["qualified_count"] = report.get("gates", {}).get("qualified_count")
        action = (
            "hotspot_hook_cycle_cleanup_preview"
            if report.get("dry_run")
            else "hotspot_hook_cycle_cleanup"
        )
        try:
            db.add_audit_log(None, "system", action, detail=json.dumps(summary, ensure_ascii=False))
        except Exception:
            logger.exception("写入 Hook 周期清理审计失败")
        return report
