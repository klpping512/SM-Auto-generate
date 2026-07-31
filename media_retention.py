"""Low-disk media lifecycle rules for hotspot source files and generated outputs."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import database as db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retention_days(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _safe_media_path(static_dir: Path, value: str) -> Path | None:
    root = Path(static_dir).resolve()
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("/static/"):
        raw = raw[len("/static/"):]
    candidate = Path(raw)
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if target == root or root not in target.parents:
        return None
    return target


def _asset_is_due(asset: dict, now: datetime, retention_days: int) -> bool:
    explicit = _as_utc(asset.get("purge_after"))
    if explicit:
        return explicit <= now
    base = _as_utc(asset.get("last_used_at")) or _as_utc(asset.get("created_at"))
    return bool(base and base + timedelta(days=retention_days) <= now)


def _cleanup(static_dir: Path, dry_run: bool, now: datetime | None = None) -> dict:
    current = (now or _utc_now()).astimezone(timezone.utc)
    static_root = Path(static_dir).resolve()
    static_root.mkdir(parents=True, exist_ok=True)
    archived_count = db.archive_stale_hotspot_media(30)
    retention_days = _retention_days("HOTSPOT_SOURCE_RETENTION_DAYS", 7)
    candidates: list[dict] = []
    output_candidates: list[dict] = []
    skipped: list[dict] = []
    deleted: list[dict] = []
    output_deleted: list[dict] = []
    estimated_bytes = 0
    released_bytes = 0

    for asset in db.list_retention_asset_candidates():
        if asset.get("pinned_at"):
            skipped.append({"asset_id": asset["id"], "reason": "pinned"})
            continue
        if not _asset_is_due(asset, current, retention_days):
            continue
        target = _safe_media_path(static_root, asset.get("filepath") or "")
        if target is None:
            skipped.append({"asset_id": asset["id"], "reason": "unsafe_path"})
            continue
        active_references = db.asset_active_reference_reasons(asset["id"])
        if active_references:
            skipped.append({
                "asset_id": asset["id"],
                "reason": "active_reference",
                "references": active_references,
            })
            continue
        size = target.stat().st_size if target.is_file() else 0
        estimated_bytes += size
        item = {
            "asset_id": asset["id"],
            "name": asset.get("name") or "",
            "path": str(target),
            "size_bytes": size,
            "reason": f"hotspot_source_older_than_{retention_days}_days",
        }
        candidates.append(item)
        if dry_run:
            continue
        if target.is_file():
            target.unlink()
            released_bytes += size
        db.mark_asset_file_purged(asset["id"], current.isoformat())
        deleted.append(item)

    output_days = _retention_days("FINAL_VIDEO_RETENTION_DAYS", 30)
    for job in db.list_expired_video_outputs(output_days):
        target = _safe_media_path(static_root, job.get("output_path") or "")
        if target is None:
            skipped.append({"job_id": job["id"], "reason": "unsafe_output_path"})
            continue
        size = target.stat().st_size if target.is_file() else 0
        estimated_bytes += size
        item = {
            "job_id": job["id"],
            "path": str(target),
            "size_bytes": size,
            "reason": f"final_output_older_than_{output_days}_days",
        }
        output_candidates.append(item)
        if dry_run:
            continue
        if target.is_file():
            target.unlink()
            released_bytes += size
        db.mark_video_output_purged(job["id"], current.isoformat())
        output_deleted.append(item)

    report = {
        "dry_run": bool(dry_run),
        "archived_count": archived_count,
        "candidate_count": len(candidates),
        "deleted_count": len(deleted),
        "output_candidate_count": len(output_candidates),
        "output_deleted_count": len(output_deleted),
        "skipped_count": len(skipped),
        "estimated_bytes": estimated_bytes,
        "released_bytes": released_bytes,
        "candidates": candidates,
        "deleted": deleted,
        "output_candidates": output_candidates,
        "output_deleted": output_deleted,
        "skipped": skipped,
        "ran_at": current.isoformat(),
    }
    db.add_audit_log(
        None,
        "system",
        "media_retention_dry_run" if dry_run else "media_retention_cleanup",
        detail=json.dumps({
            "archived_count": archived_count,
            "candidate_count": len(candidates),
            "deleted_count": len(deleted),
            "output_candidate_count": len(output_candidates),
            "output_deleted_count": len(output_deleted),
            "skipped_count": len(skipped),
            "estimated_bytes": estimated_bytes,
            "released_bytes": released_bytes,
        }, ensure_ascii=False),
    )
    return report


def preview_cleanup(static_dir: Path, now: datetime | None = None) -> dict:
    return _cleanup(static_dir=static_dir, dry_run=True, now=now)


def run_cleanup(
    static_dir: Path,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict:
    return _cleanup(static_dir=static_dir, dry_run=dry_run, now=now)


def cleanup_hotspot_hook_library(
    static_dir: Path,
    *,
    retention_days: int = 10,
    protect_days: int = 3,
    dry_run: bool = False,
) -> dict:
    """滚动轮换热点 Hook 库，且绝不触碰最近三天入库的素材。

    母片、导出的 Hook 代理和分析索引作为同一可回收单元删除；热点事实、信源、
    Buffalo 原本素材与已引用的进行中项目均被保留。
    """
    root = Path(static_dir).resolve()
    retention_days = max(1, int(retention_days))
    protect_days = max(0, min(int(protect_days), retention_days))
    candidates, skipped, deleted, released_bytes = [], [], [], 0
    for media in db.list_hotspot_hook_cleanup_candidates(retention_days, protect_days):
        media_id = int(media["id"])
        asset_id = int(media.get("asset_id") or 0)
        if db.hotspot_library_media_is_busy(media_id):
            skipped.append({"media_id": media_id, "reason": "busy"})
            continue
        references = db.asset_active_reference_reasons(asset_id) if asset_id else []
        if references:
            skipped.append({"media_id": media_id, "reason": "active_reference", "references": references})
            continue
        # The per-media deletion helper already handles shared mother assets correctly.
        candidate = {"media_id": media_id, "asset_id": asset_id, "reason": f"older_than_{retention_days}_days"}
        candidates.append(candidate)
        if dry_run:
            continue
        result = db.delete_hotspot_library(media_id=media_id)
        if result is None:
            continue
        for value in result.pop("file_paths", []):
            target = _safe_media_path(root, value)
            if target is None or not target.is_file():
                continue
            size = target.stat().st_size
            target.unlink()
            released_bytes += size
        deleted.append({"media_id": media_id, "asset_id": asset_id, **result})
    report = {
        "retention_days": retention_days,
        "protect_days": protect_days,
        "candidate_count": len(candidates),
        "deleted_count": len(deleted),
        "skipped_count": len(skipped),
        "candidates": candidates,
        "deleted": deleted,
        "skipped": skipped,
        "released_bytes": released_bytes,
    }
    db.add_audit_log(
        None, "system", "hotspot_hook_library_cleanup_preview" if dry_run else "hotspot_hook_library_cleanup",
        detail=json.dumps({key: value for key, value in report.items() if key not in {"candidates", "deleted", "skipped"}}, ensure_ascii=False),
    )
    return report


def disk_guard(static_dir: Path) -> dict:
    usage = shutil.disk_usage(Path(static_dir))
    free_percent = (usage.free / usage.total * 100) if usage.total else 0
    try:
        stop_percent = float(os.environ.get("MEDIA_DISK_STOP_PERCENT", "10"))
        degrade_percent = float(os.environ.get("MEDIA_DISK_DEGRADE_PERCENT", "20"))
        gc_percent = float(os.environ.get("MEDIA_DISK_GC_PERCENT", "30"))
        reserve_bytes = max(0, int(os.environ.get("MEDIA_DISK_RESERVE_BYTES", "0")))
    except ValueError:
        stop_percent, degrade_percent, gc_percent, reserve_bytes = 10.0, 20.0, 30.0, 0
    blocked = free_percent < stop_percent or usage.free < reserve_bytes
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_percent": round(free_percent, 2),
        "gc_required": free_percent < gc_percent,
        "degraded": free_percent < degrade_percent,
        "warning": free_percent < gc_percent,
        "blocked": blocked,
        "reserve_bytes": reserve_bytes,
        "state": "stopped" if blocked else ("degraded" if free_percent < degrade_percent else ("cleanup" if free_percent < gc_percent else "normal")),
    }


def storage_summary(static_dir: Path) -> dict:
    """Observable first-stage storage view without coupling business records to paths."""
    root = Path(static_dir).resolve()
    categories = {
        "owned_assets": root / "assets" / "library",
        "hotspot_cache": root / "assets" / "hotspot",
        "outputs": root / "uploads",
        "temporary": root / "tmp",
        "model_cache": root / "model-cache",
    }
    result = {}
    for name, directory in categories.items():
        total = 0
        count = 0
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size
                    count += 1
        result[name] = {"bytes": total, "files": count}
    return {"capacity": disk_guard(root), "categories": result}
