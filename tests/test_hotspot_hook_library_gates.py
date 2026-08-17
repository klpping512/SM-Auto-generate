import json

from fastapi.testclient import TestClient


def _create_ready_chat_hook_pair(db, title="南非港口货运卡车排队"):
    hotspot_id, _ = db.upsert_hotspot({
        "title": title, "summary": "口岸筛查让货运卡车在入口处等待",
        "source_url": "https://example.com/chat-ready-hooks", "publisher": "SA Today",
        "published_at": "2026-07-28T00:00:00Z", "retrieved_at": "2026-07-28T00:00:00Z",
        "snapshot_sha256": "chat-ready-hook-pair",
    })
    asset_id = db.create_asset({
        "name": "港口卡车现场母片", "filepath": "assets/chat-ready-hooks.mp4", "file_type": "video",
        "category": "other", "duration": 30, "size": 10, "source": "youtube", "status": "active",
        "sha256": "c" * 64,
    })
    events = db.replace_hotspot_event_clips(asset_id, hotspot_id, [
        {"event_index": 1, "start_ms": 0, "end_ms": 7_000, "title_zh": "卡车在口岸入口排队",
         "title_en": "Trucks queue at border", "confidence": .9, "review_status": "confirmed", "segments": [],
         "evidence": {"what_happened": "货运卡车在口岸入口排队等待筛查", "hook_reason": "排队现场清晰可见", "logistics_question": "等待会先影响哪个订单节点？", "event_identity": "口岸入口货车排队等待筛查"}},
        {"event_index": 2, "start_ms": 10_000, "end_ms": 17_000, "title_zh": "卡车继续等待放行",
         "title_en": "Trucks wait for clearance", "confidence": .9, "review_status": "confirmed", "segments": [],
         "evidence": {"what_happened": "另一批货运卡车仍在口岸入口等待放行", "hook_reason": "连续等待画面可核验", "logistics_question": "卖家应如何预留履约判断空间？", "event_identity": "口岸入口货车排队等待筛查"}},
    ])
    for event in events:
        db.update_hotspot_event_clip_media(event["id"], f"assets/hotspot-events/{event['id']}/event.mp4", None, "ready")
    return hotspot_id, asset_id, events


def _create_ready_chat_hook(db, *, title, summary, event_title, what_happened, logistics_question, snapshot):
    hotspot_id, _ = db.upsert_hotspot({
        "title": title, "summary": summary,
        "source_url": f"https://example.com/{snapshot}", "publisher": "SA Today",
        "published_at": "2026-07-30T00:00:00Z", "retrieved_at": "2026-07-30T00:00:00Z",
        "snapshot_sha256": snapshot,
    })
    asset_id = db.create_asset({
        "name": f"{event_title}母片", "filepath": f"assets/{snapshot}.mp4", "file_type": "video",
        "category": "other", "duration": 20, "size": 10, "source": "youtube", "status": "active",
        "sha256": (snapshot.encode("utf-8").hex() * 8)[:64],
    })
    event = db.replace_hotspot_event_clips(asset_id, hotspot_id, [
        {"event_index": 1, "start_ms": 0, "end_ms": 8_000, "title_zh": event_title,
         "title_en": event_title, "confidence": .9, "review_status": "confirmed", "segments": [],
         "evidence": {"what_happened": what_happened, "hook_reason": "现场清晰可见",
                      "logistics_question": logistics_question, "event_identity": event_title}},
    ])[0]
    db.update_hotspot_event_clip_media(event["id"], f"assets/hotspot-events/{event['id']}/event.mp4", None, "ready")
    return hotspot_id, event


def _add_owned_delivery_segments(db, count=6):
    for index in range(1, count + 1):
        asset_id = db.create_asset({
            "name": f"Buffalo 配送动态镜头 {index}", "filepath": f"assets/owned-delivery-{index}.mp4",
            "file_type": "video", "category": "delivery", "primary_category": "delivery",
            "duration": 10, "size": 10, "source": "local", "status": "active", "sha256": f"{index:x}" * 64,
        })
        db.create_asset_segment({
            "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 10_000,
            "primary_category": "delivery", "quality_score": .9,
        })


