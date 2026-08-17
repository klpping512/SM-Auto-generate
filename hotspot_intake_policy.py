"""批22 热点入库策略：失败队列、信源分层、新闻占比与物流准入。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Callable

import hotspot_lexicon


SOURCE_CLASSES = (
    "official_logistics",
    "logistics_news",
    "general_news",
    "evergreen_owned",
)
OFFICIAL_OR_LOGISTICS = frozenset({"official_logistics", "logistics_news", "evergreen_owned"})
REAL_LOGISTICS_SCENES = frozenset({
    "warehouse", "last_mile", "port", "border", "disruption", "road", "customs",
})
PSEUDO_LOGISTICS_SCENES = frozenset({"hotspot", "other", "cost_risk", ""})
METADATA_FAILED_STATUS = "metadata_failed"
PAUSED_DOWNLOAD_STATUSES = frozenset({METADATA_FAILED_STATUS, "prefiltered_skip"})
INCREMENTAL_DOWNLOAD_STATUSES = frozenset({
    "metadata_ready", "failed", "download_failed", "pending", "downloading",
    "materialization_retryable",
})
SOURCE_CLASS_PRIORITY = {
    "official_logistics": 0,
    "logistics_news": 1,
    "evergreen_owned": 2,
    "general_news": 3,
}
PRIORITY_PUBLISHERS = ("Transnet NPA", "SANRAL Corporate")
KNOWN_STUCK_MEDIA_IDS = (1315, 666, 665, 664, 468, 467, 466, 465)
PUBLISHER_SOURCE_CLASS = {
    "Transnet NPA": "official_logistics",
    "SANRAL Corporate": "official_logistics",
    "eNCA": "general_news",
    "Newzroom Afrika": "general_news",
    "CNBC Africa": "general_news",
    "BusinessDayTV": "general_news",
    "SABC News": "general_news",
    "SA Today": "general_news",
    "South Africa Now": "general_news",
    "Parliament of RSA": "general_news",
    "JusticeGOVZA": "general_news",
    "GovernmentZA": "general_news",
}
GENERIC_BRIDGE_FILLERS = (
    "异常后，Buffalo 核对仓内分拣",
    "接下来看看我们的解决方案",
    "这就是物流安全的重要性",
    "这个变化提醒风险，Buffalo把",
    "承接每一步",
)
PLACEHOLDER_LOGISTICS_QUESTIONS = (
    "会不会影响配送？",
    "物流安全的重要性",
    "接下来看看我们的解决方案",
    "待补充",
    "未记录",
    "unknown",
    "n/a",
)
AUDIT_ONLY_TOPIC_MARKERS = (
    "政治", "议会", "政党", "选举", "总统", "部长", "听证", "法庭", "法院",
    "足球", "联赛", "世界杯", "球队", "女足", "决赛", "赛事", "橄榄球", "板球", "网球",
    "采访", "主播", "演播室", "发布会", "新闻播报",
    "地图", "标题卡", "信息图", "台标",
    "parliament", "election", "president", "minister", "hearing", "court",
    "football", "soccer", "rugby", "cricket", "interview", "studio", "anchor",
)
LOGISTICS_FACT_MARKERS = (
    "港口", "码头", "集装箱", "装卸", "堆场", "port", "harbour", "harbor", "container",
    "边境", "海关", "查验", "放行", "border", "customs", "clearance",
    "公路", "道路", "铁路", "卡车", "货车", "交通", "road", "rail", "truck", "traffic",
    "仓库", "分拣", "装箱", "配送", "搬运", "warehouse", "delivery", "forklift",
    "冷链", "采收", "包装", "出口运输", "cold chain",
    "积雪", "暴雪", "洪水", "封路", "snow", "flood", "closure",
)


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def news_hook_max_ratio() -> float:
    return min(1.0, max(0.0, _env_float("HOTSPOT_NEWS_HOOK_MAX_RATIO", 0.30)))


def news_ratio_min_sample() -> int:
    return _env_int("HOTSPOT_NEWS_RATIO_MIN_SAMPLE", 10, minimum=1)


def metadata_pause_count(*, confirmed: bool) -> int:
    if confirmed:
        return _env_int("HOTSPOT_METADATA_CONFIRMED_FAIL_PAUSE", 3, minimum=1)
    return _env_int("HOTSPOT_METADATA_TRANSIENT_FAIL_PAUSE", 8, minimum=1)


def normalize_source_class(value: str | None, *, publisher: str = "") -> str:
    raw = str(value or "").strip()
    if raw in SOURCE_CLASSES:
        return raw
    mapped = PUBLISHER_SOURCE_CLASS.get(str(publisher or "").strip())
    if mapped in SOURCE_CLASSES:
        return mapped
    return "general_news"


def resolve_source_class(media: dict | None = None, hotspot: dict | None = None) -> str:
    media = media or {}
    hotspot = hotspot or {}
    return normalize_source_class(
        media.get("source_class"),
        publisher=str(media.get("publisher") or hotspot.get("publisher") or ""),
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def retry_after_iso(hours: int) -> str:
    return (now_utc() + timedelta(hours=max(1, int(hours)))).isoformat()


def metadata_backoff_hours(failure_count: int) -> int:
    exponent = max(0, int(failure_count) - 1)
    return min(24, 2 ** exponent)


def is_retry_due(item: dict, *, now: datetime | None = None) -> bool:
    retry_after = parse_iso(item.get("retry_after"))
    if retry_after is None:
        return False
    current = now or now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return retry_after <= current


def is_metadata_failed_row(item: dict) -> bool:
    return str(item.get("download_status") or "") == METADATA_FAILED_STATUS or (
        str(item.get("intake_metadata_status") or "") == "failed"
    )


def is_incremental_eligible(item: dict, *, now: datetime | None = None) -> bool:
    """Six-hour intake must not keep retrying the same dead metadata_ready rows."""
    status = str(item.get("download_status") or "")
    if status in PAUSED_DOWNLOAD_STATUSES:
        return False
    if status not in INCREMENTAL_DOWNLOAD_STATUSES:
        return False
    if status == "materialization_retryable":
        return is_retry_due(item, now=now)
    if str(item.get("intake_metadata_status") or "") == "failed":
        return is_retry_due(item, now=now)
    return True


def is_full_intake_eligible(item: dict, *, now: datetime | None = None) -> bool:
    status = str(item.get("download_status") or "")
    if status == METADATA_FAILED_STATUS:
        return False
    if status == "prefiltered_skip":
        return False
    if status == "materialization_retryable":
        return is_retry_due(item, now=now) or not str(item.get("retry_after") or "").strip()
    if status in {"metadata_ready", "failed", "download_failed", "pending", "downloading"}:
        if str(item.get("intake_metadata_status") or "") == "failed":
            retry_after = str(item.get("retry_after") or "").strip()
            return (not retry_after) or is_retry_due(item, now=now)
        return True
    if status == "downloaded" and item.get("processing_status") in {
        "not_started", "processing", "processing_failed",
    } and item.get("asset_id"):
        return True
    return False


def fair_sample(candidates: list[dict], limit: int) -> list[dict]:
    """Round-robin by publisher, preferring official logistics sources."""
    limit = max(1, int(limit))
    newest_first = sorted(
        (dict(item) for item in candidates),
        key=lambda item: (str(item.get("published_at") or ""), int(item.get("id") or 0)),
        reverse=True,
    )
    groups: dict[str, list[dict]] = {}
    for item in newest_first:
        source = str(item.get("publisher") or item.get("platform") or "unknown").strip() or "unknown"
        groups.setdefault(source, []).append(item)
    source_order = sorted(
        groups,
        key=lambda source: (
            PRIORITY_PUBLISHERS.index(source) if source in PRIORITY_PUBLISHERS else 10 + SOURCE_CLASS_PRIORITY.get(resolve_source_class(groups[source][0]), 9),
            str(groups[source][0].get("published_at") or ""),
            int(groups[source][0].get("id") or 0),
        ),
    )
    selected: list[dict] = []
    while len(selected) < limit:
        added = False
        for source in source_order:
            rows = groups[source]
            if not rows:
                continue
            selected.append(rows.pop(0))
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected


def select_incremental_media(candidates: list[dict], limit: int, *, now: datetime | None = None) -> dict:
    eligible = [item for item in candidates if is_incremental_eligible(item, now=now)]
    skipped = [item for item in candidates if item not in eligible]
    selected = fair_sample(eligible, limit)
    skipped_failed = [
        int(item["id"]) for item in skipped
        if is_metadata_failed_row(item) or str(item.get("download_status") or "") in PAUSED_DOWNLOAD_STATUSES
    ]
    official_selected = [
        item for item in selected
        if resolve_source_class(item) in OFFICIAL_OR_LOGISTICS
        or str(item.get("publisher") or "") in PRIORITY_PUBLISHERS
    ]
    stuck_in_incremental = [
        media_id for media_id in selected_ids_of(selected) if media_id in KNOWN_STUCK_MEDIA_IDS
    ]
    return {
        "selected": selected,
        "selected_ids": selected_ids_of(selected),
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "skipped_count": len(skipped),
        "skipped_failed_ids": skipped_failed,
        "official_selected_ids": selected_ids_of(official_selected),
        "official_publishers": sorted({str(item.get("publisher") or "") for item in official_selected if item.get("publisher")}),
        "known_stuck_in_incremental": stuck_in_incremental,
    }


def selected_ids_of(rows: list[dict]) -> list[int]:
    return [int(item["id"]) for item in rows]


def real_logistics_scenes(scenes: list | None, fact_text: str = "") -> list[str]:
    values = [str(item).strip() for item in (scenes or []) if str(item).strip()]
    filtered = [item for item in values if item not in PSEUDO_LOGISTICS_SCENES]
    if filtered:
        return filtered
    profile = hotspot_lexicon.category_profile(fact_text, mode="event")
    return sorted(item for item in profile if item in REAL_LOGISTICS_SCENES)


def has_real_logistics_scene(event: dict) -> bool:
    evidence = event.get("evidence") or {}
    fact_text = " ".join(
        str(event.get(key) or "")
        for key in ("title_zh", "title_en")
    ) + " " + str(evidence.get("what_happened") or "")
    scenes = real_logistics_scenes(event.get("logistics_scenes"), fact_text)
    if scenes:
        return True
    blob = fact_text.casefold()
    if any(marker.casefold() in blob for marker in LOGISTICS_FACT_MARKERS):
        return True
    question = str(evidence.get("logistics_question") or "")
    if is_placeholder_logistics_question(question):
        return False
    return any(marker.casefold() in question.casefold() for marker in LOGISTICS_FACT_MARKERS)


def is_placeholder_logistics_question(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in PLACEHOLDER_LOGISTICS_QUESTIONS)


def is_audit_only_topic(text: str) -> bool:
    blob = str(text or "").casefold()
    if not blob:
        return False
    if any(marker.casefold() in blob for marker in LOGISTICS_FACT_MARKERS):
        return False
    return any(marker.casefold() in blob for marker in AUDIT_ONLY_TOPIC_MARKERS)


def contains_generic_bridge_filler(text: str) -> bool:
    blob = str(text or "")
    return any(marker in blob for marker in GENERIC_BRIDGE_FILLERS)


def ineligible_reason(event: dict, *, hard_ready: bool, quota_held: bool = False) -> str:
    if hard_ready and not quota_held:
        return ""
    if quota_held:
        return "综合新闻占比已达上限，仅作事实归档"
    evidence = event.get("evidence") or {}
    if str(event.get("review_status") or "") != "confirmed":
        return "尚未完成视觉/文本审核"
    if str(event.get("clip_status") or "") != "ready" or not str(event.get("clip_path") or "").strip():
        return "缺少可播放派生剪辑"
    if is_placeholder_logistics_question(evidence.get("logistics_question")):
        return "缺少具体物流切入问题"
    if not has_real_logistics_scene(event):
        return "没有可核验的物流场景，hotspot 不能代替港口/道路/仓储等标签"
    fact_text = " ".join((
        str(event.get("title_zh") or ""),
        str(event.get("title_en") or ""),
        str(evidence.get("what_happened") or ""),
    ))
    if is_audit_only_topic(fact_text):
        return "题材为政治/体育/采访/演播室等，仅进审计归档"
    return "未通过可成片门禁"


def max_general_news_ready(official_count: int, *, max_ratio: float | None = None) -> int:
    ratio = news_hook_max_ratio() if max_ratio is None else max_ratio
    if ratio <= 0:
        return 0
    if ratio >= 1:
        return 10**9
    # general / (official + general) <= ratio  =>  general <= ratio/(1-ratio) * official
    return int((ratio / (1.0 - ratio)) * max(0, official_count))


def assign_ready_flags(
    events: list[dict],
    *,
    is_hard_ready: Callable[[dict], bool],
    source_class_of: Callable[[dict], str] | None = None,
) -> dict[int, dict]:
    """Return {event_id: {hard_ready, quota_held, is_renderable, source_class}}."""
    classify = source_class_of or (lambda event: normalize_source_class(event.get("source_class")))
    annotated = []
    for event in events:
        event_id = int(event.get("id") or 0)
        source_class = classify(event)
        hard_ready = bool(is_hard_ready(event))
        annotated.append((event_id, event, source_class, hard_ready))
    hard_ready_rows = [row for row in annotated if row[3]]
    apply_quota = len(hard_ready_rows) >= news_ratio_min_sample()
    official_ready = [row for row in hard_ready_rows if row[2] in OFFICIAL_OR_LOGISTICS]
    general_ready = sorted(
        [row for row in hard_ready_rows if row[2] == "general_news"],
        key=lambda row: int(row[0]),
    )
    allowed_general = set()
    if apply_quota:
        cap = max_general_news_ready(len(official_ready))
        allowed_general = {row[0] for row in general_ready[:cap]}
    else:
        allowed_general = {row[0] for row in general_ready}
    result: dict[int, dict] = {}
    for event_id, event, source_class, hard_ready in annotated:
        quota_held = bool(
            hard_ready and source_class == "general_news" and event_id not in allowed_general
        )
        result[event_id] = {
            "source_class": source_class,
            "hard_ready": hard_ready,
            "quota_held": quota_held,
            "is_renderable": hard_ready and not quota_held,
            "ineligible_reason": ineligible_reason(
                event, hard_ready=hard_ready, quota_held=quota_held
            ),
        }
    return result


def next_metadata_failure_state(
    item: dict,
    message: str,
    *,
    confirmed: bool,
) -> dict:
    count = int(item.get("failure_count") or 0) + 1
    pause_at = metadata_pause_count(confirmed=confirmed)
    paused = confirmed and count >= pause_at
    hours = metadata_backoff_hours(count)
    return {
        "intake_metadata_status": "failed",
        "failure_reason": str(message or "metadata failed")[:300],
        "failure_count": count,
        "retry_after": None if paused else retry_after_iso(hours),
        "download_status": METADATA_FAILED_STATUS if paused else str(item.get("download_status") or "metadata_ready"),
        "progress_detail": (
            f"元数据已暂停：连续 {count} 次确认视频失效"
            if paused
            else f"元数据失败，将于 {hours} 小时后重试：{str(message or '')[:120]}"
        ),
    }


def clear_failure_state() -> dict:
    return {
        "failure_reason": None,
        "failure_count": 0,
        "retry_after": None,
        "progress_detail": None,
    }
