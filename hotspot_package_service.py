"""Business composition for event-level hotspot topic packages."""
from __future__ import annotations

from collections import Counter

import database as db
import hotspot_topic_packages


def _media_form(item: dict) -> str:
    if item.get("media_kind") == "image":
        return "image"
    if item.get("media_kind") in {"video_link", "video_file"}:
        return "video"
    return "text"


def _card(package: dict) -> dict:
    media = db.list_hotspot_media(package["id"], limit=500)
    counts = Counter(_media_form(item) for item in media)
    return {
        **package,
        "video_count": counts["video"],
        "image_count": counts["image"],
        "text_count": package["signal_count"],
        "breakdown": hotspot_topic_packages._heat_breakdown(db.list_hotspot_signals(package["id"])),
    }


def list_packages(*, query: str = "", source: str = "", event_type: str = "", heat_state: str = "", media_form: str = "", since: str = "", limit: int = 100) -> list[dict]:
    query = query.casefold().strip()
    source = source.casefold().strip()
    packages = []
    for item in db.list_hotspots(limit=500):
        package = db.get_hotspot_package(item["id"])
        if not package:
            continue
        haystack = " ".join(str(package.get(key) or "") for key in ("title", "summary", "title_zh", "summary_zh")).casefold()
        signals = db.list_hotspot_signals(package["id"])
        if query and query not in haystack:
            continue
        if source and not any(source in str(signal.get("source_name") or "").casefold() for signal in signals):
            continue
        if event_type and package.get("event_type") != event_type:
            continue
        if heat_state and package.get("heat_state") != heat_state:
            continue
        if since and str(package.get("published_at") or package.get("retrieved_at") or "") < since:
            continue
        card = _card(package)
        has_media = bool(card["video_count"] or card["image_count"])
        if media_form == "has_media" and not has_media:
            continue
        if media_form == "none" and has_media:
            continue
        if media_form in {"video", "image", "text"} and not card.get(f"{media_form}_count", 0):
            continue
        packages.append(card)
    return sorted(packages, key=lambda item: (-float(item.get("heat_score") or 0), item["id"]), reverse=False)[:limit]


def get_package_detail(hotspot_id: int) -> dict | None:
    package = db.get_hotspot_package(hotspot_id)
    if not package:
        return None
    signals = db.list_hotspot_signals(hotspot_id)
    media = db.list_hotspot_media(hotspot_id, limit=500)
    groups = {"video": [], "image": [], "text": signals}
    for item in media:
        groups[_media_form(item)].append(item)
    rights = Counter(str(item.get("rights_tier") or "unknown") for item in media)
    event_type = package.get("event_type") or "unknown"
    return {
        **_card(package),
        "signals": signals,
        "event_clips": db.list_hotspot_event_clips(hotspot_id=hotspot_id),
        "media_groups": groups,
        "rights_summary": dict(rights),
        "logistics_angles": [
            {"event_type": event_type, "relevance": package.get("logistics_relevance", 0),
             "evidence_signal_ids": [item["id"] for item in signals]},
        ],
        "actions": {
            "can_confirm": package.get("package_status") == "new",
            "can_merge": bool(signals),
            "can_create_content": package.get("package_status") == "confirmed",
        },
    }


def confirm_package(hotspot_id: int, user: dict) -> dict | None:
    package = db.get_hotspot_package(hotspot_id)
    if not package:
        return None
    db.update_hotspot_package_metrics(
        hotspot_id, heat_score=package["heat_score"], heat_state=package["heat_state"],
        event_type=package["event_type"], logistics_relevance=package["logistics_relevance"],
        locations=package["locations"], entities=package["entities"], package_status="confirmed",
    )
    db.add_audit_log(user["id"], user["username"], "confirm_hotspot_package", target=str(hotspot_id))
    return get_package_detail(hotspot_id)


def merge_signals(hotspot_id: int, signal_ids: list[int], user: dict) -> dict | None:
    if not db.get_hotspot_package(hotspot_id):
        return None
    unique_ids = sorted({int(signal_id) for signal_id in signal_ids if int(signal_id) > 0})
    if not unique_ids:
        raise ValueError("至少选择一条待合并信号")
    placeholders = ",".join("?" for _ in unique_ids)
    with db.get_conn() as conn:
        conn.execute(f"UPDATE hotspot_signals SET hotspot_id=?,cluster_status='merged',updated_at=datetime('now') WHERE id IN ({placeholders})", (hotspot_id, *unique_ids))
    signals = db.list_hotspot_signals(hotspot_id)
    grouped = hotspot_topic_packages.cluster_signals(signals)
    package_data = grouped[0] if grouped else {"heat_score": 0, "heat_state": "unconfirmed", "event_type": "unknown", "logistics_relevance": 0, "locations": [], "entities": []}
    db.update_hotspot_package_metrics(hotspot_id, package_status="new", **{key: package_data[key] for key in ("heat_score", "heat_state", "event_type", "logistics_relevance", "locations", "entities")})
    db.add_audit_log(user["id"], user["username"], "merge_hotspot_signals", target=str(hotspot_id), detail=",".join(map(str, unique_ids)))
    return get_package_detail(hotspot_id)


def prepare_media(media_id: int, user: dict) -> dict:
    item = db.get_hotspot_media(media_id)
    if not item:
        raise LookupError("热点素材不存在")
    package = db.get_hotspot_package(int(item["hotspot_id"]))
    if not package or package.get("package_status") != "confirmed":
        raise PermissionError("请先确认热点专题包，再准备单个媒体")
    if item.get("rights_tier") in {"red", "unknown"}:
        raise PermissionError("媒体权利状态不允许准备分析")
    if item.get("download_status") in {"pending", "downloading", "downloaded"}:
        raise RuntimeError("该热点媒体正在下载或已经素材化")
    db.update_hotspot_media_state(media_id, download_status="pending", download_progress=5, progress_detail="任务已提交，等待下载", processing_status="not_started", error_message=None)
    db.add_audit_log(user["id"], user["username"], "prepare_hotspot_media", target=str(media_id))
    return {"id": media_id, "status": "pending"}