def _admin_client(tmp_db):
    import app
    import auth

    tmp_db.create_user("hook-admin", auth.hash_password("pw12345"), "admin", "Hook Admin")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "hook-admin", "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_hotspot_library_exposes_only_confirmed_factful_ready_hooks_and_can_clean_legacy(tmp_db):
    import database as db

    client, headers = _admin_client(tmp_db)
    hotspot_id, _ = db.upsert_hotspot({
        "title": "Truck queue near border", "summary": "Freight vehicles wait at screening",
        "source_url": "https://example.com/hook-gate", "publisher": "SA Today",
        "published_at": "2026-07-28T00:00:00Z", "retrieved_at": "2026-07-28T00:00:00Z",
        "snapshot_sha256": "hook-library-gate",
    })
    asset_id = db.create_asset({
        "name": "热点母片", "filepath": "assets/hotspot.mp4", "file_type": "video", "category": "other",
        "duration": 40, "size": 10, "source": "youtube", "status": "active", "sha256": "9" * 64,
    })
    valid, invalid, municipal, cost_leap = db.replace_hotspot_event_clips(asset_id, hotspot_id, [
        {"event_index": 1, "start_ms": 0, "end_ms": 8_000, "title_zh": "卡车排队等待筛查", "title_en": "Truck queue",
         "evidence": {"what_happened": "卡车在口岸筛查点排队", "hook_reason": "可见排队现场", "logistics_question": "等待会先影响哪个订单节点？"},
         "segments": [], "confidence": .9, "review_status": "confirmed"},
        {"event_index": 2, "start_ms": 10_000, "end_ms": 18_000, "title_zh": "待确认事件", "title_en": "Unknown",
         "evidence": {}, "segments": [], "confidence": .4, "review_status": "review_required"},
        {"event_index": 3, "start_ms": 20_000, "end_ms": 28_000, "title_zh": "市政垃圾清运罢工", "title_en": "Municipal refuse",
         "evidence": {"what_happened": "街道垃圾堆积", "hook_reason": "画面冲击明显", "logistics_question": "会不会影响配送？"},
         "segments": [], "confidence": .9, "review_status": "confirmed"},
        {"event_index": 4, "start_ms": 29_000, "end_ms": 36_000, "title_zh": "红海新闻推高国际油价", "title_en": "Red Sea oil price",
         "evidence": {"what_happened": "新闻报道国际油价变化", "hook_reason": "新闻画面", "logistics_question": "运输成本是否已同步攀升？"},
         "segments": [], "confidence": .9, "review_status": "confirmed"},
    ])
    db.update_hotspot_event_clip_media(valid["id"], "assets/hotspot-events/1/event.mp4", "assets/hotspot-events/1/event.jpg", "ready")
    db.update_hotspot_event_clip_media(municipal["id"], "assets/hotspot-events/1/municipal.mp4", "assets/hotspot-events/1/municipal.jpg", "ready")
    db.update_hotspot_event_clip_media(cost_leap["id"], "assets/hotspot-events/1/cost.mp4", "assets/hotspot-events/1/cost.jpg", "ready")

    listed = client.get("/api/hotspot-events", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [valid["id"]]

    cleaned = client.post("/api/hotspot-events/cleanup-ineligible", headers=headers)
    assert cleaned.status_code == 200
    assert cleaned.json()["event_clip_count"] == 3
    assert db.get_hotspot_event_clip(invalid["id"]) is None
    assert db.get_hotspot_event_clip(municipal["id"]) is None
    assert db.get_hotspot_event_clip(cost_leap["id"]) is None
    assert db.get_hotspot_event_clip(valid["id"]) is not None


def test_hotspot_library_derives_soft_bridge_for_logistics_adjacent_audited_hooks(tmp_db):
    import database as db

    client, headers = _admin_client(tmp_db)
    hotspot_id, _ = db.upsert_hotspot({
        "title": "Snow road report", "summary": "Road conditions after a snow event",
        "source_url": "https://example.com/audited-only", "publisher": "SA Today",
        "published_at": "2026-08-13T00:00:00Z", "retrieved_at": "2026-08-13T00:00:00Z",
        "snapshot_sha256": "audited-only-hook",
    })
    asset_id = db.create_asset({
        "name": "待补物流切入母片", "filepath": "assets/audited-only.mp4", "file_type": "video",
        "category": "other", "duration": 20, "size": 10, "source": "youtube", "status": "active",
        "sha256": "a" * 64,
    })
    event = db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 8_000, "title_zh": "积雪道路上车辆缓慢通行",
        "title_en": "Vehicles move slowly on a snowy road", "confidence": .9,
        "review_status": "confirmed", "segments": [],
        "evidence": {
            "what_happened": "车辆在积雪道路上缓慢通行",
            "hook_reason": "道路与车辆状态清晰可见",
            "visual_audit": {
                "scene_type": "road",
                "visible_objects": ["积雪道路", "车辆"],
                "visible_actions": ["缓慢通行"],
            },
        },
    }])[0]
    db.update_hotspot_event_clip_media(event["id"], "assets/hotspot-events/audited-only/event.mp4", None, "ready")

    strict = client.get("/api/hotspot-events", headers=headers)
    assert strict.status_code == 200
    assert strict.json()[0]["id"] == event["id"]
    assert strict.json()[0]["is_renderable"] is True
    assert strict.json()[0]["logistics_bridge_mode"] == "soft"
    assert strict.json()[0]["evidence"]["logistics_question"] == "道路通行受影响时，货运路线和配送节点要先核对什么？"

    audited = client.get("/api/hotspot-events?audited_only=true", headers=headers)
    assert audited.status_code == 200
    assert audited.json()[0]["id"] == event["id"]
    assert audited.json()[0]["is_renderable"] is True
    assert audited.json()[0]["library_status"] == "ready"


