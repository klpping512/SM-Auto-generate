def test_planner_returns_relevant_logistics_angle_for_risk_hotspot():
    from hotspot_logistics_planner import build_brief

    brief = build_brief({
        "title_zh": "约翰内斯堡道路安全事件",
        "summary_zh": "部分道路出现危险情况，运输车辆需要重新规划路线",
    }, [])

    assert brief["angle"]
    assert brief["logistics_topic"] in {"末端配送安全", "路线稳定性", "本地快递时效"}
    assert brief["required_evidence"]["hotspot_video"] >= 1


def test_planner_does_not_claim_unsupported_capability():
    from hotspot_logistics_planner import build_brief

    brief = build_brief({
        "title_zh": "南非电商增长",
        "summary_zh": "电商订单增长，消费者更加关注配送体验",
    }, [])

    assert "百分百" not in brief["brand_claims"]
    assert brief["negative_claims"]


def test_video_plan_has_sixty_seconds_and_mixed_evidence():
    from hotspot_video_planner import plan_followup_scenes

    brief = {
        "logistics_topic": "本地快递时效",
        "angle": "订单增长时，末端配送能否稳定兑现承诺？",
        "claim": "用可核验的仓配与末端画面说明履约能力",
        "hotspot_type": "ecommerce_growth",
        "required_evidence": {"hotspot_video": 3, "owned_video": 4, "image_ratio_max": 0.15},
    }
    events = [
        {"id": 1, "asset_id": 90, "hotspot_id": 12, "title_zh": "电商订单增长", "duration_ms": 8000, "clip_status": "ready"},
        {"id": 2, "asset_id": 90, "hotspot_id": 12, "title_zh": "配送需求增加", "duration_ms": 8000, "clip_status": "ready"},
        {"id": 3, "asset_id": 90, "hotspot_id": 12, "title_zh": "仓储扩容", "duration_ms": 8000, "clip_status": "ready"},
    ]
    owned = [
        {"id": index, "asset_id": index, "asset_file_type": "video", "primary_category": category,
         "asset_name": f"{category}-{index}", "description": description,
         "start_ms": 0, "end_ms": 10_000, "quality_score": 0.8, "tags": []}
        for index, (category, description) in enumerate([
            ("warehouse", "仓内货架分拣准备"),
            ("delivery", "车辆进行发运前准备"),
            ("staff", "工作人员检查包裹"),
            ("facility", "仓内设备处理包裹"),
            ("warehouse", "叉车搬运包裹"),
            ("delivery", "拖车等待调度"),
        ], 1)
    ]

    scenes = plan_followup_scenes(brief, events, owned)

    # 没有可用图片时不再以空白 PPT 补满 60 秒；真实证据仍足够支撑 CTA 后的成片。
    assert sum(scene["duration_ms"] for scene in scenes) >= 50_000
    assert sum(scene["scene_role"] == "hotspot_evidence" for scene in scenes) == 2
    assert sum(scene["scene_role"] == "owned_proof" for scene in scenes) >= 4
    assert len({scene["asset_id"] for scene in scenes if scene.get("scene_role") == "owned_proof"}) == sum(scene["scene_role"] == "owned_proof" for scene in scenes)
    assert not any(scene["scene_role"] == "logistics_explainer" for scene in scenes)
    assert all(not (scenes[i]["evidence_type"] == scenes[i + 1]["evidence_type"] == "image")
               for i in range(len(scenes) - 1))


def test_planner_uses_measured_real_clip_budget_and_never_assumes_a_loop():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"hotspot_title": "口岸卡车排队", "hotspot_type": "infrastructure"},
        [{"id": 1, "asset_id": 999_950, "title_zh": "卡车排队", "clip_status": "ready",
          "start_ms": 0, "end_ms": 5_400, "keywords": ["卡车", "拥堵"]}],
        [{"id": 2, "asset_id": 999_951, "asset_file_type": "video", "primary_category": "delivery",
          "asset_name": "Buffalo 运输现场", "start_ms": 0, "end_ms": 4_100, "quality_score": 0.8,
          "tags": []}],
        target_duration_ms=50_000,
    )

    real_scenes = [scene for scene in scenes if scene.get("asset_id")]
    assert [scene["duration_ms"] for scene in real_scenes] == [5_400, 4_100]
    assert not any(scene["scene_role"] == "logistics_explainer" for scene in scenes)


