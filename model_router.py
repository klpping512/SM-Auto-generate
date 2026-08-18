"""按任务角色切换兼容模型，并在远程调用前执行缓存与预算门禁。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable

import httpx

import database as db

logger = logging.getLogger(__name__)


ROLES = {
    "planner_text", "chat_text", "vision_tagger", "video_evaluator",
    "hook_visual_critic", "critic", "tts", "asr",
}

MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MINIMAX_OPENAI_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_ANTHROPIC_BASE_URL = "https://api.minimaxi.com/anthropic"

DEFAULT_ROUTES = {
    # Decision / Hook / script planning — smarter Pro model.
    "planner_text": {
        "role": "planner_text", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-pro",
        "capabilities": ["text"], "timeout": 90, "max_tokens": 1800,
        "cost_profile": "high", "enabled": True,
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    },
    # Chat / platform copy — standard MiMo text.
    "chat_text": {
        "role": "chat_text", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
        "capabilities": ["text"], "timeout": 60, "max_tokens": 2200,
        "cost_profile": "medium", "enabled": True,
        # MiMo 默认开 thinking，推理预算会耗尽 max_tokens 导致 content 恒空（批15 同款坑）。
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    },
    # Vision tagging — mimo-v2.5 multimodal (NOT asr).
    "vision_tagger": {
        "role": "vision_tagger", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
        "capabilities": ["text", "vision"], "timeout": 60, "max_tokens": 900,
        "json_mode": False, "cost_profile": "medium", "enabled": True,
        # MiMo V2.5 defaults thinking ON.  Without the native disabled flag,
        # the vision tagger can spend the whole visible-output budget on
        # reasoning and return an empty content field.
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    },
    "video_evaluator": {
        "role": "video_evaluator", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
        "capabilities": ["text", "vision"], "timeout": 120, "max_tokens": 1800,
        "cost_profile": "medium", "enabled": True,
        # MiMo 默认开 thinking，推理预算会耗尽 max_tokens 导致 content 恒空（批15）。
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    },
    # Hotspot Hook multi-frame visual critic — independent budget from planner/text critic.
    "hook_visual_critic": {
        "role": "hook_visual_critic", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
        "capabilities": ["text", "vision"], "timeout": 90, "max_tokens": 900,
        "json_mode": False, "cost_profile": "medium", "enabled": True,
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    },
    "critic": {
        "role": "critic", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-pro",
        "capabilities": ["text"], "timeout": 90, "max_tokens": 1400,
        "cost_profile": "high", "enabled": True,
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    },
    "tts": {
        "role": "tts", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-tts",
        "capabilities": ["audio"], "timeout": 60, "max_tokens": 0,
        "cost_profile": "low", "enabled": True,
    },
    # Speech-to-text only — never wire this to vision tagging.
    "asr": {
        "role": "asr", "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-asr",
        "capabilities": ["audio"], "timeout": 120, "max_tokens": 0,
        "cost_profile": "medium", "enabled": True,
    },
}

COST_PER_MILLION = {
    "low": (0.15, 0.60),
    "medium": (0.60, 2.40),
    "high": (2.00, 8.00),
}
BUDGET_POLICY_VERSION = "thinking-output-v1"

# A video review has two possible multimodal passes: a global review (including
# a validation retry) and a focused review over the risk windows.  The old
# generic 50k input ceiling was enough for a single vision call, but it could
# reject a valid focused pass before the provider was even contacted.  Keep
# the larger allowance scoped to this role; hotspot curation and other visual
# jobs should retain their tighter budgets.
MULTIMODAL_BUDGETS = {
    "video_evaluator": {
        "max_calls": 4,
        "max_input_tokens": 120_000,
        "max_output_tokens": 16_000,
    },
    "default": {
        "max_calls": 4,
        "max_input_tokens": 50_000,
        "max_output_tokens": 12_000,
    },
}


class BudgetExceeded(RuntimeError):
    pass


def _safe_request_options(route: dict) -> dict:
    """Return only API options that are safe, explicit and portable per model role."""
    raw = route.get("request_options") or {}
    if not isinstance(raw, dict):
        return {}
    options: dict[str, bool | int] = {}
    if isinstance(raw.get("enable_thinking"), bool):
        options["enable_thinking"] = raw["enable_thinking"]
    if isinstance(raw.get("thinking_budget"), int) and 0 < raw["thinking_budget"] <= 16_000:
        options["thinking_budget"] = raw["thinking_budget"]
    # MiniMax OpenAI-compatible responses otherwise may put <think> content
    # directly in `message.content`, which invalidates the strict JSON consumed
    # by the Hook curator and critic.  Its documented `reasoning_split` option
    # keeps the visible answer in a separate field.
    if isinstance(raw.get("reasoning_split"), bool):
        options["reasoning_split"] = raw["reasoning_split"]
    return options


def _provider_request_options(route: dict) -> dict:
    """Translate portable request_options into provider-native chat.completions fields."""
    options = _safe_request_options(route)
    provider = str(route.get("provider") or "")
    if provider == "mimo":
        body: dict = {}
        # MiMo V2.5 defaults thinking ON; MiniMax-style keys are ignored and the
        # reasoning budget can exhaust max_tokens, leaving message.content empty.
        if "enable_thinking" in options:
            body["thinking"] = {
                "type": "enabled" if options["enable_thinking"] else "disabled",
            }
        return body
    if provider == "minimax_anthropic":
        body = {}
        if "enable_thinking" in options:
            body["thinking"] = {
                "type": "adaptive" if options["enable_thinking"] else "disabled",
            }
        return body
    if provider == "minimax_openai":
        # MiniMax M3 exposes the native thinking switch on its
        # OpenAI-compatible endpoint.  Keeping thinking disabled for JSON
        # planning/critique prevents a long reasoning trace from consuming the
        # visible-output budget and returning an empty content field.
        body = {}
        if "reasoning_split" in options:
            body["reasoning_split"] = options["reasoning_split"]
        if "enable_thinking" in options:
            body["thinking"] = {
                "type": "enabled" if options["enable_thinking"] else "disabled",
            }
        return body
    return options


def route_scoped_job_id(job_id: str, role: str) -> str:
    """Scope a reusable workflow job to the active model route.

    Prompt caches include model identity, but persisted budgets historically did
    not. A model switch must start with a fresh route-scoped budget rather than
    inherit exhausted calls from the previous model.
    """
    route = get_route(role)
    fingerprint = json.dumps({
        "role": role,
        "provider": route.get("provider"),
        "base_url": route.get("base_url"),
        "model": route.get("model"),
        "request_options": _safe_request_options(route),
        "budget_policy": BUDGET_POLICY_VERSION,
    }, ensure_ascii=False, sort_keys=True)
    suffix = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:10]
    return f"{str(job_id)[:100]}-route-{suffix}"


def required_output_budget(role: str, visible_output_tokens: int | None = None) -> int:
    """Reserve room for both visible output and provider-reported thinking tokens."""
    route = get_route(role)
    route_limit = int(route.get("max_tokens") or visible_output_tokens or 1200)
    visible = int(visible_output_tokens or route_limit)
    visible = max(1, min(route_limit, visible))
    options = _safe_request_options(route)
    thinking = int(options.get("thinking_budget") or 0) if options.get("enable_thinking") else 0
    return visible + thinking


def get_route(role: str) -> dict:
    if role not in ROLES:
        raise ValueError("未知模型角色")
    route = db.get_model_route(role) or DEFAULT_ROUTES[role]
    return {key: value for key, value in route.items() if key != "api_key"}


def save_route(role: str, data: dict) -> dict:
    if role not in ROLES:
        raise ValueError("未知模型角色")
    route = {**data, "role": role}
    return db.upsert_model_route(route)


def create_budget(
    job_id: str,
    *,
    max_calls: int = 4,
    max_input_tokens: int = 20_000,
    max_output_tokens: int = 6_000,
    reset: bool = False,
) -> dict:
    """Create or reuse a sticky per-job budget.

    Default keeps INSERT OR IGNORE sticky semantics (cross-restart guard).
    Pass reset=True for retry-shaped callers (e.g. hotspot re-curation) that
    must start from zero usage on each attempt while still enforcing max_calls.
    """
    if reset:
        db.delete_model_budget(job_id)
    return db.create_model_budget(
        job_id, max_calls, max_input_tokens, max_output_tokens
    )


def reserve_call(
    job_id: str,
    *,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
) -> dict:
    budget = db.get_model_budget(job_id) or create_budget(job_id)
    if budget["calls_used"] + 1 > budget["max_calls"]:
        raise BudgetExceeded("模型调用次数预算已用完")
    if budget["input_tokens_used"] + estimated_input_tokens > budget["max_input_tokens"]:
        raise BudgetExceeded("模型输入 Token 预算已用完")
    if budget["output_tokens_used"] + estimated_output_tokens > budget["max_output_tokens"]:
        raise BudgetExceeded("模型输出 Token 预算已用完")
    return budget


def _estimated_cost(profile: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = COST_PER_MILLION.get(profile, COST_PER_MILLION["medium"])
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 8)


def record_call(
    job_id: str,
    role: str,
    *,
    cache_key: str,
    input_tokens: int,
    output_tokens: int,
    response: dict,
) -> dict:
    cached = db.get_model_cache(cache_key)
    if cached:
        route = get_route(role)
        return db.record_model_call(
            job_id, role, route["model"], cache_key, 0, 0, 0, response
        )
    reserve_call(
        job_id,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
    )
    route = get_route(role)
    return db.record_model_call(
        job_id,
        role,
        route["model"],
        cache_key,
        input_tokens,
        output_tokens,
        _estimated_cost(route["cost_profile"], input_tokens, output_tokens),
        response,
    )


def planner_plan_cache_rejection(content: str) -> str | None:
    """Separate 'visible text exists' from 'this is a usable video plan'.

    Empty title/angle/scenes is valid JSON and non-empty text, but must never
    enter model_call_cache — retries would keep hitting the same poison.
    """
    raw = str(content or "").strip()
    if not raw:
        return "empty_content"
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(raw)
    except Exception:
        return "invalid_json"
    if not isinstance(payload, dict):
        return "invalid_json"
    title = str(payload.get("title") or "").strip()
    angle = str(payload.get("angle") or "").strip()
    scenes = payload.get("scenes")
    if not title or not angle:
        return "empty_plan_fields"
    if not isinstance(scenes, list) or not scenes:
        return "empty_scenes"
    for item in scenes:
        if not isinstance(item, dict) or not str(item.get("voiceover") or "").strip():
            return "invalid_scene_voiceover"
    return None


def planner_plan_is_cacheable(content: str) -> bool:
    return planner_plan_cache_rejection(content) is None


def make_cache_key(role: str, payload: dict, prompt_version: str) -> str:
    route = get_route(role)
    raw = json.dumps(
        {
            "role": role,
            "provider": route.get("provider"),
            "base_url": route.get("base_url"),
            "model": route["model"],
            "prompt_version": prompt_version,
            "request_options": _safe_request_options(route),
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def key_is_available(role: str) -> bool:
    route = get_route(role)
    return bool(route["enabled"] and os.environ.get(route["api_key_env"], ""))


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _visible_text_content(payload: dict) -> str:
    """Return provider-visible answer, never provider reasoning traces.

    MiniMax / MiMo gateways may still embed `<think>…</think>` in `content` when
    thinking controls are ignored. Strip those blocks before downstream JSON
    parsers see the text. Never promote `reasoning_content` into the answer.
    """
    choices = payload.get("choices") or []
    if choices:
        content_value = ((choices[0].get("message") or {}).get("content") or "")
    else:
        # MiniMax's Anthropic-compatible response stores visible text in
        # content blocks and keeps thinking blocks separate.
        content_value = payload.get("content") or ""
    if isinstance(content_value, list):
        content = "\n".join(
            str(block.get("text") or "")
            for block in content_value
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    else:
        content = str(content_value).strip()
    if "<think>" in content.lower() or "</think>" in content.lower():
        content = _THINK_BLOCK_RE.sub("", content).strip()
    return content


def _anthropic_source(url: str, media_type: str) -> dict:
    """Convert an OpenAI data/remote URL into an Anthropic media source."""
    if str(url).startswith("data:"):
        header, _, data = str(url).partition(",")
        detected_type = header[5:].split(";", 1)[0] or media_type
        return {"type": "base64", "media_type": detected_type, "data": data}
    return {"type": "url", "url": str(url)}


def _anthropic_content(content: object) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[dict] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            blocks.append({"type": "text", "text": str(item.get("text") or "")})
        elif item_type == "image_url":
            url = (item.get("image_url") or {}).get("url")
            if url:
                blocks.append({"type": "image", "source": _anthropic_source(url, "image/jpeg")})
        elif item_type in {"video_url", "video"}:
            video = item.get("video_url") or item.get("source") or {}
            url = video.get("url") if isinstance(video, dict) else video
            if url:
                blocks.append({"type": "video", "source": _anthropic_source(url, "video/mp4")})
    return blocks or [{"type": "text", "text": ""}]


def _anthropic_payload(route: dict, messages: list[dict], max_tokens: int) -> dict:
    system_parts: list[str] = []
    converted: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
            continue
        converted.append({
            "role": role if role in {"user", "assistant"} else "user",
            "content": _anthropic_content(message.get("content")),
        })
    body = {"model": route["model"], "messages": converted, "max_tokens": max_tokens}
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    body.update(_provider_request_options(route))
    return body


def _usage_tokens(payload: dict, estimated_input: int, content: str) -> tuple[int, int]:
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or estimated_input)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or max(1, len(content) // 4))
    return input_tokens, output_tokens


async def call_text(
    job_id: str,
    role: str,
    messages: list[dict],
    *,
    prompt_version: str,
    max_output_tokens: int | None = None,
    json_mode: bool = False,
    use_cache: bool = True,
    cacheable: Callable[[str], bool] | None = None,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = 3,
) -> dict:
    route = get_route(role)
    if "text" not in route["capabilities"] or not route["enabled"]:
        raise RuntimeError(f"模型角色 {role} 未启用文本能力")
    api_key = os.environ.get(route["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"缺少模型密钥环境变量：{route['api_key_env']}")
    cache_key = make_cache_key(role, {"messages": messages, "json_mode": bool(json_mode)}, prompt_version)
    create_budget(job_id)
    cached = db.get_model_cache(cache_key) if use_cache else None
    if cached:
        cached_content = str((cached.get("response") or {}).get("content") or "")
        if cacheable is not None and not cacheable(cached_content):
            logger.warning(
                "call_text 忽略无效缓存: role=%s model=%s prompt_version=%s reason=%s",
                role,
                route.get("model"),
                prompt_version,
                planner_plan_cache_rejection(cached_content) if cacheable is planner_plan_is_cacheable else "not_cacheable",
            )
            db.delete_model_cache(cache_key)
            cached = None
        else:
            hit = record_call(
                job_id,
                role,
                cache_key=cache_key,
                input_tokens=0,
                output_tokens=0,
                response=cached["response"],
            )
            return {
                "content": hit["response"]["content"],
                "cache_hit": True,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }

    estimated_input = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
    requested_output = int(max_output_tokens or route["max_tokens"])
    visible_output = max(1, min(int(route["max_tokens"]), requested_output))
    estimated_output = required_output_budget(role, visible_output)
    reserve_call(
        job_id,
        estimated_input_tokens=estimated_input,
        estimated_output_tokens=estimated_output,
    )
    headers = {"Content-Type": "application/json"}
    if route["provider"] == "mimo":
        headers["api-key"] = api_key
    elif route["provider"] == "minimax_anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    anthropic = route["provider"] == "minimax_anthropic"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=float(route["timeout"]))
    response_payload: dict | None = None
    try:
        for attempt in range(max(1, max_attempts)):
            try:
                request_body = (
                    _anthropic_payload(route, messages, visible_output)
                    if anthropic
                    else {
                        "model": route["model"],
                        "messages": messages,
                        "max_tokens": visible_output,
                        **_provider_request_options(route),
                    }
                )
                if json_mode and not anthropic:
                    request_body["response_format"] = {"type": "json_object"}
                response = await client.post(
                    route["base_url"].rstrip("/") + ("/v1/messages" if anthropic else "/chat/completions"),
                    headers=headers,
                    json=request_body,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < max_attempts:
                        logger.warning(
                            "call_text 瞬态失败重试: role=%s attempt=%d/%d status=%d",
                            role, attempt + 1, max_attempts, response.status_code,
                        )
                        await asyncio.sleep(min(2 ** attempt, 4))
                        continue
                response.raise_for_status()
                response_payload = response.json()
                if _visible_text_content(response_payload):
                    break
                # MiMo 偶发 200 但正文为空（chat 降级根因）：当瞬态失败重试，与 429/5xx 同级。
                if attempt + 1 < max_attempts:
                    logger.warning(
                        "call_text 空内容重试: role=%s attempt=%d/%d",
                        role, attempt + 1, max_attempts,
                    )
                    await asyncio.sleep(min(2 ** attempt, 4))
                    continue
                raise RuntimeError("模型未返回可见文本内容")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 >= max_attempts:
                    raise
                logger.warning(
                    "call_text 网络错误重试: role=%s attempt=%d/%d error=%r",
                    role, attempt + 1, max_attempts, exc,
                )
                await asyncio.sleep(min(2 ** attempt, 4))
    finally:
        if owns_client:
            await client.aclose()
    if response_payload is None:
        raise RuntimeError("模型未返回可见文本内容")
    content = _visible_text_content(response_payload)
    if not content:
        # Thinking-on-by-default models can exhaust the output budget and leave
        # an empty answer; never cache that poison for Hook JSON consumers.
        raise RuntimeError("模型未返回可见文本内容")
    input_tokens, output_tokens = _usage_tokens(response_payload, estimated_input, content)
    if cacheable is not None and not cacheable(content):
        logger.warning(
            "call_text 跳过无效缓存写入: role=%s model=%s prompt_version=%s reason=%s",
            role,
            route.get("model"),
            prompt_version,
            planner_plan_cache_rejection(content) if cacheable is planner_plan_is_cacheable else "not_cacheable",
        )
        return {
            "content": content,
            "cache_hit": False,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    stored = record_call(
        job_id,
        role,
        cache_key=cache_key,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        response={"content": content},
    )
    return {
        "content": content,
        "cache_hit": stored["cache_hit"],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _estimate_multimodal_tokens(messages: list[dict]) -> int:
    text_chars = 0
    image_count = 0
    video_count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            text_chars += len(content)
            continue
        for item in content if isinstance(content, list) else []:
            if item.get("type") == "text":
                text_chars += len(str(item.get("text") or ""))
            elif item.get("type") == "image_url":
                image_count += 1
            elif item.get("type") in {"video_url", "video"}:
                video_count += 1
    # MiMo 视觉模型（mimo-v2.5）的图片 token 会在响应里返回；请求前按保守上限预留，且不把 Base64
    # 字节当作文本 token 计算。
    # bytes as text tokens.
    return max(1, text_chars // 4 + image_count * 400 + video_count * 1_200)


async def call_multimodal_json(
    job_id: str,
    role: str,
    messages: list[dict],
    *,
    prompt_version: str,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = 3,
) -> dict:
    route = get_route(role)
    capabilities = set(route["capabilities"])
    if not {"text", "vision"}.issubset(capabilities) or not route["enabled"]:
        raise RuntimeError(f"模型角色 {role} 未启用多模态能力")
    api_key = os.environ.get(route["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"缺少模型密钥环境变量：{route['api_key_env']}")
    cache_key = make_cache_key(role, {"messages": messages}, prompt_version)
    budget_limits = MULTIMODAL_BUDGETS.get(
        role, MULTIMODAL_BUDGETS["default"],
    )
    create_budget(job_id, **budget_limits)
    cached = db.get_model_cache(cache_key)
    # 防御：命中内容为空则当未命中，继续走线上（不把空结果当有效缓存）。
    if cached and not str((cached.get("response") or {}).get("content") or "").strip():
        cached = None
    if cached:
        hit = record_call(
            job_id,
            role,
            cache_key=cache_key,
            input_tokens=0,
            output_tokens=0,
            response=cached["response"],
        )
        return {
            "content": hit["response"]["content"],
            "cache_hit": True,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    estimated_input = _estimate_multimodal_tokens(messages)
    estimated_output = required_output_budget(role, int(route["max_tokens"]))
    reserve_call(
        job_id,
        estimated_input_tokens=estimated_input,
        estimated_output_tokens=estimated_output,
    )
    headers = {"Content-Type": "application/json"}
    if route["provider"] == "mimo":
        headers["api-key"] = api_key
    elif route["provider"] == "minimax_anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    anthropic = route["provider"] == "minimax_anthropic"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=float(route["timeout"]))
    response_payload: dict | None = None
    try:
        for attempt in range(max(1, max_attempts)):
            try:
                payload = (
                    _anthropic_payload(route, messages, int(route["max_tokens"]))
                    if anthropic
                    else {
                        "model": route["model"],
                        "messages": messages,
                        # 百炼 OpenAI 兼容接口只定义 `max_tokens`；发送
                        # `max_completion_tokens` 会被网关忽略并回落到服务端默认值，
                        # 在长 JSON 时留下不完整尾部。
                        "max_tokens": route["max_tokens"],
                        **_provider_request_options(route),
                    }
                )
                if route.get("json_mode", True) and not anthropic:
                    payload["response_format"] = {"type": "json_object"}
                response = await client.post(
                    route["base_url"].rstrip("/") + ("/v1/messages" if anthropic else "/chat/completions"),
                    headers=headers,
                    json=payload,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(min(2 ** attempt, 4))
                        continue
                response.raise_for_status()
                response_payload = response.json()
                if _visible_text_content(response_payload):
                    break
                # MiMo 偶发 200 但正文为空（质检不可用根因）：当瞬态失败重试，与 429/5xx 同级。
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(min(2 ** attempt, 4))
                    continue
                raise RuntimeError("多模态模型返回了空内容")
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 >= max_attempts:
                    raise
                await asyncio.sleep(min(2 ** attempt, 4))
    finally:
        if owns_client:
            await client.aclose()
    if response_payload is None:
        raise RuntimeError("多模态模型没有返回结果")
    content = _visible_text_content(response_payload)
    if not content:
        raise RuntimeError("多模态模型返回了空内容")
    input_tokens, output_tokens = _usage_tokens(response_payload, estimated_input, content)
    stored = record_call(
        job_id,
        role,
        cache_key=cache_key,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        response={"content": content},
    )
    return {
        "content": content,
        "cache_hit": stored["cache_hit"],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
