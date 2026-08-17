import pytest
import httpx
import json


def test_router_reads_key_from_environment_and_never_returns_secret(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "do-not-return-this-secret")
    route = model_router.get_route("vision_tagger")

    assert route["api_key_env"] == "MIMO_API_KEY"
    assert route["model"] == "mimo-v2.5"
    assert "do-not-return-this-secret" not in str(route)
    assert "api_key" not in route


def test_reasoning_routes_default_to_mimo_pro(tmp_db):
    import model_router

    planner = model_router.get_route("planner_text")
    critic = model_router.get_route("critic")
    chat = model_router.get_route("chat_text")

    assert planner["model"] == "mimo-v2.5-pro"
    assert planner["provider"] == "mimo"
    assert critic["model"] == "mimo-v2.5-pro"
    assert chat["model"] == "mimo-v2.5"
    assert model_router.required_output_budget("planner_text", 1000) == 1000


def test_reasoning_split_is_sent_for_compatible_text_route(tmp_db):
    import model_router

    model_router.save_route("planner_text", {
        "provider": "openai_compatible", "base_url": "https://example.com/v1",
        "api_key_env": "MINIMAX_API_KEY", "model": "MiniMax-M2.7-highspeed",
        "capabilities": ["text"], "timeout": 60, "max_tokens": 1200,
        "cost_profile": "medium", "request_options": {"reasoning_split": True}, "enabled": True,
    })

    assert model_router._safe_request_options(model_router.get_route("planner_text")) == {"reasoning_split": True}


@pytest.mark.asyncio
async def test_minimax_openai_text_route_uses_bearer_and_disables_thinking(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MINIMAX_TOKEN_PLAN_KEY", "test-minimax-key")
    model_router.save_route("planner_text", {
        "provider": "minimax_openai", "base_url": model_router.MINIMAX_OPENAI_BASE_URL,
        "api_key_env": "MINIMAX_TOKEN_PLAN_KEY", "model": "MiniMax-M3",
        "capabilities": ["text"], "timeout": 60, "max_tokens": 1200,
        "cost_profile": "high",
        "request_options": {"reasoning_split": True, "enable_thinking": False}, "enabled": True,
    })
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": "{\"ok\":true}"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await model_router.call_text(
            "minimax-openai-text", "planner_text", [{"role": "user", "content": "返回 JSON"}],
            prompt_version="minimax-openai-text-v1", json_mode=True, client=client,
        )
    finally:
        await client.aclose()

    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-minimax-key"
    assert body["model"] == "MiniMax-M3"
    assert body["reasoning_split"] is True
    assert body["thinking"] == {"type": "disabled"}
    assert result["content"] == '{"ok":true}'


@pytest.mark.asyncio
async def test_minimax_anthropic_multimodal_route_converts_image_blocks(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MINIMAX_TOKEN_PLAN_KEY", "test-minimax-key")
    model_router.save_route("video_evaluator", {
        "provider": "minimax_anthropic", "base_url": model_router.MINIMAX_ANTHROPIC_BASE_URL,
        "api_key_env": "MINIMAX_TOKEN_PLAN_KEY", "model": "MiniMax-M3",
        "capabilities": ["text", "vision"], "timeout": 60, "max_tokens": 900,
        "cost_profile": "medium", "json_mode": False,
        "request_options": {"enable_thinking": False}, "enabled": True,
    })
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, request=request, json={
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "{\"relevant\":true}"},
            ],
            "usage": {"input_tokens": 40, "output_tokens": 12},
        })

    messages = [
        {"role": "system", "content": "只返回 JSON"},
        {"role": "user", "content": [
            {"type": "text", "text": "判断画面"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,eA=="}},
        ]},
    ]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await model_router.call_multimodal_json(
            "minimax-anthropic-vision", "video_evaluator", messages,
            prompt_version="minimax-anthropic-vision-v1", client=client,
        )
    finally:
        await client.aclose()

    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/anthropic/v1/messages"
    assert requests[0].headers["x-api-key"] == "test-minimax-key"
    assert body["system"] == "只返回 JSON"
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][0]["content"][1] == {
        "type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "eA=="},
    }
    assert "response_format" not in body
    assert result["content"] == '{"relevant":true}'


def test_visible_text_content_removes_only_leading_legacy_thinking_block():
    import model_router

    payload = {"choices": [{"message": {"content": "<think>reasoning</think>\n{\"approved\":true}"}}]}
    assert model_router._visible_text_content(payload) == '{"approved":true}'