def test_soft_bridge_does_not_make_pure_sports_clip_renderable(tmp_db):
    import app

    event = {
        "title_zh": "球队训练现场",
        "title_en": "Football team training",
        "review_status": "confirmed",
        "clip_status": "ready",
        "clip_path": "assets/hotspot-events/sports/event.mp4",
        "evidence": {
            "what_happened": "球员在训练场进行传球训练，画面可见球员和球门。",
            "hook_reason": "训练动作清晰可见",
        },
    }

    assert app._is_audited_hotspot_hook(event) is True
    assert app._is_confirmed_renderable_hotspot_hook(event) is False


def test_local_buffalo_asset_cannot_be_promoted_to_a_hotspot_hook(tmp_db):
    import app
    import database as db

    hotspot_id, _ = db.upsert_hotspot({
        "title": "Legacy logistics opener", "summary": "",
        "source_url": "", "publisher": "", "published_at": "",
        "retrieved_at": "", "snapshot_sha256": "legacy-local-hook",
    })
    asset_id = db.create_asset({
        "name": "Buffalo delivery truck", "filepath": "assets/buffalo-truck.mp4",
        "file_type": "video", "category": "delivery", "duration": 17,
        "size": 10, "source": "local_directory", "status": "active", "sha256": "b" * 64,
    })
    event = db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 6_000,
        "title_zh": "末端配送作业场景", "title_en": "Last-mile delivery operation scenes",
        "review_status": "confirmed", "segments": [],
        "evidence": {
            "what_happened": "展示了末端配送与派送环节的典型作业画面。",
            "hook_reason": "作为常青物流话题的通用开场画面。",
            "logistics_question": "末端配送如何稳住最后三公里？",
        },
    }])[0]
    db.update_hotspot_event_clip_media(event["id"], "assets/hotspot-events/local/event.mp4", None, "ready")
    event = db.get_hotspot_event_clip(event["id"])

    assert app._is_audited_hotspot_hook(event) is False
    assert app._is_confirmed_renderable_hotspot_hook(event) is False


def test_downloaded_hotspot_copy_passes_when_linked_to_external_parent(tmp_db):
    import app
    import database as db

    hotspot_id, _ = db.upsert_hotspot({
        "title": "External port update", "summary": "Cargo vehicles queue at the port",
        "source_url": "https://news.example/port-update", "publisher": "SA Today",
        "published_at": "2026-08-15T00:00:00Z", "retrieved_at": "2026-08-15T00:00:00Z",
        "snapshot_sha256": "external-hotspot-source",
    })
    asset_id = db.create_asset({
        "name": "Downloaded port footage", "filepath": "assets/hotspot-port.mp4",
        "file_type": "video", "category": "other", "duration": 17,
        "size": 10, "source": "local_directory", "status": "active", "sha256": "p" * 64,
    })
    db.update_asset_provenance(asset_id, "https://news.example/port-update/video", "", "SA Today", hotspot_id)
    event = db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 6_000,
        "title_zh": "港口货车排队", "title_en": "Port truck queue",
        "review_status": "confirmed", "segments": [],
        "evidence": {
            "what_happened": "货运卡车在港口入口排队。",
            "hook_reason": "排队现场清晰可见。",
            "logistics_question": "等待会先影响哪个订单节点？",
        },
    }])[0]
    db.update_hotspot_event_clip_media(event["id"], "assets/hotspot-events/linked/event.mp4", None, "ready")
    event = db.get_hotspot_event_clip(event["id"])

    assert app._is_confirmed_renderable_hotspot_hook(event) is True


