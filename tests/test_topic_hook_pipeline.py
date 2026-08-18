from datetime import datetime, timezone

import chat_intent
import topic_hook_pipeline


TRANSNET_TOPIC = "Transnet又有动静！跨境卖家先别慌"


def test_structure_transnet_topic_has_port_rail_nodes():
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    assert "Transnet" in query["entities"]
    assert "港口" in query["logistics_nodes"]
    assert "铁路" in query["logistics_nodes"]
    assert query["time_window_days"] == 30
    assert "official_logistics" in query["source_classes"]


def test_classify_transnet_topic_as_hotspot_with_anchor():
    assert chat_intent.classify_content_mode(TRANSNET_TOPIC) == "hotspot"
    anchor = chat_intent.assess_event_anchor(TRANSNET_TOPIC)
    assert anchor["has_event_anchor"] is True
    assert chat_intent.should_enqueue_hotspot_discovery("hotspot", anchor) is True


def test_match_ready_port_hook_for_transnet():
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    event = {
        "id": 11,
        "asset_id": 90,
        "title_zh": "Transnet 德班港装卸延误",
        "title_en": "Transnet Durban delay",
        "review_status": "confirmed",
        "clip_status": "ready",
        "clip_path": "clips/11.mp4",
        "logistics_scenes": ["port"],
        "evidence": {
            "what_happened": "德班港集装箱装卸排队",
            "logistics_question": "港口延误后跨境卖家要先核对哪一趟船期？",
        },
        "parent_published_at": datetime.now(timezone.utc).isoformat(),
    }
    media = {"id": 5, "publisher": "Transnet NPA", "source_class": "official_logistics", "asset_id": 90}
    buckets = topic_hook_pipeline.match_topic_hooks(
        query,
        [event],
        media_by_asset={90: media},
        is_ready=lambda item: True,
        is_audit=lambda item: True,
    )
    assert len(buckets["matched_ready"]) == 1
    assert buckets["matched_ready"][0]["event_clip_id"] == 11


def test_audit_only_hook_does_not_enter_ready():
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    event = {
        "id": 12,
        "asset_id": 91,
        "title_zh": "Transnet 港口画面待补物流切入",
        "review_status": "confirmed",
        "clip_status": "ready",
        "clip_path": "clips/12.mp4",
        "logistics_scenes": ["port"],
        "evidence": {"what_happened": "港口作业", "logistics_question": "待补充"},
        "parent_published_at": datetime.now(timezone.utc).isoformat(),
    }
    buckets = topic_hook_pipeline.match_topic_hooks(
        query,
        [event],
        media_by_asset={91: {"publisher": "Transnet NPA", "source_class": "official_logistics"}},
        is_ready=lambda item: False,
        is_audit=lambda item: True,
    )
    assert buckets["matched_ready"] == []
    assert buckets["matched_audit_only"]


def test_road_hook_does_not_match_port_rail_topic():
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    event = {
        "id": 13,
        "asset_id": 92,
        "title_zh": "N3 道路施工",
        "review_status": "confirmed",
        "clip_status": "ready",
        "clip_path": "clips/13.mp4",
        "logistics_scenes": ["road"],
        "evidence": {
            "what_happened": "N3 封路",
            "logistics_question": "道路中断后配送要改哪条线？",
        },
        "parent_published_at": datetime.now(timezone.utc).isoformat(),
    }
    buckets = topic_hook_pipeline.match_topic_hooks(
        query,
        [event],
        media_by_asset={92: {"publisher": "SANRAL Corporate", "source_class": "official_logistics"}},
        is_ready=lambda item: True,
        is_audit=lambda item: True,
    )
    assert buckets["matched_ready"] == []


