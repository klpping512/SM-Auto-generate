"""动态热点与物流主题规划器。

该模块只负责把热点事实整理成可审查的内容简报，不直接生成成片，也不凭空
承诺时效、覆盖率或安全结果。后续素材编排必须以简报中的证据要求为约束。
"""
from __future__ import annotations

import re

import hotspot_lexicon

# Back-compat alias; canonical terms live in hotspot_lexicon.EVENT_TYPES.
SIGNAL_GROUPS = hotspot_lexicon.EVENT_TYPES

TOPICS = {
    "strike": {"topic": "路线稳定性", "keywords": ("路线", "备用", "停工", "运输"),
               "claim": "用真实路线、仓配和分拣画面说明复杂环境下如何保持履约连续性"},
    "risk": {"topic": "末端配送安全", "keywords": ("安全", "危险", "配送", "追踪", "交付"),
             "claim": "用可核验的运输、仓储和交付画面说明安全流程，而不是夸大结果"},
    "ecommerce_growth": {"topic": "本地快递时效", "keywords": ("订单", "电商", "时效", "末端", "配送"),
                          "claim": "把电商需求变化落到仓配、分拣和最后一公里的真实执行画面"},
    "infrastructure": {"topic": "跨境物流稳定性", "keywords": ("港口", "道路", "仓储", "调度", "清关"),
                         "claim": "把基础设施变化与可见的仓储、运输和调度证据连接起来"},
    "weather": {"topic": "恶劣天气下的履约", "keywords": ("天气", "路线", "安全", "调度", "交付"),
                 "claim": "说明天气风险下的安全判断和履约安排，避免把个案说成普遍保证"},
    "policy": {"topic": "南非电商物流机会", "keywords": ("政策", "电商", "仓储", "本地化", "服务"),
               "claim": "从政策或市场变化切入，落到本地仓配和客户体验的真实证据"},
}

DEFAULT_TOPIC = {"topic": "本地快递时效", "keywords": ("南非", "配送", "时效", "仓储"),
                 "claim": "从真实热点切入，解释客户为什么关心可追踪、可交付的物流体验"}


def _text(event: dict) -> str:
    return " ".join(str(event.get(key) or "") for key in (
        "title", "title_zh", "title_en", "summary", "summary_zh", "description", "location", "publisher"
    )).casefold()


def classify_hotspot(event: dict) -> str:
    package_type = str(event.get("event_type") or "")
    if package_type in TOPICS:
        return package_type
    return hotspot_lexicon.classify_event_type(_text(event))


def _custom_topic(event: dict, topic_brief: dict | None) -> dict:
    """Prefer the user's reviewed brief over the legacy fixed-topic mapping."""
    if not topic_brief:
        return {}
    raw_input = str(topic_brief.get("raw_input") or "").strip()
    subject = str(topic_brief.get("subject") or raw_input).strip()
    angle = str(topic_brief.get("angle") or "").strip()
    audience = str(topic_brief.get("audience") or "").strip()
    nodes = [str(item).strip() for item in topic_brief.get("logistics_nodes") or [] if str(item).strip()]
    if not subject:
        return {}
    focus = "、".join(nodes) if nodes else subject
    if not angle:
        angle = f"围绕{focus}梳理进入南非市场前应核对的实际流程与风险边界。"
    audience_text = f"面向{audience}" if audience else "面向有南非业务需求的客户"
    return {
        "topic": subject,
        "angle": angle,
        "claim": f"{audience_text}，使用 Buffalo 可见的仓配、分拣、运输或交付动作解释{focus}，并把动作转成品牌优势，不作无证据承诺。",
        "nodes": nodes,
    }


def build_brief(event: dict, owned_segments: list[dict], topic_brief: dict | None = None) -> dict:
    hotspot_type = classify_hotspot(event)
    topic_info = TOPICS.get(hotspot_type, DEFAULT_TOPIC)
    custom = _custom_topic(event, topic_brief)
    title = str(event.get("title_zh") or event.get("title_en") or "").strip()
    # 事件切分的占位名不能覆盖信源已经提供的热点事实标题。
    if not title or any(marker.casefold() in title.casefold() for marker in ("待确认事件", "现场动态事件", "scene update")):
        title = str(event.get("title") or "").strip() or title
    if "traffic congestion" in title.casefold() and "musina" in title.casefold() and "screening" in title.casefold():
        title = "Musina 附近因筛查出现交通拥堵，卡车排队"
    title = title or "南非现场热点"
    summary = str(event.get("summary_zh") or event.get("summary") or "").strip()
    topic = custom.get("topic") or topic_info["topic"]
    angle = custom.get("angle") or f"{title}与{topic}有什么关系？从真实现场看 Buffalo 如何把物流体验落到执行。"
    beats = [
        {"name": "hook", "instruction": f"用一段真实热点画面提出问题：{title}正在改变什么？"},
        {"name": "context", "instruction": f"解释它对{topic}的具体影响，不能只复述标题。"},
        {"name": "evidence", "instruction": "使用同源热点片段补充现场变化，不把无关画面当作事实证据。"},
        {"name": "brand_proof", "instruction": f"用 Buffalo 的仓储、分拣、运输或交付画面回应{topic}，并把一个可见动作转成品牌优势：风险前置、动作可核对、异常可留痕或交接更稳。"},
        {"name": "close", "instruction": "把前面的物流问题收束到 Buffalo 一个有证据的执行优势和客户下一步，不使用绝对化承诺。"},
    ]
    categories = sorted({str(item.get("primary_category") or "") for item in owned_segments if item.get("primary_category")})
    return {
        "hotspot_type": hotspot_type,
        "hotspot_title": title,
        "hotspot_summary": summary,
        "hotspot_id": event.get("hotspot_id"),
        "source_asset_id": event.get("asset_id"),
        "angle": angle,
        "logistics_topic": topic,
        "claim": custom.get("claim") or f"{topic_info['claim']}；把可见动作转成 Buffalo 的一个具体品牌优势，不作无证据承诺。",
        "topic_brief_id": (topic_brief or {}).get("id"),
        "audience": (topic_brief or {}).get("audience", ""),
        "goal": (topic_brief or {}).get("goal", ""),
        "freshness_mode": (topic_brief or {}).get("freshness_mode", "recent_or_evergreen"),
        "time_window_days": (topic_brief or {}).get("time_window_days", 7),
        "platforms": (topic_brief or {}).get("platforms", ["douyin"]),
        "logistics_nodes": custom.get("nodes") or (topic_brief or {}).get("logistics_nodes", []),
        "narrative_beats": beats,
        # 双素材库指热点库 + Buffalo 自有库。单个已确认 Hook 足以承担
        # 开场；同事件存在第二个 Hook 时由编排器自动增强，不能把双 Hook
        # 错当成每条视频的准入条件。
        "required_evidence": {"hotspot_video": 1, "owned_video": 4, "image_ratio_max": 0.15},
        "available_owned_categories": categories,
        "brand_claims": ["仓储、分拣、运输和交付动作必须以素材为证", "只描述素材能证明的服务动作", "把可见动作转成风险前置、动作可核对、异常可留痕或交接更稳的品牌优势"],
        "negative_claims": ["不写百分百安全", "不写绝对时效保证", "不把单个热点扩大为整个南非事实"],
        "tone": "事实清楚、克制、有现场感",
        "target_duration_ms": 60_000,
    }
