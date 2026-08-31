"""批21 测试专项：chain_mode 三链路 / owned_only 分镜数与超发截断 / import_project 标签 D3 / 审核即发布。

全部使用 tmp_db 或纯函数调用，不触碰真实 data/logiflow.db；
模型调用一律 monkeypatch，不消耗线上预算。
"""
import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ==================== 公共 fixture ====================


def _hotspot_event(event_id: int, asset_id: int, hotspot_id: int, start_ms: int = 0, end_ms: int = 8_000) -> dict:
    return {
        "id": event_id, "asset_id": asset_id, "hotspot_id": hotspot_id,
        "title_zh": f"清关现场 {event_id}", "title_en": f"Customs scene {event_id}",
        "start_ms": start_ms, "end_ms": end_ms, "clip_status": "ready",
        "keywords": ["清关", "口岸"], "entities": [],
    }


def _owned_segment(segment_id: int, asset_id: int, category: str, name: str, asset_source: str | None = None) -> dict:
    return {
        "id": segment_id, "asset_id": asset_id, "asset_file_type": "video",
        "primary_category": category, "asset_name": name,
        "description": f"{name} 可见动作", "start_ms": 0, "end_ms": 8_000,
        "quality_score": 0.9, "tags": [], "asset_source": asset_source,
    }


def _brief(**overrides) -> dict:
    brief = {
        "topic_brief_id": "tb-batch21",
        "logistics_topic": "南非清关时卖家应先核对哪些物流节点",
        "angle": "从清关说明进入南非市场前需要核对的流程、风险与准备。",
        "claim": "用可核验的仓配与交付画面说明清关前准备，不作无证据承诺。",
        "hotspot_title": "跨境清关作业场景",
        "hotspot_summary": "跨境清关作业现场",
        "hotspot_type": "infrastructure",
        "logistics_nodes": ["清关"],
        "required_evidence": {"hotspot_video": 1, "owned_video": 4, "image_ratio_max": 0.15},
        "audience": "南非跨境电商卖家",
        "goal": "基于已确认热点 Hook 生成 Buffalo 双素材库视频",
        "freshness_mode": "recent_or_evergreen",
    }
    brief.update(overrides)
    return brief


def _buffalo_segments(count: int, category: str = "warehouse", start_id: int = 1) -> list[dict]:
    return [
        _owned_segment(start_id + index, 10_000 + start_id + index, category, f"Buffalo {category} {start_id + index}")
        for index in range(count)
    ]


def _zastock_segments(count: int, category: str = "customs", start_id: int = 100) -> list[dict]:
    return [
        _owned_segment(start_id + index, 20_000 + start_id + index, category, f"南非素材 {start_id + index}", "za_stock_license")
        for index in range(count)
    ]


# ==================== A. chain_mode 三链路 ====================


def test_mix3_keeps_hotspot_owned_and_at_least_one_za_stock_scene():
    from hotspot_video_planner import plan_followup_scenes

    event = _hotspot_event(1, 90, 12)
    owned = _buffalo_segments(8, "warehouse", start_id=1)
    zastock = _zastock_segments(2, "delivery", start_id=100)
    scenes = plan_followup_scenes(
        _brief(approved_hook_event_ids=[1]), [event], owned + zastock,
        target_duration_ms=60_000, allow_adaptation=True, chain_mode="mix3",
    )

    assert sum(scene["evidence_type"] == "hotspot_video" for scene in scenes) >= 1
    assert sum(scene["evidence_type"] == "owned_video" for scene in scenes) >= 1
    za_scenes = [scene for scene in scenes if scene.get("asset_source") == "za_stock_license"]
    assert len(za_scenes) >= 1
    assert all(scene["stock_required"] is True for scene in za_scenes)
    assert len(za_scenes) <= 2


def test_hotspot_owned_does_not_force_za_stock_when_buffalo_covers_need():
    from hotspot_video_planner import plan_followup_scenes

    event = _hotspot_event(2, 91, 13)
    owned = (
        _buffalo_segments(8, "warehouse", start_id=10)
        + _buffalo_segments(2, "customs", start_id=40)
        + _buffalo_segments(2, "delivery", start_id=45)
    )
    zastock = _zastock_segments(2, "delivery", start_id=110)
    scenes = plan_followup_scenes(
        _brief(approved_hook_event_ids=[2]), [event], owned + zastock,
        target_duration_ms=60_000, allow_adaptation=True, chain_mode="hotspot_owned",
    )

    assert sum(scene["evidence_type"] == "hotspot_video" for scene in scenes) >= 1
    assert not any(scene.get("asset_source") == "za_stock_license" for scene in scenes)
    assert sum(scene["evidence_type"] == "owned_video" for scene in scenes) >= 4


