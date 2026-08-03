"""AI content generation using Alibaba Cloud Model Studio Qwen."""
import asyncio
import httpx
import json
import logging
import re
from models import Platform, GeneratedContent
from topic_library import PLATFORM_PROMPTS
import douyin_copywriting_sop

logger = logging.getLogger(__name__)

DOUYIN_TARGET_SECONDS = 60
DOUYIN_MIN_SCENES = 7
DOUYIN_MAX_SCENES = 10

# 话题关键词 → 推荐素材分类，用于 AI prompt 中的素材优先级提示
TOPIC_TO_CATEGORY_HINTS = [
    (["海外仓", "仓库", "仓储", "warehouse", "入库", "出库", "库存", "货架", "堆场"], "warehouse"),
    (["配送", "快递", "运输", "派送", "delivery", "courier", "物流", "干线", "末端"], "delivery"),
    (["清关", "海关", "报关", "customs", "税务", "退税", "关税"], "customs"),
    (["品牌", "logo", "brand", "商标", "VI", "视觉"], "brand"),
    (["设备", "叉车", "传送带", "流水线", "机器", "facility", "设施"], "facility"),
    (["客户", "案例", "好评", "customer", "见证", "买家", "卖家"], "customer"),
    (["员工", "团队", "培训", "办公", "staff", "人物", "会议"], "staff"),
]


def _get_category_priority_hint(topic: str, category: str, assets: list[dict]) -> str:
    """根据话题关键词，生成素材分类优先级提示，注入到 AI prompt 中。"""
    topic_text = f"{topic} {category}".lower()
    recommended_categories = []
    for keywords, cat in TOPIC_TO_CATEGORY_HINTS:
        if any(kw.lower() in topic_text for kw in keywords):
            if cat not in recommended_categories:
                recommended_categories.append(cat)

    if not recommended_categories:
        return ""

    # 统计各分类素材数量
    cat_counts = {}
    for a in assets:
        c = a.get("category", "other")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    lines = [
        "\n【素材分类优先级指南】",
        f"根据话题「{topic}」，推荐优先使用以下分类的素材：",
    ]
    for i, rc in enumerate(recommended_categories[:3], 1):
        count = cat_counts.get(rc, 0)
        cat_names = {
            "warehouse": "仓库/仓储",
            "delivery": "配送/运输",
            "customs": "清关/税务",
            "brand": "品牌/标识",
            "staff": "员工/团队",
            "facility": "设备/设施",
            "customer": "客户/案例",
        }
        cn = cat_names.get(rc, rc)
        lines.append(f"  {i}. {rc}（{cn}）- {count} 个可用素材")

    lines.append("请优先从推荐分类中选择 asset_id，只有推荐分类素材不足时才考虑其他分类。")
    return "\n".join(lines)


def _format_asset_catalog(assets: list[dict]) -> str:
    """将素材列表按分类分组格式化，方便 AI 快速扫描选择 asset_id。"""
    cat_names = {
        "warehouse": "仓库/仓储", "delivery": "配送/运输", "customs": "清关/税务",
        "brand": "品牌/标识", "staff": "员工/团队", "facility": "设备/设施",
        "customer": "客户/案例", "other": "其他",
    }
    groups: dict[str, list[str]] = {}
    for a in assets:
        cat = a.get("category", "other")
        groups.setdefault(cat, []).append(f"id={a['id']} {a['name']}")
    lines = ["可用素材目录（scene.asset_id 只能填以下 id，每个场景选不同素材）："]
    for cat in ["warehouse", "delivery", "customs", "facility", "brand", "customer", "staff", "other"]:
        if cat in groups:
            lines.append(f"[{cat}/{cat_names.get(cat, cat)}] " + " | ".join(groups[cat]))
    return "\n".join(lines)

# 百炼文本模型（OpenAI 兼容接口）。
DASHSCOPE_API_KEY = ""
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = "qwen-plus"


def set_api_key(key: str):
    global DASHSCOPE_API_KEY
    DASHSCOPE_API_KEY = key