def test_formal_hook_repair_keeps_snow_road_fact_in_first_voiceover(tmp_db):
    import app

    generated = {"title": "南非道路天气变化", "angle": "物流风险", "scenes": [{
        "voiceover": "先看现场。", "text_overlay": "先看现场",
    }]}
    scenes = [{
        "scene_role": "hotspot_evidence", "evidence_type": "hotspot_video", "duration_ms": 12_000,
    }]
    event = {"evidence": {
        "what_happened": "镜头记录下车辆在雪景覆盖的公路上持续行驶的状态，交通并未中断。",
        "logistics_question": "天气变化时，运输通道要先核对什么？",
    }}

    repaired = app._repair_formal_narrative_hook(generated, scenes, event)
    voiceover = repaired["scenes"][0]["voiceover"]
    assert "公路" in voiceover
    assert "车辆" in voiceover


def test_admin_can_delete_one_hook_without_deleting_mother_or_siblings(tmp_db):
    import database as db

    client, headers = _admin_client(tmp_db)
    hotspot_id, _ = db.upsert_hotspot({
        "title": "Port queue", "summary": "Cargo trucks wait", "source_url": "https://example.com/one-hook",
        "publisher": "SA Today", "published_at": "2026-07-28T00:00:00Z", "retrieved_at": "2026-07-28T00:00:00Z",
        "snapshot_sha256": "one-hook-delete",
    })
    asset_id = db.create_asset({
        "name": "热点母片", "filepath": "assets/mother.mp4", "file_type": "video", "category": "other",
        "duration": 30, "size": 10, "source": "youtube", "status": "active", "sha256": "8" * 64,
    })
    first, second = db.replace_hotspot_event_clips(asset_id, hotspot_id, [
        {"event_index": 1, "start_ms": 0, "end_ms": 6_000, "title_zh": "卡车进入港口", "title_en": "Truck arrives", "segments": [], "review_status": "confirmed"},
        {"event_index": 2, "start_ms": 8_000, "end_ms": 14_000, "title_zh": "卡车排队", "title_en": "Truck queues", "segments": [], "review_status": "confirmed"},
    ])

    response = client.delete(f"/api/hotspot-events/{first['id']}", headers=headers)
    assert response.status_code == 200
    assert db.get_hotspot_event_clip(first["id"]) is None
    assert db.get_hotspot_event_clip(second["id"]) is not None
    assert db.get_asset(asset_id) is not None


def test_chat_queues_targeted_collection_when_confirmed_hook_library_has_no_match(tmp_db, monkeypatch):
    import ai_engine
    import app
    import auth

    tmp_db.create_user("chat-editor", auth.hash_password("pw12345"), "editor", "Chat Editor")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "chat-editor", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(app, "_marketing_hook_candidates", lambda *_args, **_kwargs: ([], "", [], {
        "scanned": 0, "scene_mismatch": 0, "relevance_low": 0, "not_playable": 0,
        "kind_filtered": 0, "duplicate_or_recent": 0, "passed": 0, "selected": 0,
    }))
    refresh_calls = []
    monkeypatch.setattr(app.sched, "request_targeted_hotspot_refresh", lambda: refresh_calls.append(True) or True)

    async def generated(**_kwargs):
        return [{"platform": "xiaohongshu", "title": "测试", "body": "测试正文", "hashtags": [], "image_pages": [], "attachments": []}]

    monkeypatch.setattr(ai_engine, "chat_platforms", generated)
    topic = "德班港拥堵最新情况会影响交期吗？"
    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": topic}],
        "platforms": ["xiaohongshu"],
        "topic": topic,
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["content_mode"] == "hotspot"
    assert payload["hotspot_retrieval"]["status"] == "queued"
    assert tmp_db.list_hotspot_discovery_requests(status="pending")[0]["topic"] == topic
    assert refresh_calls == [True]


