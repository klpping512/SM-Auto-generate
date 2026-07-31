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
    help="仅本次受控验收：逗号分隔的已授权热点媒体 ID；仍必须通过 Qwen 选择与事实审计",
)
args = parser.parse_args()
if args.channel_video_limit is not None:
    os.environ["HOTSPOT_YOUTUBE_CHANNEL_VIDEO_LIMIT"] = str(max(1, min(12, args.channel_video_limit)))
os.environ.setdefault("HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED", "1")
# `HOTSPOT_HOOK_SYNC_ENABLED` is the current scheduler gate and takes
# precedence over the legacy prewarm flag when both are present in .env.
# Set both here so this explicit operator command cannot silently fetch feeds
# and then skip Qwen selection/download.
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
        PROJECT_ROOT / "static", created_by=admin["id"], video_channels=hotspot_fetcher.DEFAULT_VIDEO_CHANNELS,
    )
    # Existing metadata was collected before the organization-level approval
    # switch existed.  The user confirmed all currently configured sources are
    # in scope, so promote only their pending records before warming the cache.
    promoted = 0
    for item in db.list_hotspot_media(lifecycle_status="active", authorization_status="pending_review", limit=500):
        if item.get("platform") not in {"youtube", "direct", "tiktok"}:
            continue
        db.update_hotspot_media_authorization(
            item["id"], "authorized", "已配置信源已获企业授权，可自动下载分析；仅限已授权使用范围。",
            item.get("license_name"), item.get("attribution"), item.get("rights_evidence_url"), admin["id"],
        )
        promoted += 1
    media_ids = []
    for raw in str(args.media_ids or "").split(","):
        try:
            media_ids.append(int(raw.strip()))
        except ValueError:
            continue
    prewarm = await scheduler.prewarm_authorized_hotspot_media(media_ids=media_ids or None)
    media = db.list_hotspot_media(lifecycle_status="active", limit=500)
    summary = {
        "fetch": fetched,
        "promoted_existing_configured_media": promoted,
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