def test_formal_planner_does_not_insert_owned_images_as_context_transitions():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"hotspot_title": "道路受阻", "hotspot_type": "infrastructure"},
        [{"id": 1, "asset_id": 80, "hotspot_id": 9, "title_zh": "道路现场", "clip_status": "ready",
          "start_ms": 0, "end_ms": 6_000}],
        [{"id": 2, "asset_id": 20, "asset_file_type": "video", "primary_category": "warehouse",
          "asset_name": "Buffalo 仓内准备", "start_ms": 0, "end_ms": 6_000, "quality_score": .8, "tags": []}],
        owned_images=[
            {"id": 31, "file_type": "image", "primary_category": "warehouse", "source": "upload", "name": "Buffalo 货架"},
            {"id": 32, "file_type": "image", "primary_category": "delivery", "source": "upload", "name": "Buffalo 配送车"},
        ],
        target_duration_ms=50_000,
    )

    images = [scene for scene in scenes if scene["evidence_type"] == "image"]
    assert images == []
    assert all(scene["duration_ms"] == 2_000 for scene in images)
    assert not any(scene["evidence_type"] == "explanation_card" for scene in scenes)


def test_over_budget_plan_keeps_every_real_video_at_least_three_seconds():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"hotspot_title": "边境卡车排队", "hotspot_type": "infrastructure"},
        [
            {"id": 1, "asset_id": 801, "hotspot_id": 8, "title_zh": "边境卡车排队", "clip_status": "ready",
             "start_ms": 0, "end_ms": 7_000},
            {"id": 2, "asset_id": 801, "hotspot_id": 8, "title_zh": "口岸车辆等待", "clip_status": "ready",
             "start_ms": 8_000, "end_ms": 15_000},
        ],
        [
            {"id": index, "asset_id": 900 + index, "asset_file_type": "video", "primary_category": "delivery",
             "asset_name": f"Buffalo 运输 {index}", "start_ms": 0,
             "end_ms": 3_002 if index == 1 else 7_000, "quality_score": .9, "tags": []}
            for index in range(1, 8)
        ],
        owned_images=[{"id": 99, "asset_id": 99, "file_type": "image", "primary_category": "delivery",
                       "source": "upload", "name": "Buffalo 配送车"}],
        target_duration_ms=52_500,
    )

    assert sum(scene["duration_ms"] for scene in scenes) <= 52_500
    assert all(scene["duration_ms"] >= 3_000 for scene in scenes if scene.get("asset_id"))
    assert not any(scene["evidence_type"] == "image" for scene in scenes)


def test_unrelated_hotspot_event_is_not_selected():
    from hotspot_video_planner import plan_followup_scenes

    brief = {
        "logistics_topic": "运输安全",
        "angle": "风险事件下如何保持安全履约",
        "claim": "用真实运输与仓储画面说明安全流程",
        "hotspot_type": "risk",
        "required_evidence": {"hotspot_video": 2, "owned_video": 4, "image_ratio_max": 0.15},
    }
    events = [{"id": 999, "asset_id": 90, "hotspot_id": 12, "title_zh": "南非娱乐明星新歌发布",
               "duration_ms": 8_000, "clip_status": "ready"}]
    owned = [{"id": index, "asset_id": index, "asset_file_type": "video", "primary_category": "delivery",
              "asset_name": f"delivery-{index}", "start_ms": 0, "end_ms": 10_000, "quality_score": 0.8,
              "tags": []} for index in range(1, 7)]

    scenes = plan_followup_scenes(brief, events, owned)

    assert all(scene.get("event_clip_id") != 999 for scene in scenes)


def test_owned_planner_prefers_visible_buffalo_brand_within_same_logistics_category():
    from hotspot_video_planner import _owned_candidates

    brief = {"logistics_topic": "本地配送", "logistics_nodes": ["last_mile"]}
    generic = {"id": 1, "asset_id": 1, "asset_file_type": "video", "asset_source": "upload",
               "primary_category": "delivery", "quality_score": 0.95, "tags": []}
    branded = {"id": 2, "asset_id": 2, "asset_file_type": "video", "asset_source": "upload",
               "primary_category": "delivery", "quality_score": 0.80,
               "tags": [{"dimension": "brand", "value": "Buffalo"}]}

    candidates = _owned_candidates([generic, branded], brief)

    assert [item["id"] for item in candidates] == [2, 1]


def test_owned_planner_interleaves_visible_action_families_before_reusing_warehouse():
    from hotspot_video_planner import _diversify_owned_candidates

    candidates = [
        {"id": 1, "asset_id": 1, "primary_category": "warehouse", "tags": []},
        {"id": 2, "asset_id": 2, "primary_category": "warehouse", "tags": []},
        {"id": 3, "asset_id": 3, "primary_category": "staff", "tags": []},
        {"id": 4, "asset_id": 4, "primary_category": "delivery", "tags": []},
    ]

    ordered = _diversify_owned_candidates(candidates)

    assert [item["id"] for item in ordered[:4]] == [4, 3, 1, 2]


