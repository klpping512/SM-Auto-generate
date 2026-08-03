"""Unified hotspot / logistics matching lexicon.

Single source of truth for category taxonomies, event-type terms, hook scoring
terms, feed filters, and bilingual term extraction. Callers must not redefine
parallel word lists for the same matching job.
"""
from __future__ import annotations

import re
from typing import Iterable


# ---------------------------------------------------------------------------
# Layer 1 — Canonical taxonomies
# ---------------------------------------------------------------------------

# Chat / curator logistics category taxonomy (6 classes). Topic vs event modes
# use slightly different trigger lists so model-authored bridge sentences never
# inflate event profiles.
_TOPIC_CATEGORY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "warehouse": ("takealot", "库存", "用户体验", "海外仓", "仓库", "仓储", "入库", "分拣", "货架", "本地团队"),
    "last_mile": ("takealot", "库存", "用户体验", "末端", "配送体验"),
    "cost_risk": ("低价", "更贵", "亏钱", "成本", "运费", "货代", "报价", "坑"),
    "disruption": (
        "延误", "备用", "应急", "突发", "停摆", "路线", "路况", "封路",
        "事故", "侧翻", "火灾", "大雪", "天气",
    ),
    "border": ("边境", "口岸", "清关", "beitbridge", "customs"),
    "port": ("港口", "port", "海运", "船"),
}

_EVENT_CATEGORY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "warehouse": ("海外仓", "仓储", "仓库", "入库", "分拨", "分拣", "库存", "货架", "warehouse"),
    "last_mile": ("末端", "配送", "交付", "派送", "last mile", "delivery"),
    "cost_risk": ("成本", "运费", "报价", "货代", "cost", "freight"),
    "disruption": (
        "事故", "侧翻", "起火", "火灾", "大雪", "降雪", "拥堵", "堵车",
        "封锁", "延误", "停电", "emergency",
    ),
    "border": ("边境", "口岸", "beitbridge", "清关", "customs"),
    "port": ("港口", "port", "船", "海运"),
}

LOGISTICS_CATEGORIES = frozenset(_EVENT_CATEGORY_TRIGGERS)

# Merged from hotspot_logistics_planner.SIGNAL_GROUPS + topic_packages.LOGISTICS_TERMS
# + video_planner type_terms. Planner's fuller set is authoritative.
EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "strike": (
        "罢工", "停工", "抗议", "工会", "strike", "protest", "shutdown",
    ),
    "risk": (
        "危险", "枪击", "抢劫", "暴力", "治安", "安全", "事故", "火灾", "爆炸",
        "crime", "safety", "hijacking", "security", "劫车",
    ),
    "ecommerce_growth": (
        "电商", "订单", "网购", "零售", "消费", "增长", "delivery demand",
        "e-commerce", "takealot", "amazon", "temu", "配送", "仓储",
    ),
    "infrastructure": (
        "港口", "拥堵", "交通", "道路", "桥", "铁路", "停电", "基础设施",
        "port", "road", "rail", "traffic", "congestion", "screening",
        "customs", "warehouse", "清关",
    ),
    "weather": (
        "洪水", "暴雨", "风暴", "干旱", "天气", "flood", "storm", "weather",
    ),
    "policy": (
        "政策", "法规", "关税", "投资", "政府", "department", "regulation", "investment",
    ),
}

EVENT_RELEVANCE: dict[str, float] = {
    "strike": 85.0,
    "risk": 80.0,
    "infrastructure": 95.0,
    "ecommerce_growth": 75.0,
    "weather": 70.0,
    "policy": 65.0,
    "unknown": 0.0,
}

HOOK_SCORE_TERMS: tuple[str, ...] = (
    "traffic", "congestion", "screening", "road", "port", "strike", "storm", "flood",
    "delivery", "warehouse", "customs", "accident", "border",
    "拥堵", "交通", "道路", "港口", "罢工", "暴雨", "配送", "清关", "事故", "边境", "仓储",
)

BROAD_TERMS = frozenset({
    "南非", "物流", "跨境", "运输", "配送", "时效", "安全",
    "warehouse", "warehousing", "delivery", "road", "traffic",
})