def test_chat_uses_latest_user_question_as_copy_topic_when_no_quick_topic_is_selected(tmp_db, monkeypatch):
    import ai_engine
    import app
    import auth

    tmp_db.create_user("chat-topic-editor", auth.hash_password("pw12345"), "editor", "Chat Topic Editor")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "chat-topic-editor", "password": "pw12345"}).json()["access_token"]
    captured = {}
    monkeypatch.setattr(app, "_marketing_hook_candidates", lambda *_args, **_kwargs: ([], "", [], {
        "scanned": 0, "scene_mismatch": 0, "relevance_low": 0, "not_playable": 0,
        "kind_filtered": 0, "duplicate_or_recent": 0, "passed": 0, "selected": 0,
    }))
    monkeypatch.setattr(app.sched, "request_targeted_hotspot_refresh", lambda: False)

    async def generated(**kwargs):
        captured.update(kwargs)
        return [{"platform": "douyin", "title": "测试", "body": "测试正文", "hashtags": [], "scenes": []}]

    monkeypatch.setattr(ai_engine, "chat_platforms", generated)
    question = "Beitbridge 边境卡车排队，我这票货准备进南非海外仓"
    response = client.post("/api/ai/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "messages": [{"role": "user", "content": question}],
        "platforms": ["douyin"],
        "topic": "",
    })

    assert response.status_code == 200
    assert captured["topic"] == question


def test_chat_retrieval_locks_two_non_overlapping_hooks_from_one_parent(tmp_db, monkeypatch):
    import asyncio
    import app

    hotspot_id, asset_id, events = _create_ready_chat_hook_pair(tmp_db)
    candidate = {
        "hotspot_id": hotspot_id, "title": "南非港口货运卡车排队", "marketing_question": "等待会先影响哪个订单节点？",
        "hook_clips": [
            {"event_clip_id": event["id"], "asset_id": asset_id, "start_ms": event["start_ms"], "end_ms": event["end_ms"], "event_identity": "口岸入口货车排队等待筛查",
             "content_description": event["title_zh"]}
            for event in events
        ],
    }

    async def decided(*_args, **_kwargs):
        return [candidate], {"used": True}

    monkeypatch.setattr(app, "_marketing_hook_candidates", lambda *_args, **_kwargs: ([candidate], "", [], {"scanned":1,"passed":1,"selected":0,"scene_mismatch":0,"relevance_low":0,"not_playable":0,"kind_filtered":0,"duplicate_or_recent":0}))
    monkeypatch.setattr(app, "_model_decide_marketing_hooks", decided)

    result = asyncio.run(app._retrieve_confirmed_chat_hooks("南非港口拥堵会怎样影响跨境订单", 1))

    assert result["status"] == "matched"
    assert result["video"]["status"] == "ready"
    assert result["video"]["hotspot_event_ids"] == [event["id"] for event in events]


def test_chat_retrieval_uses_one_confirmed_hook_when_no_second_clip_exists(tmp_db, monkeypatch):
    import asyncio
    import app

    hotspot_id, asset_id, events = _create_ready_chat_hook_pair(tmp_db)
    event = events[0]
    candidate = {
        "hotspot_id": hotspot_id,
        "title": "南非港口货运卡车排队",
        "marketing_question": "等待会先影响哪个订单节点？",
        "hook_clips": [{
            "event_clip_id": event["id"], "asset_id": asset_id,
            "start_ms": event["start_ms"], "end_ms": event["end_ms"],
            "event_identity": "口岸入口货车排队等待筛查",
            "content_description": event["title_zh"],
        }],
    }

    async def decided(*_args, **_kwargs):
        return [candidate], {"used": True}

    monkeypatch.setattr(app, "_marketing_hook_candidates", lambda *_args, **_kwargs: ([candidate], "", [], {"scanned":1,"passed":1,"selected":0,"scene_mismatch":0,"relevance_low":0,"not_playable":0,"kind_filtered":0,"duplicate_or_recent":0}))
    monkeypatch.setattr(app, "_model_decide_marketing_hooks", decided)

    result = asyncio.run(app._retrieve_confirmed_chat_hooks("南非港口拥堵会怎样影响跨境订单", 1))

    assert result["status"] == "matched"
    assert result["video"]["status"] == "ready"
    assert result["video"]["hotspot_event_ids"] == [event["id"]]
    assert "一段相关" in result["message"]
    readiness = result["video"]["delivery_readiness"]
    assert readiness["delivery_ready"] is True
    assert readiness["status"] in {"delivery_ready", "delivery_ready_adapted"}
    assert readiness["coverage"]["hotspot_video"] >= 1
    assert "adaptation" in readiness


