import asyncio
import json


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


def test_intake_candidate_prefers_verified_single_video_metadata_over_placeholder():
    import hotspot_hook_intake

    candidate = hotspot_hook_intake._candidate(
        {
            "id": 31, "duration_seconds": 240, "platform": "youtube",
            "intake_title": "Warehouse staff inspect parcels before loading",
            "intake_summary": "Staff inspect parcels and load delivery vehicles.",
        },
        {"title": "Daily bulletin", "summary": "来自频道的公开视频热点。"},
    )

    assert candidate["hotspot_title"] == "Warehouse staff inspect parcels before loading"
    assert candidate["hotspot_summary"] == "Staff inspect parcels and load delivery vehicles."


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

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    hooks, meta = hotspot_hook_curator.curate_hook_clips(7, "港口入口现场", _segments())

    assert meta["status"] == "curated"
    assert meta["model"] == "qwen-test"
    assert len(hooks) == 1
    assert hooks[0]["start_ms"] == 0
    assert hooks[0]["end_ms"] == 10_000
    assert hooks[0]["segments"][0]["id"] == 11
    assert hooks[0]["evidence"] == {
        "what_happened": "多辆货车在入口排队，工作人员正在检查。",
        "hook_reason": "连续现场动作和排队画面能快速呈现压力。",
        "logistics_question": "入口检查变慢时，卖家应怎样核对到仓与配送计划？",
        "curator": "planner_text",
        "hook_sop_id": "buffalo-hotspot-hook-selection",
        "hook_sop_version": "v2",
        "event_identity": "港口入口货车排队检查",
        "selected_segment_indexes": [0, 1],
    }


def test_hook_curator_rejects_invalid_duration_or_unexplained_model_output(tmp_db, monkeypatch):
    import hotspot_hook_curator

    async def fake_call(*_args, **_kwargs):
        return {"content": json.dumps({"hooks": [{
            "start_segment_index": 0, "end_segment_index": 2,
            "title_zh": "泛新闻", "what_happened": "", "hook_reason": "吸睛", "logistics_question": "物流怎样？", "confidence": 0.9,
        }]}, ensure_ascii=False)}

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})

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
    assert "buffalo-hotspot-hook-selection" in prompt


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


def test_hook_curator_rejects_mixed_event_identities_from_one_news_compilation():
    import hotspot_hook_curator

    hooks = hotspot_hook_curator._parse(json.dumps({"hooks": [
        {"event_identity": "港口入口货车排队检查", "start_segment_index": 0, "end_segment_index": 0,
         "title_zh": "入口货车排队", "what_happened": "货车在入口排队。", "hook_reason": "现场排队清晰。",
         "logistics_question": "卖家应怎样核对到仓计划？", "confidence": 0.88},
        {"event_identity": "演播室主播报道", "start_segment_index": 1, "end_segment_index": 1,
         "title_zh": "主播报道", "what_happened": "主播介绍新闻。", "hook_reason": "信息明确。",
         "logistics_question": "卖家应怎样安排？", "confidence": 0.88},
    ]}, ensure_ascii=False), _segments())

    assert len(hooks) == 1
    assert hooks[0]["evidence"]["event_identity"] == "港口入口货车排队检查"


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

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    hooks, meta = hotspot_hook_curator.curate_hook_clips(9, "垃圾罢工导致街道垃圾成堆", _segments())

    assert hooks == []
    assert meta["status"] == "no_qualified_hooks"
    assert meta["grounding_audit"]["status"] == "rejected_all"


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


def test_qwen_decides_whether_authorized_long_video_enters_hook_library(tmp_db, monkeypatch):
    import hotspot_hook_intake

    evidence_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 有已确认的仓库、包裹检查和装卸日常作业能力。",
        "evidence_note": "可用于说明仓储与履约准备，不证明港口时效。",
        "status": "confirmed",
    })

    candidates = [
        {"id": 41, "hotspot_id": 5, "duration_seconds": 210, "platform": "youtube"},
        {"id": 42, "hotspot_id": 6, "duration_seconds": 260, "platform": "youtube"},
    ]
    hotspots = {
        5: {"title": "Warehouse teams inspect parcels before dispatch", "summary": "Staff check parcels and prepare outbound loading", "publisher": "SA News"},
        6: {"title": "Studio discussion", "summary": "Commentary programme", "publisher": "SA News"},
    }

    async def fake_call(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_intake.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"approved": [{"media_id": 41, "reason": "仓储作业证据与包裹检查现场直接对应。"}]}, ensure_ascii=False), "cache_hit": False}
        return {"content": json.dumps({"selections": [{
            "media_id": 41, "rag_evidence_ids": [f"brand:{evidence_id}"],
            "service_fit": "已确认的仓储与包裹检查作业可用于解释出库前准备。",
            "expected_hook": "工作人员检查包裹并准备装车的现场动作。",
            "why": "已知仓库包裹检查，母片可能包含连续履约动作。",
            "logistics_question": "出库前应怎样核对包裹状态？", "confidence": 0.82,
        }, {
            "media_id": 42, "rag_evidence_ids": [], "service_fit": "", "expected_hook": "", "why": "", "logistics_question": "", "confidence": 0.9,
        }]}, ensure_ascii=False), "cache_hit": False}

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(candidates, hotspots, maximum=2)

    assert [item["id"] for item in selected] == [41]
    assert selected[0]["intake_decision"]["curator"] == "planner_text"
    assert selected[0]["intake_decision"]["rag_evidence_ids"] == [f"brand:{evidence_id}"]
    assert meta["status"] == "selected"
    assert meta["audit"]["approved_count"] == 1


