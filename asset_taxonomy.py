"""自有素材画面分类（primary_category）的唯一真源。

本模块收敛散落在 media_assets / video_renderer / hotspot_video_planner /
semantic_matching 中的「画面素材分类」词表与规则。

与 ``hotspot_lexicon`` 的分工：
- ``hotspot_lexicon``：热点话题 / 事件匹配词表（物流话题、Hook、事件类型等）
- ``asset_taxonomy``：自有素材的画面分类（warehouse / delivery / customs / …）

情绪词、口播职责、场景角色别名（MOOD_TERMS / BUSINESS_TERMS /
SCENE_ROLE_ALIASES 等）不属于本模块，仍由 semantic_matching 持有。
"""
from __future__ import annotations

# 8 类官方集合——唯一真源，不得增删类目。
CATEGORIES = {"warehouse", "delivery", "customs", "brand", "staff", "facility", "customer", "other"}

# 分类优先级：平分时按此顺序选择（物流场景仓库优先于人员）
CATEGORY_PRIORITY = ["warehouse", "delivery", "customs", "facility", "brand", "customer", "staff"]

# A topic may only use a Buffalo clip when the clip's reviewed category can
# actually support the node being discussed.  A warehouse aisle is useful for
# "入库/分拣", but it is not proof of customs clearance or final-mile delivery.
NODE_CATEGORY_RULES = {
    "清关": {"customs"},
    "customs": {"customs"},
    "关税": {"customs"},
    # A warehouse or sorting shot may support the *preparation before*
    # delivery.  The narration below must label it as preparation, never as
    # proof that the final-mile hand-off happened.
    "末端": {"delivery", "warehouse", "staff", "facility"},
    "last_mile": {"delivery", "warehouse", "staff", "facility"},
    "配送": {"delivery", "warehouse", "staff", "facility"},
    "交付": {"delivery", "warehouse", "staff", "facility"},
    "运输": {"delivery"},
    "仓储": {"warehouse", "staff", "facility"},
    "入库": {"warehouse", "staff", "facility"},
    "分拣": {"warehouse", "staff", "facility"},
}

# 多维标签 → 干线/配送能力。主场景误标成仓库时，港口/集装箱等标签仍可进入运输节点。
DELIVERY_TAG_VALUES = frozenset({
    "道路运输", "delivery", "road transport", "transport",
    "卡车", "货车", "truck", "trailer", "拖车",
    "港口作业", "港口", "港口码头", "集装箱堆场", "集装箱", "货柜",
    "船舶", "货轮", "吊机", "装卸", "堆放",
    "port", "harbour", "terminal", "container", "crane", "vessel", "ship",
})
WAREHOUSE_TAG_VALUES = frozenset({
    "仓库作业", "仓库", "warehouse", "warehouse operation", "货架", "分拣", "入库", "出库",
    "货物", "纸箱", "托盘", "包裹", "周转箱", "包装盒",
    "货物存储", "货架通道", "货物分拣", "装卸货区",
    "搬运货物", "装卸货物", "处理包裹", "打包",
    "cargo", "parcel", "package", "pallet", "carton",
})
CUSTOMS_TAG_VALUES = frozenset({
    "清关", "海关", "customs", "clearance", "报关", "关税",
})

CAPABILITY_TAG_VALUES = {
    "delivery": DELIVERY_TAG_VALUES,
    "warehouse": WAREHOUSE_TAG_VALUES,
    "customs": CUSTOMS_TAG_VALUES,
}

ROLE_CATEGORY_FIT = {
    # 这是“画面职责→素材分类”的确定性证据，不是把任意素材抬成高分。
    "brand_proof": {"warehouse", "staff", "facility", "delivery", "brand"},
    "owned_proof": {"warehouse", "staff", "facility", "delivery", "brand"},
    "brand_close": {"delivery", "warehouse", "facility", "brand"},
    "impact_explainer": {"warehouse", "facility", "delivery"},
}

# 子目录名 / 文件名 / 分镜 visual 关键词 → 画面分类。
# 合并自 media_assets 与 video_renderer 两份曾漂移的 CATEGORY_KEYWORDS（并集基线）。
#
# 降噪剔除（总指挥定夺，不保留）：
# - 「访谈」「采访」：裸词不含采访对象，靠伴随词（员工/团队 vs 客户/见证）区分；
#   光秃秃的「采访.mp4」应落 other，交给 asset_processing 模型兜底。
# - 「文件」「单据」：过泛，易把办公/品牌画面误判为 customs；强特征词已够用。
CATEGORY_KEYWORDS = {
    "warehouse": (
        "海外仓", "仓库", "仓储", "warehouse", "storage", "stock", "货架", "外景",
        # 仓库作业场景 — 这些是仓库运营的一部分，不是独立的人员素材
        "打包", "分拣", "理货", "拣货", "上架", "作业", "操作员", "搬运",
        "入库", "出库", "库存", "堆场", "月台", "托盘", "扫描",
        "货物",  # 原仅 video_renderer 有，并入
    ),
    "delivery": (
        "配送", "快递", "物流", "运输", "派送", "卡车",
        "货车", "拖车", "厢式车",  # 原仅 media_assets 有，并入
        "车辆", "路线",  # 原仅 video_renderer 有，并入
        "delivery", "courier", "shipping", "logistics",
        "truck", "van", "trailer",  # 原仅 media_assets 有，并入
    ),
    "customs": ("清关", "海关", "报关", "通关", "customs", "clearance"),
    "brand": (
        "品牌", "商标", "brand", "logo", "标识",
        "信息卡", "结尾",  # 原仅 video_renderer 有，并入
    ),
    "staff": (
        # 以人物为主体：肖像、团队照、办公场景（不含裸词「访谈/采访」）
        "工作人员", "员工", "职员", "staff", "团队", "培训", "工人",
        "办公", "开会", "会议", "面试", "合照", "团建",
    ),
    "facility": ("设备", "设施", "facility", "叉车", "传送带", "机器", "流水线"),
    "customer": ("客户", "customer", "案例", "好评", "反馈", "见证"),
}
