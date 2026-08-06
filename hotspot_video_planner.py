"""将动态内容简报落实为可渲染的混合证据分镜。"""
from __future__ import annotations

from collections.abc import Iterable

from video_composition_policy import source_usage_report
from video_duration_budget import rebalance_scenes_to_budget
import asset_taxonomy
import hotspot_lexicon


# 默认 8 个镜头为 60 秒；正式成片可按目标扩展至 90 秒。
ROLE_DURATIONS = [7_000, 8_000, 7_000, 8_000, 7_000, 8_000, 7_000, 8_000]
OWNED_CATEGORIES = {"warehouse", "delivery", "staff", "facility", "brand", "customer"}
# 结尾只保留一张短 CTA。连续两张静态海报会把用户从真实热点和实拍动作中
# 拉出来，也容易被视觉质检误当作冻结。单张缓慢推进的品牌画面足以收束。
BRAND_ENDCARD_SCENES = (
    {
        "scene_role": "brand_cta", "evidence_type": "brand_endcard", "duration_ms": 3_000,
        "visual": "Buffalo 南非配送车辆", "voiceover": "南非发货，先理清订单信息。",
        "text_overlay": "Buffalo｜发货前先理清订单", "brand_endcard_path": "uploads/brand-endcards/buffalo-cape-town-van.png",
    },
)

CONTEXT_IMAGE_DURATION_MS = 2_000


def _usable_source_duration_ms(item: dict, *, start_ms: int | None = None, end_ms: int | None = None) -> int:
    """Return the one-pass visual budget for a real source.

    Seven seconds is a pacing preference, never permission to extend a short
    source.  When a reviewed range is known, a source shorter than three
    seconds is not a usable real-video beat at all; the caller must use an
    explanation card or find another source instead of looping it.
    """
    try:
        start = int(start_ms if start_ms is not None else item.get("start_ms") or 0)
        end = int(end_ms if end_ms is not None else item.get("end_ms") or 0)
    except (TypeError, ValueError):
        start, end = 0, 0
    measured = end - start
    if measured <= 0:
        try:
            measured = int(item.get("duration_ms") or 0)
        except (TypeError, ValueError):
            measured = 0
    # Legacy unit tests and unmaterialized hook rows may not carry a measured
    # range yet.  The renderer remains the final authority for those rows.
    if measured <= 0:
        return 7_000
    if measured < 3_000:
        return 0
    return min(7_000, measured)

# Back-compat aliases; canonical rules/tags live in asset_taxonomy.
NODE_CATEGORY_RULES = asset_taxonomy.NODE_CATEGORY_RULES

# Back-compat alias; expansions live in hotspot_lexicon.NODE_EXPANSIONS.
NODE_TERMS = hotspot_lexicon.NODE_EXPANSIONS


def _event_text(event: dict) -> str:
    values = [event.get("title_zh"), event.get("title_en"), event.get("location")]
    values += list(event.get("keywords") or []) + list(event.get("entities") or [])
    return " ".join(str(value or "") for value in values).casefold()


def _event_score(event: dict, brief: dict) -> tuple[int, list[str]]:
    text = _event_text(event)
    terms = set(str(item).casefold() for item in (
        brief.get("hotspot_title"), brief.get("hotspot_summary"), brief.get("logistics_topic"),
        brief.get("hotspot_type"),
    ) if item)
    terms.update({str(item).casefold() for item in (brief.get("claim") or "").split() if len(str(item)) > 1})
    terms.update(hotspot_lexicon.EVENT_TYPES.get(str(brief.get("hotspot_type") or ""), ()))
    overlap = sum(1 for term in terms if term and term.casefold() in text)
    # A custom brief is an explicit user intent.  A clip from the same source video
    # is not evidence of relevance by itself (for example, wildlife footage cannot
    # illustrate customs risk simply because it belongs to the selected hotspot).
    if brief.get("topic_brief_id"):
        custom_terms = []
        for value in brief.get("logistics_nodes") or []:
            custom_terms.extend(hotspot_lexicon.expand_node_terms(value))
        custom_terms.append(str(brief.get("logistics_topic") or "").casefold())
        # Do not count the hotspot title itself here: it merely identifies the
        # source, while custom terms establish whether this event can support
        # the user's requested logistics angle.
        if not any(term and term.casefold() in text for term in custom_terms):
            return 0, []
    same_source = bool(
        brief.get("source_asset_id") and int(event.get("asset_id") or 0) == int(brief["source_asset_id"])
    )
    same_hotspot = bool(
        brief.get("hotspot_id") and int(event.get("hotspot_id") or 0) == int(brief["hotspot_id"])
    )
    reasons = []
    if overlap:
        reasons.append("热点标题/实体与内容角度相关")
    if same_source or same_hotspot:
        reasons.append("同一热点来源补充现场画面")
    return overlap * 10 + (4 if same_hotspot else 0) + (2 if same_source else 0), reasons


def _event_display_title(event: dict, brief: dict) -> str:
    """Never expose automated placeholder event names as if they were facts."""
    title = str(event.get("title_zh") or event.get("title_en") or "").strip()
    hotspot_title = str(brief.get("hotspot_title") or "").strip()
    generic_markers = ("待确认事件", "现场动态事件", "scene update")
    if not title or any(marker.casefold() in title.casefold() for marker in generic_markers):
        title = hotspot_title or title
    text = title.casefold()
    if "traffic congestion" in text and "musina" in text and "screening" in text:
        return "Musina 附近因筛查出现交通拥堵，卡车排队"
    return title or "热点现场"