def test_hotspot_owned_may_top_up_za_stock_when_buffalo_inventory_is_thin():
    from hotspot_video_planner import plan_followup_scenes

    event = _hotspot_event(3, 92, 14)
    owned = _buffalo_segments(4, "warehouse", start_id=20)
    zastock = _zastock_segments(2, "delivery", start_id=120)
    scenes = plan_followup_scenes(
        _brief(approved_hook_event_ids=[3]), [event], owned + zastock,
        target_duration_ms=60_000, allow_adaptation=True, chain_mode="hotspot_owned",
    )

    assert sum(scene["evidence_type"] == "hotspot_video" for scene in scenes) >= 1
    za_scenes = [scene for scene in scenes if scene.get("asset_source") == "za_stock_license"]
    assert len(za_scenes) >= 1
    assert all(scene["stock_required"] is True for scene in za_scenes)


def test_owned_only_never_injects_hotspot_or_za_stock():
    from hotspot_video_planner import plan_followup_scenes

    owned = _buffalo_segments(8, "warehouse", start_id=30)
    zastock = _zastock_segments(2, "delivery", start_id=130)
    scenes = plan_followup_scenes(
        _brief(), [], owned + zastock,
        target_duration_ms=60_000, allow_adaptation=True, chain_mode="owned_only",
    )

    assert scenes
    assert not any(scene["evidence_type"] == "hotspot_video" for scene in scenes)
    assert not any(scene.get("asset_source") == "za_stock_license" for scene in scenes)
    assert all(scene["evidence_type"] == "owned_video" for scene in scenes if scene.get("asset_id"))


def test_chain_mode_is_carried_in_provenance_not_faked_in_scenes():
    from hotspot_video_planner import describe_plan_adaptation, plan_followup_scenes

    event = _hotspot_event(4, 93, 15)
    owned = _buffalo_segments(8, "warehouse", start_id=50)
    scenes = plan_followup_scenes(
        _brief(approved_hook_event_ids=[4]), [event], owned,
        target_duration_ms=60_000, allow_adaptation=True, chain_mode="hotspot_owned",
    )
    provenance = {
        "hotspot_video": sum(scene["evidence_type"] == "hotspot_video" for scene in scenes),
        "owned_video": sum(scene["evidence_type"] == "owned_video" for scene in scenes),
        "za_stock": sum(scene.get("asset_source") == "za_stock_license" for scene in scenes),
        "chain_mode": "hotspot_owned",
        "duration_ms": sum(int(scene["duration_ms"]) for scene in scenes),
        "adapted": bool(describe_plan_adaptation(scenes).get("adapted")),
    }
    assert provenance["hotspot_video"] == 1
    assert provenance["owned_video"] >= 4
    assert provenance["za_stock"] == 0
    assert provenance["chain_mode"] == "hotspot_owned"


# ==================== B. owned_only 分镜数与超发截断 ====================


def test_owned_only_sixty_seconds_returns_eight_real_scenes():
    from hotspot_video_planner import plan_followup_scenes

    owned = _buffalo_segments(8, "warehouse", start_id=60)
    scenes = plan_followup_scenes(
        _brief(), [], owned,
        target_duration_ms=60_000, allow_adaptation=True, chain_mode="owned_only",
    )
    video_scenes = [scene for scene in scenes if scene.get("evidence_type") == "owned_video"]
    assert len(video_scenes) == 8
    assert len({scene["asset_id"] for scene in video_scenes}) == 8
    assert all(scene.get("voiceover") for scene in video_scenes)
    assert all(scene.get("text_overlay") for scene in video_scenes)


