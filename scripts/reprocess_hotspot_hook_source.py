"""重新分析一条已下载热点母片，并用当前 Qwen Hook 规则重新入库。

用于提示词、预算或镜头分析版本升级后的受控回归测试；不抓取新信源，也不改变
热点事实记录。示例：python3 scripts/reprocess_hotspot_hook_source.py --media-id 97

当 Hook 策展 SOP 新增事实字段时，可使用 ``--all-legacy-ready`` 对所有已分析、
但仍缺少该字段的历史母片进行受控重策展。候选筛选只看可审计的数据库状态；
是否产生 Hook 仍完全由项目内置模型和事实审核模型决定。
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

import asset_processing
import database as db
import hotspot_event_media
import hotspot_hook_curator
import hotspot_hook_intake


def _normalized_intake_decision(raw: object) -> dict:
    """Keep malformed legacy intake JSON from aborting a model-only re-curation."""
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _needs_legacy_event_identity(media: dict) -> bool:
    """Select only old confirmed Hook sets that predate event-identity gating."""
    asset_id = media.get("asset_id")
    if not asset_id:
        return False
    events = db.list_hotspot_event_clips(asset_id=int(asset_id))
    return bool(events) and any(
        event.get("review_status") == "confirmed"
        and not str((event.get("evidence") or {}).get("event_identity") or "").strip()
        for event in events
    )


def _reprocess_media(media_id: int, *, skip_analysis: bool, refresh_intake_decision: bool) -> dict:
    """Run one fully model-governed re-curation and return its audit summary."""
    media = db.get_hotspot_media(media_id)
    if not media or not media.get("asset_id"):
        raise ValueError("热点母片不存在或尚未下载")
    asset = db.get_asset(int(media["asset_id"]))
    if not asset or asset.get("file_type") != "video":
        raise ValueError("该热点母片不是可重新分析的视频")
    hotspot = db.get_hotspot(int(media["hotspot_id"])) or {}

    if refresh_intake_decision:
        selected, intake = hotspot_hook_intake.select_for_hook_ingestion(
            [media], {int(media["hotspot_id"]): hotspot}, maximum=1,
        )
        if not selected:
            raise ValueError(f"内置 Qwen 未再次选择该母片：{json.dumps(intake, ensure_ascii=False)}")
        db.update_hotspot_media_state(
            int(media["id"]),
            intake_decision_json=json.dumps(selected[0].get("intake_decision") or {}, ensure_ascii=False),
        )
        media = db.get_hotspot_media(int(media["id"])) or media

    db.update_hotspot_media_state(
        int(media["id"]), download_status="downloaded", processing_status="processing",
        download_progress=70, progress_detail="正在按当前 Hook 规则重新分析母片", error_message=None,
    )
    try:
        if skip_analysis:
            processing = {"status": "reused", "segments": len(db.list_asset_segments(asset_id=int(asset["id"]), limit=500))}
        else:
            job_id = db.create_asset_processing_job(asset["id"], processing_version=asset_processing.PROCESSING_VERSION)
            processing = asset_processing.process_asset_job(job_id, PROJECT_ROOT / "static")
        refreshed = db.get_asset(asset["id"]) or asset
        intake_decision = _normalized_intake_decision(media.get("intake_decision_json"))
        expected_hook = str(intake_decision.get("expected_hook") or "").strip()
        source_context = "\n".join(value for value in (
            f"本轮 Qwen 已获准的画面范围：{expected_hook}" if expected_hook else "",
            f"本轮物流切入问题：{str(intake_decision.get('logistics_question') or '').strip()}" if expected_hook else "",
            str(media.get("intake_title") or "").strip() if not expected_hook else "",
            str(media.get("intake_summary") or "").strip() if not expected_hook else "",
        ) if value)[:1200]
        hooks, curation = hotspot_hook_curator.curate_hook_clips(
            int(asset["id"]),
            str(hotspot.get("title_zh") or hotspot.get("title") or ""),
            db.list_asset_segments(asset_id=int(asset["id"]), limit=500),
            source_context,
        )
        # A re-curation replaces the complete Hook set for this mother asset.  Remove
        # only its generated preview proxies first, so rejected old clips cannot
        # survive outside the database.
        hotspot_event_media.remove_materialized_event_clips(PROJECT_ROOT / "static", int(asset["id"]))
        created = db.replace_hotspot_event_clips(int(asset["id"]), int(media["hotspot_id"]), hooks)
        if created:
            hotspot_event_media.materialize_event_clips(PROJECT_ROOT / "static", refreshed, created)
        detail = (
            f"重新分析完成，内置模型筛出 {len(created)} 条精华 Hook"
            if created else "重新分析完成，内置模型未筛出可复用 Hook"
        )
        db.update_hotspot_media_state(
            int(media["id"]), download_status="downloaded", processing_status="ready",
            download_progress=100, progress_detail=detail, error_message=None,
        )
        return {
            "media_id": int(media["id"]), "asset_id": int(asset["id"]),
            "processing": processing, "curation": curation,
            "hooks": [{
                "id": item["id"], "title_zh": item["title_zh"],
                "event_identity": (item.get("evidence") or {}).get("event_identity"),
                "start_ms": item["start_ms"], "end_ms": item["end_ms"],
            } for item in created],
        }
    except Exception as exc:
        db.update_hotspot_media_state(
            int(media["id"]), download_status="downloaded", processing_status="processing_failed",
            progress_detail=f"重新分析失败：{str(exc)[:180]}", error_message=str(exc)[:500],
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="重新分析已下载热点母片并重建 Qwen Hook")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--media-id", type=int, help="单条已下载热点媒体 ID")
    selection.add_argument("--all-legacy-ready", action="store_true", help="重策展所有缺少事件身份的历史已确认 Hook 母片")
    parser.add_argument("--skip-analysis", action="store_true", help="复用已完成的镜头分析，仅重新进行 Hook 策展")
    parser.add_argument("--refresh-intake-decision", action="store_true", help="让内置 Qwen 重新确认本母片的获准事件范围")
    parser.add_argument("--max-media", type=int, default=0, help="批量模式最多处理多少条；0 表示全部")
    parser.add_argument("--dry-run", action="store_true", help="仅输出本轮将重策展的母片，不调用模型也不写库")
    args = parser.parse_args()
    db.init_db()
    if args.all_legacy_ready:
        media_ids = [
            int(media["id"])
            for media in db.list_hotspot_media(lifecycle_status="active", authorization_status="authorized", limit=500)
            if media.get("download_status") == "downloaded"
            and media.get("processing_status") == "ready"
            and _needs_legacy_event_identity(media)
        ]
    else:
        media_ids = [int(args.media_id)]
    if args.max_media > 0:
        media_ids = media_ids[:args.max_media]
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "media_ids": media_ids}, ensure_ascii=False))
        return 0
    if not media_ids:
        print(json.dumps({"status": "no_legacy_ready_media", "media_ids": []}, ensure_ascii=False))
        return 0

    completed, failed = [], []
    for media_id in media_ids:
        try:
            completed.append(_reprocess_media(
                media_id, skip_analysis=args.skip_analysis,
                refresh_intake_decision=args.refresh_intake_decision,
            ))
        except Exception as exc:
            failed.append({"media_id": media_id, "error": str(exc)[:500]})
    print(json.dumps({
        "requested_media_ids": media_ids,
        "completed": completed,
        "failed": failed,
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