def test_owned_planner_prefers_new_visible_action_before_repeating_forklift():
    from hotspot_video_planner import _diversify_owned_candidates

    candidates = [
        {"id": 1, "asset_id": 1, "primary_category": "facility", "description": "叉车搬运包裹", "tags": []},
        {"id": 2, "asset_id": 2, "primary_category": "warehouse", "description": "叉车搬运包裹", "tags": []},
        {"id": 3, "asset_id": 3, "primary_category": "staff", "description": "检查包裹", "tags": []},
        {"id": 4, "asset_id": 4, "primary_category": "warehouse", "description": "仓库货架", "tags": []},
    ]

    ordered = _diversify_owned_candidates(candidates)

    assert [item["id"] for item in ordered[:3]] == [1, 3, 4]
    assert ordered[-1]["id"] == 2


def test_formal_plan_drops_duplicate_visible_actions_instead_of_padding_duration():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"topic_brief_id": "test", "logistics_topic": "配送", "logistics_nodes": ["配送"]},
        [{"id": 1, "asset_id": 90, "hotspot_id": 12, "title_zh": "道路货车排队", "start_ms": 0, "end_ms": 7_000, "clip_status": "ready"}],
        [
            {"id": 1, "asset_id": 1, "asset_file_type": "video", "primary_category": "facility", "description": "叉车搬运包裹", "start_ms": 0, "end_ms": 7_000, "quality_score": .9, "tags": []},
            {"id": 2, "asset_id": 2, "asset_file_type": "video", "primary_category": "warehouse", "description": "叉车搬运包裹", "start_ms": 0, "end_ms": 7_000, "quality_score": .8, "tags": []},
            {"id": 3, "asset_id": 3, "asset_file_type": "video", "primary_category": "staff", "description": "工作人员检查包裹", "start_ms": 0, "end_ms": 7_000, "quality_score": .7, "tags": []},
        ],
        target_duration_ms=50_000,
    )

    anchors = [scene.get("copy_anchor") for scene in scenes if scene.get("scene_role") == "owned_proof"]
    assert anchors.count("叉车正在仓内搬运包裹。") == 1


def test_transport_topic_can_use_branded_truck_at_warehouse_without_reclassifying_it():
    from hotspot_video_planner import _owned_candidates

    brief = {"topic_brief_id": 1, "logistics_topic": "干线运输", "logistics_nodes": ["运输"]}
    branded_truck_at_warehouse = {
        "id": 145, "asset_id": 145, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "warehouse", "quality_score": 0.8,
        "tags": [
            {"dimension": "brand", "value": "Buffalo"},
            {"dimension": "scene", "value": "道路运输"},
            {"dimension": "object", "value": "卡车"},
        ],
    }
    warehouse_without_transport_evidence = {
        "id": 146, "asset_id": 146, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "warehouse", "quality_score": 0.9, "tags": [],
    }

    candidates = _owned_candidates([warehouse_without_transport_evidence, branded_truck_at_warehouse], brief)

    assert [item["id"] for item in candidates] == [145]


def test_transport_topic_admits_misclassified_warehouse_when_port_tags_prove_delivery():
    from hotspot_video_planner import _functional_categories, _owned_candidates

    brief = {"topic_brief_id": 1, "logistics_topic": "干线运输", "logistics_nodes": ["运输"]}
    mislabeled_port = {
        "id": 201, "asset_id": 201, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "warehouse", "quality_score": 0.7,
        "tags": [
            {"dimension": "scene", "value": "港口作业"},
            {"dimension": "object", "value": "集装箱"},
            {"dimension": "entity", "value": "港口"},
            {"dimension": "action", "value": "堆放"},
        ],
    }
    plain_warehouse = {
        "id": 202, "asset_id": 202, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "warehouse", "quality_score": 0.95, "tags": [],
    }

    assert "delivery" in _functional_categories(mislabeled_port)
    assert "delivery" not in _functional_categories(plain_warehouse)
    candidates = _owned_candidates([plain_warehouse, mislabeled_port], brief)
    assert [item["id"] for item in candidates] == [201]


