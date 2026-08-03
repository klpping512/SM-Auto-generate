"""Recommend currently producible chat topics from the ready Hook library.

Used when a user topic has no event anchor or no qualifying Hook — never as a
way to force-match unrelated accident footage onto a broad market pitch.
"""
from __future__ import annotations

from collections import defaultdict

import hotspot_lexicon

SCENE_LABELS = {
    "warehouse": "海外仓/仓储作业",
    "last_mile": "末端配送",
    "port": "港口作业",
    "border": "口岸/清关",
    "disruption": "道路/异常事件",
    "cost_risk": "成本与履约风险",
}

GENERIC_DISALLOWED = (
    "市政", "环卫", "垃圾", "污水", "公园", "野生动物", "治安", "犯罪",
    "政治", "委员会", "证词", "娱乐", "听证会", "体育", "运动会",
    "municipal", "refuse", "waste", "sewage", "wildlife", "testimony",
    "commission", "hearing", "sport", "commonwealth",
)


def hook_logistics_scenes(event: dict) -> list[str]:
    """Prefer persisted scenes; fall back to lexicon on fact text."""
    scenes = event.get("logistics_scenes")
    if isinstance(scenes, list) and scenes:
        return [str(item) for item in scenes if item]
    raw = event.get("logistics_scenes_json")
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw if item]
    fact = " ".join(
        str(value or "")
        for value in (
            event.get("title_zh"),
            event.get("title_en"),
            (event.get("evidence") or {}).get("what_happened"),
        )
    )
    return sorted(hotspot_lexicon.category_profile(fact, mode="event"))


def is_generic_logistics_eligible(event: dict) -> bool:
    """Reject public-affairs / sports footage from the generic opener pool."""
    blob = " ".join(
        str(value or "")
        for value in (
            event.get("title_zh"),
            event.get("title_en"),
            (event.get("evidence") or {}).get("what_happened"),
            (event.get("evidence") or {}).get("logistics_question"),
        )
    ).casefold()
    if any(term.casefold() in blob for term in GENERIC_DISALLOWED):
        return False
    scenes = set(hook_logistics_scenes(event))
    return bool(scenes & {"warehouse", "last_mile", "port", "border", "disruption"})


def recommend_producible_topics(
    events: list[dict],
    *,
    limit: int = 5,
    hotspots_by_id: dict[int, dict] | None = None,
) -> list[dict]:
    """Cluster ready Hooks into 3–5 clickable topic suggestions."""
    hotspots_by_id = hotspots_by_id or {}
    buckets: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if str(event.get("review_status") or "") != "confirmed":
            continue
        if str(event.get("clip_status") or "") != "ready" or not event.get("clip_path"):
            continue
        scenes = hook_logistics_scenes(event)
        if not scenes:
            scenes = ["disruption"]
        primary = scenes[0]
        buckets[primary].append(event)

    suggestions: list[dict] = []
    preferred_order = ("border", "port", "disruption", "warehouse", "last_mile", "cost_risk")
    for scene in preferred_order:
        rows = buckets.get(scene) or []
        if not rows:
            continue
        event = rows[0]
        hotspot = hotspots_by_id.get(int(event.get("hotspot_id") or 0)) or {}
        title = str(
            event.get("title_zh")
            or hotspot.get("title_zh")
            or hotspot.get("title")
            or "物流现场"
        ).strip()[:48]
        question = str((event.get("evidence") or {}).get("logistics_question") or "").strip()
        if scene == "border":
            topic = f"{title}：口岸排队会先影响哪段交期？"
        elif scene == "port":
            topic = f"{title}：港口拥堵时发货前要核对什么？"
        elif scene == "disruption":
            topic = f"{title}：道路异常后如何调整履约节奏？"
        elif scene == "warehouse":
            topic = f"{title}：海外仓作业如何缓冲上游波动？"
        elif scene == "last_mile":
            topic = f"{title}：末端配送异常如何提前沟通？"
        else:
            topic = f"{title}：进入南非市场前要重查哪些成本风险？"
        if question and question not in topic:
            topic = f"{title}：{question}"[:80]
        suggestions.append({
            "topic": topic,
            "scene": scene,
            "scene_label": SCENE_LABELS.get(scene, scene),
            "hook_event_id": int(event["id"]),
            "hotspot_id": int(event.get("hotspot_id") or 0) or None,
            "hook_title": title,
            "reason": f"库内已有可播放的「{SCENE_LABELS.get(scene, scene)}」Hook，现在就能拍。",
        })
        if len(suggestions) >= max(3, min(int(limit), 5)):
            break
    return suggestions[:max(3, min(int(limit), 5))] if suggestions else []
