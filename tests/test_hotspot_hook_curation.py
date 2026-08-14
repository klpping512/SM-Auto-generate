import asyncio
import json

import pytest


def _pass_visual(hooks):
    import hotspot_hook_visual_audit as visual

    for hook in hooks:
        evidence = dict(hook.get("evidence") or {})
        evidence["visual_audit"] = {
            "status": "accepted",
            "prompt_version": visual.VISUAL_AUDIT_PROMPT_VERSION,
            "scene_type": "port",
            "frame_offsets_ms": [400, 5000, 9600],
            "frame_sha256": ["a" * 64, "b" * 64, "c" * 64],
            "visible_objects": ["卡车"],
            "visible_actions": ["排队"],
            "model": "mimo-test",
            "cache_hit": False,
        }
        hook["evidence"] = evidence
    return hooks, {"status": "verified", "accepted_count": len(hooks)}


def _patch_curator_models(monkeypatch, fake_call):
    import hotspot_hook_curator

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})
    monkeypatch.setattr(
        hotspot_hook_curator.hotspot_hook_visual_audit,
        "audit_hooks",
        lambda asset_id, hooks, **kwargs: _pass_visual(hooks),
    )


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
        {
            "id": 13, "segment_index": 2, "start_ms": 10_000, "end_ms": 15_000,
            "description": "主播在演播室讲解", "transcript": "", "ocr_text": "",
            "tags": [],
        },
    ]


def test_qwen_curates_only_grounded_contiguous_hook_segments(tmp_db, monkeypatch):
    import hotspot_hook_curator

    async def fake_call(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": [{"candidate_index": 1, "reason": "画面与事件事实一致"}]}, ensure_ascii=False), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "港口入口货车排队检查",
            "start_segment_index": 0, "end_segment_index": 1,
            "title_zh": "港口入口卡车排队", "what_happened": "多辆货车在入口排队，工作人员正在检查。",
            "hook_reason": "连续现场动作和排队画面能快速呈现压力。",
            "logistics_question": "入口检查变慢时，卖家应怎样核对到仓与配送计划？", "confidence": 0.88,
        }]}, ensure_ascii=False), "cache_hit": False}

    _patch_curator_models(monkeypatch, fake_call)

    hooks, meta = hotspot_hook_curator.curate_hook_clips(7, "港口入口现场", _segments())

    assert meta["status"] == "curated"
    assert meta["model"] == "qwen-test"
    assert len(hooks) == 1
    assert hooks[0]["start_ms"] == 0
    assert hooks[0]["end_ms"] == 10_000
    assert hooks[0]["segments"][0]["id"] == 11
    assert hooks[0]["review_status"] == "confirmed"
    assert hooks[0]["evidence"]["what_happened"] == "多辆货车在入口排队，工作人员正在检查。"
    assert hooks[0]["evidence"]["hook_reason"] == "连续现场动作和排队画面能快速呈现压力。"
    assert hooks[0]["evidence"]["logistics_question"] == "入口检查变慢时，卖家应怎样核对到仓与配送计划？"
    assert hooks[0]["evidence"]["curator"] == "planner_text"
    assert hooks[0]["evidence"]["hook_sop_id"] == "buffalo-hotspot-hook-selection"
    assert hooks[0]["evidence"]["hook_sop_version"] == "v3"
    assert hooks[0]["evidence"]["event_identity"] == "港口入口货车排队检查"
    assert hooks[0]["evidence"]["selected_segment_indexes"] == [0, 1]
    assert hooks[0]["evidence"]["visual_audit"]["status"] == "accepted"
    assert hooks[0]["evidence"]["text_audit"]["status"] == "accepted"


def test_hook_curator_rejects_invalid_duration_or_unexplained_model_output(tmp_db, monkeypatch):
    import hotspot_hook_curator

    async def fake_call(*_args, **_kwargs):
        return {"content": json.dumps({"hooks": [{
            "start_segment_index": 0, "end_segment_index": 2,
            "title_zh": "泛新闻", "what_happened": "", "hook_reason": "吸睛", "logistics_question": "物流怎样？", "confidence": 0.9,
        }]}, ensure_ascii=False)}

    _patch_curator_models(monkeypatch, fake_call)

    hooks, meta = hotspot_hook_curator.curate_hook_clips(8, "泛新闻", _segments())

    assert hooks == []
    assert meta["status"] == "no_qualified_hooks"