def test_chat_retrieval_marks_delivery_ready_before_user_clicks_generate(tmp_db, monkeypatch):
    import asyncio
    import app

    hotspot_id, asset_id, events = _create_ready_chat_hook_pair(tmp_db)
    _add_owned_delivery_segments(tmp_db)
    candidate = {
        "hotspot_id": hotspot_id,
        "title": "南非港口货运卡车排队",
        "relevance": {"level": "strong_direct", "reason": "卡车排队可直接解释跨境运输节点。"},
        "hook_clips": [
            {
                "event_clip_id": event["id"], "asset_id": asset_id,
                "start_ms": event["start_ms"], "end_ms": event["end_ms"],
                "event_identity": "口岸入口货车排队等待筛查",
                "content_description": event["title_zh"],
            }
            for event in events
        ],
    }

    async def decided(_brief, candidates, *_args, **_kwargs):
        return candidates[:1], {"used": False, "fallback": "test"}

    monkeypatch.setattr(app, "_marketing_hook_candidates", lambda *_args, **_kwargs: ([candidate], "", [], {"scanned":1,"passed":1,"selected":0,"scene_mismatch":0,"relevance_low":0,"not_playable":0,"kind_filtered":0,"duplicate_or_recent":0}))
    monkeypatch.setattr(app, "_model_decide_marketing_hooks", decided)
    result = asyncio.run(app._retrieve_confirmed_chat_hooks("南非港口拥堵会怎样影响跨境订单", 1))

    readiness = result["video"]["delivery_readiness"]
    assert result["status"] == "matched"
    assert result["relevance"]["level"] in {"strong_direct", "strong_logistics_context"}
    assert readiness["status"] == "delivery_ready"
    assert readiness["delivery_ready"] is True
    assert readiness["coverage"]["hotspot_video"] >= 1
    assert readiness["coverage"]["owned_video"] >= 4
    assert 50_000 <= readiness["coverage"]["duration_ms"] <= 90_000
    assert readiness.get("adaptation", {}).get("adapted") is False


def test_chat_retrieval_can_reuse_the_same_hook_for_two_relevant_logistics_topics(tmp_db, monkeypatch):
    import asyncio
    import app

    _hotspot_id, _asset_id, events = _create_ready_chat_hook_pair(tmp_db, title="Beitbridge 边境卡车排队")

    async def decided(_brief, candidates, *_args, **_kwargs):
        return candidates[:1], {"used": False, "fallback": "test"}

    monkeypatch.setattr(app, "_model_decide_marketing_hooks", decided)
    first = asyncio.run(app._retrieve_confirmed_chat_hooks("边境排队时入库预约要先确认什么", 1))
    second = asyncio.run(app._retrieve_confirmed_chat_hooks("边境卡车拥堵时怎么更新客户交期", 1))

    expected_ids = [event["id"] for event in events]
    assert first["status"] == second["status"] == "matched"
    assert first["video"]["hotspot_event_ids"] == expected_ids
    assert second["video"]["hotspot_event_ids"] == expected_ids


def test_chat_broad_warehouse_intro_does_not_hard_match_border_accident(tmp_db, monkeypatch):
    """宽泛常青题不能再用边境事故硬配成片；应提示缺锚点并推荐可生产选题。"""
    import asyncio
    import app

    _create_ready_chat_hook_pair(tmp_db, title="南非边境卡车排队")

    async def decided(_brief, candidates, *_args, **_kwargs):
        return candidates[:1], {"used": False, "fallback": "test"}

    monkeypatch.setattr(app, "_model_decide_marketing_hooks", decided)

    result = asyncio.run(app._retrieve_confirmed_chat_hooks(
        "帮我生成一个关于南非海外仓的介绍视频",
        None,
        content_mode="evergreen",
        event_anchor=app.chat_intent.assess_event_anchor("帮我生成一个关于南非海外仓的介绍视频"),
    ))

    assert result["status"] == "not_requested"
    assert result["failure_class"] == "no_event_anchor"
    assert result.get("request_id") in (None, "")
    assert "未启动" in (result.get("message") or "") or result.get("producible_topics") is not None


def test_chat_retrieval_matches_confirmed_hook_evidence_when_parent_headline_is_generic(tmp_db, monkeypatch):
    import asyncio
    import app

    _hotspot_id, _asset_id, events = _create_ready_chat_hook_pair(
        tmp_db, title="What's Happening Across South Africa Today",
    )
    captured = {}

    async def decided(_brief, candidates, *_args, **_kwargs):
        captured["candidates"] = candidates
        return candidates[:1], {"used": False, "fallback": "test"}

    monkeypatch.setattr(app, "_model_decide_marketing_hooks", decided)
    result = asyncio.run(app._retrieve_confirmed_chat_hooks("南非边境卡车拥堵会怎样影响跨境物流", 1))

    assert captured["candidates"]
    assert captured["candidates"][0]["title"] == "卡车在口岸入口排队"
    assert result["status"] == "matched"
    assert result["video"]["hotspot_event_ids"] == [event["id"] for event in events]


