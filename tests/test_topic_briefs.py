import asyncio
import asyncio
import json

import pytest
from fastapi.testclient import TestClient


def _client(tmp_db):
    import app
    import auth

    tmp_db.create_user("brief-owner", auth.hash_password("pw12345"), "editor", "Brief Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "brief-owner", "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_broad_topic_returns_angles_without_triggering_evidence_work(tmp_db):
    client, headers = _client(tmp_db)
    response = client.post("/api/topic-briefs", headers=headers, json={"raw_input": "南非物流"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["brief"]["status"] == "angle_only"
    assert len(payload["angles"]) == 3
    assert payload["generation_allowed"] is False


def test_explicit_topic_persists_structured_brief_and_only_reference_evidence(tmp_db):
    client, headers = _client(tmp_db)
    response = client.post("/api/topic-briefs", headers=headers, json={
        "raw_input": "面向刚进入南非市场的卖家，讲清关和末端配送风险",
        "audience": "刚进入南非市场的跨境卖家",
        "platforms": ["douyin"],
    })
    brief = response.json()["brief"]
    assert brief["angle"]
    assert {"清关", "末端", "配送"} <= set(brief["logistics_nodes"])

    retrieved = client.post(f"/api/topic-briefs/{brief['id']}/retrieve-evidence", headers=headers)
    assert retrieved.status_code == 200
    payload = retrieved.json()
    assert payload["coverage"]["status"] == "needs_review"
    assert payload["evidence"] == []


def test_custom_brief_overrides_legacy_fixed_topic_mapping():
    from hotspot_logistics_planner import build_brief

    brief = build_brief(
        {"title_zh": "约翰内斯堡道路安全事件", "summary_zh": "运输路线受到影响"}, [],
        {"id": "brief-1", "raw_input": "南非清关", "subject": "南非清关与末端配送风险",
         "angle": "先解释清关与末端两个节点的准备。", "audience": "跨境卖家",
         "logistics_nodes": ["customs", "last_mile"], "platforms": ["douyin"]},
    )

    assert brief["topic_brief_id"] == "brief-1"
    assert brief["logistics_topic"] == "南非清关与末端配送风险"
    assert "清关与末端" in brief["angle"]


def test_custom_topic_does_not_use_same_source_but_unrelated_hotspot_clip():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"topic_brief_id": "brief-1", "logistics_topic": "南非清关", "angle": "清关风险", "hotspot_type": "unknown"},
        [{"id": 1, "asset_id": 9, "hotspot_id": 9, "title_zh": "克鲁格国家公园野生动物", "clip_status": "ready"}],
        [{"id": 2, "asset_id": 2, "asset_file_type": "video", "primary_category": "warehouse", "asset_name": "Buffalo 仓库", "start_ms": 0, "end_ms": 10000}],
    )

    assert all(scene.get("event_clip_id") != 1 for scene in scenes)


def test_custom_customs_and_last_mile_brief_rejects_warehouse_and_generic_stock():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"topic_brief_id": "brief-1", "logistics_topic": "南非清关与末端配送风险",
         "logistics_nodes": ["清关", "末端"], "angle": "核对节点", "hotspot_type": "unknown"},
        [],
        [
            {"id": 1, "asset_id": 1, "asset_file_type": "video", "primary_category": "warehouse", "asset_name": "Buffalo 仓库", "asset_source": "upload"},
            {"id": 2, "asset_id": 299, "asset_file_type": "video", "primary_category": "delivery", "asset_name": "通用港口", "asset_source": "mixkit_license"},
        ],
    )

    assert scenes == []