def test_hook_curation_budget_identity_changes_when_analysis_evidence_changes():
    import hotspot_hook_curator

    initial = hotspot_hook_curator._curation_job_id(7, "港口入口现场", "货车排队等待检查", _segments())
    rerun_segments = _segments()
    rerun_segments[-1]["description"] = "街道上车辆缓慢通行"
    rerun = hotspot_hook_curator._curation_job_id(7, "港口入口现场", "货车排队等待检查", rerun_segments)

    assert initial != rerun
    assert initial.startswith("hotspot-hook-curation-7-")


def test_hook_curation_context_is_part_of_prompt_and_cache_identity():
    import hotspot_hook_curator

    first = hotspot_hook_curator._curation_job_id(7, "每日合集", "边境卡车拥堵", _segments())
    second = hotspot_hook_curator._curation_job_id(7, "每日合集", "公路事故", _segments())
    prompt = hotspot_hook_curator._prompt("每日合集", "边境卡车拥堵", _segments())

    assert first != second
    assert "边境卡车拥堵" in prompt
    assert "多事件合集" in prompt
    assert "优先物流向" in prompt
    assert "buffalo-hotspot-hook-selection" in prompt
    assert hotspot_hook_curator.PROMPT_VERSION == "hotspot-hook-curation-v10-mixed-scene-repair"
    assert hotspot_hook_curator.AUDIT_PROMPT_VERSION == "hotspot-hook-grounding-audit-v7-overlay-neutral-facts"


def test_hook_curator_rejects_an_obvious_anchor_only_segment_before_model_audit():
    import hotspot_hook_curator

    payload = {
        "hooks": [{
            "start_segment_index": 2, "end_segment_index": 2,
            "title_zh": "主播讲述交通变化", "what_happened": "主播在演播室讲解交通情况。",
            "hook_reason": "信息明确", "logistics_question": "卖家应怎样安排？", "confidence": 0.9,
        }]
    }

    hooks = hotspot_hook_curator._parse(json.dumps(payload, ensure_ascii=False), _segments())

    assert hooks == []


def test_hook_curator_accepts_a_bare_model_candidate_list():
    import hotspot_hook_curator

    hooks = hotspot_hook_curator._parse(json.dumps([{
        "event_identity": "港口入口货车排队检查",
        "start_segment_index": 0, "end_segment_index": 1,
        "title_zh": "港口入口卡车排队", "what_happened": "多辆货车在入口排队，工作人员正在检查。",
        "hook_reason": "连续现场动作和排队画面能快速呈现压力。",
        "logistics_question": "入口检查变慢时，卖家应怎样核对到仓计划？", "confidence": 0.88,
    }], ensure_ascii=False), _segments())

    assert len(hooks) == 1
    assert hooks[0]["title_zh"] == "港口入口卡车排队"


def test_hook_curator_keeps_mixed_event_identities_from_one_news_compilation():
    import hotspot_hook_curator

    hooks = hotspot_hook_curator._parse(json.dumps({"hooks": [
        {"event_identity": "港口入口货车排队检查", "start_segment_index": 0, "end_segment_index": 0,
         "title_zh": "入口货车排队", "what_happened": "货车在入口排队。", "hook_reason": "现场排队清晰。",
         "logistics_question": "卖家应怎样核对到仓计划？", "confidence": 0.88},
        {"event_identity": "入口货车检查作业", "start_segment_index": 1, "end_segment_index": 1,
         "title_zh": "工作人员检查货车", "what_happened": "工作人员在入口检查货车。", "hook_reason": "连续履约动作。",
         "logistics_question": "检查变慢时卖家应怎样安排？", "confidence": 0.86},
    ]}, ensure_ascii=False), _segments())

    assert len(hooks) == 2
    assert hooks[0]["evidence"]["event_identity"] == "港口入口货车排队检查"
    assert hooks[1]["evidence"]["event_identity"] == "入口货车检查作业"
    assert hooks[0]["evidence"]["hook_sop_version"] == "v3"


def test_hook_curator_accepts_soft_confidence_floor():
    import hotspot_hook_curator

    hooks = hotspot_hook_curator._parse(json.dumps({"hooks": [{
        "event_identity": "港口入口货车排队检查",
        "start_segment_index": 0, "end_segment_index": 0,
        "title_zh": "入口货车排队", "what_happened": "货车在入口排队。",
        "hook_reason": "现场排队清晰。", "logistics_question": "卖家应怎样核对？", "confidence": 0.36,
    }]}, ensure_ascii=False), _segments())

    assert len(hooks) == 1
    assert hooks[0]["confidence"] == 0.36


