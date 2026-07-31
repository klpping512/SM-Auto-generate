"""Resolve timeline clip references without copying a hotspot mother video."""
from __future__ import annotations


class ClipReferenceError(ValueError):
    pass


def _event_lookup(event_clip_id: int, event_lookup):
    if callable(event_lookup):
        return event_lookup(event_clip_id)
    if isinstance(event_lookup, dict):
        return event_lookup.get(event_clip_id)
    return None


def resolve_clip_ref(scene: dict, asset: dict | None, event_lookup) -> dict:
    if not asset:
        raise ClipReferenceError("素材不存在")
    asset_id = int(asset.get("id") or scene.get("asset_id") or 0)
    event_clip_id = scene.get("event_clip_id")
    hotspot_id = asset.get("hotspot_id")
    if event_clip_id is not None:
        try:
            event_clip_id = int(event_clip_id)
        except (TypeError, ValueError) as exc:
            raise ClipReferenceError("热点事件片段 ID 无效") from exc
        event = _event_lookup(event_clip_id, event_lookup)
        if not event or int(event.get("asset_id") or 0) != asset_id:
            raise ClipReferenceError("热点事件片段与母片不匹配")
        start_ms = int(event.get("start_ms") or 0)
        end_ms = int(event.get("end_ms") or 0)
        if start_ms < 0 or end_ms <= start_ms:
            raise ClipReferenceError("热点事件片段时间范围无效")
        asset_duration_ms = round(float(asset.get("duration") or 0) * 1000)
        if asset_duration_ms and end_ms > asset_duration_ms:
            raise ClipReferenceError("热点事件片段超出母片时长")
        return {
            "library_origin": "hotspot_event",
            "asset_id": asset_id,
            "event_clip_id": event_clip_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
        }
    if hotspot_id:
        raise ClipReferenceError("热点母片必须选择热点事件片段")
    start_ms = max(0, int(scene.get("asset_start_ms") or 0))
    end_ms = max(0, int(scene.get("asset_end_ms") or 0))
    return {
        "library_origin": "owned",
        "asset_id": asset_id,
        "event_clip_id": None,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": max(0, end_ms - start_ms),
        "asset_segment_id": scene.get("asset_segment_id"),
    }
