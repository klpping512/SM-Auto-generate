"""Buffalo 品牌结束语语料库与场景匹配。

结束语是品牌收束，不是热点事实，也不能替代素材证据。语料只表达
“可见动作 → 可核对/更稳的物流体验 → Buffalo 选择理由”，避免绝对化
承诺；匹配使用结构化物流节点优先，主题文本只作补充。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# 语料保持短句，适配 3 秒品牌尾卡。keywords 是确定性路由词，不发送给模型。
BRAND_OUTRO_CORPUS: tuple[dict[str, Any], ...] = (
    {
        "id": "general_partner",
        "scene": "general",
        "priority": 0,
        "keywords": ("南非发货", "物流", "跨境", "shipping", "logistics"),
        "voiceover": "南非发货，选择 Buffalo 物流，做您在南非值得信赖的物流拍档。",
        "text_overlay": "Buffalo｜南非物流可靠拍档",
    },
    {
        "id": "port_rail_container",
        "scene": "port_rail_container",
        "priority": 10,
        "keywords": ("港口", "码头", "铁路", "集装箱", "跨境运输", "transnet", "port", "rail", "container"),
        "voiceover": "港口和干线变化要提前看，选择 Buffalo，让节点衔接更有准备。",
        "text_overlay": "Buffalo｜港口到干线，节点更清楚",
    },
    {
        "id": "customs_clearance",
        "scene": "customs_clearance",
        "priority": 20,
        "keywords": ("清关", "海关", "报关", "关税", "口岸", "customs", "clearance", "border"),
        "voiceover": "清关资料先核对、节点再安排，选择 Buffalo，让跨境发货少一点盲区。",
        "text_overlay": "Buffalo｜清关节点，提前核对",
    },
    {
        "id": "road_transport",
        "scene": "road_transport",
        "priority": 12,
        "keywords": ("道路", "公路", "路线", "运输", "卡车", "货车", "road", "route", "truck", "transport"),
        "voiceover": "路况会变，计划不能靠猜；选择 Buffalo，把运输节点和后续安排说清楚。",
        "text_overlay": "Buffalo｜路况变化，运输有安排",
    },
    {
        "id": "warehouse_storage",
        "scene": "warehouse_storage",
        "priority": 8,
        "keywords": ("仓储", "仓库", "入库", "库存", "warehouse", "storage", "stock"),
        "voiceover": "货到仓后，存放、分拣、出库都要有章法；Buffalo，让仓配每一步更清楚。",
        "text_overlay": "Buffalo｜仓配每一步，更清楚",
    },
    {
        "id": "sorting_handoff",
        "scene": "sorting_handoff",
        "priority": 9,
        "keywords": ("分拣", "打包", "扫描", "交接", "订单", "sorting", "packing", "scan", "order"),
        "voiceover": "订单多的时候，分拣和交接更要可核对；选择 Buffalo，让仓内动作接得更稳。",
        "text_overlay": "Buffalo｜分拣交接，可核对更稳",
    },
    {
        "id": "last_mile_delivery",
        "scene": "last_mile_delivery",
        "priority": 15,
        "keywords": ("末端", "配送", "交付", "派送", "最后一公里", "last mile", "delivery", "courier"),
        "voiceover": "从仓库到客户手里，最后一公里同样重要；选择 Buffalo，让交付安排更清晰。",
        "text_overlay": "Buffalo｜从仓到手，交付更清晰",
    },
    {
        "id": "safety_exception",
        "scene": "safety_exception",
        "priority": 30,
        "keywords": ("安全", "风险", "异常", "事故", "火灾", "暴雨", "拥堵", "safety", "risk", "incident"),
        "voiceover": "遇到异常先把风险说清，再安排下一步；Buffalo，让物流动作更可核对、更稳妥。",
        "text_overlay": "Buffalo｜异常先核对，动作更稳妥",
    },
    {
        "id": "cost_efficiency",
        "scene": "cost_efficiency",
        "priority": 18,
        "keywords": ("降本", "成本", "费用", "预算", "cost", "budget"),
        "voiceover": "降本不是少做一步，而是把每个物流节点安排好；选择 Buffalo，让成本和交付更有依据。",
        "text_overlay": "Buffalo｜节点安排好，成本更有依据",
    },
    {
        "id": "peak_season_scale",
        "scene": "peak_season_scale",
        "priority": 16,
        "keywords": ("旺季", "高峰", "增长", "订单增长", "电商", "peak", "season", "growth", "ecommerce"),
        "voiceover": "旺季订单来了，仓配和配送要提前协同；选择 Buffalo，让南非发货更从容。",
        "text_overlay": "Buffalo｜旺季仓配，提前协同",
    },
)

_GENERAL = BRAND_OUTRO_CORPUS[0]


def _flatten(value: Any) -> str:
    """把结构化 brief 安全地压成匹配文本，不把任意对象执行为代码。"""
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return " ".join(_flatten(item) for item in value)
    return str(value).casefold()


def _entry_score(entry: Mapping[str, Any], *, node_text: str, full_text: str) -> tuple[int, int]:
    node_hits = sum(1 for keyword in entry.get("keywords") or () if str(keyword).casefold() in node_text)
    full_hits = sum(1 for keyword in entry.get("keywords") or () if str(keyword).casefold() in full_text)
    # 结构化节点优先于宽泛主题；priority 只用于同分时保持确定性。
    return node_hits * 100 + full_hits * 10, int(entry.get("priority") or 0)


def select_brand_outro(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """按物流场景选一条品牌结束语，找不到明确场景时回退通用语。"""
    context = context or {}
    node_text = _flatten(context.get("logistics_nodes") or context.get("nodes"))
    full_text = _flatten(context)
    scored = [(_entry_score(entry, node_text=node_text, full_text=full_text), index, entry)
              for index, entry in enumerate(BRAND_OUTRO_CORPUS[1:], start=1)]
    best_score, _, best_entry = max(scored, key=lambda item: (item[0][0], item[0][1], -item[1]))
    if best_score[0] <= 0:
        best_entry = _GENERAL
    return {key: value for key, value in best_entry.items() if key not in {"keywords", "priority"}}