def test_custom_last_mile_brief_can_use_delivery_and_explicit_pre_delivery_preparation():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"topic_brief_id": "brief-1", "logistics_topic": "南非末端配送", "logistics_nodes": ["末端"],
         "angle": "末端配送", "hotspot_type": "unknown"},
        [],
        [
            {"id": 1, "asset_id": 1, "asset_file_type": "video", "primary_category": "warehouse", "asset_name": "仓库", "asset_source": "upload"},
            {"id": 2, "asset_id": 2, "asset_file_type": "video", "primary_category": "delivery", "asset_name": "配送", "asset_source": "upload"},
        ],
    )

    owned_scenes = [scene for scene in scenes if scene.get("scene_role") == "owned_proof"]
    assert {scene["asset_segment_id"] for scene in owned_scenes} == {1, 2}
    assert len({scene["asset_id"] for scene in owned_scenes}) == len(owned_scenes)
    assert not any(scene["scene_role"] == "logistics_explainer" for scene in scenes)
    warehouse_scene = next(scene for scene in owned_scenes if scene["asset_segment_id"] == 1)
    assert "配送前" in warehouse_scene["voiceover"]


def test_planner_json_rejects_voiceover_that_exceeds_locked_visual_duration():
    import app

    content = '{"title":"标题","angle":"角度","scenes":[' \
        '{"voiceover":"现场卡车排队。","text_overlay":"现场"},' \
        '{"voiceover":"这是一句超过锁定镜头时长上限的很长旁白。","text_overlay":"过长"}' \
        ']}'

    with pytest.raises(ValueError, match="第 2 个分镜旁白超过 8 字时长上限"):
        app._planner_json(content, 2, [28, 8])


def test_short_confirmed_hook_uses_conservative_tts_character_budget():
    import app

    assert app._scene_voiceover_max_chars({"duration_ms": 6_800}) == 24
    assert app._scene_voiceover_min_chars({"duration_ms": 6_800}) == 14


def test_planner_json_rejects_voiceover_that_would_leave_a_silent_tail():
    import app

    content = (
        '{"title":"标题","angle":"角度","scenes":['
        '{"voiceover":"现场卡车仍在排队，请先核对订单状态。","text_overlay":"现场"},'
        '{"voiceover":"核对路线。","text_overlay":"核对"}'
        ']}'
    )

    with pytest.raises(ValueError, match="第 2 个分镜旁白少于 12 字时长下限"):
        app._planner_json(content, 2, [28, 28], [12, 12])


def test_formal_planner_context_exposes_narration_lower_and_upper_bounds():
    import app

    context = app._compact_topic_evidence(
        {"subject": "边境拥堵"}, {"title_zh": "现场"},
        [{"scene": 1, "scene_role": "hotspot_evidence", "duration_ms": 6_800, "visual": "货车"}],
    )

    assert context["allowed_scenes"][0]["voiceover_min_chars"] == 14
    assert context["allowed_scenes"][0]["voiceover_max_chars"] == 24


def test_short_formal_scene_allows_a_natural_five_character_line_without_stock_padding():
    import app

    assert app._scene_voiceover_min_chars({"duration_ms": 3_000}) == 5


def test_formal_planner_rejects_a_short_model_sentence_instead_of_adding_template_copy():
    import app

    with pytest.raises(ValueError, match="第 1 个分镜旁白少于 12 字时长下限"):
        app._extend_short_formal_voiceovers(
            {"title": "标题", "angle": "角度", "scenes": [{"voiceover": "现场卡车排队。", "text_overlay": "现场"}]},
            [{"scene_role": "hotspot_evidence"}], [12], [28],
        )


def test_formal_scene_copy_contract_replaces_ocr_and_route_guess_with_visible_actions():
    import app

    repaired = app._enforce_formal_scene_copy_contract(
        {"title": "标题", "angle": "角度", "scenes": [
            {"voiceover": "R60公路卡车侧翻，你的货走的是这条线吗？", "text_overlay": "R60"},
            {"voiceover": "拖车CEKEMACH18BUFFALO已装车待发。", "text_overlay": "拖车"},
        ]},
        [
            {"scene_role": "hotspot_evidence", "visual": "R60公路卡车侧翻现场"},
            {"scene_role": "owned_proof", "copy_anchor": "现场可见一辆待处理的拖车。"},
        ],
    )

    assert repaired["scenes"][0]["voiceover"] == "现场卡车侧翻在路边。这段现场只说明道路异常。"
    assert repaired["scenes"][1]["voiceover"] == "现场可见一辆待处理的拖车。"
    assert "CEKEMACH" not in repaired["scenes"][1]["voiceover"]


