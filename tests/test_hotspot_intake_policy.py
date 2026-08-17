from datetime import datetime, timedelta

import hotspot_intake_policy as policy


def test_failed_metadata_without_retry_after_does_not_block_incremental():
    stuck = {
        "id": 1315,
        "publisher": "eNCA",
        "download_status": "metadata_ready",
        "intake_metadata_status": "failed",
        "retry_after": None,
        "source_class": "general_news",
        "published_at": "2026-08-17T00:00:00+00:00",
    }
    fresh = {
        "id": 2001,
        "publisher": "Transnet NPA",
        "download_status": "metadata_ready",
        "intake_metadata_status": "pending",
        "source_class": "official_logistics",
        "published_at": "2026-08-16T00:00:00+00:00",
    }
    result = policy.select_incremental_media([stuck, fresh], 8)
    assert result["selected_ids"] == [2001]
    assert 1315 in result["skipped_failed_ids"]


def test_confirmed_metadata_failures_pause_after_three_tries():
    item = {"id": 9, "download_status": "metadata_ready", "failure_count": 2}
    state = policy.next_metadata_failure_state(item, "This video is not available", confirmed=True)
    assert state["download_status"] == "metadata_failed"
    assert state["failure_count"] == 3
    assert state["retry_after"] is None


def test_library_sync_next_run_is_restored_not_pushed(tmp_db):
    import scheduler

    stored = (datetime.now() + timedelta(days=2)).replace(microsecond=0)
    tmp_db.upsert_scheduler_job_state(
        "hotspot_hook_library_sync",
        next_run_time=stored.isoformat(timespec="seconds"),
    )
    restored = scheduler._library_sync_next_run()
    assert restored.replace(microsecond=0) == stored


def test_hotspot_is_not_a_real_logistics_scene():
    event = {
        "title_zh": "政党集会现场",
        "title_en": "Political rally",
        "logistics_scenes": ["hotspot"],
        "evidence": {"what_happened": "候选人在台上发表竞选演讲"},
    }
    assert policy.has_real_logistics_scene(event) is False
    assert policy.real_logistics_scenes(["hotspot"], "候选人在台上发表竞选演讲") == []


def test_general_news_quota_holds_overflow_after_min_sample():
    events = []
    for index in range(7):
        events.append({
            "id": index + 1,
            "source_class": "official_logistics",
            "review_status": "confirmed",
            "evidence": {"logistics_question": "港口排队会影响哪个节点？", "what_happened": "港口卡车排队"},
        })
    for index in range(8, 18):
        events.append({
            "id": index,
            "source_class": "general_news",
            "review_status": "confirmed",
            "evidence": {"logistics_question": "道路中断要先核对哪段路？", "what_happened": "公路封闭"},
        })
    flags = policy.assign_ready_flags(events, is_hard_ready=lambda _event: True)
    official_ready = [event["id"] for event in events if flags[event["id"]]["is_renderable"] and event["source_class"] != "general_news"]
    general_ready = [event["id"] for event in events if flags[event["id"]]["is_renderable"] and event["source_class"] == "general_news"]
    assert official_ready == [1, 2, 3, 4, 5, 6, 7]
    assert len(general_ready) == 3
    assert flags[17]["quota_held"] is True


def test_fair_sample_puts_transnet_before_sanral_and_news():
    rows = [
        {"id": 1, "publisher": "eNCA", "source_class": "general_news", "published_at": "2026-08-17T10:00:00+00:00"},
        {"id": 2, "publisher": "SANRAL Corporate", "source_class": "official_logistics", "published_at": "2026-08-16T08:00:00+00:00"},
        {"id": 3, "publisher": "Transnet NPA", "source_class": "official_logistics", "published_at": "2026-08-16T07:00:00+00:00"},
        {"id": 4, "publisher": "SABC News", "source_class": "general_news", "published_at": "2026-08-17T09:00:00+00:00"},
    ]
    selected = policy.fair_sample(rows, 3)
    assert [item["publisher"] for item in selected[:2]] == ["Transnet NPA", "SANRAL Corporate"]
    result = policy.select_incremental_media(
        [{**row, "download_status": "metadata_ready", "intake_metadata_status": "pending"} for row in rows]
        + [{"id": 1315, "publisher": "eNCA", "download_status": "metadata_ready", "intake_metadata_status": "failed"}],
        8,
    )
    assert 1315 in result["skipped_failed_ids"]
    assert 1315 not in result["selected_ids"]
    assert result["known_stuck_in_incremental"] == []
    assert "Transnet NPA" in result["official_publishers"]


def test_generic_bridge_filler_is_rejected():
    assert policy.contains_generic_bridge_filler("异常后，Buffalo 核对仓内分拣。")
    assert policy.contains_generic_bridge_filler("接下来看看我们的解决方案。") is True
    assert policy.contains_generic_bridge_filler("道路封闭后，Buffalo 在仓内分拣前先核对异常订单。") is False