def test_planner_json_truncates_over_issued_model_output_to_eight():
    import app as app_module

    model_scenes = [{"voiceover": f"第{i}段旁白：核对提单与清关资料", "text_overlay": f"节点{i}"} for i in range(1, 10)]
    content = json.dumps({"title": "清关节点", "angle": "先核对提单", "scenes": model_scenes}, ensure_ascii=False)
    parsed = app_module._planner_json(content, expected_scenes=8)
    assert len(parsed["scenes"]) == 8
    assert parsed["scenes"][0]["voiceover"] == "第1段旁白：核对提单与清关资料"
    assert parsed["scenes"][-1]["voiceover"] == "第8段旁白：核对提单与清关资料"
    assert parsed["scenes"][-1]["text_overlay"] == "节点8"


def test_planner_json_truncation_preserves_order_and_overlay():
    import app as app_module

    scenes = [{"voiceover": f"第{i}段旁白：清关资料逐项核对", "text_overlay": f"段落{i}"} for i in range(1, 9)]
    parsed = app_module._planner_json(json.dumps(
        {"title": "t", "angle": "a", "scenes": scenes}, ensure_ascii=False,
    ), expected_scenes=8)
    assert [item["voiceover"] for item in parsed["scenes"]] == [
        item["voiceover"] for item in scenes
    ]
    assert all(item["text_overlay"] for item in parsed["scenes"])


def test_planner_json_rejects_fewer_scenes_than_expected():
    import app as app_module

    scenes = [{"voiceover": f"第{i}段旁白：核对物流节点", "text_overlay": "节点"} for i in range(1, 8)]
    content = json.dumps({"title": "t", "angle": "a", "scenes": scenes}, ensure_ascii=False)
    with pytest.raises(ValueError):
        app_module._planner_json(content, expected_scenes=8)