def test_chat_hook_candidates_require_renderable_hooks(tmp_db):
    import app
    import database as db

    db.upsert_hotspot({
        "title": "南非港口与物流政策更新", "summary": "港口、物流、运输政策信息",
        "source_url": "https://example.com/no-render-hook", "publisher": "Official",
        "published_at": "2026-07-30T00:00:00Z", "retrieved_at": "2026-07-30T00:00:00Z",
        "snapshot_sha256": "no-render-hook",
    })

    candidates, _kb, _rag, _funnel = app._marketing_hook_candidates({
        "raw_input": "南非物流突发延误怎么办", "subject": "南非物流突发延误怎么办",
        "angle": "备用供应链方案", "goal": "为 Buffalo 物流内容选择已确认热点 Hook",
    }, limit=8)

    assert candidates == []


def test_chat_hook_candidates_do_not_reuse_border_for_cost_risk_when_better_disruption_exists(tmp_db):
    import app
    import database as db

    border_hotspot_id, _asset_id, border_events = _create_ready_chat_hook_pair(
        db, title="Beitbridge 边境卡车排队",
    )
    accident_hotspot_id, accident_event = _create_ready_chat_hook(
        db,
        title="N3 高速货车侧翻导致物流延误",
        summary="货车事故造成道路中断",
        event_title="N3高速突发事故货车侧翻",
        what_happened="N3高速发生货车侧翻事故，车辆排队等待处置。",
        logistics_question="突发事故造成道路中断时，如何评估延误风险并启动备用路线？",
        snapshot="cost-risk-accident",
    )

    candidates, _kb, _rag, _funnel = app._marketing_hook_candidates({
        "raw_input": "低价货代可能更贵，讲清南非物流最容易亏钱的 4 个坑",
        "subject": "低价货代可能更贵",
        "angle": "南非物流亏钱风险", "goal": "为 Buffalo 物流内容选择已确认热点 Hook",
    }, limit=8)

    hotspot_ids = [item["hotspot_id"] for item in candidates]
    assert candidates
    assert candidates[0]["hotspot_id"] == accident_hotspot_id
    assert candidates[0]["hook_clips"][0]["event_clip_id"] == accident_event["id"]
    assert hotspot_ids.index(accident_hotspot_id) < hotspot_ids.index(border_hotspot_id)
    assert {hook["event_clip_id"] for hook in candidates[-1]["hook_clips"]} == {event["id"] for event in border_events}


def test_chat_hook_candidates_bind_exact_audited_logistics_question(tmp_db):
    import app
    import database as db

    hotspot_id, event = _create_ready_chat_hook(
        db,
        title="林波波省集装箱设施火情",
        summary="夜间结构燃烧并出现烟雾",
        event_title="夜间燃烧结构与烟雾",
        what_happened="夜间一个大型结构持续燃烧，周围可见火光、烟雾和人员查看现场。",
        logistics_question="集装箱类设施发生火情时，运输或存储环节如何核对安全状态？",
        snapshot="container-fire-question",
    )
    events = db.replace_hotspot_event_clips(event["asset_id"], hotspot_id, [
        {
            "event_index": 1, "start_ms": 0, "end_ms": 8_000,
            "title_zh": "积雪山区公路上车辆行驶", "title_en": "Snowy road traffic",
            "confidence": .95, "review_status": "confirmed", "segments": [],
            "evidence": {
                "what_happened": "积雪山区公路上车辆行驶。", "hook_reason": "道路与车辆动作清晰可见。",
                "logistics_question": "山区积雪和湿滑路况下，货物运输如何核对路线与安全状态？",
                "event_identity": "雪天道路现场",
            },
        },
        {
            "event_index": 2, "start_ms": 8_000, "end_ms": 16_000,
            "title_zh": "夜间燃烧结构与烟雾", "title_en": "Burning structure at night",
            "confidence": .95, "review_status": "confirmed", "segments": [],
            "evidence": {
                "what_happened": "夜间一个大型结构持续燃烧，周围可见火光、烟雾和人员查看现场。",
                "hook_reason": "燃烧结构、火光、烟雾和现场人员在连续画面中直接可见。",
                "logistics_question": "集装箱类设施发生火情时，运输或存储环节如何核对安全状态？",
                "event_identity": "集装箱设施火情",
            },
        },
    ])
    for item in events:
        db.update_hotspot_event_clip_media(item["id"], f"assets/hotspot-events/{item['id']}/event.mp4", None, "ready")
    event = events[1]

    candidates, _kb, _rag, _funnel = app._marketing_hook_candidates({
        "raw_input": "请围绕 集装箱类设施发生火情时，运输或存储环节如何核对安全状态？生成一个视频",
        "subject": "请围绕 集装箱类设施发生火情时，运输或存储环节如何核对安全状态？生成一个视频",
        "angle": "从运输或存储环节说明核对安全状态的流程",
        "goal": "基于已确认热点 Hook 生成 Buffalo 双素材库视频",
    }, limit=8, hook_kind="timely_event")

    assert candidates
    assert candidates[0]["hotspot_id"] == hotspot_id
    assert candidates[0]["hook_clips"][0]["event_clip_id"] == event["id"]
    assert candidates[0]["hook_type"] == "curated_bridge"
    assert candidates[0]["marketing_question"] == "集装箱类设施发生火情时，运输或存储环节如何核对安全状态？"