def _event_visual_range(event: dict) -> tuple[int, int, str | None]:
    """Prefer analysed field footage over an anchor/studio at the event boundary."""
    start_ms = int(event.get("start_ms") or 0)
    end_ms = int(event.get("end_ms") or 0)
    asset_id = event.get("asset_id")
    if not asset_id:
        return start_ms, end_ms, None
    try:
        import database as db
        segments = db.list_asset_segments(asset_id=int(asset_id), limit=200)
    except Exception:
        return start_ms, end_ms, None
    candidates = []
    for segment in segments:
        seg_start, seg_end = int(segment.get("start_ms") or 0), int(segment.get("end_ms") or 0)
        if seg_end <= start_ms or seg_start >= end_ms:
            continue
        description = str(segment.get("description") or "")
        text = " ".join((description, str(segment.get("transcript") or ""))).casefold()
        score = 0
        if str(segment.get("primary_category") or "") == "delivery":
            score += 20
        score += sum(5 for word in ("卡车", "道路", "拥堵", "truck", "road", "congestion") if word in text)
        score -= sum(10 for word in ("主播", "订阅", "subscribe") if word in text)
        candidates.append((score, seg_start, seg_end, description))
    if not candidates:
        return start_ms, end_ms, None
    score, seg_start, seg_end, description = max(candidates, key=lambda item: (item[0], item[1]))
    if score <= 0:
        return start_ms, end_ms, None
    # 画面优化只能在用户/模型已确认的 Hook 范围内发生；相邻原子镜头可跨越
    # Hook 边界，但绝不能借此重新引入用户没有选择的母片内容。
    return max(start_ms, seg_start), min(end_ms, seg_end), description


def _eligible_owned_categories(brief: dict) -> set[str] | None:
    """Return reviewed categories that can prove this brief, or None for legacy plans."""
    if not brief.get("topic_brief_id"):
        return None
    nodes = [str(node).casefold() for node in (brief.get("logistics_nodes") or [])]
    if any(node in {"清关", "customs", "关税"} for node in nodes):
        # 放闸(preparation 模式)：无真 customs 素材时，允许 warehouse(备货)/
        # delivery(发运)作为"清关前准备"上下文。customs 真素材仍由 rank()
        # 的节点标签相关性优先选中；准入放宽的同时，文案必须由
        # overclaim_completion_issues 门禁确保只说准备、不宣称已清关。
        return {"customs", "warehouse", "delivery"}
    categories: set[str] = set()
    for node in nodes:
        categories.update(NODE_CATEGORY_RULES.get(str(node).casefold(), set()))
    if not categories:
        topic = str(brief.get("logistics_topic") or "")
        for node, matches in NODE_TERMS.items():
            if any(match in topic.casefold() for match in matches):
                categories.update(NODE_CATEGORY_RULES.get(node, set()))
    return categories


# Back-compat aliases; canonical capability tags live in asset_taxonomy.
_DELIVERY_TAG_VALUES = asset_taxonomy.DELIVERY_TAG_VALUES
_WAREHOUSE_TAG_VALUES = asset_taxonomy.WAREHOUSE_TAG_VALUES
_CUSTOMS_TAG_VALUES = asset_taxonomy.CUSTOMS_TAG_VALUES


def _functional_categories(segment: dict) -> set[str]:
    """从主分类和已识别画面语义共同推导可支持的物流能力。

    主分类仍约束文案不能夸大（仓库前的车不能说成已完成末端交付），但不能因此
    把有卡车和道路运输标签的品牌画面完全排除在运输主题之外。
    """
    categories = {str(segment.get("primary_category") or "").casefold()}
    values = {
        str(tag.get("value") or "").casefold()
        for tag in (segment.get("tags") or [])
        if str(tag.get("dimension") or "") in {"scene", "object", "entity", "action"}
    }
    if values & _DELIVERY_TAG_VALUES:
        categories.add("delivery")
    if values & _WAREHOUSE_TAG_VALUES:
        categories.add("warehouse")
    if values & _CUSTOMS_TAG_VALUES:
        categories.add("customs")
    return categories - {""}


def _owned_tag_values(item: dict) -> set[str]:
    return {
        str(tag.get("value") or "").casefold()
        for tag in (item.get("tags") or [])
        if str(tag.get("dimension") or "") in {"scene", "object", "entity", "action"}
        and str(tag.get("value") or "").strip()
    }


def _brief_wanted_tag_terms(brief: dict) -> set[str]:
    """当前物流节点期望的多维标签词，用于候选排序而非硬门禁。"""
    wanted: set[str] = set()
    for node in brief.get("logistics_nodes") or []:
        wanted.update(str(term).casefold() for term in hotspot_lexicon.expand_node_terms(node))
        normalized = str(node).casefold()
        if normalized in {"运输", "配送", "末端", "last_mile", "交付"}:
            wanted.update(_DELIVERY_TAG_VALUES)
        if normalized in {"仓储", "入库", "分拣"}:
            wanted.update(_WAREHOUSE_TAG_VALUES)
        if normalized in {"清关", "customs", "关税"}:
            wanted.update(_CUSTOMS_TAG_VALUES)
    topic = str(brief.get("logistics_topic") or "").casefold()
    if any(token in topic for token in ("运输", "港口", "干线", "配送", "port", "shipping")):
        wanted.update(_DELIVERY_TAG_VALUES)
    if any(token in topic for token in ("仓储", "仓库", "分拣", "入库", "warehouse")):
        wanted.update(_WAREHOUSE_TAG_VALUES)
    if any(token in topic for token in ("清关", "海关", "customs", "关税")):
        wanted.update(_CUSTOMS_TAG_VALUES)
    return {term for term in wanted if term}