def test_hook_curator_allows_empty_logistics_question_for_grounded_scene():
    import hotspot_hook_curator

    hooks = hotspot_hook_curator._parse(json.dumps({"hooks": [{
        "event_identity": "港口入口货车排队检查",
        "start_segment_index": 0, "end_segment_index": 0,
        "title_zh": "入口货车排队", "what_happened": "货车在入口排队。",
        "hook_reason": "现场排队清晰。", "logistics_question": "", "confidence": 0.88,
    }]}, ensure_ascii=False), _segments())

    assert len(hooks) == 1
    assert hooks[0]["evidence"]["logistics_question"] == ""


def test_curate_records_valid_empty_response_reason(tmp_db, monkeypatch):
    import hotspot_hook_curator

    async def fake_call(*_args, **_kwargs):
        return {"content": json.dumps({"hooks": []}), "cache_hit": False}

    _patch_curator_models(monkeypatch, fake_call)

    hooks, meta = hotspot_hook_curator.curate_hook_clips(94, "港口入口现场", _segments())

    assert hooks == []
    assert meta["status"] == "no_qualified_hooks"
    rows = tmp_db.list_hook_curation_diagnostics(asset_id=94)
    assert len(rows) == 1
    assert rows[0]["error"] == "model_empty_hooks"


def test_hook_curation_prompt_keeps_all_long_video_segments_inside_input_budget():
    import hotspot_hook_curator

    long_segments = [
        {
            "segment_index": index,
            "start_ms": index * 5_000,
            "end_ms": (index + 1) * 5_000,
            "asset_name": "南非物流资讯合集",
            "description": "南非物流资讯合集 卡车在道路上排队等待检查，工作人员在入口处指挥车辆通行。" * 12,
            "transcript": "请根据现场信息核对货物与入库安排，避免把不确定事项说成承诺。" * 12,
            "ocr_text": "南非物流现场信息更新，请以实际节点为准。" * 12,
            "tags": [{"dimension": "object", "value": "卡车运输现场画面标签" * 20}] * 12,
        }
        for index in range(114)
    ]

    prompt = hotspot_hook_curator._prompt("南非物流资讯合集", "边境与道路运输现场", long_segments)
    messages = [
        {"role": "system", "content": "严格返回 JSON，不要 Markdown，不得补充镜头外事实。"},
        {"role": "user", "content": prompt},
    ]

    # 与 curate_hook_clips 的 14k 输入门禁一致；保留全量镜头索引而不是截掉尾部。
    assert len(json.dumps(messages, ensure_ascii=False)) // 4 < 14_000
    assert '"segment_index": 0' in prompt
    assert '"segment_index": 113' in prompt


def test_qwen_critic_rejects_hook_that_contradicts_verified_event_fact(tmp_db, monkeypatch):
    import hotspot_hook_curator

    async def fake_call(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": []}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "街道垃圾堆积事件",
            "start_segment_index": 0, "end_segment_index": 0,
            "title_zh": "街道堆满包裹", "what_happened": "街道上有大量包裹滞留。",
            "hook_reason": "反常画面", "logistics_question": "末端为何停摆？", "confidence": 0.9,
        }]}, ensure_ascii=False), "cache_hit": False}

    _patch_curator_models(monkeypatch, fake_call)

    hooks, meta = hotspot_hook_curator.curate_hook_clips(9, "垃圾罢工导致街道垃圾成堆", _segments())

    assert hooks == []
    assert meta["status"] == "no_qualified_hooks"
    assert meta["grounding_audit"]["status"] == "rejected_all"


def test_curator_retries_valid_empty_once_when_safe_window_exists(tmp_db, monkeypatch):
    import hotspot_hook_curator

    planner_messages = []

    async def fake_call(*args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {
                "content": json.dumps({"accepted": [{"candidate_index": 1, "reason": "画面事实成立"}]}),
                "cache_hit": False,
            }
        planner_messages.append({"messages": args[2], "kwargs": kwargs})
        if len(planner_messages) == 1:
            return {"content": json.dumps({"hooks": []}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "港口入口货车排队检查",
            "start_segment_index": 0, "end_segment_index": 1,
            "title_zh": "港口入口卡车排队", "what_happened": "多辆货车在入口排队，工作人员正在检查。",
            "hook_reason": "连续现场动作能快速建立问题。",
            "logistics_question": "入口检查变慢时，卖家应怎样核对到仓计划？", "confidence": 0.88,
        }]}), "cache_hit": False}

    _patch_curator_models(monkeypatch, fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})

    hooks, meta = hotspot_hook_curator.curate_hook_clips(10, "港口入口现场", _segments())

    assert len(hooks) == 1
    assert len(planner_messages) == 2
    assert planner_messages[1]["kwargs"]["use_cache"] is False
    assert "上一轮返回了空 hooks" in planner_messages[1]["messages"][-1]["content"]
    assert meta["empty_result_retry"] is True
    assert hooks[0]["evidence"]["visual_audit"]["status"] == "accepted"


