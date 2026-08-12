"""Run one authorized hotspot prewarm cycle now, without waiting for Sunday."""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")
parser = argparse.ArgumentParser(description="运行一次已授权热点 Hook 预热")
parser.add_argument(
    "--channel-video-limit", type=int, default=None,
    help="仅本次批量实测：每个已授权频道读取 1–12 条元数据；默认沿用三天任务配置",
)
parser.add_argument(
    "--media-ids", default="",
    help="仅本次受控验收：逗号分隔的已授权热点媒体 ID；仍必须通过模型选择与事实审计",
)
parser.add_argument(
    "--fetch-only",
    action="store_true",
    help="只抓取元数据并同步授权状态，绝不下载、分析或策展 Hook",
)
args = parser.parse_args()
if args.channel_video_limit is not None:
    os.environ["HOTSPOT_YOUTUBE_CHANNEL_VIDEO_LIMIT"] = str(max(1, min(12, args.channel_video_limit)))
# Explicit operator command: enable sync unless this is a metadata-only fetch.
# `--fetch-only` must never force the scheduler prewarm gate on.
if not args.fetch_only:
    os.environ["HOTSPOT_HOOK_SYNC_ENABLED"] = "1"
    os.environ["HOTSPOT_PREWARM_ENABLED"] = "1"

import database as db
import hotspot_fetcher
import scheduler


async def main() -> int:
    db.init_db()
    admin = db.get_user_by_username("admin")
    if not admin:
        raise SystemExit("缺少 admin 用户，无法记录热点预热")
    fetched = await hotspot_fetcher.fetch_hotspots(
        PROJECT_ROOT / "static", created_by=admin["id"], video_channels=hotspot_fetcher.configured_video_channels(),
    )
    # Existing metadata is migrated by init_db; batch22 has no green/yellow
    # approval queue. Keep this summary field for old operators' reports.
    promoted = 0
    media_ids = []
    for raw in str(args.media_ids or "").split(","):
        try:
            media_ids.append(int(raw.strip()))
        except ValueError:
            continue
    if args.fetch_only:
        prewarm = {
            "status": "skipped_fetch_only",
            "downloaded": 0,
            "processed": 0,
            "reason": "fetch-only mode never calls scheduler.prewarm_authorized_hotspot_media",
        }
    else:
        prewarm = await scheduler.prewarm_authorized_hotspot_media(media_ids=media_ids or None)
    media = db.list_hotspot_media(lifecycle_status="active", limit=500)
    summary = {
        "fetch": fetched,
        "promoted_existing_configured_media": promoted,
        "fetch_only": bool(args.fetch_only),
        "prewarm": prewarm,
        "authorized_long_video": [
            {"id": item["id"], "hotspot_id": item["hotspot_id"], "duration_seconds": item.get("duration_seconds"),
             "download_status": item.get("download_status"), "processing_status": item.get("processing_status")}
            for item in media
            if item.get("media_kind") in {"video_link", "video_file"}
            and item.get("authorization_status") == "authorized" and float(item.get("duration_seconds") or 0) >= 180
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
