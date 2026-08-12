"""Versioned post-analysis SOP for the internal hotspot Hook curator.

The pre-download SOP decides whether an authorised news video is worth
materialising.  This contract starts only after ASR/OCR/vision analysis and
answers a different question: which concrete, reusable scene may become a Hook.
"""
from __future__ import annotations


SOP_ID = "buffalo-hotspot-hook-selection"
SOP_VERSION = "v3"

# These are only deterministic reject signals for *obviously* non-event scenes.
# The model and critic still make the semantic decision for all other footage.
_NON_EVENT_SCENE_MARKERS = (
    "主播", "主持人", "演播室", "播报", "标题页", "片头", "片尾", "地图",
    "信息图", "流程图", "纯文字", "logo墙", "新闻台标",
)
_FIELD_ACTIVITY_MARKERS = (
    "道路", "公路", "卡车", "货车", "货运", "港口", "码头", "集装箱",
    "边境", "海关", "仓储", "配送", "积雪", "暴雨", "洪水", "拥堵", "起火",
    "road", "street", "truck", "cargo", "container", "port", "border",
    "customs", "warehouse", "delivery", "snow", "storm", "flood", "congestion",
)


def policy_contract() -> dict:
    """Return the stable rule set embedded in curator and critic prompts."""
    return {
        "sop_id": SOP_ID,
        "sop_version": SOP_VERSION,
        "goal": (
            "保留可核验、可复用的新闻现场短片段；优先物流向画面，"
            "社会/体育/政务等现场只要画面事实清楚也可入库，物流切入用软桥接。"
        ),
        "selection_rules": [
            "必须选择连续的真实画面，时长 4–14 秒；每段都要有可见事件、动作或明确现场状态。",
            "what_happened 只能复述选中镜头、母片标题和本轮获准事件范围共同支持的事实；不确定就拒绝。",
            "不得把未知袋子、车辆、路人或道路臆断成包裹、货物、订单、Buffalo 资产或服务结果。",
            "不得选择主播/演播室、标题页、地图、信息图、纯文字、Logo 墙、泛空镜，或只靠旁白才成立的画面。",
            "优先选择道路、港口、仓储、清关、配送等物流向现场；若母片是体育、综合日更或政务现场，"
            "只要画面可核验也可保留，并用谨慎的 logistics_question 作弱关联（条件式提问或解释可能影响），"
            "不得声称 Buffalo 已介入或已解决热点。",
            "logistics_question 只能提出条件式核对问题或解释可能影响；不得承诺时效、清关、查单、优先处理。",
            "同一母片最多返回 3 条不重叠 Hook；新闻合集允许最多 3 条不同 event_identity，"
            "优先覆盖标题范围内独立且证据充分的现场，禁止凑数。",
            "每个候选镜头都带 edge_risk 标记：渲染会把镜头居中裁切成 9:16，只保留正中约三分之一宽度。"
            "同等事实支撑下优先选 edge_risk=none 的镜头；只有没有更好候选时，才允许选 left/right/both，"
            "不得只因画面冲击力更强就无视 edge_risk 选一段关键信息会被裁掉的镜头。",
        ],
        "required_output": [
            "start_segment_index", "end_segment_index", "title_zh", "what_happened",
            "hook_reason", "logistics_question", "confidence", "event_identity",
        ],
    }


def obvious_rejection_reason(segments: list[dict]) -> str:
    """Reject only cases that are deterministically unusable before audit."""
    if not segments:
        return "没有连续镜头证据"
    segment_texts = [
        (str(segment.get("description") or "") + " "
        + " ".join(
            str(tag.get("value") or "")
            for tag in (segment.get("tags") or [])
            if isinstance(tag, dict)
        )).casefold()
        for segment in segments
    ]
    visual_text = " ".join(segment_texts)
    if not visual_text.strip():
        return "镜头缺少可见画面描述"
    only_non_event = all(
        any(marker.casefold() in segment_text for marker in _NON_EVENT_SCENE_MARKERS)
        for segment_text in segment_texts
    )
    has_field_activity = any(
        marker.casefold() in visual_text for marker in _FIELD_ACTIVITY_MARKERS
    )
    # 电视分屏/新闻包装可能同时出现主播、字幕和真实道路/港口画面。不能
    # 因为一个非事件词就把整段送进确定性黑名单；三帧视觉审核仍会拒绝
    # 实际上只有主播或标题卡的候选。
    if only_non_event and not has_field_activity:
        return "仅非事件画面不能作为 Hook"
    return ""
