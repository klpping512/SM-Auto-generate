"""按任务角色切换兼容模型，并在远程调用前执行缓存与预算门禁。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

import httpx

import database as db


ROLES = {"planner_text", "vision_tagger", "video_evaluator", "critic", "tts"}

DEFAULT_ROUTES = {
    "planner_text": {
        "role": "planner_text", "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY", "model": "qwen3.7-plus",
        "capabilities": ["text"], "timeout": 60, "max_tokens": 1800,
        "cost_profile": "medium", "request_options": {"enable_thinking": True, "thinking_budget": 3000}, "enabled": True,
    },
    "vision_tagger": {
        "role": "vision_tagger", "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY", "model": "qwen-vl-plus",
        "capabilities": ["text", "vision"], "timeout": 45, "max_tokens": 800,
        # Qwen-VL 的兼容接口以提示词约束 JSON；避免依赖不同版本对
        # response_format 的支持差异。
        "json_mode": False, "cost_profile": "medium", "enabled": True,
    },
    "video_evaluator": {
        "role": "video_evaluator", "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY", "model": "qwen-vl-plus",
        # 百炼 OpenAI 兼容接口采用 max_tokens。这里预留 1800 token，足以
        # 返回紧凑的结构化质检结论，也不会让失败时的重做建议淹没 JSON 尾部。
        "capabilities": ["text", "vision"], "timeout": 120, "max_tokens": 1800,
        "cost_profile": "medium", "enabled": True,
    },
    "critic": {
        "role": "critic", "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY", "model": "qwen3.7-plus",
        "capabilities": ["text"], "timeout": 60, "max_tokens": 1400,
        "cost_profile": "medium", "request_options": {"enable_thinking": True, "thinking_budget": 2400}, "enabled": True,
    },
    "tts": {
        "role": "tts", "provider": "dashscope_tts",
        "base_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        "api_key_env": "DASHSCOPE_API_KEY", "model": "qwen3-tts-flash",
        "capabilities": ["audio"], "timeout": 60, "max_tokens": 0,
        "cost_profile": "low", "enabled": True,
    },
}

COST_PER_MILLION = {
    "low": (0.15, 0.60),
    "medium": (0.60, 2.40),
    "high": (2.00, 8.00),
}
BUDGET_POLICY_VERSION = "thinking-output-v1"


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
) -> dict:
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


def make_cache_key(role: str, payload: dict, prompt_version: str) -> str:
    route = get_route(role)
    raw = json.dumps(
        {"role": role, "model": route["model"], "prompt_version": prompt_version, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def key_is_available(role: str) -> bool:
    route = get_route(role)
    return bool(route["enabled"] and os.environ.get(route["api_key_env"], ""))


def _visible_text_content(payload: dict) -> str:
    """Return provider-visible answer, never provider reasoning traces.

    MiniMax's compatible endpoint can return a legacy `<think>…</think>` block
    in `content` if a gateway ignores `reasoning_split`.  The workflow stores
    strict JSON in several downstream steps, so only remove a *leading*
    reasoning block rather than altering arbitrary customer-facing text.
    """
    content = str(
        (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    ).strip()
    if content.startswith("<think>"):
        closing = content.find("</think>")
        if closing >= 0:
            content = content[closing + len("</think>"):].strip()
    return content


async def call_text(
    job_id: str,
    role: str,
    messages: list[dict],
    *,
    prompt_version: str,
    max_output_tokens: int | None = None,
    json_mode: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict:
    route = get_route(role)
    if "text" not in route["capabilities"] or not route["enabled"]:
        raise RuntimeError(f"模型角色 {role} 未启用文本能力")
    api_key = os.environ.get(route["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"缺少模型密钥环境变量：{route['api_key_env']}")
    cache_key = make_cache_key(role, {"messages": messages, "json_mode": bool(json_mode)}, prompt_version)
    create_budget(job_id)
    cached = db.get_model_cache(cache_key)
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
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=float(route["timeout"]))
    try:
        request_body = {
            "model": route["model"],
            "messages": messages,
            "max_tokens": visible_output,
            **_safe_request_options(route),
        }
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}
        response = await client.post(
            route["base_url"].rstrip("/") + "/chat/completions",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await client.aclose()
    content = _visible_text_content(payload)
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or estimated_input)
    output_tokens = int(usage.get("completion_tokens") or max(1, len(content) // 4))
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
    # Qwen-VL 的图片 token 会在响应里返回；请求前按保守上限预留，且不把 Base64
    # 字节当作文本 token 计算。
    # bytes as text tokens.
    return max(1, text_chars // 4 + image_count * 400)


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
    create_budget(
        job_id,
        max_calls=4,
        max_input_tokens=50_000,
        max_output_tokens=12_000,
    )
    cached = db.get_model_cache(cache_key)
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
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=float(route["timeout"]))
    response_payload: dict | None = None
    try:
        for attempt in range(max(1, max_attempts)):
            try:
                payload = {
                    "model": route["model"],
                    "messages": messages,
                    # 百炼 OpenAI 兼容接口只定义 `max_tokens`；发送
                    # `max_completion_tokens` 会被网关忽略并回落到服务端默认值，
                    # 在长 JSON 时留下不完整尾部。
                    "max_tokens": route["max_tokens"],
                }
                payload.update(_safe_request_options(route))
                if route.get("json_mode", True):
                    payload["response_format"] = {"type": "json_object"}
                response = await client.post(
                    route["base_url"].rstrip("/") + "/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(min(2 ** attempt, 4))
                        continue
                response.raise_for_status()
                response_payload = response.json()
                break
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
    usage = response_payload.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or estimated_input)
    output_tokens = int(usage.get("completion_tokens") or max(1, len(content) // 4))
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
