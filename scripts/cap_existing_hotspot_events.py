#!/usr/bin/env python3
"""将现有热点事件代理片段限制为不超过 25 秒，并重建对应预览文件。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database as db
import hotspot_event_media


def main() -> None:
    db.init_db()
    static_dir = Path(__file__).resolve().parents[1] / "static"
    for event in list(db.list_hotspot_event_clips()):
        if int(event["duration_ms"] or 0) <= 25_000:
            continue
        start, end = int(event["start_ms"]), int(event["end_ms"])
        chunks = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + 25_000, end)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end
        with db.get_conn() as conn:
            conn.execute("UPDATE hotspot_event_clips SET end_ms=?,duration_ms=?,clip_path=NULL,clip_status='pending',clip_error=NULL WHERE id=?", (chunks[0][1], chunks[0][1] - chunks[0][0], event["id"]))
            for index, (chunk_start, chunk_end) in enumerate(chunks[1:], 2):
                cur = conn.execute("""INSERT INTO hotspot_event_clips
                    (asset_id,hotspot_id,event_index,start_ms,end_ms,title_zh,title_en,location,
                     entities_json,keywords_json,evidence_json,confidence,review_status,virtual_asset_id,
                     duration_ms,thumbnail_path,clip_path,clip_status,clip_error,library_origin)
                    SELECT asset_id,hotspot_id,? ,?,?,?,?,location,entities_json,keywords_json,evidence_json,
                           confidence,review_status,NULL,?,NULL,NULL,'pending',NULL,library_origin
                    FROM hotspot_event_clips WHERE id=?""", (event["event_index"] + index - 1, chunk_start, chunk_end,
                    f"{event['title_zh']}｜片段{index}", f"{event['title_en']} | Part {index}", chunk_end - chunk_start, event["id"]))
                new_id = int(cur.lastrowid)
                conn.execute("UPDATE hotspot_event_clips SET virtual_asset_id=? WHERE id=?", (f"hotspot-event-{new_id}", new_id))
        for item in db.list_hotspot_event_clips(asset_id=event["asset_id"]):
            if int(item["event_index"]) >= int(event["event_index"]):
                asset = db.get_asset(int(item["asset_id"]))
                if asset and item["start_ms"] >= start and item["end_ms"] <= end:
                    hotspot_event_media.materialize_event_clip(static_dir, asset, item)


if __name__ == "__main__":
    main()
