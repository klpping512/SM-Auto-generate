"""Chat content-mode routing and comparison-evidence assessment.

Safety-first: comparison intents outrank hotspot intents so evergreen review
topics never open a hotspot Hook discovery queue.
"""
from __future__ import annotations

import re
from typing import Iterable


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
    "流程说明", "服务说明", "百科",
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


def derive_result_state(
    *,
    content_mode: str,
    evidence_state: str,
    hotspot_retrieval: dict | None,
    authenticity_blocked: bool = False,
    brand_assets_insufficient: bool = False,
) -> str:
    """Collapse chat outcome into one user-facing result_state."""
    if authenticity_blocked:
        return "authenticity_blocked"
    if content_mode == "comparison_research" and evidence_state != "sufficient":
        return "framework_pending_evidence"
    retrieval = hotspot_retrieval or {}
    status = str(retrieval.get("status") or "")
    if status in {"queued", "pending", "processing"}:
        return "hotspot_retrieval_pending"
    video = retrieval.get("video") or {}
    readiness = video.get("delivery_readiness") or {}
    if brand_assets_insufficient or (
        status == "matched" and readiness and not readiness.get("delivery_ready", True)
    ):
        return "brand_assets_insufficient"
    return "formal_content"


def should_request_hotspot_retrieval(content_mode: str) -> bool:
    return content_mode == "hotspot"