def test_general_news_without_entity_is_not_ready():
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    event = {
        "id": 14,
        "asset_id": 93,
        "title_zh": "议会辩论最新",
        "review_status": "confirmed",
        "clip_status": "ready",
        "clip_path": "clips/14.mp4",
        "logistics_scenes": ["hotspot"],
        "evidence": {"what_happened": "议会发言", "logistics_question": "会不会影响配送？"},
        "parent_published_at": datetime.now(timezone.utc).isoformat(),
    }
    buckets = topic_hook_pipeline.match_topic_hooks(
        query,
        [event],
        media_by_asset={93: {"publisher": "eNCA", "source_class": "general_news"}},
        is_ready=lambda item: True,
        is_audit=lambda item: True,
    )
    assert buckets["matched_ready"] == []


def test_prefer_transnet_channel_before_news():
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    ordered = topic_hook_pipeline.prefer_official_channels(
        [
            {"name": "eNCA", "source_class": "general_news"},
            {"name": "SANRAL Corporate", "source_class": "official_logistics"},
            {"name": "Transnet NPA", "source_class": "official_logistics"},
        ],
        query,
    )
    assert ordered[0]["name"] == "Transnet NPA"
    assert ordered[1]["name"] == "SANRAL Corporate"


def test_enqueue_transnet_topic_is_idempotent(tmp_db):
    user_id = tmp_db.create_user("topic-editor", "hash", "editor", "Topic Editor")
    query = topic_hook_pipeline.structure_topic(TRANSNET_TOPIC)
    first = tmp_db.enqueue_hotspot_discovery_request(TRANSNET_TOPIC, user_id, query=query)
    second = tmp_db.enqueue_hotspot_discovery_request(TRANSNET_TOPIC, user_id, query=query)
    assert first["id"] == second["id"]
    assert first["status"] in {"queued", "pending"}
    assert tmp_db.list_hotspot_discovery_requests(status="pending")[0]["job_type"] == "topic_targeted_hotspot_intake"


def test_chat_transnet_topic_creates_discovery_job(tmp_db, monkeypatch):
    import asyncio
    import app

    monkeypatch.setattr(app.sched, "request_targeted_hotspot_refresh", lambda: True)
    result = asyncio.run(app._retrieve_confirmed_chat_hooks(
        TRANSNET_TOPIC,
        1,
        content_mode="hotspot",
        event_anchor=chat_intent.assess_event_anchor(TRANSNET_TOPIC),
    ))
    assert result["status"] == "queued"
    assert result["request_id"]
    assert "已立即复扫" not in (result.get("message") or "")
    assert "任务" in (result.get("message") or "")
    assert result["producible_topics"] == []
    row = tmp_db.get_hotspot_discovery_request(int(result["request_id"]))
    assert row["topic"] == TRANSNET_TOPIC
    assert row["job_type"] == "topic_targeted_hotspot_intake"


def test_chat_transnet_topic_falls_back_to_owned_only_when_inventory_is_ready(tmp_db, monkeypatch):
    import asyncio
    import app

    monkeypatch.setattr(
        app,
        "_chat_video_delivery_readiness",
        lambda *_args, **kwargs: (
            {"status": "owned_only_ready", "delivery_ready": True, "adaptation": {"adapted": True}}
            if kwargs.get("chain_mode") == "owned_only"
            else {"status": "needs_hook", "delivery_ready": False}
        ),
    )
    result = asyncio.run(app._retrieve_confirmed_chat_hooks(
        TRANSNET_TOPIC,
        1,
        content_mode="hotspot",
        event_anchor=chat_intent.assess_event_anchor(TRANSNET_TOPIC),
    ))

    assert result["status"] == "owned_fallback"
    assert result["video"]["chain_mode"] == "owned_only"
    assert result["video"]["hotspot_event_ids"] == []
    assert result["request_id"] is None
    assert tmp_db.list_hotspot_discovery_requests() == []