def test_formal_planner_compacts_an_overlong_model_clause_without_another_model_call():
    import app

    compacted = app._compact_long_formal_voiceovers(
        {"title": "标题", "angle": "角度", "scenes": [{
            "voiceover": "拖车驶入装车区就位，工作人员继续核对货物与发运安排。",
            "text_overlay": "拖车装车准备",
        }]},
        [18],
    )

    assert compacted["scenes"][0]["voiceover"] == "拖车驶入装车区就位。"
    assert len(compacted["scenes"][0]["voiceover"]) <= 18


def test_planner_json_requires_a_voiceover_for_every_locked_scene():
    import app

    content = '{"title":"标题","angle":"角度","scenes":[{"voiceover":"现场卡车排队。","text_overlay":"现场"}]}'

    with pytest.raises(ValueError, match="缺少标题、角度或有效分镜"):
        app._planner_json(content, 2, [28, 28])


def test_planner_json_rejects_unverified_hotspot_intensifier():
    import app

    content = (
        '{"title":"标题","angle":"角度","scenes":['
        '{"voiceover":"边境卡车堵死了，订单怎么办？","text_overlay":"边境拥堵"},'
        '{"voiceover":"车还在排队，重新核对路线。","text_overlay":"重新核对"}'
        ']}'
    )

    with pytest.raises(ValueError, match="夸张断言：堵死"):
        app._planner_json(content, 2, hotspot_scene_count=2)


