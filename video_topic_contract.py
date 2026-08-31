"""First-principles topic contracts for chat-to-video production.

The user topic is the immutable production contract.  A real timely Hook is
allowed only when the topic names a concrete, verifiable event.  Evergreen or
comparison topics instead receive a topic-specific editorial opener made from
Buffalo-owned media; it must never be labelled as hotspot evidence.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy


_CONTRACTS = (
    {
        "match": ("南非本地快递对比评测", "本地快递对比", "快递对比评测"),
        "intent": "local_courier_comparison",
        "selection_offset": 0,
        "label": "南非本地快递对比评测",
        "safe_title": "南非本地快递怎么比：三项关键维度",
        "nodes": ["末端", "配送", "仓储", "分拣"],
        "family_priority": ["delivery", "staff", "facility", "warehouse"],
        "title_groups": [("南非", "本地"), ("快递", "配送"), ("对比", "评测", "怎么选", "维度")],
        "narrative_groups": [("取件", "揽收"), ("分拣", "仓内"), ("末端", "交接", "配送"), ("对比", "维度", "评测")],
        "opening_hook": "南非本地快递怎么比？别只看每公斤价格。",
        "opening_bridge": "取件、分拣和末端交接，是三把对比尺子。",
        "event_context_lines": ["取件、分拣和末端交接，是三把对比尺子。"],
        "image_bridge_lines": [
            "先统一对比口径。",
            "同路线再比较。",
            "再看交接记录。",
        ],
        "safe_angle": "缺少同口径实测数据时，只做取件、分拣、末端交接等对比维度，不生成排名、报价或最佳结论。",
    },
    {
        "match": ("同城配送时效对比", "同城配送", "配送时效对比"),
        "intent": "same_city_delivery_sla",
        "selection_offset": 3,
        "label": "同城配送时效对比",
        "safe_title": "同城配送时效怎么比：四个关键节点",
        "nodes": ["末端", "配送", "分拣"],
        "family_priority": ["delivery", "facility", "staff", "warehouse"],
        "title_groups": [("同城",), ("配送", "快递"), ("时效", "速度"), ("对比", "差异", "怎么选", "怎么比")],
        # These are semantic alternatives, not a requirement to repeat one
        # exact word. MiniMax often says “订单接入/揽收/送达”; rejecting those
        # valid expressions made a reachable model look like a production
        # outage and caused the whole project to stop in scripting.
        "narrative_groups": [
            ("接单", "取件", "揽收", "收件", "订单接入", "订单进入", "接收订单", "下单"),
            ("分拣", "交接", "转运", "出库"),
            ("出车", "配送", "派送", "运送", "发车"),
            ("时效", "签收", "送达", "到件", "时长"),
        ],
        "opening_hook": "同城配送快不快，不能只看出车时间。",
        "opening_bridge": "接单、分拣、出车和签收，每一步都在累计时效。",
        "event_context_lines": ["接单、分拣、出车和签收，每一步都在累计时效。"],
        "image_bridge_lines": [
            "先统一计时点。",
            "再比较全程时效。",
            "异常也要留痕。",
        ],
        "safe_angle": "不编造分钟级时效或服务商排名；用可见流程解释同城配送的时效差异。",
    },
    {
        "match": ("旺季爆仓应对策略", "旺季爆仓", "爆仓应对"),
        "intent": "peak_overflow_response",
        "selection_offset": 6,
        "label": "旺季爆仓应对策略",
        "safe_title": "旺季爆仓应对策略：先稳住三处节点",
        "nodes": ["仓储", "分拣", "配送"],
        "family_priority": ["warehouse", "facility", "staff", "delivery"],
        "title_groups": [("旺季",), ("爆仓", "库容", "库位"), ("应对", "策略", "预案")],
        "narrative_groups": [("库位", "库容", "仓储"), ("分拣",), ("交接", "配送"), ("预案", "应对")],
        "opening_hook": "旺季最怕的不只是订单多，更怕仓内失序。",
        "opening_bridge": "库位、分拣、交接先失序，配送只会更慢。",
        "event_context_lines": [
            "库位、分拣、交接先失序，配送只会更慢。",
            "爆仓预案要写清触发点和应对顺序。",
        ],
        "image_bridge_lines": [
            "先核对库容。",
            "预案写明触发点。",
            "提前确认分工。",
        ],
        "safe_angle": "把爆仓风险拆到库位、分拣、交接和配送动作，说明可执行的应对顺序。",
    },
    {
        "match": ("旺季备战全流程复盘", "旺季备战", "全流程复盘"),
        "intent": "peak_full_cycle_review",
        "selection_offset": 9,
        "label": "旺季备战全流程复盘",
        "safe_title": "旺季备战全流程复盘：从入库到交付",
        "nodes": ["仓储", "分拣", "运输", "配送"],
        "family_priority": ["warehouse", "staff", "delivery", "facility"],
        "title_groups": [("旺季",), ("备战", "准备"), ("全流程", "全链路", "复盘")],
        "narrative_groups": [("入库", "仓储"), ("分拣",), ("出车", "运输"), ("交付", "配送", "末端")],
        "opening_hook": "旺季备战不是当天救火，要从全流程倒推。",
        "opening_bridge": "从入库、分拣、出车到交付，各环节提前校准。",
        "event_context_lines": ["从入库、分拣、出车到交付，各环节提前校准。"],
        "image_bridge_lines": [
            "先汇总节点记录。",
            "逐段核对准备。",
            "问题落到节点。",
        ],
        "safe_angle": "按入库、分拣、出车、交付的顺序复盘旺季准备，不把单一仓内动作冒充全流程。",
    },
    {
        "match": ("政策法规变动速递", "政策法规变动", "法规变动速递"),
        "intent": "policy_change_verification",
        "selection_offset": 12,
        "label": "政策法规变动速递",
        "safe_title": "政策法规变动核验：先确认这三项",
        "nodes": ["清关", "仓储", "运输"],
        "family_priority": ["staff", "warehouse", "facility", "delivery"],
        "title_groups": [("政策", "法规"), ("变动", "变化", "更新", "核验")],
        "narrative_groups": [("官方", "发布机构"), ("适用", "对象"), ("生效", "日期"), ("清关", "合规", "准备")],
        "opening_hook": "没有官方原文和生效日，就不叫政策速递。",
        "opening_bridge": "核对官方发布机构、对象、生效日和清关准备。",
        "event_context_lines": ["核对官方发布机构、对象、生效日和清关准备。"],
        "image_bridge_lines": [
            "先核对原文日期。",
            "来源不明待核验。",
            "再确认适用对象。",
        ],
        "safe_angle": "没有官方原文、适用对象和生效日期时，生成政策核验清单，不伪装成新规新闻。",
    },
)


_CUSTOM_TOPIC_NODE_TERMS = (
    (("海关", "清关", "关税", "报关", "进口税", "出口税", "customs", "tariff", "duty"), "清关"),
    (("港口", "码头", "口岸", "集装箱", "堆场", "transnet", "npa", "port", "harbour", "harbor"), "港口"),
    (("铁路", "rail", "railway"), "铁路"),
    (("道路", "公路", "路况", "干线", "sanral", "road", "highway"), "道路"),
    (("仓储", "仓库", "入库", "进仓", "海外仓", "仓配", "warehouse"), "仓储"),
    (("配送", "快递", "末端", "派送", "同城", "仓配", "delivery", "last mile"), "末端"),
    (("运输", "货运", "物流", "跨境", "运输", "freight", "logistics"), "运输"),
)

_CUSTOM_TOPIC_ENTITY_TERMS = (
    (("transnet", "npa"), "Transnet"),
    (("sanral",), "SANRAL"),
    (("sars",), "SARS"),
)


def normalize_topic_input(topic: str) -> str:
    """Extract the user's topic from a natural-language production command.

    Chat and the quick-topic selector both send short subjects, but the free
    input path may send text such as ``1. 南非海关政策年度变化 请围绕这个话题
    来生成一个视频``.  Treating that whole command as the subject makes the
    topic contract meaningless and lets a generic Hook pass.  This function is
    deliberately deterministic and keeps the original input out of the
    semantic subject field.
    """
    original = " ".join(str(topic or "").split())[:500]
    text = original
    if not text:
        return ""
    instruction_mode = False
    text = re.sub(r"^\s*(?:\d+[.)、:：]\s*)+", "", text)
    quoted = re.search(
        r"(?:请)?围绕\s*[「『“\"”](.+?)[」』”\"”]",
        text,
        flags=re.IGNORECASE,
    )
    if quoted:
        instruction_mode = True
        text = quoted.group(1)
    else:
        marker = re.search(r"(?:请)?围绕(?:这个|该)?话题", text, flags=re.IGNORECASE)
        if marker:
            instruction_mode = True
            prefix = text[:marker.start()].strip(" ：:，,。；;")
            if prefix:
                text = prefix
        else:
            about = re.search(
                r"(?:关于|围绕|关乎)\s*[「『“\"]?(.+?)[」』”\"]?\s*"
                r"(?:的)?(?:介绍|相关)?(?:视频|内容|文案)",
                text,
                flags=re.IGNORECASE,
            )
            if about:
                instruction_mode = True
                text = about.group(1)
            else:
                instruction_mode = bool(re.match(r"^(?:请)?围绕\s*", text, flags=re.IGNORECASE))
                text = re.sub(r"^(?:请)?围绕\s*", "", text, flags=re.IGNORECASE)
    text, suffix_removed = re.subn(
        r"\s*(?:来)?(?:生成|创作|制作)(?:一个|一篇|本)?(?:视频|内容|文案)\s*[。！？!?]*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    instruction_mode = instruction_mode or bool(suffix_removed)
    text, command_removed = re.subn(
        r"\s*(?:请帮我|帮我)\s*(?:生成|创作|制作).*$", "", text, flags=re.IGNORECASE
    )
    instruction_mode = instruction_mode or bool(command_removed)
    cleaned = (text.strip(" ：:，,。；;！？?!") if instruction_mode else text.strip(" ：:，,。；;"))[:300]
    return cleaned or original[:300]


def _custom_topic_nodes(subject: str) -> list[str]:
    folded = subject.casefold()
    nodes: list[str] = []
    for terms, node in _CUSTOM_TOPIC_NODE_TERMS:
        if any(term.casefold() in folded for term in terms) and node not in nodes:
            nodes.append(node)
    return nodes


def _custom_topic_entities(subject: str) -> list[str]:
    folded = subject.casefold()
    return [
        entity for terms, entity in _CUSTOM_TOPIC_ENTITY_TERMS
        if any(term.casefold() in folded for term in terms)
    ]


def title_group_issues(title: str, contract: dict) -> list[str]:
    """Return only title-group violations so generation and QA share one rule."""
    normalized = _normalize(title)
    errors: list[str] = []
    for group in contract.get("title_groups") or []:
        if not any(_normalize(term) in normalized for term in group):
            errors.append("标题缺少主题要素：" + "/".join(group))
    return errors


def ensure_title_satisfies_contract(title: str, contract: dict) -> str:
    """Repair a title so it can pass the same gate that later QA will apply.

    Custom topics such as “仓配怎么安排” previously generated a legal subject
    that the later script-quality pass rejected because the gate only listed
    仓储/配送.  The repaired title must remain the user topic, not a slogan.
    """
    candidate = str(title or "").strip()
    if not title_group_issues(candidate, contract):
        return candidate[:120]
    for option in (
        contract.get("safe_title"),
        contract.get("label"),
        contract.get("requested_topic"),
        contract.get("original_input"),
    ):
        option_text = str(option or "").strip()
        if option_text and not title_group_issues(option_text, contract):
            return option_text[:120]
    missing = title_group_issues(candidate, contract)
    suffix = ""
    if missing:
        first_group = (contract.get("title_groups") or [()])[0]
        suffix = next((str(term) for term in first_group if str(term).strip()), "")
    if suffix and _normalize(suffix) not in _normalize(candidate):
        base = candidate.rstrip("。！？； ") or "物流安排"
        return f"{base}｜{suffix}"[:120]
    return (candidate or suffix or "物流安排")[:120]


def _custom_contract(subject: str) -> dict:
    explicit_nodes = _custom_topic_nodes(subject)
    explicit_entities = _custom_topic_entities(subject)
    nodes = explicit_nodes or ["运输", "仓储", "配送"]
    folded_subject = subject.casefold()
    policy_subject_terms = (
        "政策", "法规", "新规", "海关", "清关", "关税", "报关",
        "进口税", "出口税", "customs", "tariff", "duty",
    )
    policy_change_terms = (
        "变动", "变化", "更新", "调整", "年度", "生效", "速递",
        "change", "update", "new rule",
    )
    policy = any(term.casefold() in folded_subject for term in policy_subject_terms) and any(
        term.casefold() in folded_subject for term in policy_change_terms
    )
    title_groups: list[tuple[str, ...]] = []
    if "南非" in subject:
        title_groups.append(("南非",))
    if "清关" in nodes:
        title_groups.append(("海关", "清关", "报关", "关税"))
    if policy:
        title_groups.append(("政策", "法规", "变动", "变化", "更新", "年度"))
    # Long free-form user topics must be represented by stable semantic
    # anchors, not by an arbitrary first-18-character substring.  The latter
    # caused the later script-quality pass to reject an otherwise valid title
    # merely because a Hook logistics question had been copied into the brief.
    node_title_groups = {
        "港口": ("港口", "码头", "口岸", "集装箱"),
        "铁路": ("铁路", "列车", "轨道"),
        "道路": ("道路", "公路", "干线", "路况", "N3"),
        "仓储": ("仓储", "仓库", "入库", "分拣", "库位", "仓配"),
        "末端": ("配送", "快递", "末端", "派送", "签收", "仓配"),
        "运输": ("运输", "货运", "物流", "跨境", "履约"),
    }
    # A custom topic may mention several adjacent logistics nodes. Requiring
    # one title token from every node produced keyword piles such as
    # “N3 + logistics + Buffalo”. Keep one semantic OR-group for the logistics
    # core; Hook compatibility is still checked against every requested node.
    semantic_title_terms: list[str] = []
    for node in explicit_nodes:
        for term in node_title_groups.get(node) or ():
            if term not in semantic_title_terms:
                semantic_title_terms.append(term)
    if semantic_title_terms:
        title_groups.append(tuple(semantic_title_terms))
    # Preserve explicit organisations and user-supplied Latin brand/entity
    # names such as Takealot even when they are not in the built-in registry.
    for entity in explicit_entities:
        group = (entity,)
        if group not in title_groups:
            title_groups.append(group)
    # Buffalo belongs in the narrative advantage, not in every title. Named
    # external organisations remain strict through ``explicit_entities``.
    generic_latin = {
        "south", "africa", "logistics", "video", "topic", "buffalo",
    }
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}", subject):
        if token.casefold() in generic_latin:
            continue
        group = (token,)
        if group not in title_groups:
            title_groups.append(group)
    if not title_groups:
        signal_groups = (
            ("火情", "起火", "火灾", "浓烟"),
            ("旺季", "爆仓", "备战", "库存"),
            ("成本", "降本", "增效", "费用"),
            ("时效", "延误", "速度", "准时"),
            ("安全", "风险", "异常", "事故"),
            ("政策", "法规", "新规", "变动"),
            ("对比", "评测", "复盘", "策略", "趋势"),
        )
        matched = next(
            (group for group in signal_groups if any(term in subject for term in group)),
            None,
        )
        if matched:
            title_groups.append(matched)
        elif len(subject) <= 18:
            title_groups.append((subject,))
        else:
            title_groups.append(("物流", "运输", "仓储", "配送", "履约", "发货", "仓配"))
    narrative_groups: list[tuple[str, ...]] = []
    if "清关" in nodes:
        narrative_groups.extend([
            ("申报", "报关", "清关", "查验", "放行"),
            ("核对", "准备", "合规", "资料"),
        ])
    elif "仓储" in nodes:
        narrative_groups.append(("入库", "仓储", "仓库", "分拣", "库位", "仓配"))
    safe_title = ensure_title_satisfies_contract(subject, {"title_groups": title_groups})
    return {
        "intent": "custom_logistics_topic",
        "label": subject,
        "safe_title": safe_title,
        "nodes": nodes,
        "family_priority": ["staff", "warehouse", "facility", "delivery"],
        "title_groups": title_groups,
        "narrative_groups": narrative_groups,
        "opening_hook": (
            f"{subject[:12]}，先把关键核对点讲清楚。"
            if len(f"{subject}，先把关键核对点讲清楚。") > 32
            else f"{subject}，先把关键核对点讲清楚。"
        ),
        "opening_bridge": "先把主题拆到可见的物流动作，再谈 Buffalo 的承接方式。",
        "event_context_lines": [],
        "image_bridge_lines": ["先核对主题，再安排对应动作。"],
        "safe_angle": "围绕原主题和可见物流动作生成，不使用无证据排名、时效或结果承诺。",
        "requires_policy_fact": policy,
        # Keep fallback planning categories separate from nodes explicitly
        # named by the user. Only the latter can impose a strict Hook-fact
        # compatibility gate.
        "custom_topic_nodes": explicit_nodes,
        "custom_topic_entities": explicit_entities,
    }


def _normalize(value: str) -> str:
    return "".join(str(value or "").casefold().split())


def build_topic_contract(topic: str, *, has_event_anchor: bool = False) -> dict:
    """Return the immutable production contract for ``topic``."""
    original_input = " ".join(str(topic or "").split())[:500]
    raw = normalize_topic_input(original_input)
    folded = _normalize(raw)
    selected = next(
        (item for item in _CONTRACTS if any(_normalize(term) in folded for term in item["match"])),
        None,
    )
    is_named_contract = selected is not None
    if selected is None:
        subject = raw.rstrip("。！？?!") or "南非物流"
        selected = _custom_contract(subject)
    contract = deepcopy(selected)
    contract.pop("match", None)
    contract.update({
        "requested_topic": raw,
        "original_input": original_input,
        "opening_mode": "timely_event" if has_event_anchor else "owned_topic_hook",
        "requires_hotspot_fact": bool(has_event_anchor),
        "contract_version": "batch22-first-principles-v1",
        "is_named_contract": is_named_contract,
    })
    if is_named_contract and contract.get("intent") == "policy_change_verification":
        contract["requires_policy_fact"] = True
    return contract


_TOPIC_HOOK_NODE_TERMS = {
    "清关": ("海关", "清关", "关税", "报关", "申报", "查验", "放行", "customs", "sars", "tariff", "duty"),
    "港口": ("港口", "码头", "口岸", "边境", "集装箱", "堆场", "装卸", "port", "harbour", "harbor"),
    "铁路": ("铁路", "列车", "rail", "railway"),
    "道路": ("道路", "公路", "路况", "干线", "封路", "road", "highway"),
    # Exact warehouse evidence must show a warehouse/intake action.  Border,
    # port and generic freight footage can still be used as an adjacent/context
    # Hook, but no longer masquerades as direct proof of overseas-warehouse
    # operations.
    "仓储": ("仓储", "仓库", "入库", "进仓", "分拣", "库位", "仓配", "warehouse"),
    "末端": ("配送", "快递", "末端", "派送", "签收", "仓配", "delivery", "last mile"),
    "运输": ("运输", "货运", "物流", "跨境", "truck", "freight", "logistics"),
}

_POLICY_FACT_TERMS = (
    "政策", "法规", "新规", "变动", "变化", "更新", "调整", "生效",
    "公告", "通知", "发布", "regulation", "policy", "rule", "change",
    "update", "effective", "notice", "announcement",
)


def topic_hook_compatibility_issues(topic: str, events: list[dict] | None) -> list[str]:
    """Reject a playable Hook whose verified fact is outside the topic node.

    ``logistics_question`` is intentionally excluded: it is an editorial
    bridge, not visual evidence.  This prevents a generic end-mile clip from
    being promoted into a customs-policy opener merely because its generated
    question mentions inventory or delivery.
    """
    contract = build_topic_contract(topic, has_event_anchor=True)
    if not events:
        return []
    # Fallback nodes (运输/仓储/配送) are planning categories, not proof
    # that a Hook is on-topic. Strict evidence matching starts only when a
    # concrete node was explicitly named or a named contract was selected.
    if not contract.get("is_named_contract") and not contract.get("custom_topic_nodes"):
        return [
            "自定义物流主题未提取出可验证物流节点；不能自动绑定泛化 Hook，"
            "请补充港口、道路、清关、仓储、配送、铁路或具体机构/事件"
        ]
    nodes = [str(item) for item in (contract.get("custom_topic_nodes") or contract.get("nodes") or [])]
    explicit_nodes = [
        node for node in nodes
        if node in _TOPIC_HOOK_NODE_TERMS and node != "运输"
    ]
    if not explicit_nodes:
        return [
            "自定义物流主题只有泛化运输描述，不能自动绑定泛化 Hook；"
            "请补充具体物流节点或可核验事件"
        ]
    required_entities = [str(item) for item in (contract.get("custom_topic_entities") or [])]
    # Customs/policy is the primary subject even when the input also says
    # “进仓”. A warehouse image alone cannot prove a customs-policy event.
    required_nodes = ["清关"] if "清关" in explicit_nodes else explicit_nodes
    errors: list[str] = []
    for event in events:
        evidence = event.get("evidence") or {}
        blob = " ".join(
            str(value or "")
            for value in (
                event.get("title_zh"), event.get("title_en"),
                event.get("publisher"), event.get("source_name"), event.get("source_url"),
                evidence.get("what_happened"), evidence.get("event_identity"),
            )
        ).casefold()
        if required_entities and not any(
            term.casefold() in blob
            for entity in required_entities
            for term in (entity, "npa" if entity == "Transnet" else entity)
        ):
            errors.append(
                f"热点 Hook 未出现主题指定机构：主题需要{'/'.join(required_entities)}，"
                "Hook 事实或来源未出现对应机构"
            )
            continue
        matched = [
            node for node in required_nodes
            if any(term.casefold() in blob for term in _TOPIC_HOOK_NODE_TERMS[node])
        ]
        if not matched:
            errors.append(
                f"热点 Hook 与主题物流节点不相关：主题需要{'/'.join(required_nodes)}，"
                "Hook 事实未出现对应场景"
            )
            continue
        if contract.get("requires_policy_fact") and not any(
            term.casefold() in blob for term in _POLICY_FACT_TERMS
        ):
            errors.append(
                "热点 Hook 只有海关/机场画面，未出现政策或法规变化事实；"
                "不能作为政策主题的真实热点开场"
            )
    return errors


def missing_narrative_groups(generated: dict, contract: dict) -> list[tuple[str, ...]]:
    """Return narrative requirement groups absent from the generated copy."""
    if not contract:
        return []
    title = _normalize(generated.get("title") or "")
    scenes = generated.get("scenes") if isinstance(generated.get("scenes"), list) else []
    voiceovers = _normalize(" ".join(str(item.get("voiceover") or "") for item in scenes))
    combined = title + voiceovers
    return [
        tuple(group)
        for group in contract.get("narrative_groups") or []
        if not any(_normalize(term) in combined for term in group)
    ]


def validate_generated_topic_contract(generated: dict, contract: dict) -> list[str]:
    """Return deterministic topic/hook violations without trusting a model score."""
    if not contract:
        return []
    title = _normalize(generated.get("title") or "")
    scenes = generated.get("scenes") if isinstance(generated.get("scenes"), list) else []
    voiceovers = _normalize(" ".join(str(item.get("voiceover") or "") for item in scenes))
    combined = title + voiceovers
    errors: list[str] = []
    errors.extend(title_group_issues(generated.get("title") or "", contract))
    for group in missing_narrative_groups(generated, contract):
        errors.append("叙事缺少主题要素：" + "/".join(group))
    if contract.get("opening_mode") == "owned_topic_hook":
        if not scenes:
            errors.append("缺少主题型开场")
        else:
            first = _normalize(scenes[0].get("voiceover") or "")
            required = _normalize(contract.get("opening_hook") or "")
            if required and required not in first and first not in required:
                errors.append("第一镜没有使用主题型开场")
    return errors


_DANGLING_ENDING = re.compile(
    # “核对。”、“应对。” are complete predicates. Matching a bare final
    # “对” also matched the last character of those words and incorrectly
    # rejected valid MiniMax/fallback narration as a dangling preposition.
    r"(?:以及|包括|通过|从|把|和|或|与|的|为|向|在|才能|需要|可以|例如|比如|分别|并且|而且|如果|因为)[。！？；]?$"
)

# A trailing full stop does not make a malformed model fragment complete.
# These are recurring MiniMax truncation tails observed in production, e.g.
# “提前核对件重和去。” and “逐件核对、可。”.
_BROKEN_MODEL_ENDING = re.compile(
    r"(?:[，、](?:可|去)|(?:和|与|及|并)去|留下记|"
    r"(?:更|随时|过程|仍|也|都|即|便)可|才|更)[。！？；]?$"
)

# A sentence may carry terminal punctuation and still begin with a fragment
# left behind by model-side compaction.  Reject only the recurring abstract
# one-word tails observed in production; complete openings such as
# “运输节奏一旦变化，…” are intentionally unaffected.
_BROKEN_MODEL_OPENING = re.compile(
    r"^(?:节奏|影响|风险|履约|物流|延误|拥堵|时效|交付)[，、]"
)


def incomplete_sentence_issues(generated: dict) -> list[str]:
    issues: list[str] = []
    for index, scene in enumerate(generated.get("scenes") or [], 1):
        text = "".join(str(scene.get("voiceover") or "").split())
        if not text:
            issues.append(f"第{index}镜旁白为空")
            continue
        if text[-1] not in "。！？；":
            issues.append(f"第{index}镜旁白不是完整句")
        if _DANGLING_ENDING.search(text):
            issues.append(f"第{index}镜旁白以悬空连接词结尾")
        if _BROKEN_MODEL_ENDING.search(text):
            issues.append(f"第{index}镜旁白存在残缺词尾")
        if _BROKEN_MODEL_OPENING.search(text):
            issues.append(f"第{index}镜旁白存在残缺开头")
    return issues


def family_rotation(topic: str, family: str, size: int) -> int:
    if size <= 1:
        return 0
    contract = build_topic_contract(topic)
    if contract.get("is_named_contract"):
        return int(contract.get("selection_offset") or 0) % size
    digest = hashlib.sha256(f"{topic}|{family}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % size