def test_chat_evergreen_topic_binds_confirmed_generic_logistics_hook(tmp_db, monkeypatch):
    import asyncio
    import app
    import database as db

    hotspot_id, _ = db.upsert_hotspot({
        "title": "Buffalo evergreen warehouse opener", "summary": "",
        "source_url": "", "publisher": "", "published_at": "",
        "retrieved_at": "", "snapshot_sha256": "generic-chat-opener",
    })
    asset_id = db.create_asset({
        "name": "Buffalo 常青仓储开场", "filepath": "assets/generic-chat-opener.mp4",
        "file_type": "video", "category": "warehouse", "duration": 7,
        "size": 10, "source": "local_directory", "status": "active", "sha256": "h" * 64,
    })
    event = db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 7_000,
        "title_zh": "仓库仓储作业场景", "title_en": "Warehouse storage and sorting scenes",
        "hook_kind": "generic_logistics", "logistics_scenes": ["warehouse"],
        "review_status": "confirmed", "segments": [],
        "evidence": {
            "what_happened": "展示了仓库内仓储、分拣与货架作业的典型画面。",
            "hook_reason": "作为常青物流话题的通用开场画面。",
            "logistics_question": "海外仓与本地仓的仓储、分拣环节如何运作？",
            "event_identity": "generic-warehouse-chat",
        },
    }])[0]
    db.update_hotspot_event_clip_media(event["id"], "assets/hotspot-events/generic-chat/event.mp4", None, "ready")

    async def choose_first(_brief, candidates, *_args, **_kwargs):
        return candidates[:1], {"used": False, "fallback": "test_first_generic"}

    monkeypatch.setattr(app, "_model_decide_marketing_hooks", choose_first)
    monkeypatch.setattr(app, "_chat_video_delivery_readiness", lambda *_args, **_kwargs: {
        "status": "delivery_ready", "delivery_ready": True,
    })

    result = asyncio.run(app._retrieve_confirmed_chat_hooks(
        "卖家出海实战经验分享", 1, content_mode="evergreen",
    ))

    assert result["status"] == "matched"
    assert result["hook_kind"] == "generic_logistics"
    assert result["video"]["hotspot_event_ids"] == [event["id"]]


def test_chat_transnet_ready_hook_binds_without_discovery(tmp_db, monkeypatch):
    import asyncio
    import app
    from tests.test_hotspot_hook_library_gates import _create_ready_chat_hook

    hotspot_id, event = _create_ready_chat_hook(
        tmp_db,
        title="Transnet 德班港集装箱装卸延误",
        summary="港口装卸排队",
        event_title="Transnet 德班港集装箱装卸延误",
        what_happened="Transnet 德班港集装箱装卸排队",
        logistics_question="港口延误后跨境卖家要先核对哪一趟船期？",
        snapshot="transnet-ready-hook",
    )
    tmp_db.update_hotspot_event_hook_kind(int(event["id"]), hook_kind="timely_event", logistics_scenes=["port"])
    tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "asset_id": event["asset_id"],
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": "transnetready",
        "publisher": "Transnet NPA",
        "source_class": "official_logistics",
        "source_page_url": "https://www.youtube.com/watch?v=transnetready",
        "original_media_url": "https://www.youtube.com/watch?v=transnetready",
        "download_status": "downloaded",
        "rights_tier": "authorized",
    })
    refresh_calls = []
    monkeypatch.setattr(app.sched, "request_targeted_hotspot_refresh", lambda: refresh_calls.append(True) or True)
    result = asyncio.run(app._retrieve_confirmed_chat_hooks(
        TRANSNET_TOPIC,
        1,
        content_mode="hotspot",
        event_anchor=chat_intent.assess_event_anchor(TRANSNET_TOPIC),
    ))
    assert result["status"] == "matched"
    assert result["video"]["status"] == "ready"
    assert int(event["id"]) in result["video"]["hotspot_event_ids"]
    assert refresh_calls == []


def test_chat_html_shows_task_id_and_forbids_fake_rescan():
    from pathlib import Path
    page = (Path(__file__).parents[1] / "static" / "chat.html").read_text()
    assert "任务 ID" in page
    assert "没有定向采集任务 ID，不能显示“已立即复扫”" in page
    assert "不会用无关新闻强行成片" in page
    assert "等待热点 Hook" in page
