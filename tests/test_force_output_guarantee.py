"""Adversarial coverage for the any-topic video availability contract."""

import pytest


def _event() -> dict:
    return {
        "id": 901,
        "asset_id": 9901,
        "hotspot_id": 77,
        "event_index": 1,
        "start_ms": 0,
        "end_ms": 7_000,
        "hook_kind": "timely_event",
        "title_zh": "南非道路货运车辆排队",
        "evidence": {
            "what_happened": "南非道路上可见货运卡车排队等待通行",
            "logistics_question": "道路变化时，卖家应先核对哪个物流节点？",
            "event_identity": "南非道路货运卡车排队等待通行",
        },
    }


def _owned_segments() -> list[dict]:
    rows = (
        (1, "warehouse", "工作人员逐件扫码并核对入库包裹"),
        (2, "staff", "工作人员分区协同并记录异常货物"),
        (3, "facility", "输送设备运行并完成分区作业"),
        (4, "delivery", "配送车辆出车前核对交接信息"),
        (5, "warehouse", "包裹在货架区完成复核和分拣"),
        (6, "staff", "现场人员打包装箱并粘贴运单"),
        (7, "delivery", "末端人员核对收件信息后交接包裹"),
    )
    return [
        {
            "id": index,
            "asset_id": 10_000 + index,
            "asset_file_type": "video",
            "asset_name": f"Buffalo 作业镜头 {index}",
            "primary_category": category,
            "description": description,
            "start_ms": 0,
            "end_ms": 8_000,
            "quality_score": 0.95,
            "tags": [],
        }
        for index, category, description in rows
    ]


@pytest.mark.parametrize("topic", [
    "同城配送时效对比",
    "南非本地快递对比评测",
    "旺季爆仓应对策略",
    "政策法规变动速递",
    "客户临时要求周日送一台咖啡机，仓配怎么安排",
])
def test_deterministic_rescue_builds_a_valid_real_hook_video_for_any_topic(topic):
    import app
    import hotspot_video_planner
    import video_topic_contract

    event = _event()
    brief = {
        "raw_input": topic,
        "requested_topic": topic,
        "subject": topic,
        "logistics_topic": topic,
        "logistics_nodes": list(
            video_topic_contract.build_topic_contract(topic, has_event_anchor=True).get("nodes") or []
        ),
        "approved_hook_event_ids": [event["id"]],
        "primary_event_id": event["id"],
        "topic_contract": video_topic_contract.build_topic_contract(topic, has_event_anchor=True),
    }
    content_scenes = hotspot_video_planner.plan_followup_scenes(
        brief,
        [event],
        _owned_segments(),
        target_duration_ms=50_000,
        allow_adaptation=True,
        chain_mode="hotspot_owned",
    )
    scenes = hotspot_video_planner.append_brand_endcard_scenes(content_scenes, context=brief)
    minimums = [app._scene_voiceover_min_chars(scene) for scene in scenes]
    maximums = [app._scene_voiceover_max_chars(scene) for scene in scenes]
    generated = app._deterministic_formal_script(
        brief,
        scenes,
        event,
        hook_binding_mode="contextual_attention",
        fallback_reason="test_model_output_invalid",
    )

    finalized = app._finalize_formal_script_candidate(
        generated,
        brief=brief,
        scenes=scenes,
        event=event,
        hook_binding_mode="contextual_attention",
        voiceover_minimums=minimums,
        voiceover_limits=maximums,
        hotspot_count=1,
        allow_fallback_bridge=True,
    )

    assert len(finalized["scenes"]) == len(scenes)
    assert all(scene["voiceover"].strip() for scene in finalized["scenes"])
    assert finalized["scenes"][0]["copy_source"] == "fallback"
    assert "test_model_output_invalid" in finalized["scenes"][0]["copy_repair_reason"]
    assert "风险影响仓配" not in "".join(
        scene["voiceover"] for scene in finalized["scenes"]
    )
    if topic == "南非本地快递对比评测":
        assert "Buffalo" in finalized["scenes"][-1]["voiceover"]
    app._validate_complete_formal_voiceovers(finalized)
    app._validate_formal_narrative(finalized, scenes, event)


def test_force_output_hook_inventory_prefers_a_real_contextual_hook_over_no_video(tmp_db, monkeypatch):
    import app

    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa freight traffic",
        "summary": "Trucks are visible on a freight road",
        "source_url": "https://example.com/freight",
        "publisher": "Official",
        "published_at": "2026-08-26T00:00:00Z",
        "retrieved_at": "2026-08-26T00:00:00Z",
        "snapshot_sha256": "force-output-contextual-hook",
    })
    asset_id = tmp_db.create_asset({
        "name": "真实货运现场",
        "filepath": "assets/real-freight.mp4",
        "file_type": "video",
        "category": "delivery",
        "duration": 20,
        "size": 100,
        "source": "official",
        "status": "active",
        "sha256": "a" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1,
        "start_ms": 0,
        "end_ms": 6_000,
        "title_zh": "道路货运车辆持续行驶",
        "title_en": "Freight trucks moving",
        "segments": [],
        "confidence": 0.95,
        "review_status": "confirmed",
        "hook_kind": "timely_event",
        "evidence": {
            "what_happened": "道路上可见货运卡车持续行驶",
            "hook_reason": "货运车辆动作清晰可见",
            "logistics_question": "物流变化时应先核对什么？",
            "event_identity": "道路货运卡车持续行驶",
            "visual_audit": {"scene_type": "road"},
        },
    }])[0]
    tmp_db.update_hotspot_event_clip_media(
        event["id"], "assets/hotspot-events/901/event.mp4", None, "ready",
    )
    monkeypatch.setattr(app, "_is_confirmed_renderable_hotspot_hook", lambda row: row.get("id") == event["id"])

    candidates = app._force_output_hook_candidates("周日配送咖啡机")

    assert candidates
    assert candidates[0]["event"]["id"] == event["id"]
    assert candidates[0]["binding"]["mode"] in {
        "exact", "adjacent_logistics", "contextual_attention",
    }


