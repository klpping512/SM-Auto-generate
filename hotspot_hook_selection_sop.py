"""Versioned post-analysis SOP for the internal hotspot Hook curator.

The pre-download SOP decides whether an authorised news video is worth
materialising.  This contract starts only after ASR/OCR/vision analysis and
answers a different question: which concrete, reusable scene may become a Hook.
"""
from __future__ import annotations


SOP_ID = "buffalo-hotspot-hook-selection"
SOP_VERSION = "v2"

# These are only deterministic reject signals for *obviously* non-event scenes.
# The model and critic still make the semantic decision for all other footage.
_NON_EVENT_SCENE_MARKERS = (
    "主播", "主持人", "演播室", "播报", "标题页", "片头", "片尾", "地图",
    "信息图", "流程图", "纯文字", "logo墙", "新闻台标",
)


def policy_contract() -> dict:
    """Return the stable rule set embedded in curator and critic prompts."""
    return {
        "sop_id": SOP_ID,
        "sop_version": SOP_VERSION,
        "goal": "只保留能用真实画面说明一个已核验外部事件、并自然引出谨慎物流问题的可复用 Hook。",
        "selection_rules": [
            "必须选择连续的真实画面，时长 4–14 秒；每段都要有可见事件、动作或明确现场状态。",
            "what_happened 只能复述选中镜头、母片标题和本轮获准事件范围共同支持的事实；不确定就拒绝。",
            "不得把未知袋子、车辆、路人或道路臆断成包裹、货物、订单、Buffalo 资产或服务结果。",
            "不得选择主播/演播室、标题页、地图、信息图、纯文字、Logo 墙、泛空镜，或只靠旁白才成立的画面。",
            "logistics_question 只能提出条件式核对问题或解释可能影响；不得承诺时效、清关、查单、优先处理或 Buffalo 已解决热点。",
            "同一母片有两段以上独立且充分证据时，优先返回两段不重叠 Hook 以支持双 Hook 成片；证据不足时允许只保留一段，禁止凑数。",
            "每个候选镜头都带 edge_risk 标记：渲染会把镜头居中裁切成 9:16，只保留正中约三分之一宽度。"
            "同等事实支撑下优先选 edge_risk=none 的镜头；只有没有更好候选时，才允许选 left/right/both，"
            "不得只因画面冲击力更强就无视 edge_risk 选一段关键信息会被裁掉的镜头。",
        ],
        "required_output": [
            "start_segment_index", "end_segment_index", "title_zh", "what_happened",
            "hook_reason", "logistics_question", "confidence",
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
    if all(
        any(marker.casefold() in segment_text for marker in _NON_EVENT_SCENE_MARKERS)
        for segment_text in segment_texts
    ):
        return "仅非事件画面不能作为 Hook"
    return ""