def _owned_node_tag_relevance(item: dict, brief: dict) -> float:
    wanted = _brief_wanted_tag_terms(brief)
    if not wanted:
        return 0.0
    overlap = len(wanted & _owned_tag_values(item))
    if not overlap:
        return 0.0
    return overlap / max(3.0, min(float(len(wanted)), 8.0))


_OWNED_ASSET_SOURCES = frozenset({
    "upload", "directory", "local_directory", "manual", "local",
    # 受控放行：za-stock 免版权通用背景。仅用于补视觉洞，口播由文案门禁
    # (apply_overclaim_guard) 强制走安全模板，不构成 Buffalo 能力证明。
    "za_stock_license",
})


def _is_owned_video_segment(item: dict) -> bool:
    return str(item.get("asset_file_type") or item.get("file_type") or "") == "video"


def _is_buffalo_usable_source(item: dict) -> bool:
    # Licensed stock or a generic library must not be represented as Buffalo
    # proof.  Existing legacy rows omit asset_source and remain usable.
    # za_stock_license 是受控例外：通用背景素材，仅补视觉洞；scene 带
    # asset_source 标记，文案门禁据此强制走安全模板（不宣称南非现场/自有能力）。
    source = item.get("asset_source")
    return not source or str(source) in _OWNED_ASSET_SOURCES


def _brand_visible(item: dict) -> bool:
    return any(
        str(tag.get("dimension") or "") == "brand" and str(tag.get("value") or "").strip()
        for tag in (item.get("tags") or [])
    )


def _owned_candidates(segments: Iterable[dict], brief: dict) -> list[dict]:
    eligible_categories = _eligible_owned_categories(brief)
    videos = []
    for item in segments:
        if not _is_owned_video_segment(item):
            continue
        if item.get("asset_deprecated"):
            continue
        if not _is_buffalo_usable_source(item):
            continue
        functional_categories = _functional_categories(item)
        if eligible_categories is not None and not (functional_categories & eligible_categories):
            continue
        videos.append(item)

    def rank(item: dict) -> tuple[float, float, float, int]:
        # 保持功能分类作为准入门槛；在同类 Buffalo 自有视频里，优先使用可见
        # 品牌标识，其次与当前物流节点重合的多维标签，避免港口运输主题误选泛仓库镜头。
        visible_brands = {
            str(tag.get("value") or "").casefold()
            for tag in (item.get("tags") or [])
            if str(tag.get("dimension") or "") == "brand"
        }
        branded = 1.0 if "buffalo" in visible_brands else 0.0
        return (
            branded,
            _owned_node_tag_relevance(item, brief),
            float(item.get("quality_score") or 0),
            -int(item.get("id") or 0),
        )

    videos.sort(key=rank, reverse=True)
    # 一个原始 Buffalo 视频无论被切成多少分析片段，在一条成片里也只能用一次。
    # 优先级最高的片段保留，其他片段留给下一条成片。
    unique_assets: set[int] = set()
    result = []
    for item in videos:
        asset_id = int(item.get("asset_id") or 0)
        if not asset_id or asset_id in unique_assets:
            continue
        unique_assets.add(asset_id)
        result.append(item)
    return result


def diagnose_owned_matching(segments: Iterable[dict], brief: dict) -> dict:
    """纯观测：按 ``_owned_candidates`` 同一套闸门逐级计数，零副作用。

    不改选片/排序/阈值；只在调用方显式请求时运行。
    """
    items = list(segments)
    eligible_categories = _eligible_owned_categories(brief)
    eligible_list = sorted(eligible_categories) if eligible_categories is not None else None

    passed_video: list[dict] = []
    passed_source: list[dict] = []
    passed_category: list[dict] = []
    dropped_by_category: list[dict] = []
    category_inventory: dict[str, int] = {}

    for item in items:
        if not _is_owned_video_segment(item):
            continue
        passed_video.append(item)
        # 与 _owned_candidates 同闸门：deprecated 素材不得出现在诊断里（批13 清洗）。
        if item.get("asset_deprecated"):
            continue
        if not _is_buffalo_usable_source(item):
            continue
        passed_source.append(item)
        functional_categories = _functional_categories(item)
        if eligible_categories is not None and not (functional_categories & eligible_categories):
            for category in sorted(functional_categories):
                category_inventory[category] = category_inventory.get(category, 0) + 1
            dropped_by_category.append({
                "asset_id": item.get("asset_id"),
                "segment_id": item.get("id"),
                "primary_category": str(item.get("primary_category") or ""),
                "functional_categories": sorted(functional_categories),
                "brand_visible": _brand_visible(item),
                "description": str(item.get("description") or "")[:40],
                "quality_score": float(item.get("quality_score") or 0),
            })
            continue
        passed_category.append(item)

    # Dedup gate mirrors _owned_candidates (unique asset_id); count only.
    unique_assets: set[int] = set()
    after_dedup = 0
    for item in passed_category:
        asset_id = int(item.get("asset_id") or 0)
        if not asset_id or asset_id in unique_assets:
            continue
        unique_assets.add(asset_id)
        after_dedup += 1

    dropped_by_category.sort(
        key=lambda row: (-float(row.get("quality_score") or 0), int(row.get("segment_id") or 0))
    )
    for row in dropped_by_category:
        row.pop("quality_score", None)

    funnel = {
        "is_video": len(passed_video),
        "not_licensed_stock": len(passed_source),
        "category_match": len(passed_category),
        "after_dedup": after_dedup,
    }

    # Verdict priority matches the ops playbook; empty usable pool collapses to empty_pool.
    if funnel["is_video"] == 0 or funnel["not_licensed_stock"] == 0:
        verdict = "empty_pool"
    elif eligible_categories is not None and funnel["category_match"] == 0:
        verdict = "category_mismatch"
    elif funnel["after_dedup"] >= 4:
        verdict = "healthy"
    elif funnel["after_dedup"] >= 1:
        verdict = "thin_but_matched"
    else:
        verdict = "empty_pool"

    return {
        "eligible_categories": eligible_list,
        "logistics_nodes": list(brief.get("logistics_nodes") or []),
        "total_segments": len(items),
        "funnel": funnel,
        "dropped_by_category_mismatch": dropped_by_category[:10],
        "category_inventory": category_inventory,
        "verdict": verdict,
    }


