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


def _is_ready_hook(event: dict) -> bool:
    return bool(
        str(event.get("review_status") or "") == "confirmed"
        and str(event.get("clip_status") or "") == "ready"
        and str(event.get("clip_path") or "").strip()
    )


def derive_hook_readiness(hotspot_id: int, *, media: list[dict] | None = None, clips: list[dict] | None = None) -> dict:
    """Derive Hook readiness independently from package fact confirmation."""
    media = media if media is not None else db.list_hotspot_media(hotspot_id, limit=500)
    clips = clips if clips is not None else db.list_hotspot_event_clips(hotspot_id=hotspot_id)
    video_media = [item for item in media if item.get("media_kind") in {"video_link", "video_file"}]
    candidate_media_count = len(video_media)
    processing_statuses = {"preparing", "downloading", "processing", "analyzing", "materializing", "queued"}
    processing_count = sum(
        1
        for item in video_media
        if str(item.get("download_status") or "").casefold() in processing_statuses
        or str(item.get("processing_status") or "").casefold() in processing_statuses
    )
    review_required_count = sum(
        1 for clip in clips if str(clip.get("review_status") or "") == "review_required"
    )
    ready_hook_count = sum(1 for clip in clips if _is_ready_hook(clip))
    failed_count = sum(
        1
        for item in video_media
        if str(item.get("download_status") or "").casefold() in {"failed", "download_failed"}
        or str(item.get("processing_status") or "").casefold() in {"failed", "curation_failed"}
    ) + sum(1 for clip in clips if str(clip.get("clip_status") or "") == "failed")

    if ready_hook_count > 0:
        state = "ready_for_video"
    elif processing_count > 0:
        state = "preparing"
    elif review_required_count > 0:
        state = "review_required"
    elif failed_count > 0 and candidate_media_count > 0:
        state = "prepare_failed"
    else:
        state = "not_prepared"

    return {
        "state": state,
        "candidate_media_count": candidate_media_count,
        "processing_count": processing_count,
        "review_required_count": review_required_count,
        "ready_hook_count": ready_hook_count,
    }


def package_status_label(status: str) -> str:
    return {
        "new": "待确认",
        "confirmed": "已确认",
        "rejected": "已驳回",
    }.get(str(status or "new"), "待确认")


def hook_readiness_label(state: str) -> str:
    return {
        "not_prepared": "未准备",
        "preparing": "准备中",
        "review_required": "待人工确认",
        "ready_for_video": "可用于成片",
        "prepare_failed": "准备失败",
    }.get(str(state or "not_prepared"), "未准备")


def _derive_actions(package: dict, readiness: dict) -> dict:
    status = str(package.get("package_status") or "new")
    confirmed = status == "confirmed"
    return {
        "can_confirm_facts": status == "new",
        "can_reject_facts": status == "new",
        "can_prepare_media": confirmed and int(readiness.get("candidate_media_count") or 0) > 0,
        "can_follow_up_video": confirmed and int(readiness.get("ready_hook_count") or 0) >= 1,
        # Legacy aliases kept for older clients during migration.
        "can_confirm": status == "new",
        "can_merge": True,
        "can_create_content": confirmed and int(readiness.get("ready_hook_count") or 0) >= 1,
    }


