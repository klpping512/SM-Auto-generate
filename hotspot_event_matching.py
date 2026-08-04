"""事件级双素材库匹配；纯本地证据排序，不调用模型。"""
from __future__ import annotations

import hotspot_lexicon


def _terms(event: dict) -> set[str]:
    raw = " ".join(str(event.get(key) or "") for key in ("title_zh", "title_en", "location"))
    raw += " " + " ".join(str(value) for value in event.get("keywords", []))
    return hotspot_lexicon.extract_terms(raw)


def _rank(event: dict, segments: list[dict], origin: str) -> list[dict]:
    wanted = _terms(event)
    scored = []
    for segment in segments:
        text = " ".join(str(segment.get(key) or "") for key in ("description", "transcript", "ocr_text"))
        hits = sorted(wanted & hotspot_lexicon.extract_terms(text))
        if not hits:
            continue
        item = dict(segment)
        item["library_origin"] = origin
        item["match_score"] = round(min(1.0, len(hits) / max(1, len(wanted))), 3)
        item["match_reasons"] = [f"关键词匹配：{'、'.join(hits[:12])}"]
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


def diagnose_event_matching(event: dict, segments: list[dict]) -> dict:
    """纯观测：复用 ``_terms`` / ``extract_terms``，暴露词面匹配结构性短板。"""
    wanted = _terms(event)
    owned = [item for item in segments if not item.get("asset_hotspot_id")]
    near_misses: list[dict] = []
    for segment in owned:
        text = " ".join(str(segment.get(key) or "") for key in ("description", "transcript", "ocr_text"))
        text_terms = hotspot_lexicon.extract_terms(text)
        if not text_terms:
            continue
        overlap = sorted(wanted & text_terms)
        if overlap:
            continue
        near_misses.append({
            "segment_id": segment.get("id"),
            "primary_category": str(segment.get("primary_category") or ""),
            "text_terms_sample": sorted(text_terms)[:12],
            "overlap": overlap,
        })
    near_misses.sort(key=lambda row: (-len(row["text_terms_sample"]), int(row.get("segment_id") or 0)))

    if not wanted:
        verdict = "no_wanted_terms"
    elif not owned:
        verdict = "pool_empty"
    elif _rank(event, owned, "owned"):
        verdict = "matched"
    else:
        verdict = "no_overlap"

    return {
        "wanted_terms": sorted(wanted),
        "owned_pool": len(owned),
        "near_misses": near_misses[:5],
        "verdict": verdict,
    }