TOPIC_KEYWORD_TERMS: tuple[str, ...] = (
    "南非", "清关", "末端", "配送", "仓储", "海外仓", "仓库", "入库", "分拣", "货架",
    "港口", "物流", "跨境", "关税", "时效", "安全", "运输", "边境", "拥堵", "堵车",
    "卡车", "道路", "事故", "侧翻", "路线", "路况", "提货", "火灾", "救援", "封路",
    "beitbridge", "r60", "robertson", "worcester", "r328", "swartberg",
    "warehouse", "warehousing", "customs", "delivery", "port", "road", "traffic", "congestion",
)

RETRIEVAL_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("warehouse", "仓库", "仓储", "海外仓", "入库", "出库"),
    ("parcel", "package", "包裹", "分拣", "检查"),
    ("loading", "装卸", "装车", "拖车", "货车"),
    ("delivery", "last mile", "配送", "末端", "派送"),
    ("cross border", "border", "跨境", "口岸", "入境"),
    ("customs", "clearance", "清关", "报关", "税务"),
    ("port", "harbour", "港口", "码头", "船期"),
)

NODE_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "清关": ("清关", "海关", "customs"),
    "customs": ("清关", "海关", "customs"),
    "末端": (
        "末端", "配送", "交付", "last mile", "last_mile", "delivery",
        "traffic", "congestion", "road", "screening", "拥堵", "道路",
    ),
    "last_mile": (
        "末端", "配送", "交付", "last mile", "last_mile", "delivery",
        "traffic", "congestion", "road", "screening", "拥堵", "道路",
    ),
    "配送": (
        "末端", "配送", "交付", "delivery", "traffic", "congestion",
        "road", "screening", "拥堵", "道路",
    ),
    "仓储": ("仓储", "仓库", "入库", "分拣", "warehouse"),
    "入库": ("仓储", "仓库", "入库", "warehouse"),
    "分拣": ("分拣", "仓库", "warehouse"),
}

FEED_FILTER_PATTERN = re.compile(
    r"(?:\bsouth africa\b|南非|物流|货运|港口|海关|清关|仓储|跨境"
    r"|\bdurban\b|\bcape town\b|\bjohannesburg\b|\bpretoria\b|\bgqeberha\b|\bport elizabeth\b|\brichards bay\b|\bbeitbridge\b"
    r"|\btransnet\b|\bsars\b|\bport\b|\bports\b|\bharbour\b|\bcustoms\b|\blogistics\b|\bfreight\b|\bshipping\b|\bwarehouse\w*"
    r"|\bcargo\b|\bcontainer\w*|\brail\w*|\btruck\w*|\bsupply chain\b|\bborder\w*|\bexport\w*|\bimport\w*|\bcourier\b|\bdelivery\b"
    r"|\bstrike\b|\bprotest\w*|\broad closure\b|\bfuel price\b|\bdiesel\b|\btoll\b|\bload.?shedding\b|\beskom\b|\bflood\w*|\bstorm\b)",
    re.I,
)

STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "south", "africa",
    "关于", "导致", "当地", "新闻", "热点", "物流", "公司", "服务", "事件",
})
_STOPWORDS = STOPWORDS  # private alias kept for older call sites

_CURATED_SUBSTRING_TERMS: tuple[str, ...] = tuple(dict.fromkeys(
    list(TOPIC_KEYWORD_TERMS)
    + list(HOOK_SCORE_TERMS)
    + [term for group in EVENT_TYPES.values() for term in group]
    + [term for group in RETRIEVAL_ALIAS_GROUPS for term in group]
    + [term for triggers in _EVENT_CATEGORY_TRIGGERS.values() for term in triggers]
    + [term for triggers in _TOPIC_CATEGORY_TRIGGERS.values() for term in triggers]
))


# ---------------------------------------------------------------------------
# Layer 2 — Tokenization
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    return str(text or "").casefold()


