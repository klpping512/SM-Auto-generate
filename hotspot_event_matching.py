"""事件级双素材库匹配；纯本地证据排序，不调用模型。"""
from __future__ import annotations

import re


def _terms(event: dict) -> set[str]:
    raw = " ".join(str(event.get(key) or "") for key in ("title_zh", "title_en", "location"))
    raw += " " + " ".join(str(value) for value in event.get("keywords", []))
    return set(re.findall(r"[a-z][a-z'-]{2,}", raw.casefold()))


def _rank(event: dict, segments: list[dict], origin: str) -> list[dict]:
    wanted = _terms(event)
    scored = []
    for segment in segments:
        text = " ".join(str(segment.get(key) or "") for key in ("description", "transcript", "ocr_text")).casefold()
        hits = sorted(wanted & set(re.findall(r"[a-z][a-z'-]{2,}", text)))
        if not hits:
            continue
        item = dict(segment)
        item["library_origin"] = origin
        item["match_score"] = round(min(1.0, len(hits) / max(1, len(wanted))), 3)
        item["match_reasons"] = [f"关键词匹配：{'、'.join(hits)}"]
        scored.append(item)
    return sorted(scored, key=lambda item: (-item["match_score"], int(item.get("id") or 0)))[:3]


def match_event(event: dict, segments: list[dict]) -> dict:
    hotspot = [item for item in segments if item.get("asset_hotspot_id") == event.get("hotspot_id")]
    owned = [item for item in segments if not item.get("asset_hotspot_id")]
    hotspot_candidates = _rank(event, hotspot, "hotspot")
    owned_candidates = _rank(event, owned, "owned")
    return {
        "event_id": event.get("id"),
        "hotspot_candidates": hotspot_candidates,
        "owned_candidates": owned_candidates,
        "owned_match_reason": "暂无可靠 Buffalo 原有素材关键词重合" if not owned_candidates else "已找到与事件证据重合的自有素材",
        "suggested_role": "brand_proof" if owned_candidates else "not_recommended",
    }