def test_curator_does_not_retry_empty_when_every_window_is_anchor_only(tmp_db, monkeypatch):
    import hotspot_hook_curator

    calls = []

    async def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return {"content": json.dumps({"hooks": []}), "cache_hit": False}

    _patch_curator_models(monkeypatch, fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})

    hooks, meta = hotspot_hook_curator.curate_hook_clips(11, "新闻播报", [_segments()[2]])

    assert hooks == []
    assert len(calls) == 1
    assert meta["empty_result_retry"] is False


def test_curator_repairs_deterministic_overlong_candidate_once(tmp_db, monkeypatch):
    import hotspot_hook_curator

    planner_calls = []

    async def fake_call(*args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": [{"candidate_index": 1, "reason": "画面事实一致"}]}), "cache_hit": False}
        planner_calls.append(args[2])
        if len(planner_calls) == 1:
            return {"content": json.dumps({"hooks": [{
                "event_identity": "道路现场",
                "start_segment_index": 0, "end_segment_index": 1,
                "title_zh": "道路现场", "what_happened": "道路上车辆通行。",
                "hook_reason": "现场动作清晰。", "logistics_question": "", "confidence": 0.88,
            }]}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "道路现场",
            "start_segment_index": 0, "end_segment_index": 0,
            "title_zh": "道路现场", "what_happened": "道路上车辆通行。",
            "hook_reason": "现场动作清晰。", "logistics_question": "", "confidence": 0.88,
        }]}), "cache_hit": False}

    _patch_curator_models(monkeypatch, fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})
    segments = [
        {
            "id": 21, "segment_index": 0, "start_ms": 0, "end_ms": 7_800,
            "description": "雨天道路上车辆通行", "transcript": "", "ocr_text": "",
            "tags": [{"dimension": "scene", "value": "道路运输"}],
        },
        {
            "id": 22, "segment_index": 1, "start_ms": 7_800, "end_ms": 15_600,
            "description": "雨天道路上车辆排队", "transcript": "", "ocr_text": "",
            "tags": [{"dimension": "object", "value": "卡车"}],
        },
    ]

    hooks, meta = hotspot_hook_curator.curate_hook_clips(95, "Traffic update", segments)

    assert len(hooks) == 1
    assert hooks[0]["start_ms"] == 7_800
    assert hooks[0]["end_ms"] == 15_600
    assert len(planner_calls) == 1
    assert meta["empty_result_retry"] is False


def test_empty_result_repair_still_goes_through_visual_audit(tmp_db, monkeypatch):
    import hotspot_hook_curator

    visual_calls = []
    planner_calls = []

    async def fake_call(*args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": [{"candidate_index": 1, "reason": "ok"}]}), "cache_hit": False}
        planner_calls.append(1)
        if len(planner_calls) == 1:
            return {"content": json.dumps({"hooks": []}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "港口入口货车排队检查",
            "start_segment_index": 0, "end_segment_index": 1,
            "title_zh": "港口入口卡车排队", "what_happened": "多辆货车在入口排队。",
            "hook_reason": "连续现场动作。",
            "logistics_question": "入口检查变慢时如何核对？", "confidence": 0.88,
        }]}), "cache_hit": False}

    def tracking_visual(asset_id, hooks, **kwargs):
        visual_calls.append(len(hooks))
        return _pass_visual(hooks)

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_a, **_k: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})
    monkeypatch.setattr(hotspot_hook_curator.hotspot_hook_visual_audit, "audit_hooks", tracking_visual)

    hooks, meta = hotspot_hook_curator.curate_hook_clips(12, "港口入口现场", _segments())
    assert len(hooks) == 1
    assert visual_calls == [1]
    assert meta["empty_result_retry"] is True
    assert len(planner_calls) == 2


def test_replacing_rejected_hooks_can_remove_only_its_generated_preview_folder(tmp_path):
    import hotspot_event_media

    static_dir = tmp_path / "static"
    target = static_dir / "assets" / "hotspot-events" / "303"
    target.mkdir(parents=True)
    (target / "event-0049.mp4").write_bytes(b"proxy")
    untouched = static_dir / "assets" / "hotspot-events" / "304"
    untouched.mkdir(parents=True)
    (untouched / "event-0050.mp4").write_bytes(b"other")

    hotspot_event_media.remove_materialized_event_clips(static_dir, 303)

    assert not target.exists()
    assert (untouched / "event-0050.mp4").is_file()
