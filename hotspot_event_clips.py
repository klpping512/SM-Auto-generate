"""热点视频事件片段：只用已有字幕/OCR/镜头证据做本地分组和命名。"""
from __future__ import annotations

import re
from collections import defaultdict


LOCATION_MAP = {
    "cape town": ("开普敦", "Cape Town"),
    "western cape": ("西开普省", "Western Cape"),
    "kgalagadi": ("卡拉哈迪跨境公园", "Kgalagadi Transfrontier Park"),
    "kruger": ("克鲁格国家公园", "Kruger National Park"),
    "limpopo": ("林波波省", "Limpopo"),
    "mpumalanga": ("姆普马兰加省", "Mpumalanga"),
    "johannesburg": ("约翰内斯堡", "Johannesburg"),
    "durban": ("德班", "Durban"),
}
ENTITY_MAP = {
    "transnet_land": ("Transnet 土地", "Transnet Land Event"),
    "transnet": ("Transnet", "Transnet"),
    "land occupation": ("土地占用", "Land Occupation"),
    "unlawful occupation": ("土地占用", "Land Occupation"),
    "park": ("公园动态", "Park Update"),
    "scene_update": ("现场动态", "Scene Update"),
}


def _text(segment: dict) -> str:
    return " ".join(str(segment.get(key) or "") for key in ("transcript", "ocr_text", "description")).strip()


def _signature(segment: dict) -> tuple[str | None, str | None, set[str]]:
    text = _text(segment).casefold()
    location = next((key for key in LOCATION_MAP if key in text), None)
    entity = "transnet_land" if "transnet" in text and ("occupation" in text or "land" in text) else next((key for key in ENTITY_MAP if key in text), None)
    tokens = set(re.findall(r"[a-z][a-z'-]{2,}", text))
    return location, entity, tokens


def _should_split(current: dict, segment: dict) -> bool:
    location, entity, _ = _signature(segment)
    if not current["segments"]:
        return False
    if location and current.get("location") and location != current["location"]:
        return True
    if entity and current.get("entity") and entity != current["entity"]:
        return True
    duration = int(segment.get("end_ms") or 0) - int(current["start_ms"])
    return duration >= 25_000 and bool(location or entity)


def _source_title_matches_event(source_title: str, event: dict) -> bool:
    """A source headline may label a clip only when analysed evidence corroborates it."""
    title_tokens = set(re.findall(r"[a-z][a-z'-]{2,}", str(source_title).casefold()))
    generic = {"the", "and", "for", "with", "from", "near", "news", "live", "video", "south", "africa"}
    title_tokens -= generic
    if len(title_tokens) < 2:
        return False
    event_text = " ".join(_text(segment) for segment in event.get("segments") or []).casefold()
    event_tokens = set(re.findall(r"[a-z][a-z'-]{2,}", event_text))
    return len(title_tokens & event_tokens) >= 2


def _event_name(event: dict, date: str, source: str, index: int, source_title: str) -> tuple[str, str, float, str] | None:
    location = event.get("location")
    entity = event.get("entity")
    if _source_title_matches_event(source_title, event):
        title = str(source_title).strip()[:160]
        suffix = f"｜现场片段 {index}"
        return title + suffix, title + f" | Clip {index}", 0.72, "review_required"
    if not location and not entity:
        return None
    zh_location, en_location = LOCATION_MAP[location] if location else ("南非", "South Africa")
    if entity:
        zh_entity, en_entity = ENTITY_MAP[entity]
    else:
        zh_entity, en_entity = ENTITY_MAP["scene_update"]
    zh = f"{date}｜{zh_location}｜{zh_entity}事件｜{source}"
    en = f"{date} | {en_location} | {en_entity} | {source}"
    confidence = 0.88 if location and entity else 0.68
    return zh, en, confidence, "confirmed" if confidence >= 0.75 else "review_required"


def build_event_clips(segments: list[dict], date: str, source: str, source_title: str = "") -> list[dict]:
    """将有可解释证据的镜头按地点/实体连续性聚合为事件片段。"""
    ordered = sorted(segments, key=lambda item: int(item.get("start_ms") or 0))
    groups: list[dict] = []
    for segment in ordered:
        location, entity, tokens = _signature(segment)
        if not groups or _should_split(groups[-1], segment):
            groups.append({
                "start_ms": int(segment.get("start_ms") or 0),
                "end_ms": int(segment.get("end_ms") or 0),
                "location": location,
                "entity": entity,
                "tokens": set(tokens),
                "segments": [segment],
            })
        else:
            group = groups[-1]
            group["end_ms"] = max(group["end_ms"], int(segment.get("end_ms") or 0))
            group["location"] = group.get("location") or location
            group["entity"] = group.get("entity") or entity
            group["tokens"].update(tokens)
            group["segments"].append(segment)
    result = []
    for index, group in enumerate(groups, 1):
        named = _event_name(group, date, source, index, source_title)
        if not named:
            continue
        title_zh, title_en, confidence, review_status = named
        result.append({
            "event_index": index,
            "start_ms": group["start_ms"],
            "end_ms": group["end_ms"],
            "title_zh": title_zh,
            "title_en": title_en,
            "location": group.get("location"),
            "entities": sorted(group["tokens"]),
            "keywords": sorted(group["tokens"]),
            "confidence": confidence,
            "review_status": review_status,
            "segments": group["segments"],
        })
    return result
