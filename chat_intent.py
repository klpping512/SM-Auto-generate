"""Chat content-mode routing, comparison evidence, and topic producibility.

Safety-first: comparison intents outrank hotspot intents; broad evergreen topics
never open a hotspot discovery queue when they lack a concrete event anchor.
"""
from __future__ import annotations

import re
from typing import Iterable

import hotspot_lexicon


CONTENT_MODES = (
    "comparison_research",
    "hotspot",
    "evergreen",
    "general_copy",
)

COMPARISON_MARKERS = (
    "对比", "测评", "评测", "排行", "哪家", "最好", "性价比", "实测",
    "对比评测", "横向对比", "选哪家", "谁更", "versus", " vs ", "compare",
    "comparison", "benchmark", "ranking",
)

HOTSPOT_MARKERS = (
    "最近", "最新", "新闻", "事故", "堵车", "拥堵", "道路中断", "突发",
    "今日", "昨晚", "刚刚", "breaking", "road closure", "accident",
    "flood", "罢工", "口岸关闭", "封路",
)

EVERGREEN_MARKERS = (
    "怎么选", "如何", "介绍", "指南", "教程", "是什么", "知识", "操作",
    "流程说明", "服务说明", "百科", "从0到1", "开拓", "市场进入",
)

# Concrete event / place anchors beyond mere time words.
EVENT_ENTITY_MARKERS = (
    "beitbridge", "德班", "durban", "richards bay", "开普敦", "cape town",
    "约翰内斯堡", "johannesburg", "r60", "r328", "robertson", "worcester",
    "swartberg", "n3", "m7", "口岸", "边境", "港口", "port", "封路",
    "事故", "侧翻", "拥堵", "堵车", "道路中断", "罢工", "洪水", "flood",
    "load shedding", "eskom", "transnet",
)

# Fabricated review language that must not appear without evidence.
FABRICATED_REVIEW_PATTERN = re.compile(
    r"(实测|我们测试了|综合评估|排名第一|最稳|最好|最优|"
    r"\d+\s*家主流|四家主流|五家主流|4家|5家|"
    r"实测对比|综合得分|推荐排名)",
    re.IGNORECASE,
)

PRICE_OR_SLA_PATTERN = re.compile(
    r"(R\s?\d|\d+\s*rand|价格|报价|时效|几天到|隔日达|附加费|费率)",
    re.IGNORECASE,
)

SOURCE_PATTERN = re.compile(
    r"(来源|官网|官方|报价单|价目|截图|测试日期|取样|http://|https://|根据.{0,12}数据)",
    re.IGNORECASE,
)

CANDIDATE_PATTERN = re.compile(
    r"(The\s+Courier\s+Guy|Fastway|RAM\s+Hand\-to\-Hand|Buffalo|DHL|FedEx|"
    r"Aramex|PostNet|Pudo|Takealot|服务商|快递公司)",
    re.IGNORECASE,
)


def _joined_text(parts: Iterable[str]) -> str:
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip())


def classify_content_mode(text: str, *, context: str = "") -> str:
    """Return the highest-priority safe content mode for a chat topic."""
    blob = _joined_text([text, context]).casefold()
    if not blob:
        return "general_copy"
    if any(marker.casefold() in blob for marker in COMPARISON_MARKERS):
        return "comparison_research"
    if any(marker.casefold() in blob for marker in HOTSPOT_MARKERS):
        return "hotspot"
    if any(marker.casefold() in blob for marker in EVERGREEN_MARKERS):
        return "evergreen"
    return "general_copy"


def assess_event_anchor(text: str, *, context: str = "") -> dict:
    """Decide whether a topic maps to a concrete timely logistics event."""
    blob = _joined_text([text, context])
    folded = blob.casefold()
    time_hits = [marker for marker in HOTSPOT_MARKERS if marker.casefold() in folded]
    entity_hits = [marker for marker in EVENT_ENTITY_MARKERS if marker.casefold() in folded]
    logistics_scenes = sorted(hotspot_lexicon.category_profile(blob, mode="topic"))
    scene_set = set(logistics_scenes)
    # Need at least one concrete entity/place/disruption term — time words alone
    # (e.g. "最近物流") are not enough to open discovery.
    has_event_anchor = bool(entity_hits) or (
        bool(time_hits) and bool(scene_set & {"port", "border", "disruption"})
    )
    return {
        "has_event_anchor": has_event_anchor,
        "event_terms": sorted({*time_hits, *entity_hits}, key=str.casefold),
        "logistics_scenes": logistics_scenes,
        "time_hits": time_hits,
        "entity_hits": entity_hits,
    }


def assess_comparison_evidence(
    messages: list[dict] | None = None,
    *,
    topic: str = "",
    context: str = "",
) -> dict:
    """Inspect user-supplied text for review candidates, metrics, and sources."""
    user_bits = [
        str(item.get("content") or "")
        for item in (messages or [])
        if str(item.get("role") or "") == "user"
    ]
    source_text = _joined_text([topic, context, *user_bits])
    has_candidates = bool(CANDIDATE_PATTERN.search(source_text))
    has_metrics = bool(PRICE_OR_SLA_PATTERN.search(source_text))
    has_sources = bool(SOURCE_PATTERN.search(source_text))
    # Formal review requires at least one named candidate plus either metrics or sources.
    sufficient = bool(has_candidates and (has_metrics or has_sources))
    return {
        "has_candidates": has_candidates,
        "has_metrics": has_metrics,
        "has_sources": has_sources,
        "sufficient": sufficient,
        "evidence_state": "sufficient" if sufficient else "insufficient",
    }


