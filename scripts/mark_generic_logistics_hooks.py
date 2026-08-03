#!/usr/bin/env python3
"""Mark audited warehouse/ops Hooks as generic_logistics openers.

Usage:
  python3 scripts/mark_generic_logistics_hooks.py --dry-run
  python3 scripts/mark_generic_logistics_hooks.py --apply --max 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
import producible_topics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist hook_kind changes")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates only (default)")
    parser.add_argument("--max", type=int, default=30, help="Max hooks to mark")
    args = parser.parse_args()
    dry_run = not args.apply

    db.init_db()
    db.backfill_hotspot_event_logistics_scenes(limit=2000)
    marked = []
    for event in db.list_hotspot_event_clips():
        if len(marked) >= max(1, args.max):
            break
        if str(event.get("hook_kind") or "") == "generic_logistics":
            continue
        if str(event.get("review_status") or "") != "confirmed":
            continue
        if str(event.get("clip_status") or "") != "ready" or not event.get("clip_path"):
            continue
        if not producible_topics.is_generic_logistics_eligible(event):
            continue
        scenes = producible_topics.hook_logistics_scenes(event)
        # Prefer warehouse / last_mile / port / border for opener pool.
        if not set(scenes) & {"warehouse", "last_mile", "port", "border"}:
            continue
        row = {
            "id": event["id"],
            "title_zh": event.get("title_zh"),
            "scenes": scenes,
            "hotspot_id": event.get("hotspot_id"),
        }
        if not dry_run:
            db.update_hotspot_event_hook_kind(
                int(event["id"]),
                hook_kind="generic_logistics",
                logistics_scenes=scenes,
            )
        marked.append(row)
    print(json.dumps({"dry_run": dry_run, "count": len(marked), "hooks": marked}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
