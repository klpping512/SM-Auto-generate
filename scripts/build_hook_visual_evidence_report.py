"""Build a three-frame contact sheet report for qualified timely Hooks."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from hotspot_hook_visual_audit import compute_frame_offsets_ms
from video_quality.frame_extractor import extract_at_timestamps


def _safe_name(value: object) -> str:
    text = "".join(ch if str(ch).isalnum() or ch in "-_" else "_" for ch in str(value or "hook"))
    return text[:48] or "hook"


def build_report(
    output_dir: Path,
    *,
    static_root: Path,
    qualified_only: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for event in db.list_hotspot_event_clips(limit=5000):
        if str(event.get("hook_kind") or "timely_event") != "timely_event":
            continue
        evidence = event.get("evidence") or {}
        visual = evidence.get("visual_audit") or {}
        text_audit = evidence.get("text_audit") or {}
        if qualified_only:
            if str(event.get("review_status") or "") != "confirmed":
                continue
            if str(event.get("clip_status") or "") != "ready":
                continue
            if str(visual.get("status") or "") != "accepted":
                continue
            if str(text_audit.get("status") or "") != "accepted":
                continue
        asset = db.get_asset(int(event["asset_id"])) if event.get("asset_id") else None
        if not asset or not asset.get("filepath"):
            continue
        video_path = static_root / asset["filepath"]
        if not video_path.is_file():
            continue
        offsets = visual.get("frame_offsets_ms") or compute_frame_offsets_ms(
            int(event.get("start_ms") or 0), int(event.get("end_ms") or 0),
        )
        if len(offsets) < 3:
            continue
        work = frames_dir / f"hook-{int(event['id'])}"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        extracted = extract_at_timestamps(
            video_path, work, [ms / 1000.0 for ms in offsets[:3]],
            resolution=512, max_frames=3, timeout=90,
        )
        frame_paths = [item.get("path") for item in extracted if item.get("path")]
        hotspot = db.get_hotspot(int(event["hotspot_id"])) if event.get("hotspot_id") else {}
        item = {
            "hook_id": int(event["id"]),
            "asset_id": int(event["asset_id"]),
            "hotspot_id": int(event.get("hotspot_id") or 0),
            "title_zh": event.get("title_zh"),
            "what_happened": evidence.get("what_happened"),
            "start_ms": event.get("start_ms"),
            "end_ms": event.get("end_ms"),
            "duration_ms": int(event.get("end_ms") or 0) - int(event.get("start_ms") or 0),
            "scene_type": visual.get("scene_type"),
            "frame_offsets_ms": offsets[:3],
            "frame_sha256": visual.get("frame_sha256") or [],
            "visual_audit_status": visual.get("status"),
            "text_audit_status": text_audit.get("status"),
            "source_published_at": (hotspot or {}).get("published_at"),
            "frame_paths": frame_paths,
            "contact_label": _safe_name(event.get("title_zh")),
        }
        manifest.append(item)
    report_path = output_dir / "manifest.json"
    report_path.write_text(json.dumps({
        "count": len(manifest),
        "qualified_only": qualified_only,
        "hooks": manifest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output_dir), "count": len(manifest), "manifest": str(report_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-only", action="store_true", default=True)
    parser.add_argument("--include-unqualified", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--static-root", default=str(ROOT / "static"))
    args = parser.parse_args()
    db.init_db()
    result = build_report(
        Path(args.output),
        static_root=Path(args.static_root),
        qualified_only=not args.include_unqualified,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
