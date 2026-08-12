"""Read-only audit of qualified timely_event Hooks against batch-22 hard gates."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db

CST = timezone(timedelta(hours=8))
MIN_MS = 4_000
MAX_MS = 14_000


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.startswith("1970-"):
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(text.replace("Z", "+0000"), fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(CST)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(CST)
    except ValueError:
        return None


def _ffprobe(path: Path) -> dict:
    try:
        raw = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type:format=duration",
                "-of", "json", str(path),
            ],
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        duration = float((payload.get("format") or {}).get("duration") or 0)
        streams = payload.get("streams") or []
        return {"ok": bool(streams) and duration > 0, "duration": duration}
    except Exception as exc:
        return {"ok": False, "duration": 0.0, "error": str(exc)[:120]}


def _scene_bucket(event: dict) -> str:
    evidence = event.get("evidence") or {}
    visual = evidence.get("visual_audit") or {}
    scene = str(visual.get("scene_type") or "").strip().lower()
    if scene in {"port", "border", "road", "warehouse", "delivery", "agriculture", "other"}:
        return scene
    scenes = event.get("logistics_scenes") or []
    if isinstance(scenes, str):
        try:
            scenes = json.loads(scenes)
        except Exception:
            scenes = []
    joined = " ".join(str(item) for item in scenes).lower()
    text = f"{event.get('title_zh') or ''} {evidence.get('what_happened') or ''} {joined}".lower()
    mapping = [
        ("port", ("port", "港口", "码头", "集装箱", "吊机")),
        ("border", ("border", "边境", "清关", "海关", "口岸")),
        ("road", ("road", "linehaul", "道路", "干线", "中断", "卡车", "公路")),
        ("warehouse", ("warehouse", "仓储", "仓库", "货架", "设施")),
        ("delivery", ("delivery", "last_mile", "配送", "末端", "派送")),
        ("agriculture", ("agriculture", "farm", "牧场", "农场", "牛群", "畜牧", "农田")),
    ]
    for name, keys in mapping:
        if any(key in text for key in keys):
            return name
    return "other"


def audit(static_root: Path, *, target: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(CST)
    events = db.list_hotspot_event_clips(limit=5000)
    generic_total = 0
    timely_rows = []
    failures = {
        "missing_visual_audit": 0,
        "missing_text_audit": 0,
        "missing_or_unplayable_files": 0,
        "ffprobe_failed": 0,
        "duration_out_of_range": 0,
        "unauthorized_sources": 0,
        "missing_published_at": 0,
        "older_than_30_days": 0,
        "title_logo_anchor_flags": 0,
        "overlap_or_duplicate": 0,
        "review_or_clip_not_ready": 0,
    }
    seen_ranges: dict[int, list[tuple[int, int, int]]] = {}
    seen_frame_sets: set[tuple[str, ...]] = set()
    seen_sha: set[str] = set()

    for event in events:
        hook_kind = str(event.get("hook_kind") or "timely_event")
        if hook_kind == "generic_logistics":
            generic_total += 1
            continue
        if hook_kind != "timely_event":
            continue
        # Only claimed-confirmed timely hooks can create false-positive risk.
        if str(event.get("review_status") or "") != "confirmed":
            continue
        evidence = event.get("evidence") or {}
        visual = evidence.get("visual_audit") or {}
        text_audit = evidence.get("text_audit") or {}
        asset = db.get_asset(int(event["asset_id"])) if event.get("asset_id") else None
        hotspot = db.get_hotspot(int(event["hotspot_id"])) if event.get("hotspot_id") else None
        media_rows = [
            item for item in db.list_hotspot_media(lifecycle_status="active", limit=500)
            if item.get("asset_id") == event.get("asset_id")
        ]
        media = media_rows[0] if media_rows else None
        published = _parse_dt(
            (media or {}).get("published_at")
            or (hotspot or {}).get("published_at")
            or event.get("parent_published_at")
        )
        clip_path = event.get("clip_path")
        abs_clip = (static_root / clip_path) if clip_path else None
        duration_ms = int(event.get("end_ms") or 0) - int(event.get("start_ms") or 0)
        row_fail = False

        if str(event.get("clip_status") or "") != "ready":
            failures["review_or_clip_not_ready"] += 1
            row_fail = True
        if str(visual.get("status") or "") != "accepted":
            failures["missing_visual_audit"] += 1
            row_fail = True
        if str(text_audit.get("status") or "") != "accepted":
            failures["missing_text_audit"] += 1
            row_fail = True
        if any(bool(visual.get(flag)) for flag in (
            "is_title_or_logo_card", "is_anchor_or_studio", "is_map_or_infographic",
        )):
            failures["title_logo_anchor_flags"] += 1
            row_fail = True
        if not abs_clip or not abs_clip.is_file():
            failures["missing_or_unplayable_files"] += 1
            row_fail = True
            probe = {"ok": False, "duration": 0.0}
        else:
            probe = _ffprobe(abs_clip)
            if not probe.get("ok"):
                failures["ffprobe_failed"] += 1
                failures["missing_or_unplayable_files"] += 1
                row_fail = True
        if not (MIN_MS <= duration_ms <= MAX_MS):
            failures["duration_out_of_range"] += 1
            row_fail = True
        if media and media.get("authorization_status") != "authorized":
            failures["unauthorized_sources"] += 1
            row_fail = True
        if published is None:
            failures["missing_published_at"] += 1
            row_fail = True
            age_days = None
        else:
            age_days = (now - published).total_seconds() / 86400.0
            if age_days > 30:
                failures["older_than_30_days"] += 1
                row_fail = True

        start_ms = int(event.get("start_ms") or 0)
        end_ms = int(event.get("end_ms") or 0)
        asset_id = int(event.get("asset_id") or 0)
        overlaps = False
        for other_start, other_end, other_id in seen_ranges.get(asset_id, []):
            if start_ms < other_end and end_ms > other_start:
                overlaps = True
                break
        frame_set = tuple(str(item) for item in (visual.get("frame_sha256") or []) if item)
        source_sha = str((asset or {}).get("sha256") or "")
        if overlaps or (frame_set and frame_set in seen_frame_sets):
            failures["overlap_or_duplicate"] += 1
            row_fail = True
        seen_ranges.setdefault(asset_id, []).append((start_ms, end_ms, int(event["id"])))
        if frame_set:
            seen_frame_sets.add(frame_set)
        if source_sha:
            seen_sha.add(source_sha)

        if row_fail:
            continue
        timely_rows.append({
            "id": int(event["id"]),
            "asset_id": asset_id,
            "hotspot_id": int(event.get("hotspot_id") or 0),
            "title_zh": event.get("title_zh"),
            "duration_ms": duration_ms,
            "age_days": round(age_days, 2) if age_days is not None else None,
            "scene": _scene_bucket(event),
            "source_sha": source_sha,
            "frame_sha256": list(frame_set),
            "visual_audit_status": visual.get("status"),
            "text_audit_status": text_audit.get("status"),
            "published_at": published.isoformat() if published else None,
        })

    fresh_0_10 = sum(1 for row in timely_rows if row["age_days"] is not None and row["age_days"] <= 10)
    mid_11_30 = sum(1 for row in timely_rows if row["age_days"] is not None and 10 < row["age_days"] <= 30)
    scene_counts: dict[str, int] = {}
    for row in timely_rows:
        scene_counts[row["scene"]] = scene_counts.get(row["scene"], 0) + 1

    hard_ok = (
        len(timely_rows) >= target
        and fresh_0_10 >= min(70, target)
        and mid_11_30 <= 30
        and failures["missing_visual_audit"] == 0
        and failures["missing_text_audit"] == 0
        and failures["missing_or_unplayable_files"] == 0
        and failures["ffprobe_failed"] == 0
        and failures["duration_out_of_range"] == 0
        and failures["unauthorized_sources"] == 0
        and failures["missing_published_at"] == 0
        and failures["older_than_30_days"] == 0
        and failures["title_logo_anchor_flags"] == 0
        and failures["overlap_or_duplicate"] == 0
        and failures["review_or_clip_not_ready"] == 0
    )
    # Hard gates above already require zero failures among scanned timely hooks;
    # qualified list only contains survivors.
    return {
        "qualified_timely_total": len(timely_rows),
        "fresh_0_10_days": fresh_0_10,
        "mid_11_30_days": mid_11_30,
        "distinct_assets": len({row["asset_id"] for row in timely_rows}),
        "distinct_hotspots": len({row["hotspot_id"] for row in timely_rows if row["hotspot_id"]}),
        "distinct_source_sha": len({row["source_sha"] for row in timely_rows if row["source_sha"]}),
        "scene_distribution": scene_counts,
        "generic_total": generic_total,
        "failures": failures,
        "hard_gates_passed": hard_ok and all(value == 0 for value in failures.values()),
        "target": target,
        "qualified_hooks": timely_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--static-root", default=str(ROOT / "static"))
    args = parser.parse_args()
    db.init_db()
    report = audit(Path(args.static_root), target=int(args.target))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("hard_gates_passed") and report["qualified_timely_total"] >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