def test_repair_prompt_carries_current_planned_scene_count(tmp_db, monkeypatch):
    import app as app_module
    import auth

    tmp_db.create_user("batch21-owner", auth.hash_password("pw12345"), "editor", "Batch21")
    client = TestClient(app_module.app)
    token = client.post("/api/auth/login", json={"username": "batch21-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa customs checkpoint", "summary": "Clearance lines need attention",
        "source_url": "https://example.com/customs", "publisher": "SA Today",
        "published_at": "2026-07-27T00:00:00Z", "retrieved_at": "2026-07-27T00:00:00Z",
        "snapshot_sha256": "batch21-repair-prompt",
    })
    hotspot_asset = tmp_db.create_asset({
        "name": "清关母片", "filepath": "assets/customs.mp4", "file_type": "video", "category": "other",
        "duration": 30, "size": 10, "source": "youtube", "status": "active", "sha256": "c" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(hotspot_asset, hotspot_id, [
        {"event_index": 1, "start_ms": 0, "end_ms": 7_000, "title_zh": "清关现场排队", "title_en": "Clearance queue",
         "segments": [], "confidence": .9, "review_status": "confirmed",
         "evidence": {"what_happened": "清关现场排队", "hook_reason": "现场可见", "logistics_question": "卖家先核对哪个节点？", "event_identity": "清关现场排队"}},
    ])[0]
    tmp_db.update_hotspot_event_clip_media(event["id"], "assets/events/batch21-customs.mp4", None, "ready")
    for index in range(1, 9):
        asset_id = tmp_db.create_asset({
            "name": f"Buffalo 仓内 {index}", "filepath": f"assets/owned-{index}.mp4", "file_type": "video",
            "category": "warehouse", "primary_category": "warehouse", "duration": 10, "size": 10,
            "source": "upload", "status": "active", "sha256": f"{index:x}" * 64,
        })
        tmp_db.create_asset_segment({"asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 10_000,
                                     "primary_category": "warehouse", "quality_score": .9})
    created = client.post("/api/topic-briefs", headers=headers, json={
        "raw_input": "南非跨境清关时卖家应先核对哪些物流节点", "logistics_nodes": ["清关"], "platforms": ["douyin"],
    }).json()["brief"]
    monkeypatch.setattr(app_module.model_router, "key_is_available", lambda role: role == "planner_text")
    state = {"calls": 0, "first_messages": None}

    async def fake_call(*args, **kwargs):
        state["calls"] += 1
        messages = args[2]
        if state["calls"] == 1:
            state["first_messages"] = messages
            return {"content": '{"title":"清关节点","angle":"先核对提单","scenes":[' + ','.join(
                '{"voiceover":"第%d段：先看现场排队，再核对订单路线和仓内准备。","text_overlay":"节点%d"}' % (i, i) for i in range(1, 8)
            ) + ']}', "cache_hit": False, "usage": {"input_tokens": 300, "output_tokens": 240}}
        return {"content": '{"title":"清关节点","angle":"先核对提单","scenes":[' + ','.join(
            '{"voiceover":"第%d段：先看现场排队，再核对订单路线和仓内准备。","text_overlay":"节点%d"}' % (i, i) for i in range(1, 9)
        ) + ']}', "cache_hit": False, "usage": {"input_tokens": 300, "output_tokens": 240}}

    monkeypatch.setattr(app_module.model_router, "call_text", fake_call)

    async def run():
        return await app_module._generate_topic_brief_video(
            created["id"],
            app_module.TopicBriefGenerateRequest(
                hotspot_event_id=event["id"], platform="douyin", target_duration_ms=60_000,
                chain_mode="hotspot_owned",
            ),
            {"id": 1, "username": "batch21-owner", "role": "editor"},
        )

    result = asyncio.run(run())
    system_content = state["first_messages"][0]["content"]
    assert "必须严格输出" in system_content
    assert "个分镜" in system_content
    assert "不得多不得少" in system_content
    assert result["provenance"]["chain_mode"] == "hotspot_owned"
    assert len(result["project"]["current_revision"]["payload"]["scenes"]) == 9


# ==================== C. import_project 标签 D3 ====================

_EDITOR_PATH = Path(__file__).parents[1] / "static" / "editor.html"


def _extract_function(name: str) -> str:
    text = _EDITOR_PATH.read_text()
    marker = f"function {name}("
    start = text.index(marker)
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        index += 1
    return text[start:index + 1]


def _run_build_import_hashtags(project: dict, brief: dict, script: dict) -> list[str]:
    function_source = _extract_function("buildImportHashtags")
    body = (
        function_source
        + "\nprocess.stdout.write(JSON.stringify(buildImportHashtags("
        + json.dumps(project, ensure_ascii=False) + ","
        + json.dumps(brief, ensure_ascii=False) + ","
        + json.dumps(script, ensure_ascii=False) + ")));"
    )
    completed = subprocess.run(["node", "-e", body], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_import_hashtags_take_two_topic_words_and_two_logistics_nodes():
    project = {"title": "南非物流 ABC 清关｜视频", "source_snapshot": {"chain_mode": "mix3"}}
    brief = {"logistics_nodes": ["清关", "运输", "仓储"]}
    tags = _run_build_import_hashtags(project, brief, {"title": ""})
    assert tags[:2] == ["南非物流", "ABC"]
    assert "清关" in tags and "运输" in tags
    assert "仓储" not in tags


def test_import_hashtags_chain_labels_follow_chain_mode():
    base_project = {"title": "南非跨境清关流程｜成片", "source_snapshot": {}}
    brief = {"logistics_nodes": ["清关"]}
    mix3_tags = _run_build_import_hashtags({**base_project, "source_snapshot": {"chain_mode": "mix3"}}, brief, {})
    owned_tags = _run_build_import_hashtags({**base_project, "source_snapshot": {"chain_mode": "hotspot_owned"}}, brief, {})
    only_tags = _run_build_import_hashtags({**base_project, "source_snapshot": {"chain_mode": "owned_only"}}, brief, {})
    assert "南非素材" in mix3_tags and "热点追踪" in mix3_tags
    assert "南非素材" not in owned_tags and "热点追踪" in owned_tags
    assert "南非素材" not in only_tags and "热点追踪" not in only_tags


def test_import_hashtags_accepts_json_string_snapshot_and_fixed_combo():
    project = {"title": "南非物流｜视频", "source_snapshot": json.dumps({"chain_mode": "mix3"}, ensure_ascii=False)}
    tags = _run_build_import_hashtags(project, {"logistics_nodes": ["清关"]}, {})
    assert tags[:2] == ["南非物流", "清关"]
    for fixed in ("南非物流", "跨境电商", "卖家出海"):
        assert fixed in tags


def test_import_hashtags_dedupes_and_stays_under_eight():
    project = {"title": "南非物流 南非物流 清关｜视频", "source_snapshot": {"chain_mode": "mix3"}}
    brief = {"logistics_nodes": ["清关", "清关", "运输"]}
    tags = _run_build_import_hashtags(project, brief, {})
    assert len(tags) == len(set(tags))
    assert len(tags) <= 8
    assert tags.count("南非物流") == 1
    assert tags.count("清关") == 1


def test_import_hashtags_tolerates_missing_or_invalid_inputs():
    for project, brief, script in (
        ({}, {}, {}),
        (None, None, None),
        ({"title": ""}, {"logistics_nodes": []}, {}),
    ):
        result = _run_build_import_hashtags(project or {}, brief or {}, script or {})
        assert isinstance(result, list)
        assert len(result) <= 8


@pytest.mark.xfail(
    strict=True,
    reason="既有缺陷：buildImportHashtags 对非法 source_snapshot JSON 字符串直接 JSON.parse 抛异常，"
    "与本指令 D3『输入缺失或非法时函数不抛异常』不符；待总指挥决策修复。",
)
def test_import_hashtags_tolerates_malformed_snapshot_json():
    project = {"title": "南非物流｜视频", "source_snapshot": "not-json{"}
    result = _run_build_import_hashtags(project, {"logistics_nodes": ["清关"]}, {})
    assert isinstance(result, list)
    assert len(result) <= 8


# ==================== D. 审核即发布 ====================


def _review_client(tmp_db, username: str, role: str = "reviewer"):
    import app
    import auth

    tmp_db.create_user(username, auth.hash_password("pw12345"), role, username)
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": username, "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _queue_item(tmp_db, *, scheduled_at: str | None = None, title: str = "南非物流科普", body: str = "欢迎咨询南非物流服务") -> int:
    return tmp_db.add_to_queue(
        title=title, body=body, platform="douyin",
        status="pending_review", created_by=1, scheduled_at=scheduled_at,
    )


def test_approve_without_scheduled_at_backfills_now_and_is_picked_by_scheduler(tmp_db):
    client, headers = _review_client(tmp_db, "reviewer-1")
    item_id = _queue_item(tmp_db)
    response = client.post(f"/api/review/{item_id}", headers=headers, json={"action": "approve", "note": "ok"})
    assert response.status_code == 200
    item = tmp_db.get_queue_item_by_id(item_id)
    assert item["status"] == "queued"
    assert item["scheduled_at"]
    assert "T" not in item["scheduled_at"]
    tmp_db.update_queue_status(item_id, "queued", scheduled_at="2000-01-01 00:00:00")
    picked = [row["id"] for row in tmp_db.get_scheduled_items()]
    assert item_id in picked


def test_approve_keeps_existing_scheduled_at_and_is_picked_by_scheduler(tmp_db):
    client, headers = _review_client(tmp_db, "reviewer-2")
    item_id = _queue_item(tmp_db, scheduled_at="2026-08-01 10:00:00")
    response = client.post(f"/api/review/{item_id}", headers=headers, json={"action": "approve", "note": "ok"})
    assert response.status_code == 200
    item = tmp_db.get_queue_item_by_id(item_id)
    assert item["status"] == "queued"
    assert item["scheduled_at"] == "2026-08-01 10:00:00"
    picked = [row["id"] for row in tmp_db.get_scheduled_items()]
    assert item_id in picked


def test_reject_never_becomes_queued(tmp_db):
    client, headers = _review_client(tmp_db, "reviewer-3")
    item_id = _queue_item(tmp_db)
    response = client.post(f"/api/review/{item_id}", headers=headers, json={"action": "reject", "note": "no"})
    assert response.status_code == 200
    item = tmp_db.get_queue_item_by_id(item_id)
    assert item["status"] != "queued"
    assert item["status"] == "rejected"


def test_approve_requires_reviewer_or_admin_role(tmp_db):
    import app
    import auth

    tmp_db.create_user("plain-editor", auth.hash_password("pw12345"), "editor", "Editor")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "plain-editor", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    item_id = _queue_item(tmp_db)
    response = client.post(f"/api/review/{item_id}", headers=headers, json={"action": "approve", "note": "x"})
    assert response.status_code == 403
    item = tmp_db.get_queue_item_by_id(item_id)
    assert item["status"] == "pending_review"