def test_targeted_repair_signature_detects_contract_level_no_progress():
    import app

    first = app._targeted_repair_failure_signature(
        {1, 3},
        {
            1: ValueError("MiniMax 第 2 镜旁白超过 28 字时长上限"),
            3: ValueError("MiniMax 第 4 镜没有使用锁定镜头中的真实可见动作"),
        },
    )
    repeated_with_different_words = app._targeted_repair_failure_signature(
        {1, 3},
        {
            1: ValueError("MiniMax 第 2 镜旁白超过 31 字时长上限"),
            3: ValueError("MiniMax 第 4 镜没有使用锁定镜头中的真实可见动作"),
        },
    )
    progressed = app._targeted_repair_failure_signature(
        {3},
        {3: ValueError("MiniMax 第 4 镜没有使用锁定镜头中的真实可见动作")},
    )

    assert first == repeated_with_different_words
    assert progressed != first


@pytest.mark.parametrize("voiceover", [
    "分拣线扫码留痕，让城区调拨更可。",
    "每段时效都记录在案，过程随时可。",
    "延误，Buffalo以分拣留痕，更可控。",
])
def test_final_sentence_gate_rejects_production_truncation_patterns(voiceover):
    import video_topic_contract

    assert video_topic_contract.incomplete_sentence_issues({
        "scenes": [{"voiceover": voiceover}],
    })


def test_overclaim_guard_uses_immutable_user_topic_not_retrieval_rescue_nodes():
    import app

    peak_brief = {
        "requested_topic": "旺季爆仓应对策略",
        "logistics_nodes": ["仓储", "清关", "配送"],
        "topic_contract": {
            "nodes": ["仓储", "分拣", "交接"],
        },
    }
    policy_brief = {
        "requested_topic": "政策法规变动速递",
        "logistics_nodes": ["仓储", "配送"],
        "topic_contract": {
            "nodes": ["清关"],
        },
    }

    assert "清关" not in app._immutable_topic_guard_nodes(peak_brief)
    assert app._immutable_topic_guard_nodes(policy_brief) == ["清关"]


def test_custom_cangpei_topic_title_passes_the_same_gate_used_in_script_quality():
    import video_topic_contract

    topic = "客户临时要求周日送一台咖啡机，仓配怎么安排"
    contract = video_topic_contract.build_topic_contract(topic, has_event_anchor=True)
    assert not video_topic_contract.title_group_issues(contract["safe_title"], contract)
    assert "仓配" in " ".join(
        term for group in contract["title_groups"] for term in group
    )
    generated = {
        "title": "周日临时订单安排",
        "scenes": [{"voiceover": "仓配先核对接货和分拣动作。"}],
    }
    repaired_title = video_topic_contract.ensure_title_satisfies_contract(
        generated["title"], contract,
    )
    generated["title"] = repaired_title
    assert not video_topic_contract.validate_generated_topic_contract(generated, contract)
    assert "仓配" in repaired_title


def test_road_truck_hook_is_not_exact_evidence_for_cangpei_topic(tmp_db):
    import app
    import video_topic_contract

    topic = "客户临时要求周日送一台咖啡机，仓配怎么安排"
    event = {
        "id": 108,
        "title_zh": "车顶带货架的橙色货车驶过",
        "title_en": "Orange truck with a roof rack",
        "evidence": {
            "what_happened": "监控画面显示一辆车顶装有货架的橙色货车在道路行驶。",
            "event_identity": "橙色货车在道路行驶",
        },
        "keywords": ["仓储", "配送"],
        "logistics_scenes": ["warehouse", "last_mile"],
    }
    issues = video_topic_contract.topic_hook_compatibility_issues(
        topic, [{**event, "logistics_scenes": []}],
    )
    assert issues
    assert app._hook_fact_supports_exact_topic_intent(topic, event) is False
    assessment = app._hook_binding_assessment(topic, [event])
    assert assessment["mode"] != "exact"


def test_immutable_nodes_keep_zastock_from_polluting_cangpei_with_customs_copy():
    import app
    import hotspot_preview_narration as narration

    brief = {
        "requested_topic": "客户临时要求周日送一台咖啡机，仓配怎么安排",
        "logistics_nodes": ["仓储", "清关", "配送"],
        "topic_contract": {"nodes": ["仓储", "末端"]},
    }
    nodes = app._immutable_topic_guard_nodes(brief)
    assert "清关" not in nodes
    scenes = [{
        "scene": 1,
        "scene_role": "owned_proof",
        "evidence_type": "owned_video",
        "duration_ms": 8_000,
        "primary_category": "delivery",
        "asset_source": "za_stock_license",
        "asset_id": 866,
    }]
    generated = [{
        "voiceover": "仓配先核对接货和出车。",
        "text_overlay": "仓配核对",
    }]
    records = narration.apply_overclaim_guard(generated, scenes, nodes)
    assert records == []
    assert generated[0]["voiceover"] == "仓配先核对接货和出车。"
    for term in ("清关", "海关", "放行"):
        assert term not in generated[0]["voiceover"]