def test_hook_ingestion_never_falls_back_to_keyword_selection_when_model_missing(tmp_db, monkeypatch):
    import hotspot_hook_intake

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: False)
    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(
        [{"id": 1, "hotspot_id": 1, "duration_seconds": 240}], {1: {"title": "港口拥堵"}}
    )

    assert selected == []
    assert meta["status"] == "model_unavailable"


def test_rag_sop_critic_rejects_municipal_waste_but_keeps_direct_warehouse_case(tmp_db, monkeypatch):
    import hotspot_hook_intake
    import hotspot_intake_sop

    evidence_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 已确认有海外仓、包裹检查和装卸作业画面。",
        "evidence_note": "仅支持仓库履约准备与包裹处理，不覆盖市政环卫服务。",
        "status": "confirmed",
    })
    candidates = [
        {"id": 71, "hotspot_id": 15, "duration_seconds": 240, "platform": "youtube"},
        {"id": 72, "hotspot_id": 16, "duration_seconds": 240, "platform": "youtube"},
    ]
    hotspots = {
        15: {"title": "Pikitup strike leaves streets lined with waste", "summary": "Municipal refuse collection strike", "publisher": "SABC"},
        16: {"title": "Warehouse teams prepare parcels for dispatch", "summary": "Staff inspect parcels and load delivery vehicles", "publisher": "SA Business"},
    }

    async def fake_call(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_intake.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"approved": [{"media_id": 72, "reason": "仓储、包裹检查与证据直接对应。"}]}, ensure_ascii=False), "cache_hit": False}
        return {"content": json.dumps({"selections": [
            {"media_id": 71, "rag_evidence_ids": [f"brand:{evidence_id}"], "service_fit": "垃圾堆积会影响物流", "expected_hook": "街头垃圾", "why": "反常画面", "logistics_question": "会不会影响配送？", "confidence": 0.86},
            {"media_id": 72, "rag_evidence_ids": [f"brand:{evidence_id}"], "service_fit": "仓库包裹检查与已确认作业直接对应", "expected_hook": "检查包裹和装车", "why": "连续履约动作", "logistics_question": "出库前应核对什么？", "confidence": 0.91},
        ]}, ensure_ascii=False), "cache_hit": False}

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(candidates, hotspots, maximum=2)

    assert [item["id"] for item in selected] == [72]
    assert meta["audit"]["planner_selected_count"] == 2
    assert meta["audit"]["approved_count"] == 1
    assert selected[0]["intake_decision"]["sop_version"] == hotspot_intake_sop.SOP_VERSION


def test_rag_sop_allows_grounded_contextual_logistics_hook_without_claiming_event_solution(tmp_db, monkeypatch):
    import hotspot_hook_intake

    evidence_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 已确认有海外仓、包裹检查和装卸日常作业。",
        "evidence_note": "仅支持仓储履约准备，不证明港口事件已被解决。",
        "status": "confirmed",
    })

    async def fake_call(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_intake.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"approved": [{
                "media_id": 73,
                "reason": "货运港口排队是明确物流运行变化；品牌动作只限仓内出库准备。",
            }]}, ensure_ascii=False), "cache_hit": False}
        return {"content": json.dumps({"selections": [{
            "media_id": 73,
            "admission_mode": "contextual",
            "rag_evidence_ids": [f"brand:{evidence_id}"],
            "service_fit": "RAG 仅支持 Buffalo 仓内包裹检查和出库装卸准备。",
            "expected_hook": "港口入口货车排队等待，工作人员检查进场货运车辆。",
            "why": "货运拥堵可作为卖家核对出库节奏和到港预期的外部背景。",
            "logistics_question": "港口排队变化时，出库前应怎样同步装载与客户预期？",
            "confidence": 0.89,
        }]}, ensure_ascii=False), "cache_hit": False}

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(
        [{"id": 73, "hotspot_id": 17, "duration_seconds": 240, "platform": "youtube"}],
        {17: {
            "title": "Cargo trucks queue at a port entrance",
            "summary": "Freight vehicles wait at the port while staff inspect incoming trucks.",
        }},
    )

    assert [item["id"] for item in selected] == [73]
    assert selected[0]["intake_decision"]["admission_mode"] == "contextual"
    assert meta["audit"]["approved_count"] == 1


