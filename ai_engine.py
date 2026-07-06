"""AI content generation using Xiaomi MiMo V2.5."""
import asyncio
import httpx
import json
import logging
import re
from models import Platform, GeneratedContent
from topic_library import PLATFORM_PROMPTS

logger = logging.getLogger(__name__)

# MiMo API config
MIMO_API_KEY = ""
MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.5"


def set_api_key(key: str):
    global MIMO_API_KEY
    MIMO_API_KEY = key


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
            asset_catalog = [{"id": a["id"], "name": a["name"], "type": a["file_type"], "category": a["category"]} for a in (assets or [])]
            asset_instruction = """
【重要规则】必须生成 4-6 个 scenes，总时长约 30 秒。
每个场景必须包含：scene、duration（整数秒）、visual、voiceover、text_overlay、asset_id。

【强制要求】
- 前 4 个场景的 asset_id 必须从下方素材目录中选择，不能为 null
- 最后 1 个场景可以是品牌信息卡（asset_id=null）
- 每个场景选择最匹配画面描述的素材

可用素材（必须从中选择）：
""" + json.dumps(asset_catalog, ensure_ascii=False)
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
  "body": "正文内容",
  "hashtags": ["标签1", "标签2", "标签3"],
  "image_pages": [{{"type": "cover", "headline": "封面标题", "subheadline": "封面副标题", "points": []}}],
  "scenes": [{{"scene": 1, "duration": 5, "visual": "画面", "voiceover": "口播", "text_overlay": "字幕", "asset_id": 1}}],
  "music_suggestion": "音乐风格"
}}