def test_generate_topic_brief_uses_one_model_plan_for_a_verified_sixty_second_project(tmp_db, monkeypatch):
    import app
    import auth

    tmp_db.create_user("planner-owner", auth.hash_password("pw12345"), "editor", "Planner Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "planner-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa delivery disruption", "summary": "Delivery routes need attention",
        "source_url": "https://example.com/delivery", "publisher": "SA Today",
        "published_at": "2026-07-27T00:00:00Z", "retrieved_at": "2026-07-27T00:00:00Z",
        "snapshot_sha256": "topic-planner-hotspot",
    })
    hotspot_asset = tmp_db.create_asset({
        "name": "热点配送视频", "filepath": "assets/hotspot.mp4", "file_type": "video", "category": "other",
        "duration": 30, "size": 10, "source": "youtube", "status": "active", "sha256": "b" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(hotspot_asset, hotspot_id, [
        {"event_index": index, "start_ms": (index - 1) * 7000, "end_ms": index * 7000,
         "title_zh": f"配送现场 {index}", "title_en": "Delivery scene", "location": "Johannesburg",
         "segments": [], "confidence": .9, "review_status": "confirmed"}
        for index in range(1, 4)
    ])[0]
    for index in range(1, 6):
        asset_id = tmp_db.create_asset({
            "name": f"Buffalo 配送 {index}", "filepath": f"assets/delivery-{index}.mp4", "file_type": "video",
            "category": "delivery", "primary_category": "delivery", "duration": 10, "size": 10,
            "source": "upload", "status": "active", "sha256": f"{index:x}" * 64,
        })
        tmp_db.create_asset_segment({"asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 10000,
                                     "primary_category": "delivery", "quality_score": .9})
    created = client.post("/api/topic-briefs", headers=headers, json={
        "raw_input": "面向南非卖家说明末端配送风险", "logistics_nodes": ["末端"], "platforms": ["douyin"],
    }).json()["brief"]
    monkeypatch.setattr(app.model_router, "key_is_available", lambda role: role == "planner_text")
    captured = {}

    async def fake_call(*args, **kwargs):
        captured["messages"] = args[2]
        captured["prompt_version"] = kwargs["prompt_version"]
        return {"content": '{"title":"配送风险先看哪里","angle":"从现场变化看末端准备","scenes":[' + ','.join(
                '{"voiceover":"第%d段：先看现场卡车排队，再核对订单、路线、仓内分拣和配送前准备。","text_overlay":"配送核对"}' % index for index in range(1, 8)
        ) + ']}', "cache_hit": False, "usage": {"input_tokens": 300, "output_tokens": 240}}

    monkeypatch.setattr(app.model_router, "call_text", fake_call)
    response = client.post(f"/api/topic-briefs/{created['id']}/generate", headers=headers, json={
        "hotspot_event_id": event["id"], "platform": "douyin", "target_duration_ms": 60000,
    })

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["coverage"] == {"hotspot_video": 2, "owned_video": 5, "duration_ms": 52000}
    revision = payload["project"]["current_revision"]
    assert len(revision["payload"]["scenes"]) == 8
    assert revision["payload"]["scenes"][0]["voiceover"].startswith("第1段")
    assert captured["prompt_version"] == "topic-brief-video-plan-v10"
    assert "一线物流同行" in captured["messages"][0]["content"]
    assert json.loads(payload["project"]["source_snapshot"])["copywriting_sop"] == {
        "id": "south-africa-logistics-douyin-copy-style", "version": "v2",
    }


def test_chat_dual_library_route_persists_one_locked_hook_as_a_complete_project(tmp_db, monkeypatch):
    import app
    import auth

    tmp_db.create_user("chat-dual-owner", auth.hash_password("pw12345"), "editor", "Chat Dual Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "chat-dual-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "南非口岸卡车排队影响货运", "summary": "货运卡车等待口岸筛查和放行",
        "source_url": "https://example.com/chat-dual", "publisher": "SA Today",
        "published_at": "2026-07-29T00:00:00Z", "retrieved_at": "2026-07-29T00:00:00Z",
        "snapshot_sha256": "chat-dual-end-to-end",
    })
    hotspot_asset = tmp_db.create_asset({
        "name": "口岸卡车母片", "filepath": "assets/chat-dual.mp4", "file_type": "video", "category": "other",
        "duration": 30, "size": 10, "source": "youtube", "status": "active", "sha256": "d" * 64,
    })
    events = tmp_db.replace_hotspot_event_clips(hotspot_asset, hotspot_id, [
        {"event_index": 1, "start_ms": 0, "end_ms": 7_000, "title_zh": "货运卡车排队等待筛查", "title_en": "Truck queue",
         "segments": [], "confidence": .9, "review_status": "confirmed",
             "evidence": {"what_happened": "货运卡车在口岸入口排队等待筛查", "hook_reason": "现场排队画面清晰", "logistics_question": "等待会先影响哪个订单节点？", "event_identity": "口岸入口货车排队等待筛查"}},
        {"event_index": 2, "start_ms": 10_000, "end_ms": 17_000, "title_zh": "货运卡车等待放行", "title_en": "Truck clearance",
         "segments": [], "confidence": .9, "review_status": "confirmed",
             "evidence": {"what_happened": "另一批货运卡车仍在口岸入口等待放行", "hook_reason": "等待过程可直接核验", "logistics_question": "卖家应预留哪些履约判断空间？", "event_identity": "口岸入口货车排队等待筛查"}},
    ])
    for event in events:
        tmp_db.update_hotspot_event_clip_media(event["id"], f"assets/hotspot-events/{event['id']}/event.mp4", None, "ready")
    for index in range(1, 7):
        asset_id = tmp_db.create_asset({
            "name": f"Buffalo 配送镜头 {index}", "filepath": f"assets/chat-dual-owned-{index}.mp4", "file_type": "video",
            "category": "delivery", "primary_category": "delivery", "duration": 10, "size": 10,
            "source": "upload", "status": "active", "sha256": f"{index:x}" * 64,
        })
        tmp_db.create_asset_segment({"asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 10_000,
                                     "primary_category": "delivery", "quality_score": .9})
    async def no_model_in_request(*_args, **_kwargs):
        raise AssertionError("创建聊天视频任务不应调用模型")

    monkeypatch.setattr(app.model_router, "call_text", no_model_in_request)
    response = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "港口", "hotspot_event_ids": [events[0]["id"]],
        "platform": "douyin", "target_duration_ms": 60_000, "session_id": "chat-dual-e2e",
    })

    assert response.status_code == 202, response.json()
    payload = response.json()
    assert payload["created"] is True
    assert payload["job"]["status"] == "pending"
    assert payload["job"]["stage"] == "queued"
    assert payload["project"]["active_job_id"] == payload["job_id"]

    repeated = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "港口", "hotspot_event_ids": [events[0]["id"]],
        "platform": "douyin", "target_duration_ms": 60_000, "session_id": "chat-dual-e2e",
    })
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert repeated.json()["job_id"] == payload["job_id"]

    async def fake_call(*_args, **_kwargs):
        scenes = ','.join(
            '{"voiceover":"第%d段：先看口岸现场卡车排队，再核对订单、路线、仓内分拣和配送前准备。","text_overlay":"配送核对"}' % index
            for index in range(1, 8)
        )
        return {"content": f'{{"title":"口岸等待先看哪里","angle":"从现场变化看履约准备","scenes":[{scenes}]}}',
                "cache_hit": False, "usage": {"input_tokens": 300, "output_tokens": 240}}

    monkeypatch.setattr(app.model_router, "call_text", fake_call)
    monkeypatch.setattr(app.model_router, "key_is_available", lambda role: role == "planner_text")
    handlers = app._build_video_generation_handlers(app.STATIC_DIR)
    job = tmp_db.get_video_generation_job(payload["job_id"])
    for expected_stage in (
        app.video_generation.PipelineStage.TOPIC_BRIEF,
        app.video_generation.PipelineStage.HOOK_LOCKING,
        app.video_generation.PipelineStage.SCRIPTING,
        app.video_generation.PipelineStage.PROJECT_BUILDING,
    ):
        current_stage = app.video_generation.PipelineStage(job["stage"])
        returned_stage = asyncio.run(handlers[current_stage](job))
        assert returned_stage == expected_stage
        job = tmp_db.update_video_generation_job(job["id"], stage=returned_stage.value)
    project = tmp_db.get_video_project(payload["project"]["id"], created_by=job["created_by"])
    snapshot = json.loads(project["source_snapshot"])
    assert snapshot["matched_event_clip_ids"] == [events[0]["id"]]
    assert snapshot["logistics_nodes"] == ["运输", "配送"]
    assert project["current_revision"]["payload"]["brief"]["approved_hook_event_ids"] == [events[0]["id"]]
    hotspot_scene_ids = [
        scene["event_clip_id"]
        for scene in project["current_revision"]["payload"]["scenes"]
        if scene.get("evidence_type") == "hotspot_video"
    ]
    assert hotspot_scene_ids == [events[0]["id"]]


