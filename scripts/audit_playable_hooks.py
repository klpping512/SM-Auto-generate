#!/usr/bin/env python3
"""Audit playable Hooks before enabling generic_logistics auto-match.

Operations checklist (production):
1. Backup the SQLite DB and credentials.
2. Stop the service (do not rely on hot _ensure_column while serving traffic).
3. Start once so schema_migrations / hook_kind / logistics_scenes land.
4. Dry-run this script, manually review candidates, then --apply.
5. Restart and verify chat evergreen matching.

Usage:
  python3 scripts/audit_playable_hooks.py --dry-run
  python3 scripts/audit_playable_hooks.py --apply --max 69
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
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max", type=int, default=69)
    args = parser.parse_args()
    dry_run = not args.apply

    db.init_db()
    db.backfill_hotspot_event_logistics_scenes(limit=5000)
    report = {"playable": [], "generic_eligible": [], "disallowed": [], "timely_only": []}
    for event in db.list_hotspot_event_clips():
        if str(event.get("review_status") or "") != "confirmed":
            continue
        if str(event.get("clip_status") or "") != "ready" or not event.get("clip_path"):
            continue
        row = {
            "id": event["id"],
            "title_zh": event.get("title_zh"),
            "hook_kind": event.get("hook_kind") or "timely_event",
            "scenes": producible_topics.hook_logistics_scenes(event),
            "hotspot_id": event.get("hotspot_id"),
        }
        report["playable"].append(row)
        if not producible_topics.is_generic_logistics_eligible(event):
            report["disallowed"].append(row)
            continue
        if set(row["scenes"]) & {"warehouse", "last_mile", "port", "border", "linehaul"}:
            report["generic_eligible"].append(row)
            if not dry_run and row["hook_kind"] != "generic_logistics":
                if len([item for item in report["generic_eligible"] if item.get("applied")]) >= args.max:
                    continue
                db.update_hotspot_event_hook_kind(
                    int(event["id"]),
                    hook_kind="generic_logistics",
                    logistics_scenes=row["scenes"],
                )
                row["applied"] = True
        else:
            report["timely_only"].append(row)

    summary = {
        "dry_run": dry_run,
        "playable_count": len(report["playable"]),
        "generic_eligible_count": len(report["generic_eligible"]),
        "disallowed_count": len(report["disallowed"]),
        "timely_only_count": len(report["timely_only"]),
        "hooks": report,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
