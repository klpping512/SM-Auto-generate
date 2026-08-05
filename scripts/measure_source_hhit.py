#!/usr/bin/env python3
"""计算本轮新频道母片的真 H-hit（铁律 E：技术失败从分母剔除并单列）。

用法：在授权预热/重策展后运行。
  python3 scripts/measure_source_hhit.py --publishers "eNCA,Newzroom Afrika,CNBC Africa,BusinessDayTV,Transnet NPA" --since-hours 24
"""
from __future__ import annotations

import argparse
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
import hotspot_lexicon  # noqa: E402

LOGISTICS_NODES = {"warehouse", "delivery", "customs", "port", "border", "facility"}

TECH_FAIL_MARKERS = (
    "未返回合法 JSON",
    "budget",
    "Budget",
    "token",
    "预算已用完",
    "Token 预算",
    "Server disconnected",
    "disconnected",
    "temporarily_unavailable",
    "暂时不可用",
)


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
    return any(marker in blob for marker in TECH_FAIL_MARKERS)


def _hook_is_logistics(event: dict) -> bool:
    text = " ".join(
        [
            str(event.get("title_zh") or ""),
            str(event.get("title_en") or ""),
            " ".join(event.get("keywords") or []),
            " ".join((event.get("evidence") or {}).get("what_happened", "") if isinstance(event.get("evidence"), dict) else []),
            str((event.get("evidence") or {}).get("logistics_question") or "") if isinstance(event.get("evidence"), dict) else "",
        ]
    )
    profile = hotspot_lexicon.category_profile(text)
    if profile & LOGISTICS_NODES:
        return True
    if hotspot_lexicon.FEED_FILTER_PATTERN.search(text):
        # 排除仅靠 "South Africa" 虚高：要求至少还有物流词族
        lowered = text.casefold()
        logistics_tokens = (
            "port", "harbour", "customs", "freight", "logistics", "transnet", "warehouse",
            "cargo", "container", "border", "shipping", "truck", "rail", "delivery",
            "港口", "清关", "海关", "货运", "物流", "仓储", "跨境", "铁路",
        )
        return any(token in lowered for token in logistics_tokens)
    return False


def measure(publishers: set[str], since: datetime | None) -> dict:
    db.init_db()
    media_rows = db.list_hotspot_media(lifecycle_status="active", limit=500)
    selected = []
    for item in media_rows:
        if str(item.get("publisher") or "") not in publishers:
            continue
        if item.get("media_kind") not in {"video_link", "video_file"}:
            continue
        created = _parse_ts(item.get("created_at") or item.get("published_at"))
        if since and created and created < since:
            continue
        selected.append(item)

    tech_fails = []
    curated_ok = []  # mothers that finished curation without tech fail
    logistics_hooks = 0
    total_hooks = 0
    no_hook_mothers = 0
    per_publisher: dict[str, dict] = {}

    for media in selected:
        pub = str(media.get("publisher") or "")
        bucket = per_publisher.setdefault(pub, {
            "mothers": 0, "tech_fail": 0, "curated_denominator": 0,
            "with_hooks": 0, "hooks": 0, "logistics_hooks": 0,
        })
        bucket["mothers"] += 1
        if str(media.get("download_status") or "") == "prefiltered_skip":
            # 主动不下：单列，不进 H-hit 分母（同铁律 E 隔离思路，但不是 tech_fail）。
            bucket.setdefault("prefiltered_skip", 0)
            bucket["prefiltered_skip"] += 1
            continue
        if _is_tech_fail(media):
            tech_fails.append({
                "media_id": media.get("id"),
                "publisher": pub,
                "detail": str(media.get("progress_detail") or media.get("error_message") or "")[:200],
            })
            bucket["tech_fail"] += 1
            continue
        # Only count mothers that reached a terminal curation outcome
        detail = str(media.get("progress_detail") or "")
        processing = str(media.get("processing_status") or "")
        downloaded = str(media.get("download_status") or "")
        # 兼容：asset 已就绪但 download_status 被重抓误写回 metadata_ready 的母片仍计入分母
        curated_ready = (
            (downloaded == "downloaded" and processing in {"ready", "processing_failed", "failed"})
            or (
                bool(media.get("asset_id"))
                and processing in {"ready", "processing_failed", "failed"}
            )
        )
        if not curated_ready:
            # still in flight — exclude from H-hit denominator (not tech fail, not success)
            continue
        curated_ok.append(media)
        bucket["curated_denominator"] += 1
        asset_id = media.get("asset_id")
        events = db.list_hotspot_event_clips(asset_id=int(asset_id)) if asset_id else []
        confirmed = [e for e in events if e.get("review_status") == "confirmed"]
        if not confirmed:
            no_hook_mothers += 1
            continue
        bucket["with_hooks"] += 1
        for event in confirmed:
            total_hooks += 1
            bucket["hooks"] += 1
            if _hook_is_logistics(event):
                logistics_hooks += 1
                bucket["logistics_hooks"] += 1

    denom_mothers = len(curated_ok)
    mothers_with_logistics_hook = sum(
        1 for m in curated_ok
        if any(
            _hook_is_logistics(e)
            for e in (db.list_hotspot_event_clips(asset_id=int(m["asset_id"])) if m.get("asset_id") else [])
            if e.get("review_status") == "confirmed"
        )
    )
    return {
        "publishers": sorted(publishers),
        "since": since.isoformat() if since else None,
        "mothers_scanned": len(selected),
        "tech_fail_count": len(tech_fails),
        "tech_fails": tech_fails[:20],
        "curated_denominator_mothers": denom_mothers,
        "mothers_with_any_hook": denom_mothers - no_hook_mothers,
        "mothers_no_qualified_hooks": no_hook_mothers,
        "total_confirmed_hooks": total_hooks,
        "logistics_hooks": logistics_hooks,
        "H_hit_hook_share": round(logistics_hooks / total_hooks, 4) if total_hooks else None,
        "H_hit_mother_share": round(mothers_with_logistics_hook / denom_mothers, 4) if denom_mothers else None,
        "per_publisher": per_publisher,
        "iron_E_note": "tech_fail_count 已从 H-hit 分母剔除；in-flight 母片亦不计入分母",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--publishers",
        default="eNCA,Newzroom Afrika,CNBC Africa,BusinessDayTV,Transnet NPA",
    )
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--all-time", action="store_true")
    args = parser.parse_args()
    publishers = {p.strip() for p in args.publishers.split(",") if p.strip()}
    since = None if args.all_time else datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    report = measure(publishers, since)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