def test_takealot_topic_cannot_be_replaced_by_a_hotspot_headline():
    import app

    brief = {
        "raw_input": "Takealot真正拼的不是低价，而是库存、配送和用户体验",
        "subject": "Takealot真正拼的不是低价，而是库存、配送和用户体验",
    }
    replaced = {
        "title": "南非边境卡车滞留，卖家如何核对流程？",
        "scenes": [{"voiceover": "现场卡车排队，通行正在受影响。"}],
    }
    with pytest.raises(ValueError, match="标题没有回应用户主题"):
        app._validate_generated_topic_anchor(replaced, brief)

    accepted = {
        "title": "Takealot拼的，是库存、配送和用户体验",
        "scenes": [{"voiceover": "库存准备和配送节奏，决定用户体验。"}],
    }
    app._validate_generated_topic_anchor(accepted, brief)


def test_formal_planner_rejects_empty_short_slogans_before_project_creation():
    import app

    with pytest.raises(ValueError, match="脱离画面的空泛短句"):
        app._validate_formal_copy_specificity({
            "scenes": [{"voiceover": "库存要对得上。"}],
        })


def test_followup_planner_can_expand_a_verified_project_to_ninety_seconds():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"topic_brief_id": "brief-1", "logistics_topic": "南非本地配送", "logistics_nodes": ["配送"],
         "hotspot_type": "infrastructure"},
        [{"id": index, "asset_id": 8, "hotspot_id": 5, "title_zh": "Musina 路段交通拥堵", "keywords": ["traffic"],
          "clip_status": "ready"} for index in range(1, 4)],
        [{"id": 10, "asset_id": 11, "asset_file_type": "video", "primary_category": "delivery",
          "asset_name": "Buffalo 配送现场", "asset_source": "upload", "start_ms": 0, "end_ms": 10_000}],
        target_duration_ms=90_000,
    )

    # 素材不足以支撑 90 秒时，规划器宁可返回不可发布的短计划，也绝不循环
    # 同一条 Buffalo 或热点原视频；上层证据门禁会据此要求补充素材。
    assert sum(scene["duration_ms"] for scene in scenes) < 50_000
    assert len({scene["asset_id"] for scene in scenes if scene.get("scene_role") == "owned_proof"}) == 1
    assert scenes[0]["voiceover"].startswith("Musina 现场，筛查让卡车排起长队")


