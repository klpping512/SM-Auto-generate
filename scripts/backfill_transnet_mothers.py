#!/usr/bin/env python3
"""P0：加深抓取 Transnet 可物料化母片，预热分析，并写铁律 E 口径 H-hit 报告。

用法：
  python3 scripts/backfill_transnet_mothers.py
  python3 scripts/backfill_transnet_mothers.py --skip-prewarm   # 只抓取+统计
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
import hotspot_video_sources  # noqa: E402
import scheduler as sched  # noqa: E402

REPORT_PATH = PROJECT_ROOT / "docs" / "总指挥指令-2026-08-04" / "transnet-backfill-hhit.md"
PUBLISHER = "Transnet NPA"


def _parse_ts(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _is_tech_fail(media: dict) -> bool:
    blob = f"{media.get('progress_detail') or ''}\n{media.get('error_message') or ''}"
    markers = (
        "未返回合法 JSON", "budget", "Budget", "token", "预算已用完",
        "Token 预算", "Server disconnected", "disconnected",
        "temporarily_unavailable", "暂时不可用",
    )
    return any(marker in blob for marker in markers)


def _is_prefiltered_skip(media: dict) -> bool:
    return str(media.get("download_status") or "") == "prefiltered_skip"


def collect_stats(since: datetime) -> dict:
    rows = db.list_hotspot_media(lifecycle_status="active", limit=500)
    selected = []
    for item in rows:
        if str(item.get("publisher") or "") != PUBLISHER:
            continue
        if item.get("media_kind") not in {"video_link", "video_file"}:
            continue
        created = _parse_ts(item.get("created_at") or item.get("updated_at") or item.get("published_at"))
        if created and created < since:
            continue
        selected.append(item)

    tech_fail = []
    retryable = []
    metadata_ready = []
    curated = []
    prefiltered = []
    confirmed_hooks = 0
    no_qualified = 0

    for media in selected:
        status = str(media.get("download_status") or "")
        if status == "materialization_retryable" or int(media.get("materialization_retryable") or 0) == 1:
            retryable.append(media)
            continue
        if _is_prefiltered_skip(media):
            prefiltered.append(media)
            continue
        if _is_tech_fail(media):
            tech_fail.append(media)
            continue
        if status == "metadata_ready":
            metadata_ready.append(media)
        processing = str(media.get("processing_status") or "")
        if status == "downloaded" and processing in {"ready", "processing_failed", "failed"}:
            curated.append(media)
            asset_id = media.get("asset_id")
            events = db.list_hotspot_event_clips(asset_id=int(asset_id)) if asset_id else []
            confirmed = [e for e in events if e.get("review_status") == "confirmed"]
            if confirmed:
                confirmed_hooks += len(confirmed)
            else:
                no_qualified += 1

    denom = len(curated)
    mothers_with_hooks = denom - no_qualified
    return {
        "publisher": PUBLISHER,
        "since": since.isoformat(),
        "mothers_scanned": len(selected),
        "tech_fail": len(tech_fail),
        "prefiltered_skip": len(prefiltered),
        "materialization_retryable": len(retryable),
        "metadata_ready_inflight": len(metadata_ready),
        "curated_denominator": denom,
        "no_qualified_hooks": no_qualified,
        "mothers_with_confirmed_hooks": mothers_with_hooks,
        "confirmed_hooks": confirmed_hooks,
        "H_hit_mother_share": round(mothers_with_hooks / denom, 4) if denom else None,
        "retryable_ids": [int(m["id"]) for m in retryable[:30]],
        "downloadable_media_ids": [
            int(m["id"]) for m in selected
            if str(m.get("download_status") or "") in {"metadata_ready", "downloaded", "downloading"}
        ],
        "tech_fail_samples": [
            {
                "media_id": m.get("id"),
                "detail": str(m.get("progress_detail") or m.get("error_message") or "")[:180],
            }
            for m in tech_fail[:10]
        ],
    }


def write_report(fetch_result: dict, stats: dict, prewarm: dict | None) -> None:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    health = (fetch_result.get("source_health") or [{}])[0]
    lines = [
        "# Transnet 回填 H-hit（铁律 E）",
        "",
        f"> 生成时间：{now}",
        "> 口径：tech_fail / materialization_retryable 从 H-hit 分母剔除；仅已下载且策展终态母片进分母。",
        "",
        "## 抓取硬化结果",
        "",
        f"- scanned: {health.get('scanned')}",
        f"- downloadable: {fetch_result.get('downloadable')}（目标 ≥10）",
        f"- retryable: {fetch_result.get('retryable')}",
        f"- accepted_media_ids: {fetch_result.get('accepted_media_ids')}",
        f"- health_error: {health.get('error') or '（无）'}",
        "",
        "## 铁律 E 分台统计（Transnet NPA）",
        "",
        f"- mothers_scanned: {stats['mothers_scanned']}",
        f"- tech_fail: {stats['tech_fail']}",
        f"- prefiltered_skip: {stats.get('prefiltered_skip', 0)}",
        f"- materialization_retryable: {stats['materialization_retryable']}",
        f"- metadata_ready_inflight: {stats['metadata_ready_inflight']}",
        f"- curated_denominator: {stats['curated_denominator']}",
        f"- no_qualified_hooks: {stats['no_qualified_hooks']}",
        f"- mothers_with_confirmed_hooks: {stats['mothers_with_confirmed_hooks']}",
        f"- confirmed_hooks: {stats['confirmed_hooks']}",
        f"- **H-hit_mother_share: {stats['H_hit_mother_share']}**",
        "",
        "## 预热",
        "",
        "```json",
        json.dumps(prewarm or {"status": "skipped"}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 原始 JSON",
        "",
        "```json",
        json.dumps({"fetch": fetch_result, "stats": stats}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-prewarm", action="store_true")
    parser.add_argument("--since-hours", type=float, default=72.0)
    args = parser.parse_args()

    db.init_db()
    channel = next(
        (c for c in hotspot_video_sources.configured_channels() if c.get("name") == PUBLISHER),
        None,
    )
    if not channel:
        print("ERROR: Transnet NPA 不在当前频道清单，禁止继续", file=sys.stderr)
        return 2
    channel = {
        **channel,
        "evergreen": True,
        "min_downloadable": max(10, int(channel.get("min_downloadable") or 10)),
        "playlist_scan_cap": max(20, int(channel.get("playlist_scan_cap") or 20)),
    }
    print(f"fetching {channel} …")
    fetch_result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [channel], precheck=True,
    )
    print(json.dumps(fetch_result, ensure_ascii=False, indent=2))

    media_ids = list(fetch_result.get("accepted_media_ids") or [])
    prewarm = None
    if not args.skip_prewarm and media_ids:
        print(f"prewarm {len(media_ids)} media ids …")
        prewarm = await sched.prewarm_authorized_hotspot_media(media_ids)
        print(json.dumps(prewarm, ensure_ascii=False, indent=2, default=str))

    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    stats = collect_stats(since)
    write_report(fetch_result, stats, prewarm)
    ok = int(fetch_result.get("downloadable") or 0) >= 10
    print(f"downloadable={fetch_result.get('downloadable')} target>=10 → {'PASS' if ok else 'SHORT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