def count_matching_hotspot_hooks(brief: dict, hotspot_events: Iterable[dict]) -> int:
    """Count confirmed/ready-or-pending hooks that hit the brief's node lexicon.

    Reuses ``expand_node_terms`` / ``extract_terms`` / ``category_profile`` — same
    term surface as event matching — without inventing a parallel word list.
    """
    nodes = [str(node) for node in (brief.get("logistics_nodes") or []) if str(node).strip()]
    topic = str(brief.get("logistics_topic") or "")
    probe = " ".join([topic, *nodes]).strip()
    wanted_terms: set[str] = set(hotspot_lexicon.extract_terms(probe))
    for node in nodes:
        wanted_terms.update(str(term).casefold() for term in hotspot_lexicon.expand_node_terms(node))
    topic_cats = hotspot_lexicon.category_profile(probe, mode="topic") if probe else set()

    count = 0
    for event in hotspot_events:
        if str(event.get("review_status") or "confirmed") != "confirmed":
            continue
        if str(event.get("clip_status") or "ready") not in {"ready", "pending"}:
            continue
        text = " ".join(
            str(event.get(key) or "")
            for key in ("title_zh", "title_en", "location")
        )
        text += " " + " ".join(str(value) for value in (event.get("keywords") or []))
        event_cats = hotspot_lexicon.category_profile(text, mode="event")
        event_terms = hotspot_lexicon.extract_terms(text)
        if (wanted_terms and wanted_terms & event_terms) or (topic_cats and topic_cats & event_cats):
            count += 1
    return count


def diagnose_starving_side(
    *,
    owned_pool: int,
    hotspot_pool: int,
    hotspot_batch_age: str | None = None,
) -> dict:
    """Point ops at which library is hungry: hotspot batch vs Buffalo owned."""
    if hotspot_pool == 0:
        side = "hotspot"
    elif owned_pool < 4:
        side = "owned"
    else:
        side = "none"
    return {
        "starving_side": side,
        "hotspot_pool": int(hotspot_pool),
        "owned_pool": int(owned_pool),
        "hotspot_batch_age": hotspot_batch_age,
    }


def _owned_visual_family(item: dict) -> str:
    """Return a broad, visible-action family used to avoid warehouse monotony."""
    categories = _functional_categories(item)
    for category in ("delivery", "facility", "staff", "warehouse"):
        if category in categories:
            return category
    return str(item.get("primary_category") or "other").casefold() or "other"


def _diversify_owned_candidates(items: list[dict]) -> list[dict]:
    """Prefer distinct visible actions before reusing a broad visual family."""
    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(_owned_visual_family(item), []).append(item)
    priority = {"delivery": 0, "facility": 1, "staff": 2, "warehouse": 3, "other": 4}
    ordered: list[dict] = []
    used_actions: set[str] = set()
    previous = ""
    while buckets:
        options = [
            (family, index, item)
            for family, bucket in buckets.items()
            for index, item in enumerate(bucket)
        ]
        family, index, selected = min(
            options,
            key=lambda option: (
                _owned_copy_anchor(option[2]) in used_actions,
                option[0] == previous,
                priority.get(option[0], 9),
                option[1],
                option[0],
            ),
        )
        ordered.append(buckets[family].pop(index))
        used_actions.add(_owned_copy_anchor(selected))
        if not buckets[family]:
            del buckets[family]
        previous = family
    return ordered


def _owned_copy_anchor(segment: dict) -> str:
    """Use only the action visible in reviewed metadata, never raw OCR text."""
    text = " ".join(str(segment.get(key) or "") for key in ("description", "asset_name", "name")).casefold()
    family = _owned_visual_family(segment)
    if "叉车" in text or "forklift" in text:
        return "叉车正在仓内搬运包裹。"
    if any(term in text for term in ("检查包裹", "核对", "检查")):
        return "工作人员正在逐件核对包裹。"
    if any(term in text for term in ("拖车", "trailer")):
        return "现场可见一辆待处理的拖车。"
    if family == "delivery":
        return "车辆正在进行发运前准备。"
    if family == "staff":
        return "工作人员正在处理仓内包裹。"
    if family == "facility":
        return "仓内设备正在处理包裹。"
    return "仓内正在进行分拣准备。"