def test_followup_planner_allows_two_model_curated_hooks_without_inventing_a_third():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"hotspot_title": "城市清运中断", "hotspot_id": 3, "hotspot_type": "infrastructure"},
        [
            {"id": 1, "asset_id": 7, "hotspot_id": 3, "title_zh": "街道垃圾堆积", "keywords": ["垃圾", "街道"], "clip_status": "ready"},
            {"id": 2, "asset_id": 7, "hotspot_id": 3, "title_zh": "居民翻找物品", "keywords": ["垃圾", "居民"], "clip_status": "ready"},
        ],
        [{"id": 10, "asset_id": 11, "asset_file_type": "video", "primary_category": "warehouse",
          "asset_name": "Buffalo 仓库", "asset_source": "upload", "start_ms": 0, "end_ms": 10_000}],
    )

    hotspot_scenes = [scene for scene in scenes if scene["evidence_type"] == "hotspot_video"]
    assert [scene["event_clip_id"] for scene in hotspot_scenes] == [1, 2]
    assert len(hotspot_scenes) == 2


def test_followup_planner_keeps_all_explicitly_approved_hooks_without_rekeyword_filtering():
    from hotspot_video_planner import plan_followup_scenes

    scenes = plan_followup_scenes(
        {"hotspot_title": "边境货运拥堵", "approved_hook_event_ids": [1, 2]},
        [
            {"id": 1, "asset_id": 7, "hotspot_id": 3, "title_zh": "卡车拥堵", "clip_status": "ready"},
            {"id": 2, "asset_id": 7, "hotspot_id": 3, "title_zh": "边境卡车排长龙", "clip_status": "ready"},
        ],
        [{"id": 10, "asset_id": 11, "asset_file_type": "video", "primary_category": "warehouse",
          "asset_name": "Buffalo 仓库", "asset_source": "upload", "start_ms": 0, "end_ms": 10_000}],
    )

    assert [scene["event_clip_id"] for scene in scenes if scene["evidence_type"] == "hotspot_video"] == [1, 2]


