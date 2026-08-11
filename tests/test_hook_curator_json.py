import json

import pytest


def _segments():
    return [
        {
            "id": 11, "segment_index": 0, "start_ms": 0, "end_ms": 5_000,
            "description": "数十辆卡车在港口入口排队", "transcript": "", "ocr_text": "",
            "tags": [{"dimension": "object", "value": "卡车"}],
        },
        {
            "id": 12, "segment_index": 1, "start_ms": 5_000, "end_ms": 10_000,
            "description": "工作人员在入口检查货车", "transcript": "", "ocr_text": "",
            "tags": [{"dimension": "action", "value": "检查"}],
        },
    ]


def _hooks_payload() -> dict:
    return {
        "hooks": [{
            "event_identity": "港口入口货车排队检查",
            "start_segment_index": 0,
            "end_segment_index": 1,
            "title_zh": "港口入口卡车排队",
            "what_happened": "多辆货车在入口排队，工作人员正在检查。",
            "hook_reason": "连续现场动作和排队画面能快速呈现压力。",
            "logistics_question": "入口检查变慢时，卖家应怎样核对到仓与配送计划？",
            "confidence": 0.88,
        }]
    }


def test_parse_strips_think_blocks_before_json():
    import hotspot_hook_curator

    body = json.dumps(_hooks_payload(), ensure_ascii=False)
    dirty = f"<think>推理过程，包含 {{伪 JSON}}</think>\n{body}"
    hooks = hotspot_hook_curator._parse(dirty, _segments())
    assert len(hooks) == 1
    assert hooks[0]["title_zh"] == "港口入口卡车排队"


def test_parse_accepts_markdown_json_fence():
    import hotspot_hook_curator

    body = json.dumps(_hooks_payload(), ensure_ascii=False)
    dirty = f"```json\n{body}\n```"
    hooks = hotspot_hook_curator._parse(dirty, _segments())
    assert len(hooks) == 1
    assert hooks[0]["evidence"]["event_identity"] == "港口入口货车排队检查"


def test_parse_extracts_balanced_json_after_preamble():
    import hotspot_hook_curator

    body = json.dumps(_hooks_payload(), ensure_ascii=False)
    dirty = f"好的,结果如下:{body}"
    hooks = hotspot_hook_curator._parse(dirty, _segments())
    assert len(hooks) == 1
    assert hooks[0]["start_ms"] == 0
    assert hooks[0]["end_ms"] == 10_000


def test_parse_still_rejects_non_json():
    import hotspot_hook_curator

    with pytest.raises(ValueError, match="Hook 策展模型未返回合法 JSON"):
        hotspot_hook_curator._parse("这不是 JSON，也没有大括号对象", _segments())


def test_extract_json_used_by_audit_path():
    import hotspot_hook_curator

    dirty = '<think>核对中</think>\n{"accepted":[{"candidate_index":1,"reason":"画面一致"}]}'
    payload = hotspot_hook_curator._extract_json(dirty)
    assert payload == {"accepted": [{"candidate_index": 1, "reason": "画面一致"}]}


def test_curate_retries_once_on_json_parse_failure_then_succeeds(tmp_db, monkeypatch):
    import hotspot_hook_curator

    calls = []

    async def fake_call(*_args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("prompt_version") == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {
                "content": json.dumps(
                    {"accepted": [{"candidate_index": 1, "reason": "画面与事件事实一致"}]},
                    ensure_ascii=False,
                ),
                "cache_hit": False,
            }
        if len([c for c in calls if c.get("prompt_version") == hotspot_hook_curator.PROMPT_VERSION]) == 1:
            return {"content": "这不是 JSON，也没有大括号对象", "cache_hit": True}
        return {
            "content": json.dumps(_hooks_payload(), ensure_ascii=False),
            "cache_hit": False,
        }

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    hooks, meta = hotspot_hook_curator.curate_hook_clips(91, "港口入口现场", _segments())

    planner_calls = [c for c in calls if c.get("prompt_version") == hotspot_hook_curator.PROMPT_VERSION]
    assert len(planner_calls) == 2
    assert planner_calls[1].get("use_cache") is False
    assert len(hooks) == 1
    assert meta["status"] == "curated"


def test_curate_records_diagnostic_on_both_failures(tmp_db, monkeypatch):
    import hotspot_hook_curator

    async def fake_call(*_args, **kwargs):
        if kwargs.get("use_cache") is False:
            return {"content": "", "cache_hit": False}
        return {"content": "纯错误文本无括号", "cache_hit": True}

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    with pytest.raises(ValueError, match="Hook 策展模型未返回合法 JSON"):
        hotspot_hook_curator.curate_hook_clips(92, "港口入口现场", _segments())

    rows = tmp_db.list_hook_curation_diagnostics(asset_id=92)
    assert len(rows) == 2
    by_attempt = {int(r["attempt_number"]): r for r in rows}
    assert by_attempt[1]["raw_content"] == "纯错误文本无括号"
    assert int(by_attempt[1]["cache_hit"]) == 1
    assert by_attempt[2]["raw_content"] == ""
    assert int(by_attempt[2]["cache_hit"]) == 0


def test_curate_budget_allows_two_calls(tmp_db, monkeypatch):
    import hotspot_hook_curator

    budgets = []

    def fake_budget(job_id, **kwargs):
        budgets.append({"job_id": job_id, **kwargs})

    async def fake_call(*_args, **kwargs):
        if kwargs.get("prompt_version") == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {
                "content": json.dumps(
                    {"accepted": [{"candidate_index": 1, "reason": "画面与事件事实一致"}]},
                    ensure_ascii=False,
                ),
                "cache_hit": False,
            }
        return {
            "content": json.dumps(_hooks_payload(), ensure_ascii=False),
            "cache_hit": False,
        }

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", fake_budget)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    hotspot_hook_curator.curate_hook_clips(93, "港口入口现场", _segments())
    # 第一条是策展 budget（max_calls=2）；后续 critic audit 仍保持 max_calls=1。
    assert budgets[0].get("max_calls") == 2
    assert budgets[0].get("max_input_tokens") == 28_000
    assert budgets[0].get("max_output_tokens") == 2_000
    assert budgets[0].get("reset") is True