def _owned_action_key(segment: dict) -> str:
    """Return a stable key only when the reviewed metadata identifies an action."""
    text = " ".join(str(segment.get(key) or "") for key in ("description", "asset_name", "name")).casefold()
    if any(term in text for term in ("叉车", "forklift", "检查包裹", "核对", "检查", "拖车", "trailer")):
        return _owned_copy_anchor(segment)
    return f"asset:{int(segment.get('asset_id') or segment.get('id') or 0)}"


def _owned_image_candidates(images: Iterable[dict], brief: dict) -> list[dict]:
    """Return distinct Buffalo-owned stills for short visual transitions.

    A photo is deliberately labelled as a context image, never as proof that a
    specific delivery result happened.  It is preferable to the former blank
    "route/track/order" information card when real video is sparse.
    """
    eligible_categories = _eligible_owned_categories(brief)
    preferred = ["delivery", "warehouse", "staff", "facility", "brand", "customer", "other"]
    priority = {category: len(preferred) - index for index, category in enumerate(preferred)}
    candidates = []
    for item in images:
        file_type = str(item.get("asset_file_type") or item.get("file_type") or "").casefold()
        if file_type != "image" or item.get("asset_hotspot_id") or item.get("hotspot_id"):
            continue
        source = str(item.get("asset_source") or item.get("source") or "").casefold()
        if source and source not in {"upload", "directory", "local_directory", "manual", "local"}:
            continue
        category = str(item.get("primary_category") or item.get("category") or "other").casefold()
        # A category restriction is useful for a formal custom brief, but a
        # brand/delivery photo remains a valid *context* transition when the
        # brief is broader than a single logistics node.
        if eligible_categories is not None and category not in eligible_categories | {"brand", "other"}:
            continue
        try:
            asset_id = int(item.get("asset_id") or item.get("id") or 0)
        except (TypeError, ValueError):
            asset_id = 0
        if asset_id:
            candidates.append({**item, "asset_id": asset_id, "primary_category": category})
    candidates.sort(key=lambda item: (
        priority.get(str(item.get("primary_category") or "other"), 0),
        float(item.get("quality_score") or 0),
        int(item.get("asset_id") or 0),
    ), reverse=True)
    result: list[dict] = []
    seen: set[int] = set()
    for item in candidates:
        asset_id = int(item["asset_id"])
        if asset_id in seen:
            continue
        seen.add(asset_id)
        result.append(item)
    return result


def safe_customs_preparation_copy(category: str, max_chars: int | None = None,
                                  min_chars: int | None = None) -> str:
    """清关 preparation 模式的安全兼底文案：只说准备、绝不含完成词。

    纯确定性、无模型调用；供规划模板与过度宣称门禁回退共用。
    长句在前、短句在后，按字数边界选最长可用档位。
    """
    labels = {"warehouse": "仓内备货", "delivery": "发运准备",
              "staff": "分拣与核对", "facility": "现场准备"}
    label = labels.get(str(category or "").casefold(), "仓内准备")
    variants = (
        f"清关前的{label}：先在仓内把单证与货物备齐，等待海关放行。",
        f"清关前的{label}：单证与货物正在备齐，等待海关放行。",
        f"清关前的{label}：先把单证与货物备齐。",
        f"清关前的{label}：单证与货物备齐中。",
    )
    fallback = variants[-1]
    for copy in variants:
        compact_length = len("".join(copy.split()))
        if min_chars is not None and compact_length < min_chars:
            continue
        if max_chars is not None and compact_length > max_chars:
            continue
        return copy
    return fallback


def _voiceover(brief: dict, role: str, index: int, title: str, category: str = "") -> str:
    topic = brief.get("logistics_topic") or "物流体验"
    if role == "hotspot_evidence":
        if index == 1:
            if "musina" in title.casefold() and "拥堵" in title:
                return "Musina 现场，筛查让卡车排起长队。你的订单，还能按原计划走吗？"
            return f"现场正在发生：{title}。你的订单，还能按原计划走吗？"
        if index == 2:
            return f"堵的不只是一条路，{topic}的交付预期也要重新核对。"
        return "热点不是 Buffalo 的服务证明；真正该问的是，异常出现时，谁在提前调整路线和沟通？"
    if role == "owned_proof":
        labels = {"warehouse": "仓储和备货", "staff": "分拣与检查", "facility": "现场设施", "delivery": "运输与交付"}
        openings = (
            "先看执行现场：", "再往下拆一层：", "真正影响客户体验的是：",
            "从仓内细节看：", "换到另一处动作：", "再补一个核对节点：",
            "最后落到交付这一环：", "这一步画面显示的是：",
        )
        if category in {"warehouse", "staff", "facility"} and any(
            str(node).casefold() in {"末端", "last_mile", "配送", "交付"}
            for node in (brief.get("logistics_nodes") or [])
        ):
            return f"{openings[(index - 1) % len(openings)]}配送前的{labels.get(category, '仓内准备')}，先把异常留在仓内。"
        if category in {"warehouse", "delivery"} and any(
            str(node).casefold() in {"清关", "customs", "关税"}
            for node in (brief.get("logistics_nodes") or [])
        ):
            # preparation 模式安全基线：非真 customs 素材只能作清关前准备
            # 上下文；文案只用准备词，绝不宣称已清关/已放行。不加开场
            # 前缀，避免叠加后突破预览链的单镜字数上限。
            return safe_customs_preparation_copy(category)
        return f"{openings[(index - 1) % len(openings)]}Buffalo 用{labels.get(category, '仓配流程')}承接每一步。"
    return "热点会变化，履约准备要先到位。"