def test_mimo_maps_enable_thinking_false_to_native_thinking_disabled(tmp_db):
    import model_router

    model_router.save_route("planner_text", {
        "provider": "mimo", "base_url": model_router.MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-pro",
        "capabilities": ["text"], "timeout": 90, "max_tokens": 1800,
        "cost_profile": "high",
        "request_options": {"reasoning_split": True, "enable_thinking": False},
        "enabled": True,
    })
    route = model_router.get_route("planner_text")
    assert model_router._safe_request_options(route) == {
        "enable_thinking": False, "reasoning_split": True,
    }
    assert model_router._provider_request_options(route) == {
        "thinking": {"type": "disabled"},
    }


def test_video_evaluator_route_disables_thinking_like_critic(tmp_db):
    """批15：video_evaluator 必须与 planner/critic 对齐关闭 thinking，
    否则 MiMo 推理预算耗尽 max_tokens，质检 content 恒空。"""
    import model_router

    route = model_router.get_route("video_evaluator")
    assert model_router._safe_request_options(route) == {
        "enable_thinking": False, "reasoning_split": True,
    }


def test_hook_visual_critic_route_is_independent_vision_role(tmp_db):
    import model_router

    assert "hook_visual_critic" in model_router.ROLES
    route = model_router.get_route("hook_visual_critic")
    assert route["model"] == "mimo-v2.5"
    assert set(route["capabilities"]) == {"text", "vision"}
    assert route["timeout"] == 90
    assert route["max_tokens"] == 900
    assert route["api_key_env"] == "MIMO_API_KEY"
    assert model_router._safe_request_options(route) == {
        "enable_thinking": False, "reasoning_split": True,
    }
    # Separate cache identity from text critic / planner.
    key_visual = model_router.make_cache_key(
        "hook_visual_critic", {"messages": [{"role": "user", "content": "x"}]}, "hotspot-hook-visual-audit-v1",
    )
    key_critic = model_router.make_cache_key(
        "critic", {"messages": [{"role": "user", "content": "x"}]}, "hotspot-hook-grounding-audit-v6",
    )
    assert key_visual != key_critic
    assert model_router._provider_request_options(route) == {
        "thinking": {"type": "disabled"},
    }
    # 三个 JSON 敏感角色全量对齐，防后续改路由时漏配
    for role in ("planner_text", "critic", "video_evaluator"):
        assert model_router._safe_request_options(model_router.get_route(role)).get("enable_thinking") is False


def test_vision_tagger_route_disables_thinking(tmp_db):
    """视觉标签必须和其他 JSON 敏感角色一样关闭 MiMo thinking。"""
    import model_router

    route = model_router.get_route("vision_tagger")
    assert route["model"] == "mimo-v2.5"
    assert set(route["capabilities"]) == {"text", "vision"}
    assert model_router._safe_request_options(route) == {
        "enable_thinking": False, "reasoning_split": True,
    }
    assert model_router._provider_request_options(route) == {
        "thinking": {"type": "disabled"},
    }



@pytest.mark.asyncio
async def test_call_text_does_not_cache_empty_planner_json(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    calls = {"count": 0}

    def handler(request: httpx.Request):
        calls["count"] += 1
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": '{"title":"","angle":"","scenes":[]}'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await model_router.call_text(
            "empty-plan", "planner_text", [{"role": "user", "content": "规划视频"}],
            prompt_version="topic-brief-video-plan-v11", client=client, max_attempts=1,
            cacheable=model_router.planner_plan_is_cacheable,
        )
        second = await model_router.call_text(
            "empty-plan", "planner_text", [{"role": "user", "content": "规划视频"}],
            prompt_version="topic-brief-video-plan-v11", client=client, max_attempts=1,
            cacheable=model_router.planner_plan_is_cacheable,
        )
    finally:
        await client.aclose()

    assert first["cache_hit"] is False
    assert second["cache_hit"] is False
    assert calls["count"] == 2
    assert model_router.planner_plan_cache_rejection(first["content"]) == "empty_plan_fields"
    assert tmp_db.get_model_cache(
        model_router.make_cache_key(
            "planner_text",
            {"messages": [{"role": "user", "content": "规划视频"}], "json_mode": False},
            "topic-brief-video-plan-v11",
        )
    ) is None