def extract_terms(text: str, *, include_chinese: bool = True, expand_aliases: bool = True) -> set[str]:
    """English tokens + optional Chinese 2–4-grams + curated hits + alias expansion."""
    normalized = normalize(text)
    if not normalized:
        return set()

    words: set[str] = set(re.findall(r"[a-z0-9][a-z0-9'-]{1,}", normalized))
    words.update(re.findall(r"[a-z0-9]{3,}", normalized))

    if include_chinese:
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
        for width in (2, 3, 4):
            words.update(
                chinese[index:index + width]
                for index in range(max(0, len(chinese) - width + 1))
            )

    for term in _CURATED_SUBSTRING_TERMS:
        folded = term.casefold()
        if folded and folded in normalized:
            words.add(folded)

    if expand_aliases:
        for aliases in RETRIEVAL_ALIAS_GROUPS:
            if any(alias.casefold() in normalized for alias in aliases):
                words.update(alias.casefold() for alias in aliases)

    return {word for word in words if word and word not in _STOPWORDS}


# ---------------------------------------------------------------------------
# Layer 3 — Matching APIs
# ---------------------------------------------------------------------------

def category_profile(text: str, *, mode: str = "event") -> set[str]:
    """Return logistics category tags for topic or grounded event fact text."""
    normalized = normalize(text)
    triggers = _TOPIC_CATEGORY_TRIGGERS if mode == "topic" else _EVENT_CATEGORY_TRIGGERS
    # Topic mode: takealot/库存/用户体验 jointly imply warehouse + last_mile.
    if mode == "topic":
        profile: set[str] = set()
        if any(term in normalized for term in ("takealot", "库存", "用户体验", "末端", "配送体验")):
            profile.update({"warehouse", "last_mile"})
        if any(term in normalized for term in ("海外仓", "仓库", "仓储", "入库", "分拣", "货架", "本地团队")):
            profile.add("warehouse")
        if any(term in normalized for term in triggers["cost_risk"]):
            profile.add("cost_risk")
        if any(term in normalized for term in triggers["disruption"]):
            profile.add("disruption")
        if any(term in normalized for term in triggers["border"]):
            profile.add("border")
        if any(term in normalized for term in triggers["port"]):
            profile.add("port")
        return profile

    profile = set()
    for category, terms in triggers.items():
        if any(term.casefold() in normalized for term in terms):
            profile.add(category)
    return profile


def classify_event_type(text: str) -> str:
    """Best-matching EVENT_TYPES key, or 'unknown' when nothing hits."""
    normalized = normalize(text)
    scores = {
        event_type: sum(1 for term in terms if term.casefold() in normalized)
        for event_type, terms in EVENT_TYPES.items()
    }
    best, score = max(scores.items(), key=lambda item: item[1])
    return best if score else "unknown"


def classify_event_type_with_relevance(text: str) -> tuple[str, float]:
    event_type = classify_event_type(text)
    return event_type, float(EVENT_RELEVANCE.get(event_type, 0.0))


def match_hook_terms(text: str) -> list[str]:
    normalized = normalize(text)
    return [term for term in HOOK_SCORE_TERMS if term.casefold() in normalized]


def topic_keyword_hits(text: str) -> list[str]:
    """Deterministic logistics / place keyword hits for topic briefs."""
    normalized = normalize(text)
    return [term for term in TOPIC_KEYWORD_TERMS if term.casefold() in normalized]


def score_substring_hits(text: str, terms: Iterable[str]) -> int:
    normalized = normalize(text)
    return sum(1 for term in terms if term and term.casefold() in normalized)


def overlap_score(left: str, right: str) -> float:
    """Jaccard-like overlap of extracted terms (0–1)."""
    left_terms = extract_terms(left)
    right_terms = extract_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    hits = left_terms & right_terms
    return round(min(1.0, len(hits) / max(1, len(left_terms))), 3)


def overlap_hits(left: str, right: str) -> list[str]:
    hits = extract_terms(left) & extract_terms(right)
    return sorted(hits)


def expand_node_terms(node: str) -> tuple[str, ...]:
    normalized = str(node or "").casefold()
    return NODE_EXPANSIONS.get(normalized, (normalized,) if normalized else ())
