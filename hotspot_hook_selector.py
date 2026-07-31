"""Deterministic Hook ranking for pre-analysed hotspot event clips."""
from __future__ import annotations


HOOK_TERMS = (
    "traffic", "congestion", "screening", "road", "port", "strike", "storm", "flood",
    "delivery", "warehouse", "customs", "拥堵", "交通", "道路", "港口", "罢工", "暴雨", "配送", "清关",
)


def _text(event: dict) -> str:
    return " ".join([
        str(event.get("title_zh") or ""), str(event.get("title_en") or ""),
        str(event.get("location") or ""), *[str(item) for item in (event.get("keywords") or [])],
        *[str(item) for item in (event.get("entities") or [])],
    ]).casefold()


def rank_hook_clips(events: list[dict], *, limit: int = 3) -> list[dict]:
    """Return explainable 5–12 second hook candidates; no model and no user picking."""
    ranked = []
    for event in events:
        if event.get("clip_status") not in {None, "ready", "pending"}:
            continue
        duration_ms = max(0, int(event.get("end_ms") or 0) - int(event.get("start_ms") or 0))
        text = _text(event)
        matched = [term for term in HOOK_TERMS if term.casefold() in text]
        score = min(40, len(matched) * 12)
        score += 25 if 5_000 <= duration_ms <= 12_000 else 15 if 3_000 <= duration_ms <= 20_000 else 5
        score += round(float(event.get("confidence") or 0) * 20)
        if event.get("review_status") == "confirmed":
            score += 10
        description = str(event.get("title_zh") or event.get("title_en") or "热点现场")
        reasons = (["物流/风险关键词：" + "、".join(matched[:3])] if matched else [])
        reasons.append(f"片段时长 {duration_ms / 1000:.1f} 秒")
        ranked.append({
            "event_clip_id": event.get("id"), "asset_id": event.get("asset_id"),
            "start_ms": event.get("start_ms"), "end_ms": event.get("end_ms"),
            "event_identity": str((event.get("evidence") or {}).get("event_identity") or "").strip(),
            "hook_score": min(100, score), "hook_reason": "；".join(reasons),
            "content_description": description[:240],
        })
    ranked.sort(key=lambda item: (-item["hook_score"], int(item.get("event_clip_id") or 0)))
    return ranked[:max(1, min(3, limit))]
