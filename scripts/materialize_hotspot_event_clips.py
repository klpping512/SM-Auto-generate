#!/usr/bin/env python3
"""为既有热点事件生成低清独立预览片段，不改动母片。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
import hotspot_event_media


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int)
    args = parser.parse_args()
    db.init_db()
    events = db.list_hotspot_event_clips(asset_id=args.asset_id)
    done = []
    for event in events:
        asset = db.get_asset(int(event["asset_id"]))
        if not asset or asset.get("file_type") != "video":
            continue
        done.append(hotspot_event_media.materialize_event_clip(Path(__file__).resolve().parents[1] / "static", asset, event))
    print(json.dumps({"count": len(done), "events": [{"id": item["id"], "clip_path": item.get("clip_path"), "duration_ms": item.get("duration_ms")} for item in done]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