def test_chat_video_nodes_include_only_logistics_actions_supported_by_locked_hook_evidence():
    import app

    nodes = app._chat_video_logistics_nodes("边境拥堵时怎样管理交期与运输", [{
        "title_zh": "边境卡车排队",
        "evidence": {
            "logistics_question": "海外仓如何调整入库、分拣和分拨节奏以缓解履约延迟？",
        },
    }])

    assert nodes == ["运输", "仓储", "配送"]


def test_chat_video_nodes_allow_visible_preparation_for_an_explicit_road_disruption():
    import app

    nodes = app._chat_video_logistics_nodes("R60 事故后路线要不要调整", [{
        "title_zh": "R60 公路卡车侧翻现场",
        "evidence": {
            "logistics_question": "主干道交通中断时，货物如何评估延误风险并启动备用路线？",
        },
    }])

    assert nodes == ["运输", "配送"]


def test_chat_dual_library_video_creates_locked_dual_library_project(tmp_db, monkeypatch):
    import app

    client, headers = _admin_client(tmp_db)
    _hotspot_id, _asset_id, events = _create_ready_chat_hook_pair(tmp_db)
    # This test asserts on job/project orchestration, not the readiness scoring
    # itself; the fixture has no owned assets, so bypass the delivery_ready
    # hard gate added in app.py.
    monkeypatch.setattr(
        app, "_chat_video_delivery_readiness",
        lambda *_args, **_kwargs: {"status": "delivery_ready", "delivery_ready": True},
    )
    response = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "港口", "hotspot_event_ids": [event["id"] for event in events],
        "platform": "douyin", "target_duration_ms": 60_000, "session_id": "chat-test-session",
    })

    assert response.status_code == 202
    payload = response.json()
    assert payload["created"] is True
    assert payload["project"]["source_type"] == "topic_brief_dual_library"
    assert payload["job"]["status"] == "pending"
    assert payload["job"]["stage"] == "queued"
    snapshot = json.loads(payload["project"]["source_snapshot"])
    assert snapshot["matched_event_clip_ids"] == [event["id"] for event in events]
    assert snapshot["pipeline"][:4] == ["topic_brief", "hook_locking", "scripting", "project_building"]

    repeated = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "港口", "hotspot_event_ids": [event["id"] for event in events],
        "platform": "douyin", "target_duration_ms": 60_000, "session_id": "chat-test-session",
    })
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert repeated.json()["job_id"] == payload["job_id"]


def test_chat_dual_library_video_accepts_one_locked_hook(tmp_db, monkeypatch):
    import app

    client, headers = _admin_client(tmp_db)
    _hotspot_id, _asset_id, events = _create_ready_chat_hook_pair(tmp_db)
    monkeypatch.setattr(
        app, "_chat_video_delivery_readiness",
        lambda *_args, **_kwargs: {"status": "delivery_ready", "delivery_ready": True},
    )
    event = events[0]
    response = client.post("/api/ai/chat/dual-library-video", headers=headers, json={
        "topic": "港口", "hotspot_event_ids": [event["id"]],
        "platform": "douyin", "target_duration_ms": 60_000, "session_id": "single-hook-chat",
    })

    assert response.status_code == 202, response.json()
    payload = response.json()
    assert payload["job"]["stage"] == "queued"
    assert json.loads(payload["project"]["source_snapshot"])["matched_event_clip_ids"] == [event["id"]]