def _limit_distinct_hotspot_hooks(selected_events: list[tuple[int, dict, list[str]]]) -> list[tuple[int, dict, list[str]]]:
    """Keep at most two non-overlapping hooks from one hotspot parent video."""
    result: list[tuple[int, dict, list[str]]] = []
    by_asset: dict[int, list[tuple[int, int]]] = {}
    for item in selected_events:
        event = item[1]
        if str(event.get("review_status") or "confirmed") != "confirmed":
            continue
        # A confirmed Hook may still be waiting for its short local proxy.  It
        # remains eligible for planning because the renderer can materialize its
        # exact source range; failed/unreviewed rows never enter the plan.
        if str(event.get("clip_status") or "ready") not in {"ready", "pending"}:
            continue
        asset_id = int(event.get("asset_id") or 0)
        start_ms, end_ms = int(event.get("start_ms") or 0), int(event.get("end_ms") or 0)
        ranges = by_asset.setdefault(asset_id, [])
        if len(ranges) >= 2:
            continue
        if end_ms > start_ms and any(max(start_ms, start) < min(end_ms, end) for start, end in ranges):
            continue
        ranges.append((start_ms, end_ms))
        result.append(item)
    return result


def plan_followup_scenes(
    brief: dict, hotspot_events: list[dict], owned_segments: list[dict], target_duration_ms: int = 60_000,
    owned_images: list[dict] | None = None,
    *,
    allow_adaptation: bool = False,
) -> list[dict]:
    # A MiMo + critic approved set has already passed factual relevance review.
    # Preserve that decision rather than applying a second keyword-only filter
    # that can accidentally drop one of two complementary shots from the same
    # verified event (for example, "traffic congestion" then "trucks queued").
    approved_ids = {
        int(value) for value in (brief.get("approved_hook_event_ids") or [])
        if str(value).strip().isdigit()
    }
    if approved_ids:
        selected_events = [
            (100, event, ["内置模型已确认的同一热点 Hook"])
            for event in hotspot_events
            if int(event.get("id") or 0) in approved_ids
        ][:3]
    else:
        selected_events = []
    ranked_events = []
    if not selected_events:
        for event in hotspot_events:
            score, reasons = _event_score(event, brief)
            if score > 0:
                ranked_events.append((score, event, reasons))
        ranked_events.sort(key=lambda item: (-item[0], int(item[1].get("event_index") or item[1].get("id") or 0)))
        selected_events = ranked_events[:3]
    primary_event_id = int(brief.get("primary_event_id") or 0)
    if primary_event_id:
        # 其余同源 Hook 可以作为补充现场证据，但用户点选的 Hook 必须承担开场。
        selected_events.sort(
            key=lambda item: (
                0 if int(item[1].get("id") or 0) == primary_event_id else 1,
                int(item[1].get("event_index") or item[1].get("id") or 0),
            )
        )
    selected_events = _limit_distinct_hotspot_hooks(selected_events)
    owned = _diversify_owned_candidates(_owned_candidates(owned_segments, brief))
    # A candidate whose reviewed range is under 3s can never become a scene
    # (see the >=3_000 check below). Dropping it here, before the owned_limit
    # slice, lets a deep candidate pool backfill that slot with the next
    # usable clip instead of silently losing a scene the library could have
    # covered — this used to shrink a 60s plan to ~7 scenes of real footage
    # whenever one of the first `owned_limit` diversified picks happened to
    # be a too-short clip.
    owned = [item for item in owned if _usable_source_duration_ms(item) >= 3_000]
    context_images = _owned_image_candidates(owned_images or [], brief)
    scenes = []
    target_duration_ms = max(50_000, min(90_000, int(target_duration_ms)))
    # 60 秒默认使用七段不重复 Buffalo 现场；90 秒最多扩展到八段。若素材不足，
    # 后续质量门禁会停止而不是循环旧叉车画面。
    owned_limit = 7 if target_duration_ms <= 60_000 else 8
    # Similar assets from different files are not genuinely different proof.
    # A formal user video may show each reviewed action once; when the library
    # cannot support the requested duration after this filter, the caller must
    # surface the coverage gap instead of padding with more forklift footage.
    distinct_owned = []
    used_actions: set[str] = set()
    for item in owned:
        action = _owned_action_key(item)
        if action in used_actions:
            continue
        used_actions.add(action)
        distinct_owned.append(item)
    # ---- za_stock 补充层：硬上限 2，仅在有缺口时进片（批13）----
    # 必须在全量 distinct_owned 上分层，而非已按 owned_limit 切片的子集：
    # za_stock 被 rank 的 -int(id) 垫底，先切片会把它们全切掉，补充层就成空操作。
    ZA_STOCK_SOURCE = "za_stock_license"
    ZA_STOCK_MAX_SCENES = 2
    buffalo = [item for item in distinct_owned if item.get("asset_source") != ZA_STOCK_SOURCE]
    zastock = [item for item in distinct_owned if item.get("asset_source") == ZA_STOCK_SOURCE]
    # 缺口 = brief 需要但 buffalo 未覆盖的功能类目
    eligible = _eligible_owned_categories(brief)
    covered: set[str] = set()
    for item in buffalo:
        covered |= _functional_categories(item)
    gap = (eligible - covered) if eligible is not None else set()
    # 1) 优先补缺口类目；2) buffalo 总量不足 owned_limit 时补位
    picks = [it for it in zastock if _functional_categories(it) & gap][:ZA_STOCK_MAX_SCENES]
    if len(picks) < ZA_STOCK_MAX_SCENES and len(buffalo) < owned_limit:
        picks += [it for it in zastock if it not in picks][:ZA_STOCK_MAX_SCENES - len(picks)]
    # buffalo 保底 + za_stock 追加后重新交织，避免 za_stock 堆在片尾
    owned = _diversify_owned_candidates(buffalo[:owned_limit - len(picks)] + picks)
    # Ideal formal plans avoid automatic image inserts. Adaptive chat plans may
    # re-enable up to three stills as rhythm bridges when owned footage is thin.
    if allow_adaptation and len(owned) < 4:
        context_images = context_images[:3]
    else:
        context_images = []

    # Do not schedule a source that is already known to be too short.  This is
    # intentionally before the duration calculation so a 1-second segment can
    # never quietly cause a real-video loop later in the renderer.
    source_slots: list[tuple[str, object]] = []
    for item in selected_events:
        event = item[1]
        event_start, event_end, _ = _event_visual_range(event)
        if _usable_source_duration_ms(event, start_ms=event_start, end_ms=event_end) >= 3_000:
            source_slots.append(("hotspot", item))
    for segment in owned:
        if _usable_source_duration_ms(segment) >= 3_000:
            source_slots.append(("owned", segment))
    if not source_slots:
        return []
    # 用每个真实素材实际可用时长计算，而不是把全部视频假定为 7 秒。
    # 素材不足只会让计划被上层拒绝，绝不生成信息图或循环旧片段来补时长。
    def source_duration(slot: tuple[str, object]) -> int:
        kind, payload = slot
        if kind == "hotspot":
            event = payload[1]
            start, end, _ = _event_visual_range(event)
            return _usable_source_duration_ms(event, start_ms=start, end_ms=end)
        return _usable_source_duration_ms(payload)

    slots: list[tuple[str, object]] = []
    hotspot_slots = [item for item in source_slots if item[0] == "hotspot"]
    owned_slots = [item for item in source_slots if item[0] == "owned"]
    # 开头先给热点事实，随后交替 Buffalo 实拍与自有照片；不再自动生成空白
    # 路线/订单 PPT。若图库也不足，宁可输出较短计划并由上层提示补素材。
    slots.extend(hotspot_slots[:2])
    if len(hotspot_slots) > 2:
        slots.append(hotspot_slots[2])
    image_index = 0
    for position, item in enumerate(owned_slots):
        slots.append(item)
        if image_index < len(context_images) and position in {0, 2, 4}:
            slots.append(("image", context_images[image_index]))
            image_index += 1
    # Adaptive: if owned footage is sparse, append remaining stills as bridges.
    while allow_adaptation and image_index < len(context_images):
        slots.append(("image", context_images[image_index]))
        image_index += 1

    for position, (kind, payload) in enumerate(slots, 1):
        if kind == "hotspot":
            _, event, reasons = payload
            title = _event_display_title(event, brief)
            asset_start_ms, asset_end_ms, visual_description = _event_visual_range(event)
            duration_ms = _usable_source_duration_ms(
                event, start_ms=asset_start_ms, end_ms=asset_end_ms,
            )
            scenes.append({
                "scene": position, "scene_role": "hotspot_evidence", "evidence_type": "hotspot_video",
                "duration_ms": duration_ms, "duration": duration_ms / 1000,
                "visual": title, "voiceover": _voiceover(brief, "hotspot_evidence", position, title),
                "text_overlay": title[:24], "asset_id": event.get("asset_id"), "event_clip_id": event.get("id"),
                "asset_start_ms": asset_start_ms, "asset_end_ms": asset_end_ms,
                "match_reasons": (reasons or ["热点来源画面"]) + ([f"优先现场子片段：{visual_description}"] if visual_description else []),
            })
            continue
        if kind == "owned":
            segment = payload
            duration_ms = _usable_source_duration_ms(segment)
            category = str(segment.get("primary_category") or "")
            visible_brands = [
                str(tag.get("value") or "") for tag in (segment.get("tags") or [])
                if str(tag.get("dimension") or "") == "brand"
            ]
            description = str(segment.get("description") or "").strip()
            visual = description or str(segment.get("asset_name") or "Buffalo 履约现场")
            scenes.append({
                "scene": position, "scene_role": "owned_proof", "evidence_type": "owned_video",
                "duration_ms": duration_ms, "duration": duration_ms / 1000,
                "visual": visual,
                "voiceover": _voiceover(brief, "owned_proof", position, "", category),
                "text_overlay": f"{brief.get('logistics_topic', '物流体验')}｜{category or '履约现场'}"[:24],
                "asset_id": segment.get("asset_id"), "asset_segment_id": segment.get("id"),
                # 文案门禁需要按镜头主分类精准拦截过度宣称。
                "primary_category": category,
                # 受控开闸：za-stock 通用背景段必须带来源标记，门禁据此强制安全模板。
                "asset_source": segment.get("asset_source") or "",
                "asset_start_ms": segment.get("start_ms", 0), "asset_end_ms": segment.get("end_ms", 0),
                "copy_anchor": _owned_copy_anchor(segment),
                "action_key": _owned_action_key(segment),
                "match_reasons": [f"素材分类匹配：{category or '已审核视频'}"]
                    + ([f"可见品牌露出：{'、'.join(visible_brands)}"] if visible_brands else [])
                    + ["仅作为可见执行动作证据，不替代不可见服务承诺"],
            })
            continue
        if kind == "image":
            image = payload
            category = str(image.get("primary_category") or image.get("category") or "素材库")
            visual = str(image.get("name") or image.get("asset_name") or "Buffalo 自有素材")
            scenes.append({
                "scene": position, "scene_role": "owned_context_image", "evidence_type": "image",
                "duration_ms": CONTEXT_IMAGE_DURATION_MS, "duration": CONTEXT_IMAGE_DURATION_MS / 1000,
                "visual": visual,
                "voiceover": "把仓内准备和外部变化分开看。",
                "text_overlay": "仓内准备分开看",
                "asset_id": image.get("asset_id"),
                "match_reasons": [
                    f"Buffalo 自有{category}图片：只作场景过渡，不作为热点或服务结果证据",
                ],
            })
            continue
        raise ValueError(f"不支持的素材槽位类型：{kind}")
    if sum(int(scene["duration_ms"]) for scene in scenes) > target_duration_ms:
        # 图片只是一段 2 秒的节奏过渡，不能为了压缩一个正式双素材视频而
        # 挤占实拍镜头的最低可用时长。先移除可选图片；仍超预算时，所有剩余
        # 槽位都是真实视频，统一按 3 秒下限收缩，渲染器便不会收到会被迫循环
        # 或只剩一闪而过的真实片段。
        if not allow_adaptation:
            scenes = [scene for scene in scenes if scene.get("evidence_type") != "image"]
    if sum(int(scene["duration_ms"]) for scene in scenes) > target_duration_ms:
        scenes = rebalance_scenes_to_budget(scenes, target_duration_ms, minimum_scene_ms=3_000)
    for scene in scenes:
        if scene.get("evidence_type") in {"hotspot_video", "owned_video"} and int(scene["duration_ms"]) < 3_000:
            raise ValueError("真实视频不足 3 秒；请减少镜头或补充未使用的 Buffalo 自有图片")
    usage = source_usage_report(scenes)
    if not usage["passed"]:
        raise ValueError("成片素材重复硬门禁未通过：" + "；".join(usage["issues"]))
    return scenes


