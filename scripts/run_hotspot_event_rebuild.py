#!/usr/bin/env python3
"""重建热点资产的事件层，不复制视频、不调用模型。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
from hotspot_event_clips import build_event_clips


def rebuild(asset_id: int) -> list[dict]:
    db.init_db()
    asset = db.get_asset(asset_id)
    if not asset or not asset.get("hotspot_id"):
        raise SystemExit(f"资产 {asset_id} 不是热点素材，无法重建")
    db.update_asset_semantic_state(asset_id, "other", asset.get("processing_status") or "review_required")
    hotspot = db.get_hotspot(asset["hotspot_id"]) or {}
    segments = db.list_asset_segments(asset_id=asset_id)
    events = build_event_clips(
        segments,
        date=str(hotspot.get("published_at") or asset.get("created_at") or "")[:10] or "未知日期",
        source=hotspot.get("publisher") or "热点来源",
        source_title=str(hotspot.get("title_zh") or hotspot.get("title") or ""),
    )
    created = db.replace_hotspot_event_clips(asset_id, asset["hotspot_id"], events)
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int, required=True)
    args = parser.parse_args()
    events = rebuild(args.asset_id)
    print(json.dumps({
        "asset_id": args.asset_id,
        "event_count": len(events),
        "mimo_calls": 0,
        "events": [
            {"title_zh": item["title_zh"], "title_en": item["title_en"],
             "start_ms": item["start_ms"], "end_ms": item["end_ms"],
             "review_status": item["review_status"]}
            for item in events
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