def test_rag_sop_rejects_model_selection_without_a_cited_evidence_id(tmp_db, monkeypatch):
    import hotspot_hook_intake

    tmp_db.create_brand_evidence({
        "claim": "Buffalo 有仓库操作能力。", "evidence_note": "已确认。", "status": "confirmed",
    })
    calls = []

    async def fake_call(*_args, **kwargs):
        calls.append(kwargs["prompt_version"])
        return {"content": json.dumps({"selections": [{
            "media_id": 81, "rag_evidence_ids": ["brand:999"], "service_fit": "仓库关联",
            "expected_hook": "装车", "why": "现场", "logistics_question": "如何准备？", "confidence": 0.9,
        }]}, ensure_ascii=False), "cache_hit": False}

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(
        [{"id": 81, "hotspot_id": 18, "duration_seconds": 240, "platform": "youtube"}],
        {18: {"title": "Warehouse loading", "summary": "Warehouse loading parcels"}},
    )

    assert selected == []
    assert meta["status"] == "no_qualified_media"
    assert calls == [hotspot_hook_intake.PROMPT_VERSION]


def test_rag_sop_does_not_allow_rag_to_invent_brand_visuals(tmp_db, monkeypatch):
    import hotspot_hook_intake

    evidence_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 有仓库操作能力。", "evidence_note": "已确认。", "status": "confirmed",
    })
    calls = []

    async def fake_call(*_args, **kwargs):
        calls.append(kwargs["prompt_version"])
        return {"content": json.dumps({"selections": [{
            "media_id": 82, "rag_evidence_ids": [f"brand:{evidence_id}"], "service_fit": "仓储关联",
            "expected_hook": "仓内可见 Buffalo 标识的工作人员装车", "why": "现场", "logistics_question": "如何准备？", "confidence": 0.9,
        }]}, ensure_ascii=False), "cache_hit": False}

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "create_budget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "call_text", fake_call)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "get_route", lambda _role: {"model": "qwen-test"})

    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(
        [{"id": 82, "hotspot_id": 20, "duration_seconds": 240, "platform": "youtube"}],
        {20: {"title": "Warehouse loading", "summary": "Workers load parcels"}},
    )

    assert selected == []
    assert meta["status"] == "no_qualified_media"
    assert calls == [hotspot_hook_intake.PROMPT_VERSION]


def test_rag_sop_fails_closed_when_no_confirmed_knowledge_exists(tmp_db, monkeypatch):
    import hotspot_hook_intake

    monkeypatch.setattr(hotspot_hook_intake.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_intake.model_router, "call_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应调用模型")))

    selected, meta = hotspot_hook_intake.select_for_hook_ingestion(
        [{"id": 91, "hotspot_id": 19, "duration_seconds": 240, "platform": "youtube"}],
        {19: {"title": "Port traffic", "summary": "Trucks queue at port"}},
    )

    assert selected == []
    assert meta["status"] == "no_rag_evidence"
    assert meta["rag_sop"]["knowledge_sources"] == 0


def test_rag_sop_retrieves_using_hotspot_candidate_title_and_summary_fields(tmp_db):
    import hotspot_intake_sop

    warehouse_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 南非海外仓提供包裹检查与装卸日常作业。",
        "evidence_note": "仓储实拍。", "status": "confirmed",
    })
    tmp_db.create_brand_evidence({
        "claim": "Buffalo 清关资料仅用于税务知识说明。",
        "evidence_note": "清关资料。", "status": "confirmed",
    })

    evidence = hotspot_intake_sop.retrieve_service_evidence({
        "hotspot_title": "Warehouse workers inspect parcels before loading",
        "hotspot_summary": "Parcel inspection and warehouse loading in South Africa",
    })

    assert evidence[0]["id"] == f"brand:{warehouse_id}"
    assert evidence[0]["retrieval_score"] > evidence[1]["retrieval_score"]