再次强调：scenes 中的 asset_id 必须是素材目录中的真实 ID，不能为 null（最后一个场景除外）。"""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{MIMO_BASE_URL}/chat/completions",
                    headers={
                        "api-key": MIMO_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": MIMO_MODEL,
                        "messages": [
                            {"role": "system", "content": prompt_config["system"]},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.7,
                        "max_completion_tokens": prompt_config["max_len"],
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
                    duration_target=30 if platform == Platform.DOUYIN else None,
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


def _unsupported_claim_warnings(body: str, source_text: str) -> list[str]:
    warnings = []
    if UNSUPPORTED_METRIC_PATTERN.search(body) and not UNSUPPORTED_METRIC_PATTERN.search(source_text):
        warnings.append("输入中未提供的具体时间或数据")
    if UNSUPPORTED_ATTRIBUTION_PATTERN.search(body) and not UNSUPPORTED_ATTRIBUTION_PATTERN.search(source_text):
        warnings.append("输入中未提供来源的报告或官方数据归因")
    return warnings


def _platform_format_warnings(platform: str, body: str) -> list[str]:
    if platform == "douyin" and not ("【画面】" in body and "【口播】" in body):
        return ["抖音稿缺少【画面】和【口播】脚本格式"]
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
    if 4 <= len(scenes) <= 6: return scenes
    return [
        {"scene": 1, "duration": 4, "visual": "港口或集装箱开场", "voiceover": f"做南非物流的注意了，{topic}值得马上关注。", "text_overlay": "南非物流提醒", "asset_id": None},
        {"scene": 2, "duration": 6, "visual": "仓库作业或货物画面", "voiceover": "第一，及时核对最新船期与节点状态。", "text_overlay": "核对最新动态", "asset_id": None},
        {"scene": 3, "duration": 6, "visual": "员工操作或文件画面", "voiceover": "第二，提前检查资料，避免关键环节临时返工。", "text_overlay": "提前检查资料", "asset_id": None},
        {"scene": 4, "duration": 7, "visual": "卡车配送或路线画面", "voiceover": "第三，为运输节点预留缓冲，并及时同步客户。", "text_overlay": "预留运输缓冲", "asset_id": None},
        {"scene": 5, "duration": 7, "visual": "品牌结尾信息卡", "voiceover": "关注 Buffalo，持续获取真实可执行的南非物流建议。", "text_overlay": "关注南非物流动态", "asset_id": None},
    ]


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
        Platform.DOUYIN: GeneratedContent(
            platform=platform,
            title=f"{topic}｜60秒物流提醒",
            body=f"【画面】港口与货运现场快切\n【口播】做南非物流的注意了！{topic}正在影响时效。"
                 f"建议马上确认船期、预留缓冲时间，并提前同步客户。关注我们，获取最新物流预警。",
            hashtags=["南非物流", "跨境电商", "物流避坑"],
            duration_target=30,
            scenes=[
                {"scene": 1, "duration": 4, "visual": "港口或集装箱开场", "voiceover": f"做南非物流的注意了，{topic}值得马上关注。", "text_overlay": "南非物流提醒", "asset_id": None},
                {"scene": 2, "duration": 6, "visual": "仓库作业或货物画面", "voiceover": "第一，及时核对最新船期与港口状态。", "text_overlay": "核对最新动态", "asset_id": None},
                {"scene": 3, "duration": 6, "visual": "员工操作或文件画面", "voiceover": "第二，提前检查资料，避免关键环节临时返工。", "text_overlay": "提前检查资料", "asset_id": None},
                {"scene": 4, "duration": 7, "visual": "卡车配送或路线画面", "voiceover": "第三，为运输节点预留缓冲，并及时同步客户。", "text_overlay": "预留运输缓冲", "asset_id": None},
                {"scene": 5, "duration": 7, "visual": "品牌结尾信息卡", "voiceover": "关注 Buffalo，持续获取真实可执行的南非物流建议。", "text_overlay": "关注南非物流动态", "asset_id": None},
            ],
            music_suggestion="稳健、有节奏的商务电子乐",
        ),
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
        + "\n事实要求：不得编造实时状态、比例、天数、价格或其他具体数据；用户未提供可靠数据时，用条件式表达并提醒核实最新官方信息。"
        + f"\n平台硬性格式：{config['format']}"
        + ("\n小红书还必须返回 image_pages 数组，共 5-7 页。每项格式为 {\"type\":\"cover或content\",\"headline\":\"不超过18字\",\"subheadline\":\"可选副标题\",\"points\":[\"2-4条具体短句，每条说明一个真实问题或行动\"]}。第一页是有冲击力的封面，内页信息具体，最后一页是建议或互动引导，避免空泛口号。" if platform == "xiaohongshu" else "")
        + ("\n抖音必须返回 4-6 个 scenes，总时长约30秒；每项含 scene、duration整数秒、visual、voiceover、text_overlay、asset_id=null。" if platform == "douyin" else "")
        + (("\n可用素材目录（只能引用这些 id）：" + json.dumps([{"id": a["id"], "name": a["name"], "type": a["file_type"], "category": a["category"]} for a in (assets or [])], ensure_ascii=False)) if platform == "douyin" else "")
        + "\n请返回严格 JSON：{\"title\":\"标题\",\"body\":\"正文\",\"hashtags\":[\"标签\"],\"image_pages\":[],\"scenes\":[],\"music_suggestion\":\"\"}，不要输出 Markdown 代码块或解释。"
    )
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT_CHAT + "\n" + config["system"] + parameter_prompt}] + messages

    if not MIMO_API_KEY:
        seed = topic or (messages[-1]["content"] if messages else "南非跨境物流")
        try:
            fallback = _fallback_content(Platform(platform), seed, "")
        except ValueError:
            fallback = _fallback_content(Platform.FACEBOOK, seed, "")
        return {"platform": platform, "title": fallback.title, "body": fallback.body, "hashtags": fallback.hashtags, "image_pages": fallback.image_pages, "duration_target": fallback.duration_target, "scenes": fallback.scenes, "music_suggestion": fallback.music_suggestion, "content": fallback.body}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{MIMO_BASE_URL}/chat/completions",
                headers={
                    "api-key": MIMO_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "model": MIMO_MODEL,
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_completion_tokens": config["max_len"],
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
            needs_twitter_trim = platform == "twitter" and len(body) > 280
            if unsupported_claims or needs_twitter_trim:
                constraints = ["Remove every unsupported numeric claim, vague time range, percentage, price, statistic, and unsupported attribution such as 'official data shows' or 'industry reports'. Keep numbered action-list labels, but make no claim whose source was not supplied by the user."]
                if platform == "twitter":
                    constraints.append("The body must be a standalone post that includes the warning context and actions; do not rely on the title. The body, including hashtags, MUST be 260 characters or fewer.")
                repair_resp = await client.post(
                    f"{MIMO_BASE_URL}/chat/completions",
                    headers={
                        "api-key": MIMO_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": MIMO_MODEL,
                        "messages": [
                            {"role": "system", "content": f"You edit {platform_names.get(platform, platform)} posts. Return strict JSON with title, body, hashtags. Preserve this platform format exactly: {config['format']} Language: {language_rules.get(platform, '')} " + " ".join(constraints)},
                            {"role": "user", "content": f"Rewrite this draft while preserving its platform-native style and key actions:\n{body}"},
                        ],
                        "temperature": 0.3,
                        "max_completion_tokens": 180 if platform == "twitter" else config["max_len"],
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
                    f"{MIMO_BASE_URL}/chat/completions",
                    headers={
                        "api-key": MIMO_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": MIMO_MODEL,
                        "messages": [
                            {"role": "system", "content": f"Rewrite for {platform_names.get(platform, platform)}. Return strict JSON with title, body, hashtags. Mandatory format: {config['format']} Language: {language_rules.get(platform, '')} Do not add any unsupported real-time data, vague time ranges, statistics, or source attribution."},
                            {"role": "user", "content": body},
                        ],
                        "temperature": 0.3,
                        "max_completion_tokens": config["max_len"],
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
            quality_warnings = [f"仍包含{item}，请人工核实" for item in _unsupported_claim_warnings(body, source_text)]
            quality_warnings.extend(_platform_format_warnings(platform, body))
            return {"platform": platform, "title": title[:100], "body": body, "hashtags": hashtags, "image_pages": parsed.get("image_pages", []) if platform == "xiaohongshu" else [], "duration_target": 30 if platform == "douyin" else None, "scenes": _normalize_douyin_scenes(parsed.get("scenes"), topic or title, {a["id"] for a in (assets or [])}) if platform == "douyin" else [], "music_suggestion": parsed.get("music_suggestion", "") if platform == "douyin" else "", "content": raw, "quality_warnings": quality_warnings}
    except Exception as e:
        logger.error("AI 对话失败: platform=%s, error=%s", platform, e)
        seed = topic or (messages[-1].get("content", "") if messages else "南非物流")
        try:
            fallback = _fallback_content(Platform(platform), seed[:40], "")
        except ValueError:
            fallback = _fallback_content(Platform.FACEBOOK, seed[:40], "")
        return {
            "platform": platform, "title": fallback.title, "body": fallback.body,
            "hashtags": fallback.hashtags, "image_pages": fallback.image_pages,
            "duration_target": fallback.duration_target, "scenes": fallback.scenes,
            "music_suggestion": fallback.music_suggestion, "content": fallback.body,
            "quality_warnings": ["AI 服务暂不可用，当前为可编辑模板内容，请人工确认"],
            "source": "fallback",
        }
