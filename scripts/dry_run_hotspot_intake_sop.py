"""Run the backend Buffalo RAG intake SOP against existing hotspot metadata only.

No download, source mutation, asset analysis or clip rendering is performed.  The
script is intended for safely evaluating the currently configured internal model
after an SOP or knowledge-base change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db
import hotspot_hook_intake


def main() -> int:
    parser = argparse.ArgumentParser(description="仅干跑 Buffalo RAG 热点入库 SOP，不下载素材")
    parser.add_argument("--media-id", action="append", type=int, help="可重复传入多个热点媒体 ID")
    parser.add_argument(
        "--scenario", choices=["warehouse-direct"],
        help="仅用于验证 SOP 的内存正向样本，不读取或写入热点记录",
    )
    parser.add_argument("--maximum", type=int, default=4)
    args = parser.parse_args()
    if bool(args.media_id) == bool(args.scenario):
        parser.error("请二选一提供 --media-id 或 --scenario")
    db.init_db()
    rows = []
    hotspots: dict[int, dict] = {}
    if args.scenario == "warehouse-direct":
        # Synthetic metadata only: validates the RAG/SOP contract without creating
        # a database record or claiming an external event is real.
        rows = [{"id": -1001, "hotspot_id": -501, "duration_seconds": 245, "platform": "fixture"}]
        hotspots = {
            -501: {
                "title": "Buffalo warehouse team inspects parcels before outbound loading",
                "summary": "On-site footage visibly shows Buffalo branding, parcel inspection, sorting and loading at a South African warehouse.",
                "publisher": "SOP fixture only",
            }
        }
    else:
        for media_id in args.media_id:
            media = db.get_hotspot_media(media_id)
            if not media:
                raise SystemExit(f"热点媒体不存在：{media_id}")
            rows.append(media)
            hotspot = db.get_hotspot(int(media["hotspot_id"])) or {}
            hotspots[int(media["hotspot_id"])] = hotspot
    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(
        rows, hotspots, maximum=args.maximum,
    )
    print(json.dumps({
        "mode": "dry_run_no_download",
        "requested_media_ids": args.media_id or [],
        "scenario": args.scenario,
        "selected_media_ids": [int(item["id"]) for item in selected],
        "decisions": [{"media_id": int(item["id"]), "decision": item["intake_decision"]} for item in selected],
        "meta": meta,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