def test_hotspot_planner_uses_field_subclip_and_replaces_generic_event_title(tmp_db):
    from hotspot_video_planner import plan_followup_scenes

    asset_id = tmp_db.create_asset({
        "name": "SABC 拥堵报道", "filepath": "assets/news.mp4", "file_type": "video", "category": "other",
        "duration": 90, "size": 10, "source": "upload", "status": "active", "sha256": "d" * 64,
    })
    tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 8_000,
        "primary_category": "other", "description": "新闻主播播报", "quality_score": .8,
    })
    tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 1, "start_ms": 8_000, "end_ms": 16_000,
        "primary_category": "delivery", "description": "多辆卡车在道路上排队造成交通拥堵", "quality_score": .8,
    })
    scenes = plan_followup_scenes(
        {"hotspot_title": "Traffic congestion near Musina due to screening", "hotspot_type": "infrastructure"},
        [{"id": index, "asset_id": asset_id, "hotspot_id": 1, "start_ms": 0, "end_ms": 14_000,
          "title_zh": "待确认事件 01", "keywords": ["traffic", "congestion"], "clip_status": "ready"}
         for index in range(1, 4)],
        [],
    )

    assert scenes[0]["asset_start_ms"] == 8_000
    assert scenes[0]["asset_end_ms"] == 14_000
    assert "你的订单，还能按原计划走吗" in scenes[0]["voiceover"]
    assert "现场子片段" in scenes[0]["match_reasons"][-1]


def test_rag_recommender_finds_contextual_marketing_hook_without_claiming_brand_results(tmp_db):
    import app
    import auth

    tmp_db.create_user("recommend-owner", auth.hash_password("pw12345"), "editor", "Recommend Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "recommend-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Beitbridge border trucks queue for screening",
        "summary": "Freight trucks wait at the border screening gate",
        "source_url": "https://example.com/port", "publisher": "SA Today",
        "published_at": "2026-07-27T00:00:00Z", "retrieved_at": "2026-07-27T00:00:00Z",
        "snapshot_sha256": "rag-hotspot",
    })
    asset_id = tmp_db.create_asset({
        "name": "边境排队母片", "filepath": "assets/rag-port.mp4", "file_type": "video",
        "category": "other", "duration": 20, "size": 1, "source": "youtube",
        "status": "active", "sha256": "b" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 7_000,
        "title_zh": "边境货车排队等待筛查", "title_en": "Border truck queue",
        "segments": [], "confidence": 0.9, "review_status": "confirmed",
        "evidence": {
            "what_happened": "货运卡车在边境筛查入口排队等待",
            "hook_reason": "排队现场清晰可见",
            "logistics_question": "边境等待会先影响哪个履约节点？",
            "event_identity": "边境入口货车排队等待筛查",
        },
    }])[0]
    tmp_db.update_hotspot_event_clip_media(event["id"], f"assets/hotspot-events/{event['id']}/event.mp4", None, "ready")
    brief = client.post("/api/topic-briefs", headers=headers, json={
        "raw_input": "南非边境拥堵时卖家应先核对哪些履约节点", "platforms": ["douyin"],
    }).json()["brief"]

    response = client.post(f"/api/topic-briefs/{brief['id']}/recommend-hotspots", headers=headers, json={"use_model": False})

    assert response.status_code == 200
    recommendations = response.json()["recommendations"]
    assert recommendations
    candidate = recommendations[0]
    assert candidate.get("can_render_video") is True
    assert "热点只用于提出问题" in candidate["usage_boundary"]
    assert "只有该 Hook 是当前主题最强事实现场时才复用" in candidate["reuse_policy"]