def comparison_to_evergreen_topic(topic: str) -> str:
    """把对比评测题材确定性改写为安全科普视角。

    保证：输出永远不含 COMPARISON_MARKERS（chat_intent.COMPARISON_MARKERS），
    因此重走 classify_content_mode 不会再进对比门禁（无死循环）。
    """
    raw = " ".join(str(topic or "").split())
    if not raw:
        return "南非物流怎么选？关键维度科普"
    if "南非" in raw and any(
        w in raw for w in ("快递", "物流", "货运", "清关", "仓储", "配送")
    ):
        return "南非本地快递怎么选？关键维度科普" if "快递" in raw else "南非物流怎么选？关键维度科普"
    if any(w in raw for w in ("快递", "物流", "货运", "配送")):
        return "本地快递怎么选？关键维度科普"
    return "物流服务怎么选？关键维度科普"


def comparison_authenticity_violations(
    *texts: str,
    evidence: dict | None = None,
) -> list[str]:
    """Flag fabricated ranking/test language when evidence is missing."""
    evidence = evidence or {}
    blob = _joined_text(texts)
    if not blob:
        return []
    if evidence.get("sufficient"):
        return []
    hits = sorted({match.group(0) for match in FABRICATED_REVIEW_PATTERN.finditer(blob)})
    return [f"无依据评测表述：{hit}" for hit in hits]


def classify_hook_failure(
    *,
    content_mode: str,
    event_anchor: dict | None,
    hotspot_retrieval: dict | None,
) -> str | None:
    """Map retrieval outcome to one of the three failure classes (or None if matched)."""
    if content_mode == "comparison_research":
        return None
    retrieval = hotspot_retrieval or {}
    status = str(retrieval.get("status") or "")
    if status == "matched":
        return None
    explicit = str(retrieval.get("failure_class") or "").strip()
    if explicit in {"no_event_anchor", "coverage_gap", "gate_blocked"}:
        return explicit
    anchor = event_anchor or {}
    if content_mode in {"evergreen", "general_copy"} or not anchor.get("has_event_anchor"):
        return "no_event_anchor"
    funnel = retrieval.get("funnel") or {}
    if int(funnel.get("scanned") or 0) > 0 and int(funnel.get("passed") or 0) == 0:
        return "gate_blocked"
    if status in {"queued", "pending", "processing", "unmatched", "failed"}:
        return "coverage_gap"
    return "coverage_gap"


def derive_result_state(
    *,
    content_mode: str,
    evidence_state: str,
    hotspot_retrieval: dict | None,
    authenticity_blocked: bool = False,
    brand_assets_insufficient: bool = False,
    event_anchor: dict | None = None,
) -> str:
    """Collapse chat outcome into one user-facing result_state."""
    if authenticity_blocked:
        return "authenticity_blocked"
    if content_mode == "comparison_research" and evidence_state != "sufficient":
        return "framework_pending_evidence"
    if content_mode == "comparison_research":
        return "formal_content"
    retrieval = hotspot_retrieval or {}
    status = str(retrieval.get("status") or "")
    failure = classify_hook_failure(
        content_mode=content_mode,
        event_anchor=event_anchor,
        hotspot_retrieval=retrieval,
    )
    if status == "matched":
        video = retrieval.get("video") or {}
        readiness = video.get("delivery_readiness") or {}
        # Soft inventory adaptation keeps delivery_ready=true; only hard
        # planning failures surface as brand_assets_insufficient.
        if brand_assets_insufficient or (readiness and not readiness.get("delivery_ready", True)):
            return "brand_assets_insufficient"
        return "formal_content"
    if failure == "no_event_anchor" and status in {"", "not_requested"}:
        topics = retrieval.get("producible_topics") or []
        if topics:
            return "hook_selection_required"
        return "topic_needs_event_anchor"
    if failure == "gate_blocked":
        if retrieval.get("producible_topics"):
            return "hook_selection_required"
        return "hook_gate_blocked"
    if status in {"queued", "pending", "processing"} or failure == "coverage_gap":
        if failure == "coverage_gap" and status == "not_requested":
            if retrieval.get("producible_topics"):
                return "hook_selection_required"
            return "hook_coverage_gap"
        if status in {"queued", "pending", "processing"}:
            return "hotspot_retrieval_pending"
        if retrieval.get("producible_topics"):
            return "hook_selection_required"
        return "hook_coverage_gap"
    return "formal_content"


def should_attempt_hook_retrieval(content_mode: str) -> bool:
    """Evergreen/general may still try generic logistics openers; comparison does not."""
    return content_mode in {"hotspot", "evergreen", "general_copy"}


def should_enqueue_hotspot_discovery(content_mode: str, event_anchor: dict | None) -> bool:
    """Only concrete timely-event topics may open the discovery queue."""
    return content_mode == "hotspot" and bool((event_anchor or {}).get("has_event_anchor"))


def should_request_hotspot_retrieval(content_mode: str) -> bool:
    """Backward-compatible alias: historic callers meant 'run Hook retrieval for hotspots'."""
    return content_mode == "hotspot"
