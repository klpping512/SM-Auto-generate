"""List strict, renderable same-event Hook pairs without changing the library.

This is an operator audit for C-end video coverage.  It deliberately shares the
API eligibility rules instead of treating downloaded media or a single Hook as
video-ready evidence.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
from app import _is_confirmed_renderable_hotspot_hook, _is_same_confirmed_hotspot_event


def eligible_hook_pairs(events: list[dict]) -> list[dict]:
    """Return one usable non-overlapping pair for each factual source event."""
    groups: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for event in events:
        if not _is_confirmed_renderable_hotspot_hook(event):
            continue
        identity = str((event.get("evidence") or {}).get("event_identity") or "").strip()
        if not identity:
            continue
        groups[(int(event["hotspot_id"]), int(event["asset_id"]), identity.casefold())].append(event)

    pairs = []
    for (hotspot_id, asset_id, identity), candidates in groups.items():
        ordered = sorted(candidates, key=lambda item: (int(item["start_ms"]), int(item["id"])))
        chosen = None
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                if int(second["start_ms"]) >= int(first["end_ms"]):
                    chosen = [first, second]
                    break
            if chosen:
                break
        if not chosen or not _is_same_confirmed_hotspot_event(chosen):
            continue
        evidence = chosen[0].get("evidence") or {}
        pairs.append({
            "hotspot_id": hotspot_id,
            "asset_id": asset_id,
            "event_identity": identity,
            "hook_ids": [int(item["id"]) for item in chosen],
            "title": str(chosen[0].get("title_zh") or "")[:80],
            "what_happened": str(evidence.get("what_happened") or "")[:180],
            "logistics_question": str(evidence.get("logistics_question") or "")[:180],
        })
    return sorted(pairs, key=lambda item: (item["hotspot_id"], item["asset_id"], item["hook_ids"]))


def main() -> int:
    pairs = eligible_hook_pairs(db.list_hotspot_event_clips())
    print(json.dumps({
        "eligible_pair_count": len(pairs),
        "pairs": pairs,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