def describe_plan_adaptation(
    scenes: list[dict],
    *,
    ideal_owned: int = 4,
    ideal_min_scenes: int = 7,
) -> dict:
    """Summarize how the plan differs from the ideal dual-library inventory."""
    hotspot_count = sum(scene.get("evidence_type") == "hotspot_video" for scene in scenes)
    owned_count = sum(scene.get("evidence_type") == "owned_video" for scene in scenes)
    image_count = sum(scene.get("evidence_type") == "image" for scene in scenes)
    duration_ms = sum(int(scene.get("duration_ms") or 0) for scene in scenes)
    strategies: list[str] = []
    adapted = False
    if owned_count < ideal_owned:
        adapted = True
        strategies.append("reduce_owned_requirement")
    if image_count:
        adapted = True
        strategies.append("use_owned_images_as_bridges")
    if len(scenes) < ideal_min_scenes:
        adapted = True
        strategies.append("shorten_structure")
    if adapted:
        strategies.append("brand_endcard_close")
    return {
        "adapted": adapted,
        "strategies": list(dict.fromkeys(strategies)),
        "coverage": {
            "hotspot_video": hotspot_count,
            "owned_video": owned_count,
            "image": image_count,
            "scene_count": len(scenes),
            "duration_ms": duration_ms,
        },
        "ideal": {
            "hotspot_video": 1,
            "owned_video": ideal_owned,
            "scene_count": f"{ideal_min_scenes}–10",
            "duration_ms": "50000–90000",
        },
        "message": (
            "库存不足，已按现有 Hook + Buffalo 素材自适应规划并继续生产。"
            if adapted
            else "库存满足理想双素材结构。"
        ),
    }


def append_brand_endcard_scenes(scenes: list[dict]) -> list[dict]:
    """Append the user-provided Buffalo CTA endcard to every rendered topic video."""
    combined = [dict(scene) for scene in scenes]
    for template in BRAND_ENDCARD_SCENES:
        combined.append({**template, "scene": len(combined) + 1,
                         "duration": template["duration_ms"] / 1000})
    return combined


def build_scenes(package: dict, *, owned_segments: list[dict]) -> list[dict]:
    """Build a 60-second evidence plan from one confirmed topic package only."""
    from hotspot_logistics_planner import build_brief

    event = {
        **package,
        "hotspot_id": package.get("id") or package.get("hotspot_id"),
        "title_en": package.get("title") or package.get("title_en"),
    }
    brief = build_brief(event, owned_segments)
    scenes = plan_followup_scenes(brief, list(package.get("event_clips") or []), owned_segments)
    for scene in scenes:
        scene["source_type"] = scene["evidence_type"]
    return scenes