def _card(package: dict) -> dict:
    media = db.list_hotspot_media(package["id"], limit=500)
    counts = Counter(_media_form(item) for item in media)
    readiness = derive_hook_readiness(package["id"], media=media)
    return {
        **package,
        "video_count": counts["video"],
        "image_count": counts["image"],
        "text_count": package["signal_count"],
        "breakdown": hotspot_topic_packages._heat_breakdown(db.list_hotspot_signals(package["id"])),
        "package_status_label": package_status_label(package.get("package_status")),
        "hook_readiness": readiness,
        "hook_readiness_label": hook_readiness_label(readiness["state"]),
        "actions": _derive_actions(package, readiness),
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
    clips = db.list_hotspot_event_clips(hotspot_id=hotspot_id)
    groups = {"video": [], "image": [], "text": signals}
    for item in media:
        groups[_media_form(item)].append(item)
    rights = Counter(str(item.get("authorization_status") or "authorized") for item in media)
    event_type = package.get("event_type") or "unknown"
    readiness = derive_hook_readiness(hotspot_id, media=media, clips=clips)
    card = {
        **package,
        "video_count": Counter(_media_form(item) for item in media)["video"],
        "image_count": Counter(_media_form(item) for item in media)["image"],
        "text_count": package["signal_count"],
        "breakdown": hotspot_topic_packages._heat_breakdown(signals),
        "package_status_label": package_status_label(package.get("package_status")),
        "hook_readiness": readiness,
        "hook_readiness_label": hook_readiness_label(readiness["state"]),
        "actions": _derive_actions(package, readiness),
    }
    return {
        **card,
        "signals": signals,
        "event_clips": clips,
        "media_groups": groups,
        "rights_summary": dict(rights),
        "logistics_angles": [
            {"event_type": event_type, "relevance": package.get("logistics_relevance", 0),
             "evidence_signal_ids": [item["id"] for item in signals]},
        ],
    }


def confirm_package(hotspot_id: int, user: dict) -> dict | None:
    """Confirm topic facts only — never marks Hooks ready or creates video projects."""
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


def reject_package(hotspot_id: int, user: dict) -> dict | None:
    package = db.get_hotspot_package(hotspot_id)
    if not package:
        return None
    db.update_hotspot_package_metrics(
        hotspot_id, heat_score=package["heat_score"], heat_state=package["heat_state"],
        event_type=package["event_type"], logistics_relevance=package["logistics_relevance"],
        locations=package["locations"], entities=package["entities"], package_status="rejected",
    )
    db.add_audit_log(user["id"], user["username"], "reject_hotspot_package", target=str(hotspot_id))
    return get_package_detail(hotspot_id)


def merge_signals(hotspot_id: int, signal_ids: list[int], user: dict) -> dict | None:
    package = db.get_hotspot_package(hotspot_id)
    if not package:
        return None
    unique_ids = sorted({int(signal_id) for signal_id in signal_ids if int(signal_id) > 0})
    if not unique_ids:
        raise ValueError("至少选择一条待合并信号")
    placeholders = ",".join("?" for _ in unique_ids)
    with db.get_conn() as conn:
        conn.execute(
            f"UPDATE hotspot_signals SET hotspot_id=?,cluster_status='merged',updated_at=datetime('now') WHERE id IN ({placeholders})",
            (hotspot_id, *unique_ids),
        )
    signals = db.list_hotspot_signals(hotspot_id)
    grouped = hotspot_topic_packages.cluster_signals(signals)
    package_data = grouped[0] if grouped else {
        "heat_score": 0, "heat_state": "unconfirmed", "event_type": "unknown",
        "logistics_relevance": 0, "locations": [], "entities": [],
    }
    # Preserve confirmed/rejected fact status when merging signals.
    preserved = package.get("package_status") or "new"
    if preserved not in {"confirmed", "rejected"}:
        preserved = "new"
    db.update_hotspot_package_metrics(
        hotspot_id,
        package_status=preserved,
        **{key: package_data[key] for key in ("heat_score", "heat_state", "event_type", "logistics_relevance", "locations", "entities")},
    )
    db.add_audit_log(
        user["id"], user["username"], "merge_hotspot_signals",
        target=str(hotspot_id), detail=",".join(map(str, unique_ids)),
    )
    return get_package_detail(hotspot_id)


def prepare_package_media(hotspot_id: int, user: dict) -> dict | None:
    package = db.get_hotspot_package(hotspot_id)
    if not package or package.get("package_status") != "confirmed":
        return None
    return get_package_detail(hotspot_id)


def prepare_media(media_id: int, user: dict) -> dict:
    item = db.get_hotspot_media(media_id)
    if not item:
        raise LookupError("热点素材不存在")
    package = db.get_hotspot_package(int(item["hotspot_id"]))
    if not package or package.get("package_status") != "confirmed":
        raise PermissionError("请先确认热点专题包，再准备单个媒体")
    if item.get("authorization_status") == "blocked":
        raise PermissionError("该媒体已被管理员停用")
    if item.get("download_status") in {"pending", "downloading", "downloaded"}:
        raise RuntimeError("该热点媒体正在下载或已经素材化")
    db.update_hotspot_media_state(
        media_id,
        download_status="pending",
        download_progress=5,
        progress_detail="任务已提交，等待下载",
        processing_status="not_started",
        error_message=None,
    )
    db.add_audit_log(user["id"], user["username"], "prepare_hotspot_media", target=str(media_id))
    return {"id": media_id, "status": "pending"}