def test_rag_recommender_returns_empty_when_no_renderable_hooks(tmp_db):
    import app
    import auth

    tmp_db.create_user("empty-recommend-owner", auth.hash_password("pw12345"), "editor", "Empty Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "empty-recommend-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tmp_db.upsert_hotspot({
        "title": "Port infrastructure disruption in South Africa", "summary": "Port and road routes need attention",
        "source_url": "https://example.com/port-empty", "publisher": "SA Today",
        "published_at": "2026-07-27T00:00:00Z", "retrieved_at": "2026-07-27T00:00:00Z",
        "snapshot_sha256": "rag-hotspot-empty",
    })
    brief = client.post("/api/topic-briefs", headers=headers, json={
        "raw_input": "为进入南非市场的卖家讲物流风险", "platforms": ["douyin"],
    }).json()["brief"]

    response = client.post(f"/api/topic-briefs/{brief['id']}/recommend-hotspots", headers=headers, json={"use_model": False})

    assert response.status_code == 200
    assert response.json()["recommendations"] == []


def test_model_hook_reason_is_rebuilt_from_grounded_hotspot_and_hook_evidence(tmp_db, monkeypatch):
    import app
    import auth

    tmp_db.create_user("grounded-recommend-owner", auth.hash_password("pw12345"), "editor", "Grounded Owner")
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Road congestion", "summary": "Traffic delays", "source_url": "https://example.com/road",
        "publisher": "SA Today", "published_at": "2026-07-28T00:00:00Z", "retrieved_at": "2026-07-28T00:00:00Z",
        "snapshot_sha256": "grounded-model-reason",
    })
    candidate = {
        "hotspot_id": hotspot_id, "title": "Road congestion", "hook_clips": [
            {"event_clip_id": 7, "content_description": "卡车在道路排队"},
            {"event_clip_id": 8, "content_description": "道路拥堵现场"},
            {"event_clip_id": 9, "content_description": "货车缓慢通行"},
        ],
    }
    monkeypatch.setattr(app.model_router, "key_is_available", lambda _role: True)
    captured = {}

    async def fake_call(*args, **kwargs):
        captured["messages"] = args[2]
        captured["prompt_version"] = kwargs["prompt_version"]
        return {"content": '{"recommendations":[{"hotspot_id":%d,"hook_event_ids":[7,8,9],"marketing_question":"会影响什么？","why":"虚构的边境与服务成果"}]}' % hotspot_id,
                "cache_hit": False, "usage": {"input_tokens": 10, "output_tokens": 10}}

    monkeypatch.setattr(app.model_router, "call_text", fake_call)
    selected, meta = __import__("asyncio").run(app._model_decide_marketing_hooks(
        {"id": "grounded-brief"}, [candidate], "", [], 1,
    ))

    assert meta["used"] is True
    assert meta["prompt_version"] == "topic-content-decision-v3"
    assert captured["prompt_version"] == "topic-content-decision-v3"
    assert "不要把同一个 Hook 当作通用开场模板" in captured["messages"][0]["content"]
    assert "最强事实现场时才可复用" in captured["messages"][0]["content"]
    assert "虚构" not in selected[0]["why"]
    assert "卡车在道路排队" in selected[0]["why"]


def test_model_decision_keeps_two_grounded_hooks_renderable(tmp_db, monkeypatch):
    import app

    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Port queue", "summary": "Freight vehicles wait at the entrance",
        "source_url": "https://example.com/port-queue", "publisher": "SA Today",
        "published_at": "2026-07-28T00:00:00Z", "retrieved_at": "2026-07-28T00:00:00Z",
        "snapshot_sha256": "two-grounded-hooks",
    })
    candidate = {
        "hotspot_id": hotspot_id,
        "title": "Port queue",
        "hook_clips": [
            {"event_clip_id": 41, "content_description": "货车在港口入口排队"},
            {"event_clip_id": 42, "content_description": "工作人员检查货运车辆"},
        ],
    }
    monkeypatch.setattr(app.model_router, "key_is_available", lambda _role: True)

    async def fake_call(*_args, **_kwargs):
        return {"content": '{"recommendations":[{"hotspot_id":%d,"hook_event_ids":[41,42],"marketing_question":"会怎样影响出库？","why":"模型理由"}]}' % hotspot_id,
                "cache_hit": False, "usage": {"input_tokens": 10, "output_tokens": 10}}

    monkeypatch.setattr(app.model_router, "call_text", fake_call)
    selected, _meta = __import__("asyncio").run(app._model_decide_marketing_hooks(
        {"id": "two-hook-brief"}, [candidate], "", [], 1,
    ))

    assert len(selected[0]["hook_clips"]) == 2
    assert selected[0]["can_render_video"] is True
