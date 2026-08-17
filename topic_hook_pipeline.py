"""Theme → logistics Hook matching and targeted intake query.

A chat topic must close to matched_ready, a real discovery job, or an
explained no_match. Unrelated news Hooks must not fill the ready bucket.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

import hotspot_intake_policy

TRANSNET_SAMPLE_TOPIC = "Transnet又有动静！跨境卖家先别慌"

OFFICIAL_ENTITY_PROFILES = {
    "transnet": {
        "entities": ["Transnet", "南非"],
        "logistics_nodes": ["港口", "铁路", "集装箱", "跨境运输", "仓储"],
        "scene_terms": ["港口作业", "铁路运输", "堆场", "装卸", "排队", "延误"],
        "scene_keys": {"port", "warehouse"},
        "publishers": ["Transnet NPA", "Transnet National Ports Authority", "Transnet Port Terminals"],
    },
    "sanral": {
        "entities": ["SANRAL", "南非"],
        "logistics_nodes": ["公路", "干线运输", "配送"],
        "scene_terms": ["道路施工", "封路", "排队", "延误"],
        "scene_keys": {"road", "disruption"},
        "publishers": ["SANRAL Corporate", "SANRAL"],
    },
    "sars": {
        "entities": ["SARS", "南非"],
        "logistics_nodes": ["海关", "清关", "跨境运输"],
        "scene_terms": ["查验", "放行", "申报"],
        "scene_keys": {"customs", "border"},
        "publishers": ["SARS Customs Updates"],
    },
}

ENTITY_ALIASES = {
    "transnet": ("transnet", "npa"),
    "sanral": ("sanral",),
    "sars": ("sars", "customs"),
}

NODE_SCENE_KEYS = {
    "港口": "port", "码头": "port", "集装箱": "port", "铁路": "port",
    "公路": "road", "道路": "road", "干线运输": "road",
    "海关": "customs", "清关": "customs", "边境": "border", "口岸": "border",
    "仓储": "warehouse", "仓库": "warehouse", "配送": "last_mile",
    "跨境运输": "port",
}

ACTIVE_DISCOVERY_STATUSES = frozenset({
    "pending", "queued", "processing", "fetching", "downloading",
    "analyzing", "reviewing", "matched",
})
DISPLAY_STAGES = (
    "queued", "fetching", "downloading", "analyzing", "reviewing",
    "matched", "ready", "no_match", "failed",
)
LEGACY_STAGE_MAP = {
    "pending": "queued",
    "queued": "queued",
    "processing": "fetching",
    "fetch_sources": "fetching",
    "analyze_media": "analyzing",
    "hooks_ready": "matched",
    "matched": "matched",
    "unmatched": "no_match",
    "cancelled_misrouted": "failed",
    "done": "no_match",
}


def autofetch_enabled() -> bool:
    return str(os.environ.get("TOPIC_HOOK_AUTOFETCH_ENABLED", "1")).strip() != "0"


def autofetch_timeout_seconds() -> int:
    try:
        return max(30, int(os.environ.get("TOPIC_HOOK_AUTOFETCH_TIMEOUT_SECONDS", "120")))
    except ValueError:
        return 120


def autofetch_max_candidates() -> int:
    try:
        return max(1, min(40, int(os.environ.get("TOPIC_HOOK_AUTOFETCH_MAX_CANDIDATES", "20"))))
    except ValueError:
        return 20


def autofetch_source_classes() -> list[str]:
    raw = str(os.environ.get(
        "TOPIC_HOOK_AUTOFETCH_SOURCE_CLASSES",
        "official_logistics,logistics_news,general_news",
    ))
    values = [item.strip() for item in raw.split(",") if item.strip()]
    allowed = set(hotspot_intake_policy.SOURCE_CLASSES)
    return [item for item in values if item in allowed] or ["official_logistics", "logistics_news"]


def structure_topic(text: str, *, time_window_days: int = 30) -> dict:
    """Turn a free-form logistics topic into retrieval conditions."""
    topic = " ".join(str(text or "").split())[:300]
    folded = topic.casefold()
    entities: list[str] = []
    logistics_nodes: list[str] = []
    scene_terms: list[str] = []
    scene_keys: set[str] = set()
    publishers: list[str] = []
    for key, aliases in ENTITY_ALIASES.items():
        if any(alias in folded for alias in aliases):
            profile = OFFICIAL_ENTITY_PROFILES[key]
            for item in profile["entities"]:
                if item not in entities:
                    entities.append(item)
            for item in profile["logistics_nodes"]:
                if item not in logistics_nodes:
                    logistics_nodes.append(item)
            for item in profile["scene_terms"]:
                if item not in scene_terms:
                    scene_terms.append(item)
            scene_keys.update(profile["scene_keys"])
            for item in profile["publishers"]:
                if item not in publishers:
                    publishers.append(item)
    if "南非" in topic and "南非" not in entities:
        entities.append("南非")
    for node, scene in NODE_SCENE_KEYS.items():
        if node in topic and node not in logistics_nodes:
            logistics_nodes.append(node)
            scene_keys.add(scene)
    if not logistics_nodes:
        logistics_nodes = ["港口", "铁路", "跨境运输"] if "transnet" in folded else ["运输", "仓储", "配送"]
    if not scene_terms:
        scene_terms = ["港口作业", "铁路运输", "堆场", "装卸", "排队", "延误"] if "transnet" in folded else ["作业现场"]
    return {
        "topic": topic,
        "entities": entities,
        "logistics_nodes": logistics_nodes,
        "scene_terms": scene_terms,
        "scene_keys": sorted(scene_keys),
        "preferred_publishers": publishers,
        "time_window_days": int(time_window_days),
        "source_classes": autofetch_source_classes(),
        "max_candidates": autofetch_max_candidates(),
    }


def display_stage(status: str | None, stage: str | None = None) -> str:
    raw = str(stage or status or "queued").strip()
    mapped = LEGACY_STAGE_MAP.get(raw, raw)
    if mapped == "matched" and str(status or "") == "matched":
        return "ready" if stage in {"hooks_ready", "ready"} else "matched"
    return mapped if mapped in DISPLAY_STAGES else "queued"


def _event_blob(event: dict) -> str:
    evidence = event.get("evidence") or {}
    return " ".join(
        str(value or "")
        for value in (
            event.get("title_zh"), event.get("title_en"),
            evidence.get("what_happened"), evidence.get("event_identity"),
            evidence.get("logistics_question"),
            " ".join(str(item) for item in (event.get("keywords") or [])),
            " ".join(str(item) for item in (event.get("logistics_scenes") or [])),
        )
    )


def _within_window(event: dict, media: dict | None, days: int, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    for raw in (
        (media or {}).get("published_at"),
        event.get("parent_published_at"),
        event.get("created_at"),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= now - timedelta(days=max(1, days))
    return True


def _match_reasons(query: dict, event: dict, media: dict | None) -> tuple[int, list[str], list[str]]:
    """Return score, hits, and gap reasons."""
    blob = _event_blob(event).casefold()
    publisher = str((media or {}).get("publisher") or "")
    source_class = hotspot_intake_policy.resolve_source_class(media or {"publisher": publisher})
    hits: list[str] = []
    gaps: list[str] = []
    score = 0
    entity_hit = False
    for entity in query.get("entities") or []:
        folded = entity.casefold()
        if folded and (folded in blob or folded in publisher.casefold()):
            entity_hit = True
            hits.append(f"实体命中:{entity}")
            score += 40
    if query.get("preferred_publishers") and publisher in set(query["preferred_publishers"]):
        hits.append(f"官方信源:{publisher}")
        score += 24
        entity_hit = True
    question = str((event.get("evidence") or {}).get("logistics_question") or "")
    node_hit = False
    for node in query.get("logistics_nodes") or []:
        if node and (node in question or node in _event_blob(event)):
            node_hit = True
            hits.append(f"物流节点:{node}")
            score += 12
    scenes = set(hotspot_intake_policy.real_logistics_scenes(
        event.get("logistics_scenes"),
        _event_blob(event),
    ))
    wanted = set(query.get("scene_keys") or [])
    if wanted and scenes & wanted:
        hits.append("真实物流场景命中")
        score += 16
    elif wanted and scenes and not (scenes & wanted):
        gaps.append(f"场景是{sorted(scenes)}，主题需要{sorted(wanted)}")
        score -= 20
    if not scenes:
        gaps.append("缺少真实物流场景，hotspot 不能代替港口/道路/仓储")
        score -= 30
    if hotspot_intake_policy.is_placeholder_logistics_question(question):
        gaps.append("缺少具体 logistics_question")
        score -= 20
    if source_class == "general_news" and not entity_hit:
        gaps.append("综合新闻未命中主题实体，不得冒充物流 Hook")
        score -= 40
    elif source_class in hotspot_intake_policy.OFFICIAL_OR_LOGISTICS:
        score += 8
    if not entity_hit and not node_hit:
        gaps.append("标题/事实未命中主题实体或物流节点")
        score -= 50
    return score, hits, gaps


def match_topic_hooks(
    query: dict,
    events: list[dict],
    *,
    media_by_asset: dict[int, dict] | None = None,
    is_ready: Callable[[dict], bool] | None = None,
    is_audit: Callable[[dict], bool] | None = None,
    now: datetime | None = None,
) -> dict:
    """Split library hits into ready / audit_only / unmatched with reasons."""
    media_by_asset = media_by_asset or {}
    matched_ready: list[dict] = []
    matched_audit_only: list[dict] = []
    unmatched: list[dict] = []
    news_ready = 0
    for event in events:
        asset_id = int(event.get("asset_id") or 0)
        media = media_by_asset.get(asset_id) or {}
        score, hits, gaps = _match_reasons(query, event, media)
        window_ok = _within_window(event, media, int(query.get("time_window_days") or 30), now=now)
        if not window_ok:
            gaps.append(f"超出 {query.get('time_window_days')} 天时效窗")
            score -= 15
        ready = bool(is_ready(event)) if is_ready else False
        audit = bool(is_audit(event)) if is_audit else False
        row = {
            "event_clip_id": event.get("id"),
            "asset_id": event.get("asset_id"),
            "title": event.get("title_zh") or event.get("title_en"),
            "score": score,
            "hits": hits,
            "gaps": gaps,
            "source_class": hotspot_intake_policy.resolve_source_class(media or {}),
            "publisher": media.get("publisher"),
        }
        related = score >= 40 and bool(hits) and not any(item.startswith("场景是") for item in gaps)
        if related and ready and not gaps:
            matched_ready.append(row)
            if row["source_class"] == "general_news":
                news_ready += 1
        elif related and (audit or ready):
            if not gaps and not ready:
                gaps.append("已审计但不可直接成片")
                row["gaps"] = gaps
            matched_audit_only.append(row)
        else:
            unmatched.append(row)
    matched_ready.sort(key=lambda item: (item["score"], int(item.get("event_clip_id") or 0)), reverse=True)
    if (
        matched_ready
        and len(matched_ready) >= hotspot_intake_policy.news_ratio_min_sample()
        and (news_ready / len(matched_ready)) > hotspot_intake_policy.news_hook_max_ratio()
    ):
        demoted = [item for item in matched_ready if item.get("source_class") == "general_news"]
        matched_ready = [item for item in matched_ready if item.get("source_class") != "general_news"]
        for item in demoted:
            item["gaps"] = list(item.get("gaps") or []) + ["综合新闻占比超过 0.30，降入审计归档"]
            matched_audit_only.append(item)
    matched_audit_only.sort(key=lambda item: item["score"], reverse=True)
    return {
        "query": query,
        "matched_ready": matched_ready,
        "matched_audit_only": matched_audit_only,
        "unmatched": unmatched,
        "checked": len(events),
        "news_ready_ratio": (news_ready / len(matched_ready)) if matched_ready else 0.0,
    }


def prefer_official_channels(channels: list[dict], query: dict) -> list[dict]:
    """Transnet NPA → SANRAL → other official → logistics news → general news."""
    preferred = {str(item).casefold() for item in (query.get("preferred_publishers") or [])}
    wanted = set(query.get("source_classes") or autofetch_source_classes())

    def rank(channel: dict) -> tuple[int, int, str]:
        name = str(channel.get("name") or "")
        source_class = str(channel.get("source_class") or hotspot_intake_policy.resolve_source_class(channel))
        official_first = 2
        lowered = name.casefold()
        if "transnet" in lowered or lowered in preferred:
            official_first = 0
        elif "sanral" in lowered:
            official_first = 1
        class_rank = hotspot_intake_policy.SOURCE_CLASS_PRIORITY.get(source_class, 9)
        return (official_first, class_rank, name)

    filtered = [
        item for item in channels
        if str(item.get("source_class") or hotspot_intake_policy.resolve_source_class(item)) in wanted
        or any(token in str(item.get("name") or "").casefold() for token in ("transnet", "sanral"))
    ]
    ordered = sorted(filtered or list(channels), key=rank)
    limit = int(query.get("max_candidates") or autofetch_max_candidates())
    return ordered[: max(1, min(limit, len(ordered) or 1))]


def prefer_official_feeds(feeds: list[dict], query: dict) -> list[dict]:
    preferred = {str(item).casefold() for item in (query.get("preferred_publishers") or [])}
    entities = {str(item).casefold() for item in (query.get("entities") or [])}

    def rank(feed: dict) -> tuple[int, str]:
        name = str(feed.get("name") or "")
        blob = f"{name} {feed.get('purpose') or ''}".casefold()
        hit = 0 if (name.casefold() in preferred or entities & set(re.findall(r"[a-z]+", blob))) else 1
        return (hit, name)

    official = [
        item for item in feeds
        if any(token in str(item.get("name") or "").casefold() for token in ("transnet", "sanral", "sars", "samsa", "border", "npa"))
    ]
    return sorted(official or feeds, key=rank)


def discovery_payload(request: dict, *, hooks: list[dict] | None = None) -> dict:
    status = str(request.get("status") or "queued")
    stage = display_stage(status, request.get("stage"))
    return {
        "id": request.get("id"),
        "job_type": request.get("job_type") or "topic_targeted_hotspot_intake",
        "topic": request.get("topic"),
        "status": "no_match" if status == "unmatched" else status,
        "stage": stage,
        "candidate_count": int(request.get("candidate_count") or 0),
        "error_message": request.get("error_message"),
        "matched_media_id": request.get("matched_media_id"),
        "next_run_time": request.get("next_run_time"),
        "updated_at": request.get("updated_at"),
        "hooks": hooks or [],
        "query": request.get("query") if isinstance(request.get("query"), dict) else None,
    }


def transnet_bridge_lines(query: dict, event: dict) -> dict:
    """Hotspot fact → logistics impact → Buffalo advantage, no empty fillers."""
    evidence = event.get("evidence") or {}
    happened = str(evidence.get("what_happened") or event.get("title_zh") or "港口作业出现变化").strip()[:40]
    node = next(iter(query.get("logistics_nodes") or ["港口"]), "港口")
    question = str(evidence.get("logistics_question") or f"{node}作业变化后，跨境货物要先核对哪一环？")
    return {
        "hook_fact": f"{happened}。先看现场发生了什么，不夸大、不改归因。",
        "logistics_impact": f"这会直接影响{node}的装卸、堆场或跨境运输节奏；{question}",
        "buffalo_advantage": (
            "Buffalo 用仓内核对、分拣留痕和可追踪交接，把同一风险落到卖家能看见的动作上，"
            "而不是口头承诺安全。"
        ),
    }