async def generate_content(
    topic: str,
    category: str,
    platforms: list[Platform],
    tone: str = "professional",
    length: str = "medium",
    instruction: str = "",
    kb_context: str = "",
    assets: list[dict] | None = None,
) -> list[GeneratedContent]:
    """Generate platform-specific content for a given topic."""
    results = []
    for platform in platforms:
        prompt_config = PLATFORM_PROMPTS.get(platform.value, PLATFORM_PROMPTS["facebook"])

        tone_map = {
            "professional": "专业严谨、数据驱动",
            "friendly": "亲切友好、口语化",
            "urgent": "紧急通知、强调时效性",
        }
        tone_desc = tone_map.get(tone, tone_map["professional"])

        extra = f"\n额外要求：{instruction}\n" if instruction else ""
        kb_block = (
            f"\n以下是公司内部知识库资料，请优先依据这些真实信息生成内容，"
            f"不要编造与之矛盾的事实：\n----\n{kb_context}\n----\n"
            if kb_context else ""
        )
        asset_instruction = ""
        if platform == Platform.XIAOHONGSHU:
            asset_instruction = """
除文案外，必须生成 5-7 页可直接制作成小红书轮播图的 image_pages。视觉内容应适合 BUFFALO 金棕品牌风格：封面标题有冲击力，内页每条要点应包含具体信息，避免空泛口号。
第 1 页 type=cover，其余 type=content；每页 headline 不超过 18 字，
points 为 1-3 条短句、每条不超过 28 字。最后一页给出实用建议或互动引导。
"""
        elif platform == Platform.DOUYIN:
            category_hint = _get_category_priority_hint(topic, category, assets or [])
            asset_instruction = """
【重要规则】必须生成 7-10 个 scenes，总时长 50-65 秒，目标 60 秒。
每个场景必须包含：scene、duration（整数秒）、visual、voiceover、text_overlay、asset_id。

【强制要求】
- 前 4 个场景的 asset_id 必须从下方素材目录中选择，不能为 null
- 最后 1 个场景可以是品牌信息卡（asset_id=null）
- 每个场景选择最匹配画面描述的素材，同一素材不能重复使用
- visual 字段用简短的素材描述关键词（如：海外仓全景、仓库入库操作），不要写电影镜头语言
""" + "\n" + douyin_copywriting_sop.prompt_for_chat_douyin() + category_hint + "\n" + _format_asset_catalog(assets or [])
        user_prompt = f"""请为以下物流主题生成{platform.value}平台的内容：

主题：{topic}
分类：{category}
语气：{tone_desc}
长度：{length}
{extra}{kb_block}
{prompt_config['format']}

{asset_instruction}

【最终要求】请严格按照以下JSON格式返回，不要有任何其他文字：
{{
  "title": "标题",
  "body": "正文内容（抖音的 body 是面向观众的发布文案，不要写【画面】【口播】脚本标记；分镜写进 scenes）",
  "hashtags": ["标签1", "标签2", "标签3"],
  "image_pages": [{{"type": "cover", "headline": "封面标题", "subheadline": "封面副标题", "points": []}}],
  "scenes": [{{"scene": 1, "duration": 5, "visual": "画面", "voiceover": "口播", "text_overlay": "字幕", "asset_id": 1}}],
  "music_suggestion": "音乐风格"
}}

再次强调：scenes 中的 asset_id 必须是素材目录中的真实 ID，不能为 null（最后一个场景除外）。"""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{QWEN_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": QWEN_MODEL,
                        "messages": [
                            {"role": "system", "content": prompt_config["system"]},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": prompt_config["max_len"],
                        "response_format": {"type": "json_object"},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content_text = data["choices"][0]["message"]["content"]

                parsed = _parse_json_response(content_text)
                results.append(GeneratedContent(
                    platform=platform,
                    title=parsed.get("title", topic),
                    body=parsed.get("body", content_text),
                    hashtags=parsed.get("hashtags", []),
                    image_pages=parsed.get("image_pages", []) if platform == Platform.XIAOHONGSHU else [],
                    duration_target=DOUYIN_TARGET_SECONDS if platform == Platform.DOUYIN else None,
                    scenes=_normalize_douyin_scenes(parsed.get("scenes"), topic, {a["id"] for a in (assets or [])}) if platform == Platform.DOUYIN else [],
                    music_suggestion=parsed.get("music_suggestion", "") if platform == Platform.DOUYIN else "",
                ))
                logger.info("AI 内容生成成功: platform=%s, topic=%s", platform.value, topic)
        except Exception as e:
            logger.error("AI 内容生成失败: platform=%s, topic=%s, error=%s", platform.value, topic, e)
            results.append(_fallback_content(platform, topic, category))

    return results


def _parse_json_response(text: str) -> dict:
    """Try to extract JSON from LLM response."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return {"title": "", "body": text, "hashtags": []}


def _normalize_hashtags(value, body: str = "") -> list[str]:
    """兼容模型把 hashtags 返回为字符串、数组或空值。"""
    if isinstance(value, list):
        tags = [str(item).strip().lstrip("#") for item in value]
    elif isinstance(value, str):
        tags = [item.lstrip("#") for item in re.findall(r"#?[\w\u4e00-\u9fff-]+", value)]
    else:
        tags = []
    tags = [tag for tag in tags if tag]
    if not tags:
        tags = re.findall(r"#([\w\u4e00-\u9fff-]+)", body)
    return list(dict.fromkeys(tags))[:8]


def _truncate_twitter_body(body: str, limit: int = 280) -> str:
    """超限时优先保留最后一个完整句子，避免发布半句话。"""
    if len(body) <= limit:
        return body
    candidate = body[:limit]
    sentence_ends = [match.end() for match in re.finditer(r"[.!?](?=\s|$)", candidate)]
    complete_end = max((end for end in sentence_ends if end >= limit // 2), default=0)
    if complete_end:
        return candidate[:complete_end].rstrip()
    return candidate[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


UNSUPPORTED_METRIC_PATTERN = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*%|\b\d+\s*(?:-|–|—|to)\s*\d+\s*(?:days?|hours?|weeks?|天|小时|周)|"
    r"\b\d+(?:\.\d+)?\s*(?:days?|hours?|weeks?|天|小时|周)|(?:数|几|多)\s*(?:天|周|小时))",
    re.I,
)
UNSUPPORTED_ATTRIBUTION_PATTERN = re.compile(
    r"(?:官方数据(?:显示|表明)|数据显示|行业报告(?:显示|指出)|根据[^。.!?]{0,20}(?:报告|数据)|according to [^.!?]{0,30}(?:report|data))",
    re.I,
)
UNSUPPORTED_OPERATIONAL_CLAIM_PATTERN = re.compile(
    r"(?:"
    r"不影响(?:整体)?(?:交期|时效)(?:承诺)?|交期承诺|时效承诺|"
    r"(?:本地清关|自营海外仓|自营仓|自有仓)(?:团队|服务|双(?:链路|保障))?|"
    r"(?:已|会)?优先(?:安排|调用|发运|处理)|(?:已|会)?实时(?:同步|监控|跟进)|"
    r"(?:我(?:们)?|本地|南非).{0,8}(?:仓|仓库).{0,18}(?:提前)?(?:布局|运营|投入|启用|落地)|"
    r"(?:货物|订单|包裹).{0,16}优先(?:入仓|分拣|备货|处理|出库)|"
    r"(?:最大限度|有效).{0,16}(?:缓冲|降低|避免)(?:通关|延误|波动)|"
    r"(?:点击|欢迎).{0,16}(?:咨询|联系).{0,16}(?:实时)?(?:查|查询)(?:进度|状态)|"
    r"(?:海外仓|本地仓|自有仓|自营仓).{0,24}(?:备案|预审|成熟|协同|响应|合规)|"
    r"(?:清关文件|单证).{0,16}(?:预审|预检|预处理)|(?:本地(?:合作)?报关行|本地化协同)(?:能力|机制|服务)?|"
    r"专人(?:同步|跟进|对接)|(?:实时)(?:合规|进展|状态|提醒)|"
    r"(?:查单|评估时效).{0,20}(?:专人|我们|同步|跟进)|"
    r"(?:调取|查询|获取|核查).{0,16}(?:运单|订单).{0,12}(?:通关)?(?:最新|实时)?(?:状态|进度)|"
    r"(?:联动|同步).{0,16}(?:南非)?(?:本地)?(?:合作方|合作伙伴)|"
    r"(?:马上|立刻|优先).{0,18}(?:为您|帮你|帮您).{0,18}(?:核查|查询|查).{0,12}(?:进度|状态|运单)|"
    r"(?:私信|留言|发送).{0,16}(?:运单号|单号).{0,24}(?:协助|帮(?:您|你)?|为您)?(?:核查|核对|查询|查|盯).{0,12}(?:进度|状态)?|"
    r"(?:点击|进入).{0,16}(?:主页|咨询).{0,24}(?:订单|实时).{0,16}(?:节点|进度|状态|解读)|"
    r"订单.{0,16}(?:实时|动态).{0,16}(?:节点|进度|状态|解读|预判)|"
    r"(?:快速|帮(?:您|你)?|我们).{0,20}(?:定位|查询|核对|同步).{0,12}(?:当前)?(?:订单|运单|货物).{0,16}(?:在途)?(?:节点|进度|状态)|"
    r"(?:评论区|私信|留言).{0,24}(?:查节点|查单|查进度|查状态|定位订单)|"
    r"(?:承运商|物流商).{0,18}(?:系统|平台).{0,18}(?:在途)?(?:节点|进度|状态)|"
    r"(?:官网|平台).{0,12}(?:实时)?(?:路况|交通状态).{0,12}(?:更新|同步|查询)|"
    r"(?:实时|最新).{0,24}(?:路况|交通|封控|道路|节点|状态|数据|公告)|"
    r"(?:交管(?:局)?|道路局|交通部|国家道路局|当地电台|GPS).{0,30}(?:官网|状态页|公告|数据|同步|封控)|"
    r"(?:评论区|私信|留言).{0,30}(?:清单|模板|查询|核查|节点|状态|进度)|"
    r"(?:每隔|出发前).{0,12}(?:\d+|一刻钟|几分钟).{0,12}(?:查看|刷新|核对|查询)|"
    r"全程(?:可追踪|可查|透明|操作可追溯)|不等不拖|绝不让(?:您|你)?(?:被动)?等待|"
    r"保障(?:交付|时效|连续性)|保证(?:到货|交期|时效)|(?:已经|已).{0,20}(?:正式)?(?:启用|落地)|"
    r"全链路支持|库存(?:实时)?可视|"
    r"(?:超)?千平米|(?:48|24)小时(?:内)?|专人对接|"
    r"(?:一件代发|FBA中转|退(?:换)?货(?:处理|回检)|本地收货|全流程?标准化作业|"
    r"(?:货物|订单|包裹).{0,16}(?:完成|进行)(?:清关|质检|贴标|上架|出库)|"
    r"清关[→＞>\-].{0,24}(?:质检|贴标|上架|出库)|(?:稳|保)交付|可(?:控|查|视|追溯))"
    r")",
    re.I,
)


def _brand_evidence_allowlist_text(brand_evidence: list[dict] | None) -> str:
    parts = []
    for item in brand_evidence or []:
        status = str(item.get("status") or "confirmed").strip().casefold()
        if status and status != "confirmed":
            continue
        parts.append(str(item.get("claim") or ""))
        parts.append(str(item.get("evidence_note") or ""))
    return " ".join(parts)


def _unsupported_claim_warnings(body: str, source_text: str) -> list[str]:
    warnings = []
    if UNSUPPORTED_METRIC_PATTERN.search(body) and not UNSUPPORTED_METRIC_PATTERN.search(source_text):
        warnings.append("输入中未提供的具体时间或数据")
    if UNSUPPORTED_ATTRIBUTION_PATTERN.search(body) and not UNSUPPORTED_ATTRIBUTION_PATTERN.search(source_text):
        warnings.append("输入中未提供来源的报告或官方数据归因")
    return warnings


def _unsupported_operational_claim_warnings(
    body: str,
    brand_evidence: list[dict] | None = None,
) -> list[str]:
    """Reject hard service promises; allow confirmed brand-evidence capability text."""
    matches = list(dict.fromkeys(match.group(0) for match in UNSUPPORTED_OPERATIONAL_CLAIM_PATTERN.finditer(body)))
    allowlist = _brand_evidence_allowlist_text(brand_evidence)
    if allowlist:
        matches = [item for item in matches if item not in allowlist]
    return [f"未经证据支持的服务能力或交付承诺：{'、'.join(matches)}"] if matches else []


def _sanitize_operational_copy(text: str, fallback: str = "") -> tuple[str, bool]:
    """Remove only unsupported sentences instead of discarding the whole draft."""
    raw = str(text or "").strip()
    if not raw:
        return fallback, False
    units = re.findall(r"[^。！？!?.\n]+[。！？!?]?|\n+", raw)
    kept: list[str] = []
    removed = False
    for unit in units:
        candidate = unit.strip()
        if not candidate:
            continue
        if _unsupported_operational_claim_warnings(candidate):
            removed = True
            continue
        kept.append(candidate)
    cleaned = "".join(kept).strip()
    if not cleaned:
        cleaned = fallback.strip()
    return cleaned, removed


def _conservative_chat_body(topic: str) -> str:
    """A user-facing fallback when the model keeps inventing service facts."""
    return (
        f"围绕「{topic or '南非仓配'}」，先把现场能确认的情况和订单当前节点分开说清楚。"
        "承运方尚未确认的信息，不把它写成路线调整或交期结论。"
        "系统会在两段相关热点 Hook 完成下载、分析与事实核验后，再生成正式视频。"
    )


def _conservative_chat_subject(topic: str, messages: list[dict] | None = None) -> str:
    """Return a neutral user-intent label, never a model-invented headline."""
    raw = " ".join(str(topic or "").split())
    if not raw:
        for message in reversed(messages or []):
            if str(message.get("role") or "") == "user":
                raw = " ".join(str(message.get("content") or "").split())
                if raw:
                    break
    lowered = raw.casefold()
    if "swartberg" in lowered or "r328" in lowered:
        return "Swartberg Pass 路况提醒"
    if "beitbridge" in lowered or "贝特布里奇" in raw:
        return "Beitbridge 物流提醒"
    if "德班" in raw and "港" in raw:
        return "德班港物流提醒"
    # A concrete disruption stays the topic even when the user also mentions
    # an overseas warehouse.  The latter is normally the affected node, not
    # the video subject the user asked us to explain.
    if "海外仓" in raw and ("南非" in raw or "south africa" in lowered):
        return "南非海外仓介绍"
    first_clause = re.split(r"[。！？\n]", raw, maxsplit=1)[0].strip()
    return first_clause[:36] or "南非物流信息"


def _platform_format_warnings(platform: str, body: str) -> list[str]:
    if platform == "douyin" and ("【画面】" in body or "【口播】" in body):
        return ["抖音发布文案不应包含【画面】【口播】脚本标记，请改写为面向观众的种草文案（分镜写进 scenes）"]
    if platform == "twitter" and len(body) > 280:
        return ["Twitter/X 正文超过 280 字符"]
    return []


def _normalize_douyin_scenes(value, topic: str, allowed_asset_ids: set[int] | None = None) -> list[dict]:
    scenes = []
    for index, item in enumerate(value if isinstance(value, list) else []):
        if not isinstance(item, dict): continue
        try: duration = max(3, min(8, int(item.get("duration") or 5)))
        except (TypeError, ValueError): duration = 5
        raw_asset = item.get("asset_id")
        # 允许 int 或字符串类型的 asset_id
        try:
            asset_id_int = int(raw_asset) if raw_asset is not None else None
        except (TypeError, ValueError):
            asset_id_int = None
        asset_id = asset_id_int if asset_id_int is not None and asset_id_int in (allowed_asset_ids or set()) else None
        scenes.append({"scene": index + 1, "duration": duration, "visual": str(item.get("visual") or "品牌信息卡")[:80], "voiceover": str(item.get("voiceover") or "")[:180], "text_overlay": str(item.get("text_overlay") or item.get("voiceover") or "")[:48], "asset_id": asset_id})
    if DOUYIN_MIN_SCENES <= len(scenes) <= DOUYIN_MAX_SCENES and sum(
        scene["duration"] for scene in scenes
    ) >= 50:
        return scenes
    return [
        {"scene": 1, "duration": 7, "visual": "与主题直接相关的热点现场", "voiceover": f"{topic}，先从一个能被核实的现场问题说起。", "text_overlay": "先看真实现场", "asset_id": None},
        {"scene": 2, "duration": 8, "visual": "南非市场或运输节点", "voiceover": "进入一个新市场，先别急着谈结果，要先看货物会经过哪些真实节点。", "text_overlay": "先拆真实节点", "asset_id": None},
        {"scene": 3, "duration": 8, "visual": "仓库入库操作", "voiceover": "镜头转到仓内，入库、核对和分拣是否顺畅，会直接影响后面的执行节奏。", "text_overlay": "入库与核对", "asset_id": None},
        {"scene": 4, "duration": 8, "visual": "仓内分拣操作", "voiceover": "再看分拣环节，订单信息、货物状态和操作要求，需要在这里逐项对应。", "text_overlay": "分拣逐项对应", "asset_id": None},
        {"scene": 5, "duration": 8, "visual": "打包与出库准备", "voiceover": "出库之前，把包装、标签和交接信息核对清楚，才能减少后续反复确认。", "text_overlay": "出库前再核对", "asset_id": None},
        {"scene": 6, "duration": 8, "visual": "卡车运输或末端配送", "voiceover": "到了运输和配送环节，路线与时效判断都应以订单节点和承运方确认信息为准。", "text_overlay": "以确认信息为准", "asset_id": None},
        {"scene": 7, "duration": 8, "visual": "客户签收或交付现场", "voiceover": "最后回到交付结果，先把每个节点做实，再讨论这套方案是否适合自己的业务。", "text_overlay": "节点做实再判断", "asset_id": None},
        {"scene": 8, "duration": 5, "visual": "品牌结尾信息卡", "voiceover": "做南非物流，先把信息和执行路径理清楚。", "text_overlay": "先把路径理清楚", "asset_id": None},
    ]


def _conservative_douyin_scenes(
    scenes: list[dict], force_all: bool = False, topic: str = "",
) -> list[dict]:
    """Keep chat-preview scenes from making an unsupported operational promise.

    The formal dual-library planner receives the original user request and
    verified Hook evidence separately.  These chat-preview scenes are not
    allowed to manufacture capabilities while that formal evidence chain is
    still being prepared.
    """
    safe_scenes = []
    safe_topic = _conservative_chat_subject(topic) if topic.strip() else ""
    tailored_lines = (
        ("热点现场", f"{safe_topic}，先看现场能确认的情况。", "先看现场"),
        ("订单信息核对", "先把订单当前节点和已知安排对一遍。", "先核对订单节点"),
        ("运输准备画面", "路线要不要调整，先以承运方确认的信息为准。", "以确认信息为准"),
        ("客户沟通画面", "客户沟通时，把已确认和待确认的部分分开说清楚。", "已确认与待确认分开说"),
        ("仓储作业现场", "信息核对清楚后，再安排下一步。", "核对后再安排"),
        ("出库准备现场", "包装、标签和交接信息，也要在出库前逐项确认。", "出库前逐项确认"),
        ("运输配送现场", "运输与配送安排，以订单节点和承运方确认信息为准。", "以确认信息为准"),
        ("品牌信息卡", "做南非物流，先把信息理清楚。", "先把信息理清楚"),
    )
    for index, scene in enumerate(scenes):
        item = dict(scene)
        text = " ".join(str(item.get(key) or "") for key in ("visual", "voiceover", "text_overlay"))
        if force_all and safe_topic:
            if index == len(scenes) - 1 and len(scenes) < len(tailored_lines):
                item.update({
                    "visual": "品牌信息卡",
                    "voiceover": "做南非物流，先把信息核实清楚。",
                    "text_overlay": "信息以核实为准",
                })
            else:
                visual, voiceover, overlay = tailored_lines[min(index, len(tailored_lines) - 1)]
                item.update({"visual": visual, "voiceover": voiceover, "text_overlay": overlay})
        elif force_all and index == len(scenes) - 1:
            item["visual"] = "品牌信息卡"
            item["voiceover"] = "SA-LogiFlow，持续关注南非物流信息。"
            item["text_overlay"] = "信息以核实为准"
        elif force_all or _unsupported_operational_claim_warnings(text):
            item["visual"] = "仓储作业现场"
            item["voiceover"] = "请以订单节点和已确认的通关信息为准，提前核对入库与派送安排。"
            item["text_overlay"] = "以订单节点为准"
        safe_scenes.append(item)
    return safe_scenes


def _safe_chat_fallback(platform: str, topic: str, messages: list[dict] | None = None) -> dict:
    """Return an editable but non-promissory chat result when the model is unavailable."""
    subject = _conservative_chat_subject(topic, messages)
    lead = {
        "xiaohongshu": "先把信息核实清楚：",
        "douyin": "物流提醒：",
        "twitter": "Update: ",
        "facebook": "提醒：",
        "reddit": "Context first: ",
    }.get(platform, "提醒：")
    body = lead + _conservative_chat_body(subject)
    scenes = []
    if platform == "douyin":
        scenes = _conservative_douyin_scenes(
            _normalize_douyin_scenes([], subject, set()), force_all=True, topic=subject,
        )
    return {
        "platform": platform,
        "title": f"{subject}｜信息待核实",
        "body": body,
        "hashtags": ["南非物流", "信息核实"],
        "image_pages": [],
        "duration_target": DOUYIN_TARGET_SECONDS if platform == "douyin" else None,
        "scenes": scenes,
        "music_suggestion": "",
        "content": body,
        "quality_warnings": ["AI 服务暂不可用，已切换为无服务承诺的可编辑提示"],
        "source": "safe_fallback",
    }


COMPARISON_DIMENSIONS = (
    ("覆盖范围", "哪些城市/乡镇可服务，偏远点是否可达"),
    ("偏远地区附加费", "附加费规则与计费方式（待填，需来源）"),
    ("揽收方式", "上门揽收 / 服务点投寄 / 预约窗口"),
    ("签收证明", "电子签收、拍照签收、纸质回单能力"),
    ("异常件处理", "破损、丢失、拒收的处理时效与责任边界"),
    ("轨迹能力", "节点更新频率、是否可对外查询"),
    ("客服响应", "渠道、工作时间、升级路径"),
)

COMPARISON_CHECKLIST = (
    "候选服务商名单",
    "服务区域说明",
    "价格来源（官网/报价单/截图链接）",
    "时效来源与取样条件",
    "测试日期与路线",
    "异常案例与处理记录",
)


def _comparison_framework_title(topic: str) -> str:
    raw = " ".join(str(topic or "").split())
    if "南非" in raw and ("快递" in raw or "本地" in raw):
        return "南非本地快递怎么选？先比较这几个关键维度"
    subject = raw[:24] or "物流服务"
    return f"{subject}怎么选？先比较这几个关键维度"


def build_comparison_framework(
    topic: str,
    platforms: list[str] | None = None,
    evidence_state: dict | None = None,
) -> list[dict]:
    """Deterministic comparison outline with blank matrix — no rankings or fake tests."""
    evidence_state = evidence_state or {}
    title = _comparison_framework_title(topic)
    dimension_lines = "\n".join(
        f"{index}. {name}：{hint}" for index, (name, hint) in enumerate(COMPARISON_DIMENSIONS, 1)
    )
    checklist = "\n".join(f"- {item}" for item in COMPARISON_CHECKLIST)
    candidate_note = (
        "已记录候选服务商名称，价格/时效/排名仍留空，待带来源的资料补齐后再填。"
        if evidence_state.get("has_candidates")
        else "候选服务商尚未提供，矩阵先留空，请补充名单与来源。"
    )
    body = (
        "当前没有真实评测数据，因此未生成服务商排名和推荐结论。\n\n"
        f"{candidate_note}\n\n"
        "建议先按下列维度收集资料，再做正式对比：\n"
        f"{dimension_lines}\n\n"
        "资料清单：\n"
        f"{checklist}\n\n"
        "在资料确认前，请勿使用「实测、4家、最稳、最好、排名第一」等结论性表述。"
    )
    hashtags = ["对比框架", "待补资料", "南非物流"]
    platform_list = list(dict.fromkeys(platforms or ["xiaohongshu"]))
    outputs = []
    for platform in platform_list:
        scenes = []
        if platform == "douyin":
            scenes = [
                {"scene": 1, "duration": 5, "visual": "对比维度信息卡", "voiceover": "先比关键维度，再谈哪家合适。", "text_overlay": "先比维度", "asset_id": None},
                {"scene": 2, "duration": 6, "visual": "空白对比表", "voiceover": "价格和时效先空着，等有来源再填。", "text_overlay": "待补资料", "asset_id": None},
                {"scene": 3, "duration": 6, "visual": "资料清单卡片", "voiceover": "把服务商、报价来源和测试日期准备齐。", "text_overlay": "准备资料", "asset_id": None},
                {"scene": 4, "duration": 5, "visual": "品牌信息卡", "voiceover": "资料齐了再出正式评测。", "text_overlay": "资料确认后再评", "asset_id": None},
            ]
        outputs.append({
            "platform": platform,
            "title": title,
            "body": body,
            "hashtags": hashtags,
            "image_pages": [
                {"type": "cover", "headline": title[:18], "subheadline": "对比框架 · 待补资料", "points": ["无实测排名", "先列维度", "再补来源"]},
                {"type": "content", "headline": "关键对比维度", "points": [name for name, _ in COMPARISON_DIMENSIONS[:4]]},
                {"type": "content", "headline": "资料清单", "points": list(COMPARISON_CHECKLIST[:4])},
                {"type": "content", "headline": "下一步", "points": ["补充评测资料", "确认来源后再生成正式评测"]},
            ] if platform == "xiaohongshu" else [],
            "duration_target": DOUYIN_TARGET_SECONDS if platform == "douyin" else None,
            "scenes": scenes,
            "music_suggestion": "",
            "content": body,
            "quality_warnings": ["当前为对比框架，证据不足，不可作为正式评测发布"],
            "content_mode": "comparison_research",
            "result_kind": "framework",
            "source": "comparison_framework",
        })
    return outputs


def enforce_comparison_authenticity(outputs: list[dict], evidence: dict) -> tuple[list[dict], bool]:
    """Downgrade fabricated review copy to the comparison framework when evidence is thin."""
    import chat_intent

    blocked = False
    for item in outputs:
        scenes = item.get("scenes") or []
        scene_text = " ".join(
            str(scene.get(key) or "")
            for scene in scenes if isinstance(scene, dict)
            for key in ("visual", "voiceover", "text_overlay")
        )
        tags = " ".join(str(tag) for tag in (item.get("hashtags") or []))
        violations = chat_intent.comparison_authenticity_violations(
            item.get("title") or "",
            item.get("body") or "",
            tags,
            scene_text,
            evidence=evidence,
        )
        if violations:
            blocked = True
            break
    if not blocked:
        return outputs, False
    topic = next((item.get("title") or "" for item in outputs), "对比评测")
    platforms = [item.get("platform") or "xiaohongshu" for item in outputs]
    return build_comparison_framework(topic, platforms, evidence), True


def _safe_douyin_generated_content(topic: str) -> GeneratedContent:
    """Keep the non-chat generation endpoint on the same Douyin SOP fallback."""
    subject = _conservative_chat_subject(topic)
    scenes = _conservative_douyin_scenes(
        _normalize_douyin_scenes([], subject, set()), force_all=True, topic=subject,
    )
    return GeneratedContent(
        platform=Platform.DOUYIN,
        title=f"{subject}｜先核实再安排",
        body="物流提醒：" + _conservative_chat_body(subject),
        hashtags=["南非物流", "信息核实"],
        duration_target=DOUYIN_TARGET_SECONDS,
        scenes=scenes,
        music_suggestion="稳健、克制的商务节奏",
    )


def _fallback_content(platform: Platform, topic: str, category: str) -> GeneratedContent:
    """Generate template content when API fails."""
    templates = {
        Platform.XIAOHONGSHU: GeneratedContent(
            platform=platform,
            title=f"📦 {topic}｜物流人必看！",
            body=f"最近很多做南非跨境的朋友都在问{topic}的问题，今天来给大家详细说说～\n\n"
                 f"🔹 要点一：及时关注最新动态\n"
                 f"🔹 要点二：提前做好应对方案\n"
                 f"🔹 要点三：找靠谱的物流合作伙伴\n\n"
                 f"有什么问题欢迎评论区交流👇",
            hashtags=["南非物流", "跨境货运", "物流干货", topic[:4]],
            image_pages=[
                {"type": "cover", "headline": topic[:18], "subheadline": "南非物流实用指南", "points": []},
                {"type": "content", "headline": "为什么要关注？", "points": ["物流节点变化会影响整体时效", "提前掌握信息，减少临时调整"]},
                {"type": "content", "headline": "第一步：确认动态", "points": ["及时核对船期与港口状态", "向服务商确认最新可执行方案"]},
                {"type": "content", "headline": "第二步：预留缓冲", "points": ["为关键节点留出合理时间", "提前同步客户，管理交付预期"]},
                {"type": "content", "headline": "第三步：准备预案", "points": ["评估替代路线与资源", "关键资料提前检查并留档"]},
                {"type": "content", "headline": "收藏这份提醒", "points": ["持续关注南非物流动态", "有具体问题欢迎留言交流"]},
            ],
        ),
        Platform.DOUYIN: _safe_douyin_generated_content(topic),
        Platform.FACEBOOK: GeneratedContent(
            platform=platform,
            title=f"📢 {topic} - Latest Update",
            body=f"Attention all logistics professionals!\n\n"
                 f"We want to share the latest developments regarding {topic}. "
                 f"As the South African logistics landscape continues to evolve, staying informed is crucial.\n\n"
                 f"Key takeaways:\n"
                 f"✅ Monitor developments closely\n"
                 f"✅ Plan ahead for potential disruptions\n"
                 f"✅ Reach out to us for expert guidance\n\n"
                 f"Contact us for a free consultation!",
            hashtags=["SouthAfrica", "Logistics", "SupplyChain"],
        ),
        Platform.TWITTER: GeneratedContent(
            platform=platform,
            title=f"🚨 {topic}",
            body=f"🚨 Breaking: {topic} update for SA logistics.\n\n"
                 f"Stay ahead of the curve. DM us for details.\n\n"
                 f"#SouthAfrica #Logistics",
            hashtags=["SouthAfrica", "Logistics"],
        ),
        Platform.REDDIT: GeneratedContent(
            platform=platform,
            title=f"[Discussion] {topic} - Impact on SA Supply Chain",
            body=f"I've been tracking {topic} and its implications for South African supply chains.\n\n"
                 f"Here are my observations:\n\n"
                 f"1. The immediate impact is felt most in Durban and Cape Town corridors\n"
                 f"2. Companies with diversified routing are faring better\n"
                 f"3. Early planning and buffer time remain critical\n\n"
                 f"What's your experience? How is your operation handling this?",
            hashtags=["logistics", "southafrica", "supplychain"],
        ),
    }
    # 通用 fallback
    generic = GeneratedContent(
        platform=platform,
        title=f"{topic}",
        body=f"关于「{topic}」的最新资讯。\n\n持续关注南非物流行业动态，为您带来第一手信息。",
        hashtags=["南非物流", "跨境货运"],
    )
    return templates.get(platform, generic)


# ==================== AI 多轮对话 ====================

SYSTEM_PROMPT_CHAT = (
    "你是 SA-LogiFlow AI 内容助手，专注于南非跨境物流行业。"
    "用户可能请你生成、优化、扩写、缩写、翻译社媒内容。"
    "你必须结合当前会话的全部历史消息理解上下文、承接上一版内容，并正确处理‘继续’、‘改短一点’、‘沿用刚才语气’等省略和指代；"
    "如果新要求与旧要求冲突，以用户最新一条消息为准。"
    "回复简洁、专业，直接给出可用文案，不要多余的解释开头。"
    "不得把未提供证据的服务能力、当前处置、实时跟进、优先处理、自营团队、交期或时效保证写成既成事实；"
    "这类信息只能改写为条件式的准备建议，例如‘请以订单节点和最新通关状态为准’。"
    "如果用户使用快捷指令，请按指令要求处理下方提供的编辑器内容。"
)

COMMANDS = {
    '/optimize':  '请优化以下内容，使其更专业、更有说服力，保持原意：\n{}',
    '/shorten':   '请将以下内容精简到50%以内，保留核心信息：\n{}',
    '/expand':    '请将以下内容扩展到200%，增加细节和数据支撑：\n{}',
    '/translate': '请将以下内容翻译成英文，保持专业语气：\n{}',
    '/hashtags':  '请为以下内容生成5个相关的话题标签，用逗号分隔：\n{}',
}


async def chat(
    messages: list[dict], context: str = "", command: str = None,
    tone: str = "professional", length: str = "medium",
    platforms: list[str] | None = None, topic: str = "",
) -> str:
    """兼容旧调用：生成首个平台版本并返回纯文本。"""
    outputs = await chat_platforms(
        messages=messages, context=context, command=command, tone=tone,
        length=length, platforms=platforms, topic=topic,
    )
    return outputs[0]["content"]


async def chat_platforms(
    messages: list[dict], context: str = "", command: str = None,
    tone: str = "professional", length: str = "medium",
    platforms: list[str] | None = None, topic: str = "",
    assets: list[dict] | None = None,
) -> list[dict]:
    """按平台并行生成真正独立的内容版本。"""
    if command and command in COMMANDS:
        if not context.strip():
            return [{"platform": (platforms or ["xiaohongshu"])[0], "title": "需要内容", "body": "请先生成内容，再使用快捷指令。", "hashtags": [], "content": "请先生成内容，再使用快捷指令。"}]
        messages = [{"role": "user", "content": COMMANDS[command].format(context)}]
    elif context.strip() and messages and messages[-1]["role"] == "user":
        # 把编辑器内容作为隐含上下文注入最后一条 user 消息
        messages = messages.copy()
        messages[-1] = {
            "role": "user",
            "content": f"[编辑器当前内容]\n{context}\n\n[用户消息]\n{messages[-1]['content']}",
        }

    platform_list = list(dict.fromkeys(platforms or ["xiaohongshu"]))
    return await asyncio.gather(*[
        _chat_one_platform(messages, platform, tone, length, topic, assets or [])
        for platform in platform_list
    ])


async def _chat_one_platform(
    messages: list[dict], platform: str, tone: str, length: str, topic: str, assets: list[dict] | None = None,
) -> dict:
    tone_map = {"professional": "专业严谨、信息可信", "friendly": "亲切自然、口语化", "urgent": "简洁紧迫、突出时效"}
    length_map = {"short": "短篇", "medium": "中篇", "long": "长篇"}
    platform_names = {"xiaohongshu": "小红书", "douyin": "抖音", "facebook": "Facebook", "twitter": "Twitter/X", "reddit": "Reddit"}
    language_rules = {
        "xiaohongshu": "使用简体中文。",
        "douyin": "使用简体中文口播语言。",
        "facebook": "默认使用自然、专业的英文；只有用户明确要求中文时才使用中文。",
        "twitter": "默认使用简洁有力的英文；只有用户明确要求中文时才使用中文。",
        "reddit": "默认使用自然、专业的英文；只有用户明确要求中文时才使用中文。",
    }
    config = PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS["facebook"])
    parameter_prompt = (
        f"\n你当前只为【{platform_names.get(platform, platform)}】创作，不要混用其他平台的格式。"
        f"\n本轮偏好：语气={tone_map.get(tone, tone)}；长度={length_map.get(length, length)}。"
        + (f"\n主题：{topic}。" if topic else "")
        + f"\n语言要求：{language_rules.get(platform, '根据目标平台选择自然语言。')}"
        + "\n事实要求：不得编造实时状态、比例、天数、价格或其他具体数据；也不得虚构 Buffalo 已启动应急方案、优先安排、实时跟进、自营团队、全程可追踪或不影响交期等服务承诺。用户未提供可靠数据时，用条件式表达并提醒核实最新官方信息。"
        + f"\n平台硬性格式：{config['format']}"
        + ("\n小红书还必须返回 image_pages 数组，共 5-7 页。每项格式为 {\"type\":\"cover或content\",\"headline\":\"不超过18字\",\"subheadline\":\"可选副标题\",\"points\":[\"2-4条具体短句，每条说明一个真实问题或行动\"]}。第一页是有冲击力的封面，内页信息具体，最后一页是建议或互动引导，避免空泛口号。" if platform == "xiaohongshu" else "")
        + ("\n抖音的 body 是面向观众的发布文案（不要写【画面】【口播】标记）；分镜必须返回 7-10 个 scenes，总时长 50-65 秒，目标 60 秒。每项含 scene、duration整数秒、visual、voiceover、text_overlay、asset_id。每段旁白必须提供新的具体信息，不能用空泛口号凑时长。**每个场景的 asset_id 必须从素材目录中选择一个真实 ID**，同一视频不能重复引用同一素材，最后1个场景可以是品牌信息卡（asset_id=null）。visual 字段用简短的素材描述关键词（如：海外仓全景、仓库入库操作），不要写电影镜头语言。" if platform == "douyin" else "")
        + (("\n" + douyin_copywriting_sop.prompt_for_chat_douyin()) if platform == "douyin" else "")
        + (("\n" + _get_category_priority_hint(topic or messages[-1]["content"][:40] if messages else "", "douyin", assets or []) + "\n" + _format_asset_catalog(assets or [])) if platform == "douyin" else "")
        + "\n请返回严格 JSON：{\"title\":\"标题\",\"body\":\"正文\",\"hashtags\":[\"标签\"],\"image_pages\":[],\"scenes\":[],\"music_suggestion\":\"\"}，不要输出 Markdown 代码块或解释。"
    )
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT_CHAT + "\n" + config["system"] + parameter_prompt}] + messages

    if not DASHSCOPE_API_KEY:
        return _safe_chat_fallback(platform, topic, messages)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{QWEN_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": QWEN_MODEL,
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": config["max_len"],
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            parsed = _parse_json_response(raw)
            body = parsed.get("body") or raw
            title = parsed.get("title") or next((line.strip("# *") for line in body.splitlines() if line.strip()), platform_names.get(platform, platform))
            hashtags = _normalize_hashtags(parsed.get("hashtags"), body)
            source_text = " ".join(item.get("content", "") for item in messages) + " " + topic
            unsupported_claims = _unsupported_claim_warnings(body, source_text)
            unsupported_operational_claims = _unsupported_operational_claim_warnings(body)
            needs_twitter_trim = platform == "twitter" and len(body) > 280
            if unsupported_claims or unsupported_operational_claims or needs_twitter_trim:
                constraints = ["Remove every unsupported numeric claim, vague time range, percentage, price, statistic, and unsupported attribution such as 'official data shows' or 'industry reports'. Keep numbered action-list labels, but make no claim whose source was not supplied by the user."]
                if unsupported_operational_claims:
                    constraints.append("Remove every unverified operational or delivery claim: do not state that Buffalo has already acted, provides a self-operated team or warehouse, prioritizes a shipment, tracks in real time, guarantees delivery, or that congestion will not affect lead time. Replace such statements with conditional planning guidance and ‘以订单节点和最新通关状态为准’." )
                if platform == "twitter":
                    constraints.append("The body must be a standalone post that includes the warning context and actions; do not rely on the title. The body, including hashtags, MUST be 260 characters or fewer.")
                repair_resp = await client.post(
                    f"{QWEN_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": QWEN_MODEL,
                        "messages": [
                            {"role": "system", "content": f"You edit {platform_names.get(platform, platform)} posts. Return strict JSON with title, body, hashtags. Preserve this platform format exactly: {config['format']} Language: {language_rules.get(platform, '')} " + " ".join(constraints)},
                            {"role": "user", "content": f"Rewrite this draft while preserving its platform-native style and key actions:\n{body}"},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 180 if platform == "twitter" else config["max_len"],
                        "response_format": {"type": "json_object"},
                    },
                )
                repair_resp.raise_for_status()
                repaired_raw = repair_resp.json()["choices"][0]["message"]["content"]
                repaired = _parse_json_response(repaired_raw)
                body = repaired.get("body") or repaired_raw
                title = repaired.get("title") or title
                hashtags = _normalize_hashtags(repaired.get("hashtags"), body) or hashtags
                raw = repaired_raw
            if _platform_format_warnings(platform, body):
                format_resp = await client.post(
                    f"{QWEN_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": QWEN_MODEL,
                        "messages": [
                            {"role": "system", "content": f"Rewrite for {platform_names.get(platform, platform)}. Return strict JSON with title, body, hashtags. Mandatory format: {config['format']} Language: {language_rules.get(platform, '')} Do not add any unsupported real-time data, vague time ranges, statistics, or source attribution."},
                            {"role": "user", "content": body},
                        ],
                        "temperature": 0.3,
                        "max_tokens": config["max_len"],
                        "response_format": {"type": "json_object"},
                    },
                )
                format_resp.raise_for_status()
                format_raw = format_resp.json()["choices"][0]["message"]["content"]
                formatted = _parse_json_response(format_raw)
                body = formatted.get("body") or format_raw
                title = formatted.get("title") or title
                hashtags = _normalize_hashtags(formatted.get("hashtags"), body) or hashtags
                raw = format_raw
            if platform == "twitter" and len(body) > 280:
                body = _truncate_twitter_body(body)
            # 提示词和一次模型重写仍无法保证模型放弃虚构的“已启用/可保证”
            # 表述时，用可审计的产品事实兜底。不能把未经证据支持的文案继续
            # 展示给用户，尤其不能让其出现在视频生成按钮旁。
            scene_claim_text = " ".join(
                str(scene.get(key) or "")
                for scene in (parsed.get("scenes") or []) if isinstance(scene, dict)
                for key in ("visual", "voiceover", "text_overlay")
            )
            # 标题、发布文案、标签和预览分镜都在同一个用户可见卡片上；任一位置
            # 出现没有证据的服务承诺，就整体降级为中性提示，不能只清洗 body。
            tag_text = " ".join(str(tag) for tag in hashtags)
            safe_subject = _conservative_chat_subject(topic, messages)
            title, title_sanitized = _sanitize_operational_copy(title, safe_subject)
            body, body_sanitized = _sanitize_operational_copy(
                body,
                f"围绕「{safe_subject}」，先说明能被资料和画面支持的事实，再给出可执行的核对步骤。",
            )
            hashtags = [
                tag for tag in hashtags
                if not _unsupported_operational_claim_warnings(str(tag))
            ] or ["南非物流"]
            use_safe_fallback = title_sanitized or body_sanitized or bool(
                _unsupported_operational_claim_warnings(scene_claim_text)
                or _unsupported_operational_claim_warnings(tag_text)
            )
            quality_warnings = [f"仍包含{item}，请人工核实" for item in _unsupported_claim_warnings(body, source_text)]
            quality_warnings.extend(
                f"仍包含{item}，请人工核实" for item in _unsupported_operational_claim_warnings(body)
            )
            quality_warnings.extend(_platform_format_warnings(platform, body))
            normalized_scenes = _normalize_douyin_scenes(
                parsed.get("scenes"), topic or title, {a["id"] for a in (assets or [])},
            ) if platform == "douyin" else []
            if use_safe_fallback:
                quality_warnings.append("已删除草稿中无证据支持的服务承诺，保留其余可用内容")
            return {"platform": platform, "title": title[:100], "body": body, "hashtags": hashtags, "image_pages": parsed.get("image_pages", []) if platform == "xiaohongshu" else [], "duration_target": DOUYIN_TARGET_SECONDS if platform == "douyin" else None, "scenes": _conservative_douyin_scenes(normalized_scenes) if platform == "douyin" else [], "music_suggestion": parsed.get("music_suggestion", "") if platform == "douyin" else "", "content": raw, "quality_warnings": quality_warnings, "source": "model_sanitized" if use_safe_fallback else "model"}
    except Exception as e:
        logger.error("AI 对话失败: platform=%s, error=%s", platform, e)
        return _safe_chat_fallback(platform, topic, messages)