@pytest.mark.asyncio
async def test_call_text_skips_poisoned_planner_cache_and_repair_bypasses_cache(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    messages = [{"role": "user", "content": "规划视频"}]
    cache_key = model_router.make_cache_key(
        "planner_text", {"messages": messages, "json_mode": False}, "topic-brief-video-plan-v11",
    )
    tmp_db.create_model_budget("poison-plan", 10, 1_000, 10_000)
    tmp_db.record_model_call(
        "poison-plan", "planner_text", "mimo-v2.5-pro", cache_key, 1, 1, 0.01,
        {"content": '{"title":"","angle":"","scenes":[]}'},
    )
    calls = {"count": 0}

    def handler(request: httpx.Request):
        calls["count"] += 1
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": '{"title":"港口提醒","angle":"先看现场","scenes":[{"voiceover":"先核对船期再安排入库。","text_overlay":"核对船期"}]}'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 16},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        skipped = await model_router.call_text(
            "poison-plan", "planner_text", messages,
            prompt_version="topic-brief-video-plan-v11", client=client, max_attempts=1,
            cacheable=model_router.planner_plan_is_cacheable,
        )
        repaired = await model_router.call_text(
            "poison-plan", "planner_text", messages,
            prompt_version="topic-brief-video-plan-v11-repair", client=client, max_attempts=1,
            use_cache=False, cacheable=model_router.planner_plan_is_cacheable,
        )
    finally:
        await client.aclose()

    assert skipped["cache_hit"] is False
    assert repaired["cache_hit"] is False
    assert calls["count"] == 2
    assert model_router.planner_plan_is_cacheable(skipped["content"])
    assert model_router.planner_plan_is_cacheable(repaired["content"])


