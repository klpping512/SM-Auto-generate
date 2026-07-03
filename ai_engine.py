"""AI Content Generation Engine using DeepSeek API."""
import httpx
import json
import logging
import re
from models import Platform, GeneratedContent
from topic_library import PLATFORM_PROMPTS

logger = logging.getLogger(__name__)

# DeepSeek API config
DEEPSEEK_API_KEY = ""
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


def set_api_key(key: str):
    global DEEPSEEK_API_KEY
    DEEPSEEK_API_KEY = key


async def generate_content(
    topic: str,
    category: str,
    platforms: list[Platform],
    tone: str = "professional",
    length: str = "medium",
    instruction: str = "",
    kb_context: str = "",
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
        user_prompt = f"""请为以下物流主题生成{platform.value}平台的内容：

主题：{topic}
分类：{category}
语气：{tone_desc}
长度：{length}
{extra}{kb_block}
{prompt_config['format']}

请严格按照以下JSON格式返回，不要有任何其他文字：
{{
  "title": "标题",
  "body": "正文内容",
  "hashtags": ["标签1", "标签2", "标签3"]
}}"""

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": prompt_config["system"]},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": prompt_config["max_len"],
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
    """多轮对话 / 快捷指令。返回纯文本回复。"""
    if command and command in COMMANDS:
        if not context.strip():
            return "请先在编辑器输入内容，再使用快捷指令。"
        messages = [{"role": "user", "content": COMMANDS[command].format(context)}]
    elif context.strip() and messages and messages[-1]["role"] == "user":
        # 把编辑器内容作为隐含上下文注入最后一条 user 消息
        messages = messages.copy()
        messages[-1] = {
            "role": "user",
            "content": f"[编辑器当前内容]\n{context}\n\n[用户消息]\n{messages[-1]['content']}",
        }

    tone_map = {"professional": "专业严谨", "friendly": "亲切自然", "urgent": "简洁紧迫"}
    length_map = {"short": "短篇", "medium": "中篇", "long": "长篇"}
    platform_text = "、".join(platforms or ["xiaohongshu"])
    parameter_prompt = (
        f"\n本轮偏好：语气={tone_map.get(tone, tone)}；长度={length_map.get(length, length)}；"
        f"目标平台={platform_text}。"
        + (f"主题={topic}。" if topic else "")
    )
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT_CHAT + parameter_prompt}] + messages

    if not DEEPSEEK_API_KEY:
        seed = topic or (messages[-1]["content"] if messages else context) or "南非跨境物流"
        return (
            f"{seed[:36]}｜物流运营建议\n\n"
            f"围绕「{seed}」，建议从时效变化、成本影响和客户应对三个角度组织内容。"
            "先给出清晰结论，再补充可执行建议，并用真实业务场景增强可信度。\n\n"
            "#南非物流 #跨境物流 #供应链"
        )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error("AI 对话失败: %s", e)
        return f"AI 暂时无法响应（{e}）。请检查 API Key 是否已设置。"
