"""Deterministic normalization, clustering, and scoring for hotspot topic packages."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

import hotspot_lexicon

# Back-compat aliases; canonical terms live in hotspot_lexicon.
LOGISTICS_TERMS = hotspot_lexicon.EVENT_TYPES
EVENT_RELEVANCE = hotspot_lexicon.EVENT_RELEVANCE
HEAT_WEIGHTS = {
    "search_growth": 0.25,
    "local_coverage": 0.20,
    "cross_platform": 0.15,
    "video_growth": 0.15,
    "freshness": 0.10,
    "logistics_relevance": 0.10,
    "media_richness": 0.05,
}
CITY_NAMES = (
    "Johannesburg", "Cape Town", "Durban", "Pretoria", "Gqeberha", "Bloemfontein",
    "East London", "Polokwane", "Nelspruit", "South Africa",
)
ENTITY_PATTERNS = {
    "driver": ("driver", "drivers", "e-hailing", "uber", "bolt"),
    "transnet": ("transnet",),
    "customs": ("customs", "sars", "海关"),
    "port": ("port", "harbour", "港口"),
    "warehouse": ("warehouse", "仓库"),
    "amazon": ("amazon",),
    "temu": ("temu",),
    "takealot": ("takealot",),
}
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)


def _clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _as_mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _parse_datetime(value) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _tokens(value: str) -> set[str]:
    normalized = _clean_text(value).casefold().replace("e-hailing", "driver")
    tokens = set(TOKEN_RE.findall(normalized))
    if "drivers" in tokens:
        tokens.remove("drivers")
        tokens.add("driver")
    return {token for token in tokens if len(token) > 1}


def _locations(text: str) -> list[str]:
    lower = text.casefold()
    return [city for city in CITY_NAMES if city.casefold() in lower]


def _entities(text: str) -> list[str]:
    lower = text.casefold()
    return [entity for entity, terms in ENTITY_PATTERNS.items() if any(term.casefold() in lower for term in terms)]


def classify_event(text: str) -> tuple[str, float]:
    """Classify a signal and return its event type plus logistics relevance (0–100)."""
    return hotspot_lexicon.classify_event_type_with_relevance(_clean_text(text))


def normalize_signal(raw: dict) -> dict:
    """Normalize feed and platform metadata into one safe, deterministic signal schema."""
    title = _clean_text(raw.get("title"))[:300]
    summary = _clean_text(raw.get("summary"))[:2000]
    source_url = _clean_text(raw.get("source_url"))
    source_type = _clean_text(raw.get("source_type") or "news").casefold() or "news"
    external_id = _clean_text(raw.get("external_id"))
    if not external_id:
        external_id = hashlib.sha256(f"{source_type}|{source_url}|{title}".encode("utf-8")).hexdigest()
    text = f"{title} {summary}"
    event_type, relevance = classify_event(text)
    published_at = _clean_text(raw.get("published_at")) or None
    retrieved_at = _clean_text(raw.get("retrieved_at")) or datetime.now(timezone.utc).isoformat()
    metrics = _as_mapping(raw.get("metrics"))
    return {
        "hotspot_id": raw.get("hotspot_id"),
        "source_name": _clean_text(raw.get("source_name") or raw.get("publisher") or source_type)[:200],
        "source_type": source_type,
        "external_id": external_id[:500],
        "title": title,
        "summary": summary,
        "source_url": source_url,
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "metrics": metrics,
        "raw_payload": _as_mapping(raw.get("raw_payload")),
        "event_type": event_type,
        "logistics_relevance": relevance,
        "locations": _locations(text),
        "entities": _entities(text),
    }


def _value_between_zero_and_hundred(value) -> float:
    try:
        return max(0.0, min(float(value), 100.0))
    except (TypeError, ValueError):
        return 0.0


def _average_metric(signals: list[dict], name: str) -> float:
    values = [_value_between_zero_and_hundred(_as_mapping(item.get("metrics")).get(name)) for item in signals]
    return sum(values) / len(values) if values else 0.0


def _freshness_score(signals: list[dict], now: datetime) -> float:
    dates = [_parse_datetime(item.get("published_at") or item.get("retrieved_at")) for item in signals]
    latest = max((date for date in dates if date is not None), default=None)
    if latest is None:
        return 0.0
    age_hours = max(0.0, (now - latest).total_seconds() / 3600)
    return max(0.0, 100.0 - age_hours / 48 * 100.0)


def _heat_breakdown(signals: list[dict], now: datetime | None = None) -> dict[str, float]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_names = {item.get("source_name") for item in signals if item.get("source_name")}
    source_types = {item.get("source_type") for item in signals if item.get("source_type")}
    event_relevance = [float(item.get("logistics_relevance") or 0) for item in signals]
    media_items = sum(int(_as_mapping(item.get("metrics")).get("media_count") or 0) for item in signals)
    return {
        "search_growth": _average_metric(signals, "search_growth"),
        "local_coverage": min(100.0, len(source_names) / 5 * 100),
        "cross_platform": min(100.0, len(source_types) / 3 * 100),
        "video_growth": _average_metric(signals, "video_growth"),
        "freshness": _freshness_score(signals, now),
        "logistics_relevance": sum(event_relevance) / len(event_relevance) if event_relevance else 0.0,
        "media_richness": min(100.0, media_items / 5 * 100),
    }


def calculate_heat_score(signals: list[dict], *, now: datetime | None = None) -> float:
    """Calculate the documented 100-point heat score using deterministic source metadata."""
    normalized = [normalize_signal(signal) for signal in signals]
    breakdown = _heat_breakdown(normalized, now)
    return round(sum(breakdown[name] * weight for name, weight in HEAT_WEIGHTS.items()), 2)


def _signal_similarity(first: dict, second: dict) -> float:
    first_text = f"{first['title']} {first['summary']}"
    second_text = f"{second['title']} {second['summary']}"
    first_tokens, second_tokens = _tokens(first_text), _tokens(second_text)
    token_similarity = len(first_tokens & second_tokens) / max(1, len(first_tokens | second_tokens))
    lexical_similarity = SequenceMatcher(None, first_text.casefold(), second_text.casefold()).ratio()
    shared_locations = set(first["locations"]) & set(second["locations"])
    shared_entities = set(first["entities"]) & set(second["entities"])
    event_match = first["event_type"] != "unknown" and first["event_type"] == second["event_type"]
    return min(
        1.0,
        lexical_similarity * 0.25 + token_similarity * 0.30
        + (0.25 if shared_locations else 0.0)
        + (0.25 if shared_entities else 0.0)
        + (0.20 if event_match else 0.0),
    )


def _can_cluster(first: dict, second: dict) -> bool:
    dates = (_parse_datetime(first.get("published_at")), _parse_datetime(second.get("published_at")))
    if all(dates) and abs((dates[0] - dates[1]).total_seconds()) > 48 * 3600:
        return False
    shares_entity = bool(set(first["locations"]) & set(second["locations"]) or set(first["entities"]) & set(second["entities"]))
    return shares_entity and _signal_similarity(first, second) >= 0.82


def cluster_signals(signals: list[dict]) -> list[dict]:
    """Group compatible signals into event packages; uncertain singletons remain unconfirmed."""
    normalized = [normalize_signal(signal) for signal in signals]
    normalized.sort(key=lambda item: item.get("published_at") or item.get("retrieved_at") or "", reverse=True)
    clusters: list[list[dict]] = []
    for signal in normalized:
        for cluster in clusters:
            if any(_can_cluster(signal, candidate) for candidate in cluster):
                cluster.append(signal)
                break
        else:
            clusters.append([signal])

    packages = []
    for cluster in clusters:
        primary = cluster[0]
        event_type, relevance = classify_event(" ".join(
            f"{signal['title']} {signal['summary']}" for signal in cluster
        ))
        locations = sorted({location for signal in cluster for location in signal["locations"]})
        entities = sorted({entity for signal in cluster for entity in signal["entities"]})
        breakdown = _heat_breakdown(cluster)
        heat_score = round(sum(breakdown[name] * weight for name, weight in HEAT_WEIGHTS.items()), 2)
        packages.append({
            "title": primary["title"],
            "summary": primary["summary"],
            "event_type": event_type,
            "logistics_relevance": max(relevance, breakdown["logistics_relevance"]),
            "locations": locations,
            "entities": entities,
            "signals": cluster,
            "heat_score": heat_score,
            "heat_state": "unconfirmed" if len(cluster) == 1 else "rising",
            "breakdown": breakdown,
        })
    return sorted(packages, key=lambda item: (-item["heat_score"], item["title"].casefold()))