@pytest.mark.asyncio
async def test_call_text_does_not_cache_empty_visible_content(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")

    def handler(request: httpx.Request):
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": "", "reasoning_content": "thinking only"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 40},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="未返回可见文本内容"):
            await model_router.call_text(
                "empty-content", "planner_text", [{"role": "user", "content": "返回 JSON"}],
                prompt_version="empty-content-v1", client=client, max_attempts=1,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_text_json_mode_is_part_of_request_and_cache_identity(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": '{"approved":true}'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await model_router.call_text(
            "text-json-mode", "planner_text", [{"role": "user", "content": "返回 JSON"}],
            prompt_version="text-json-mode-v1", json_mode=True, client=client,
        )
    finally:
        await client.aclose()

    body = json.loads(requests[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert result["content"] == '{"approved":true}'
    assert requests[0].headers.get("api-key") == "test-key"


def test_route_scoped_job_id_changes_when_text_model_route_changes(tmp_db):
    import model_router

    initial = model_router.route_scoped_job_id("hotspot-intake", "planner_text")
    model_router.save_route("planner_text", {
        "provider": "openai_compatible", "base_url": "https://example.com/v1",
        "api_key_env": "DASHSCOPE_API_KEY", "model": "another-model", "capabilities": ["text"],
        "timeout": 30, "max_tokens": 1000, "cost_profile": "medium",
        "request_options": {"enable_thinking": False}, "enabled": True,
    })
    changed = model_router.route_scoped_job_id("hotspot-intake", "planner_text")

    assert initial != changed
    assert changed.startswith("hotspot-intake-route-")


def test_cached_call_does_not_consume_budget_twice(tmp_db):
    import model_router

    job_id = "sample-budget-cache"
    model_router.create_budget(job_id)

    first = model_router.record_call(
        job_id,
        "planner_text",
        cache_key="same-input",
        input_tokens=100,
        output_tokens=20,
        response={"content": "draft"},
    )
    second = model_router.record_call(
        job_id,
        "planner_text",
        cache_key="same-input",
        input_tokens=100,
        output_tokens=20,
        response={"content": "ignored"},
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    budget = tmp_db.get_model_budget(job_id)
    assert budget["calls_used"] == 1
    assert budget["input_tokens_used"] == 100
    assert budget["output_tokens_used"] == 20


def test_budget_limit_stops_remote_call(tmp_db):
    import model_router

    job_id = "sample-budget-stop"
    model_router.create_budget(
        job_id,
        max_calls=1,
        max_input_tokens=100,
        max_output_tokens=50,
    )
    model_router.record_call(
        job_id,
        "planner_text",
        cache_key="first",
        input_tokens=80,
        output_tokens=20,
        response={"content": "first"},
    )

    with pytest.raises(model_router.BudgetExceeded, match="预算"):
        model_router.reserve_call(job_id, estimated_input_tokens=30, estimated_output_tokens=10)


def test_create_budget_reset_allows_retry_after_exhausted(tmp_db):
    """Hotspot re-curation must not inherit exhausted sticky budget rows."""
    import model_router

    job_id = "hotspot-budget-reset"
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=100, max_output_tokens=50,
    )
    model_router.record_call(
        job_id,
        "planner_text",
        cache_key="spent-once",
        input_tokens=80,
        output_tokens=20,
        response={"content": "spent"},
    )
    with pytest.raises(model_router.BudgetExceeded):
        model_router.reserve_call(job_id, estimated_input_tokens=10, estimated_output_tokens=5)

    fresh = model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=100, max_output_tokens=50, reset=True,
    )
    assert fresh["calls_used"] == 0
    assert fresh["input_tokens_used"] == 0
    assert fresh["output_tokens_used"] == 0
    # Retry attempt can reserve again under the same max_calls=1 ceiling.
    model_router.reserve_call(job_id, estimated_input_tokens=10, estimated_output_tokens=5)


def test_model_route_admin_api_never_exposes_environment_secret(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    import app, auth

    monkeypatch.setenv("CUSTOM_PLANNER_KEY", "hidden-secret")
    tmp_db.create_user("route-admin", auth.hash_password("pw12345"), "admin", "Admin")
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": "route-admin", "password": "pw12345"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    updated = client.put("/api/model-routes/planner_text", headers=headers, json={
        "provider": "openai_compatible",
        "base_url": "https://model.example.com/v1",
        "api_key_env": "CUSTOM_PLANNER_KEY",
        "model": "planner-small",
        "capabilities": ["text"],
        "timeout": 20,
        "max_tokens": 1200,
        "cost_profile": "low",
        "request_options": {"enable_thinking": True, "thinking_budget": 1800},
        "enabled": True,
    })
    assert updated.status_code == 200
    route = client.get("/api/model-routes/planner_text", headers=headers).json()
    assert route["model"] == "planner-small"
    assert route["api_key_env"] == "CUSTOM_PLANNER_KEY"
    assert route["request_options"] == {"enable_thinking": True, "thinking_budget": 1800}
    assert "hidden-secret" not in str(route)


@pytest.mark.asyncio
async def test_text_call_uses_compatible_endpoint_and_cache(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": "evidence-bound draft"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await model_router.call_text(
            "sample-call",
            "planner_text",
            [{"role": "user", "content": "Use claim C1 only"}],
            prompt_version="sample-v1",
            client=client,
        )
        second = await model_router.call_text(
            "sample-call",
            "planner_text",
            [{"role": "user", "content": "Use claim C1 only"}],
            prompt_version="sample-v1",
            client=client,
        )
    finally:
        await client.aclose()

    assert first["content"] == "evidence-bound draft"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/chat/completions")
    assert requests[0].headers.get("api-key") == "test-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "mimo-v2.5-pro"
    assert tmp_db.get_model_budget("sample-call")["calls_used"] == 1


def test_video_evaluator_route_is_multimodal_and_swappable(tmp_db):
    import model_router

    route = model_router.get_route("video_evaluator")

    assert route["model"] == "mimo-v2.5"
    assert {"text", "vision"}.issubset(route["capabilities"])
    assert route["enabled"] is True


@pytest.mark.asyncio
async def test_multimodal_json_call_uses_image_content_and_json_mode(tmp_db, monkeypatch):
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": '{"passed":true}'}}],
            "usage": {"prompt_tokens": 220, "completion_tokens": 12},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    messages = [
        {"role": "system", "content": "Return JSON"},
        {"role": "user", "content": [
            {"type": "text", "text": "FRAME_0001@1.50s"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,eA=="}},
        ]},
    ]
    try:
        result = await model_router.call_multimodal_json(
            "video-eval-call",
            "video_evaluator",
            messages,
            prompt_version="video-qa-v1",
            client=client,
        )
    finally:
        await client.aclose()

    body = json.loads(requests[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"][1]["type"] == "image_url"
    assert body["max_tokens"] == model_router.get_route("video_evaluator")["max_tokens"]
    assert "max_completion_tokens" not in body
    assert result["content"] == '{"passed":true}'
    assert tmp_db.get_model_budget("video-eval-call")["calls_used"] == 1


async def test_multimodal_json_retries_on_empty_content_then_succeeds(tmp_db, monkeypatch):
    # 批13 块E1：MiMo 偶发 200 但正文为空 → 当瞬态失败重试；首两次空、第三次正常应成功且共调 3 次。
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(200, request=request, json={"choices": [{"message": {"content": ""}}], "usage": {}})
        return httpx.Response(200, request=request, json={
            "choices": [{"message": {"content": '{"passed":true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await model_router.call_multimodal_json(
            "eval-empty-retry", "video_evaluator",
            [{"role": "user", "content": "Return JSON"}],
            prompt_version="video-qa-empty", client=client,
        )
    finally:
        await client.aclose()

    assert calls["n"] == 3
    assert result["content"] == '{"passed":true}'


async def test_multimodal_json_raises_when_all_attempts_empty(tmp_db, monkeypatch):
    # 批13 块E1：连续全空 → 重试耗尽后抛「多模态模型返回了空内容」。
    import model_router

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": ""}}], "usage": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="空内容"):
            await model_router.call_multimodal_json(
                "eval-all-empty", "video_evaluator",
                [{"role": "user", "content": "Return JSON"}],
                prompt_version="video-qa-all-empty", client=client, max_attempts=3,
            )
    finally:
        await client.aclose()

    assert calls["n"] == 3