def test_customs_node_still_rejects_port_tags_without_customs_evidence():
    from hotspot_video_planner import _owned_candidates

    brief = {"topic_brief_id": 1, "logistics_topic": "清关风险", "logistics_nodes": ["清关"]}
    port_only = {
        "id": 301, "asset_id": 301, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "warehouse", "quality_score": 0.9,
        "tags": [
            {"dimension": "scene", "value": "港口作业"},
            {"dimension": "object", "value": "集装箱"},
        ],
    }
    customs_clip = {
        "id": 302, "asset_id": 302, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "customs", "quality_score": 0.8,
        "tags": [{"dimension": "scene", "value": "清关"}],
    }

    candidates = _owned_candidates([port_only, customs_clip], brief)
    assert [item["id"] for item in candidates] == [302]


def test_transport_ranking_prefers_port_tag_overlap_over_generic_delivery_quality():
    from hotspot_video_planner import _owned_candidates

    brief = {"topic_brief_id": 1, "logistics_topic": "干线运输", "logistics_nodes": ["运输"]}
    generic_delivery = {
        "id": 401, "asset_id": 401, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "delivery", "quality_score": 0.95,
        "tags": [{"dimension": "brand", "value": "Buffalo"}],
    }
    port_delivery = {
        "id": 402, "asset_id": 402, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "delivery", "quality_score": 0.7,
        "tags": [
            {"dimension": "brand", "value": "Buffalo"},
            {"dimension": "scene", "value": "港口作业"},
            {"dimension": "object", "value": "集装箱"},
            {"dimension": "entity", "value": "船舶"},
        ],
    }

    candidates = _owned_candidates([generic_delivery, port_delivery], brief)
    assert [item["id"] for item in candidates] == [402, 401]


def test_road_disruption_can_use_warehouse_preparation_as_delivery_context():
    from hotspot_video_planner import _owned_candidates

    brief = {
        "topic_brief_id": 1, "logistics_topic": "道路异常下的配送安排",
        "logistics_nodes": ["运输", "配送"],
    }
    warehouse_preparation = {
        "id": 147, "asset_id": 147, "asset_file_type": "video", "asset_source": "upload",
        "primary_category": "warehouse", "quality_score": 0.8, "tags": [],
    }

    candidates = _owned_candidates([warehouse_preparation], brief)

    assert [item["id"] for item in candidates] == [147]


def test_dynamic_script_avoids_repeating_same_voiceover():
    from hotspot_video_planner import plan_followup_scenes

    brief = {
        "hotspot_title": "南非道路风险",
        "hotspot_summary": "道路通行受到影响",
        "hotspot_type": "risk",
        "logistics_topic": "末端配送安全",
        "angle": "道路风险如何影响末端配送？",
        "claim": "用真实运输与仓储画面说明安全流程",
        "source_asset_id": 90,
        "hotspot_id": 12,
    }
    events = [{"id": index, "asset_id": 90, "hotspot_id": 12, "title_zh": f"道路现场{index}", "clip_status": "ready"}
              for index in range(1, 4)]
    owned = [{"id": index, "asset_id": index, "asset_file_type": "video", "primary_category": category,
              "asset_name": f"{category}-{index}", "start_ms": 0, "end_ms": 10000, "quality_score": 0.8}
             for index, category in enumerate(["warehouse", "staff", "delivery", "facility", "warehouse"], 1)]

    scenes = plan_followup_scenes(brief, events, owned)

    assert len({scene["voiceover"] for scene in scenes}) == len(scenes)


def test_default_brand_endcard_is_limited_to_three_seconds():
    from hotspot_video_planner import BRAND_ENDCARD_SCENES

    assert BRAND_ENDCARD_SCENES[0]["duration_ms"] == 3_000


def test_user_selected_hook_is_first_hotspot_scene():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"hotspot_title": "边境卡车拥堵", "hotspot_type": "infrastructure", "primary_event_id": 3},
        [
            {"id": 1, "asset_id": 7, "hotspot_id": 3, "title_zh": "卡车排队", "keywords": ["卡车", "拥堵"], "clip_status": "ready"},
            {"id": 2, "asset_id": 7, "hotspot_id": 3, "title_zh": "口岸等待", "keywords": ["口岸", "拥堵"], "clip_status": "ready"},
            {"id": 3, "asset_id": 7, "hotspot_id": 3, "title_zh": "用户选择的现场 Hook", "keywords": ["卡车", "拥堵"], "clip_status": "ready"},
        ],
        [],
    )

    hotspot_scenes = [scene for scene in scenes if scene["evidence_type"] == "hotspot_video"]
    assert hotspot_scenes[0]["event_clip_id"] == 3
