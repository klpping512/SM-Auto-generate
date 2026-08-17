import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _candidate(tmp_db):
    admin_id = tmp_db.create_user("admin", "hash", "admin", "管理员")
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Port freight queue affects cargo handling",
        "summary": "Collector placeholder only",
        "source_url": "https://www.youtube.com/watch?v=workflow001",
        "publisher": "SA Today",
        "published_at": "2026-07-28T00:00:00+00:00",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": "prewarm-workflow",
    })
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": "workflow001",
        "source_page_url": "https://www.youtube.com/watch?v=workflow001",
        "original_media_url": "https://www.youtube.com/watch?v=workflow001",
        "duration_seconds": 240,
        "rights_tier": "green",
        "download_status": "metadata_ready",
    })
    return admin_id, hotspot_id, media_id


def test_legacy_list_intake_decision_never_blocks_hook_curation():
    import app

    assert app._normalized_hotspot_intake_decision('[]') == {}
    assert app._normalized_hotspot_intake_decision('{"expected_hook":"货车排队"}') == {
        "expected_hook": "货车排队",
    }


@pytest.mark.asyncio
async def test_prewarm_reads_video_facts_and_materializes_every_authorized_candidate(tmp_db, monkeypatch):
    import app
    import scheduler

    _admin_id, hotspot_id, media_id = _candidate(tmp_db)
    second_media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": "workflow002",
        "source_page_url": "https://www.youtube.com/watch?v=workflow002",
        "original_media_url": "https://www.youtube.com/watch?v=workflow002",
        "duration_seconds": 240,
        "rights_tier": "green",
        "download_status": "metadata_ready",
    })
    direct_media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "direct",
        "source_page_url": "https://media.example.com/warehouse-update.mp4",
        "original_media_url": "https://media.example.com/warehouse-update.mp4",
        "rights_tier": "green",
        "download_status": "metadata_ready",
    })
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")
    seen = {"hydrated": None, "materialized": []}

    def hydrate(rows, **_kwargs):
        assert {item["id"] for item in rows} == {media_id, second_media_id, direct_media_id}
        hydrated = [
            {
                **row,
                "intake_title": f"Freight video {row['id']}",
                "intake_summary": "Cargo trucks wait while staff check incoming freight vehicles.",
                "intake_metadata_status": "ready",
            }
            for row in rows
        ]
        seen["hydrated"] = hydrated
        return hydrated, {"requested": 3, "ready": 3, "cached": 0, "failed": []}

    async def materialize(media_id_arg, created_by):
        seen["materialized"].append((media_id_arg, created_by))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media()

    assert seen["hydrated"]
    assert {item[0] for item in seen["materialized"]} == {media_id, second_media_id, direct_media_id}
    assert all(item[1] == _admin_id for item in seen["materialized"])
    state = tmp_db.get_hotspot_media(media_id)
    assert state["download_status"] == "pending"
    assert "下载、分析" in state["progress_detail"]
    assert report["status"] == "materialized"
    assert set(report["selected_media_ids"]) == {media_id, second_media_id, direct_media_id}
    assert report["intake"]["mode"] == "all_authorized_video_analysis"


@pytest.mark.asyncio
async def test_prewarm_never_calls_qwen_or_download_when_video_facts_cannot_be_read(tmp_db, monkeypatch):
    import scheduler

    _candidate(tmp_db)
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")

    def hydrate(rows, **_kwargs):
        return [{**rows[0], "intake_metadata_status": "failed"}], {
            "requested": 1, "ready": 0, "cached": 0, "failed": [{"media_id": rows[0]["id"], "error": "unavailable"}],
        }

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    report = await scheduler.prewarm_authorized_hotspot_media()

    assert report["status"] == "metadata_unavailable"


@pytest.mark.asyncio
async def test_targeted_prewarm_retries_legacy_prefiltered_media(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    tmp_db.update_hotspot_media_state(
        media_id,
        download_status="prefiltered_skip",
        processing_status="not_started",
        progress_detail="prefilter skip: noise_topic_blocklist:trial",
    )
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")

    def hydrate(rows, **_kwargs):
        assert [item["id"] for item in rows] == [media_id]
        return [{**rows[0], "intake_metadata_status": "ready"}], {
            "requested": 0, "ready": 0, "cached": 1, "failed": [],
        }

    seen = []

    async def materialize(media_id_arg, created_by):
        seen.append((media_id_arg, created_by))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media(media_ids=[media_id])

    assert seen == [(media_id, admin_id)]
    assert report["selected_media_ids"] == [media_id]


@pytest.mark.asyncio
async def test_targeted_discovery_only_materializes_fetched_media(tmp_db, monkeypatch):
    import scheduler

    admin_id = tmp_db.create_user("admin", "hash", "admin", "管理员")
    tmp_db.enqueue_hotspot_discovery_request(
        "Transnet又有动静！跨境卖家先别慌",
        admin_id,
        query={"entities": ["Transnet"], "source_classes": ["official_logistics"]},
    )
    monkeypatch.setenv("TOPIC_HOOK_AUTOFETCH_ENABLED", "1")
    monkeypatch.setattr(scheduler.hotspot_fetcher, "configured_video_channels", lambda: [])
    monkeypatch.setattr(scheduler.hotspot_fetcher, "configured_feeds", lambda: [])
    media_rows = {
        media_id: {"id": media_id, "source_class": "general_news", "published_at": "2026-08-10"}
        for media_id in range(1, 26)
    }
    media_rows[25]["source_class"] = "official_logistics"
    media_rows[25]["published_at"] = "2026-08-01"
    monkeypatch.setattr(scheduler.db, "get_hotspot_media", lambda media_id: media_rows.get(media_id))

    async def fetch_hotspots(**_kwargs):
        return {"new": 25, "video_media": 25, "media_ids": list(range(1, 26))}

    seen = {}

    async def targeted_prewarm(*, media_ids=None):
        seen["media_ids"] = list(media_ids or [])
        return {"status": "materialized", "summary": {"confirmed_hooks": 0}}

    monkeypatch.setattr(scheduler.hotspot_fetcher, "fetch_hotspots", fetch_hotspots)
    monkeypatch.setattr(scheduler, "prewarm_authorized_hotspot_media", targeted_prewarm)

    report = await scheduler.refresh_targeted_hotspot_hooks()

    assert len(seen["media_ids"]) == 20
    assert seen["media_ids"][0] == 25
    assert report["status"] == "completed"
    row = tmp_db.list_hotspot_discovery_requests(limit=1)[0]
    assert row["status"] == "no_match"


@pytest.mark.asyncio
async def test_targeted_discovery_timeout_records_explicit_failure(tmp_db, monkeypatch):
    import scheduler

    admin_id = tmp_db.create_user("admin", "hash", "admin", "管理员")
    tmp_db.enqueue_hotspot_discovery_request(
        "Transnet又有动静！跨境卖家先别慌", admin_id,
    )
    monkeypatch.setenv("TOPIC_HOOK_AUTOFETCH_ENABLED", "1")
    monkeypatch.setattr(scheduler.hotspot_fetcher, "configured_video_channels", lambda: [])
    monkeypatch.setattr(scheduler.hotspot_fetcher, "configured_feeds", lambda: [])

    async def timeout_fetch(**_kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(scheduler.hotspot_fetcher, "fetch_hotspots", timeout_fetch)
    report = await scheduler.refresh_targeted_hotspot_hooks()

    assert report["status"] == "failed"
    row = tmp_db.list_hotspot_discovery_requests(limit=1)[0]
    assert row["status"] == "failed"
    assert "超时" in row["error_message"]


@pytest.mark.asyncio
async def test_targeted_prewarm_recurates_downloaded_mother_without_hooks(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    asset_id = tmp_db.create_asset({
        "name": "已分析但未产出 Hook 的母片",
        "filepath": "assets/library/video/targeted-recuration.mp4",
        "file_type": "video",
        "category": "other",
        "duration": 240,
        "width": 1920,
        "height": 1080,
        "size": 100,
        "thumbnail": "assets/thumbnails/targeted-recuration.jpg",
        "sha256": "8" * 64,
        "source": "youtube",
        "status": "active",
        "created_by": None,
    })
    tmp_db.update_hotspot_media_state(
        media_id,
        asset_id=asset_id,
        download_status="downloaded",
        processing_status="ready",
        progress_detail="镜头已分析，但内置模型未筛出可复用 Hook",
    )
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")
    seen = []

    def hydrate(rows, **_kwargs):
        assert [item["id"] for item in rows] == [media_id]
        return [{
            **rows[0],
            "intake_title": "Freight update",
            "intake_summary": "Truck queue update.",
            "intake_metadata_status": "ready",
        }], {"requested": 0, "ready": 0, "cached": 1, "failed": []}

    async def materialize(media_id_arg, created_by):
        seen.append((media_id_arg, created_by, tmp_db.get_hotspot_media(media_id_arg)["asset_id"]))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media(media_ids=[media_id])

    assert seen == [(media_id, admin_id, asset_id)]
    assert report["selected_media_ids"] == [media_id]


@pytest.mark.asyncio
async def test_prewarm_defaults_to_every_authorized_video_without_duration_filter(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, hotspot_id, _media_id = _candidate(tmp_db)
    short_media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": "short-authorized-video",
        "source_page_url": "https://www.youtube.com/watch?v=short-authorized-video",
        "original_media_url": "https://www.youtube.com/watch?v=short-authorized-video",
        "duration_seconds": 18,
        "rights_tier": "green",
        "download_status": "metadata_ready",
    })
    monkeypatch.delenv("HOTSPOT_HOOK_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("HOTSPOT_PREWARM_ENABLED", raising=False)
    monkeypatch.delenv("HOTSPOT_PREWARM_MIN_DURATION_SECONDS", raising=False)
    seen = []

    def hydrate(rows, **_kwargs):
        return [
            {**row, "intake_title": "Freight brief", "intake_summary": "A short logistics update.",
             "intake_metadata_status": "ready"}
            for row in rows
        ], {"requested": len(rows), "ready": len(rows), "cached": 0, "failed": []}

    async def materialize(media_id_arg, created_by):
        seen.append((media_id_arg, created_by))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media(media_ids=[short_media_id])

    assert report["selected_media_ids"] == [short_media_id]
    assert seen == [(short_media_id, admin_id)]


@pytest.mark.asyncio
async def test_prewarm_uses_individual_video_duration_before_excluding_a_channel_item(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    # The flat channel feed can expose an inaccurate short duration. The
    # project must first fetch the video's own facts before applying the
    # 3-minute Hook-analysis threshold.
    tmp_db.update_hotspot_media_state(media_id, duration_seconds=45)
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")
    seen = {"hydrated": [], "materialized": []}

    def hydrate(rows, **_kwargs):
        seen["hydrated"] = [item["id"] for item in rows]
        return [{
            **rows[0], "duration_seconds": 240,
            "intake_title": "Freight queue update",
            "intake_summary": "Trucks wait at an active cargo checkpoint.",
            "intake_metadata_status": "ready",
        }], {"requested": 1, "ready": 1, "cached": 0, "failed": []}

    async def materialize(media_id_arg, created_by):
        seen["materialized"].append((media_id_arg, created_by))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media()

    assert seen["hydrated"] == [media_id]
    assert seen["materialized"] == [(media_id, admin_id)]
    assert report["selected_media_ids"] == [media_id]


@pytest.mark.asyncio
async def test_prewarm_requeues_an_authorized_download_interrupted_by_service_restart(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    tmp_db.update_hotspot_media_state(media_id, download_status="downloading", download_progress=42)
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")
    seen = []

    def hydrate(rows, **_kwargs):
        return [{
            **rows[0], "intake_title": "Freight update", "intake_summary": "Truck queue update.",
            "intake_metadata_status": "ready",
        }], {"requested": 1, "ready": 1, "cached": 0, "failed": []}

    async def materialize(media_id_arg, created_by):
        seen.append((media_id_arg, created_by))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media()

    assert report["selected_media_ids"] == [media_id]
    assert seen == [(media_id, admin_id)]
    assert tmp_db.get_hotspot_media(media_id)["download_status"] == "pending"


@pytest.mark.asyncio
async def test_prewarm_keeps_a_downloaded_mother_video_for_resume(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    asset_id = tmp_db.create_asset({
        "name": "已下载热点母片",
        "filepath": "assets/library/video/prewarm-resume.mp4",
        "file_type": "video",
        "category": "other",
        "duration": 240,
        "width": 1080,
        "height": 1920,
        "size": 100,
        "thumbnail": "assets/thumbnails/prewarm-resume.jpg",
        "sha256": "9" * 64,
        "source": "youtube",
        "status": "active",
        "created_by": None,
    })
    tmp_db.update_hotspot_media_state(
        media_id, asset_id=asset_id, download_status="downloading", processing_status="not_started"
    )
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")
    seen = []

    def hydrate(rows, **_kwargs):
        return [{
            **rows[0], "intake_title": "Freight update", "intake_summary": "Truck queue update.",
            "intake_metadata_status": "ready",
        }], {"requested": 1, "ready": 1, "cached": 0, "failed": []}

    async def materialize(media_id_arg, created_by):
        state = tmp_db.get_hotspot_media(media_id_arg)
        seen.append((media_id_arg, created_by, state["download_status"], state["asset_id"]))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    await scheduler.prewarm_authorized_hotspot_media()

    assert seen == [(media_id, admin_id, "downloaded", asset_id)]


@pytest.mark.asyncio
async def test_prewarm_retries_a_previous_download_failure_on_the_next_full_cycle(tmp_db, monkeypatch):
    import app
    import scheduler

    admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    tmp_db.update_hotspot_media_state(
        media_id, download_status="download_failed", error_message="下载超时：上轮网络不可用"
    )
    monkeypatch.setenv("HOTSPOT_HOOK_SYNC_ENABLED", "1")
    seen = []

    def hydrate(rows, **_kwargs):
        return [{
            **rows[0], "intake_title": "Freight update", "intake_summary": "Truck queue update.",
            "intake_metadata_status": "ready",
        }], {"requested": 1, "ready": 1, "cached": 0, "failed": []}

    async def materialize(media_id_arg, created_by):
        seen.append((media_id_arg, created_by))

    monkeypatch.setattr(scheduler.hotspot_video_sources, "hydrate_youtube_intake_metadata", hydrate)
    monkeypatch.setattr(scheduler.model_router, "key_is_available", lambda _route: True)
    monkeypatch.setattr(app, "_run_hotspot_media_materialization", materialize)

    report = await scheduler.prewarm_authorized_hotspot_media()

    assert report["selected_media_ids"] == [media_id]
    assert seen == [(media_id, admin_id)]


def test_startup_requeues_only_retryable_hook_curation_failures(tmp_db):
    _admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    tmp_db.update_hotspot_media_state(
        media_id,
        download_status="downloaded",
        processing_status="ready",
        progress_detail="镜头已分析，但内置模型未筛出可复用 Hook：内置 Hook 策展暂时不可用：模型预算不足",
    )

    recovered = tmp_db.recover_retryable_hotspot_hook_curation()

    item = tmp_db.get_hotspot_media(media_id)
    assert recovered == 1
    assert item["processing_status"] == "processing_failed"
    assert "策展暂时不可用" in item["error_message"]


@pytest.mark.asyncio
async def test_chat_targeted_refresh_rescans_authorised_sources_before_hook_intake(tmp_db, monkeypatch):
    import scheduler

    _admin_id, _hotspot_id, media_id = _candidate(tmp_db)
    tmp_db.enqueue_hotspot_discovery_request("德班港拥堵最新", requested_by=_admin_id)
    captured = {}

    async def fetch_hotspots(**kwargs):
        captured["fetch"] = kwargs
        return {"new": 2, "video_media": 3, "media_ids": [media_id]}

    async def prewarm(*, media_ids=None):
        captured["prewarm"] = True
        captured["prewarm_media_ids"] = list(media_ids or [])
        return {"status": "materialized"}

    monkeypatch.setattr(scheduler.hotspot_fetcher, "fetch_hotspots", fetch_hotspots)
    monkeypatch.setattr(scheduler, "prewarm_authorized_hotspot_media", prewarm)

    report = await scheduler.refresh_targeted_hotspot_hooks()

    assert report["status"] == "completed"
    import hotspot_video_sources

    assert captured["fetch"]["video_limit"] <= hotspot_video_sources.MAX_CHANNEL_VIDEO_LIMIT
    names = [item.get("name") for item in captured["fetch"]["video_channels"]]
    assert "Transnet NPA" in names
    assert names.index("Transnet NPA") < names.index("eNCA")
    assert captured["prewarm"] is True
    assert captured["prewarm_media_ids"] == [media_id]


def test_intake_metadata_sample_round_robins_newest_records_without_keyword_ranking():
    import scheduler

    sample = scheduler._intake_metadata_sample([
        {"id": 1, "publisher": "SABC", "published_at": "2026-07-28T01:00:00+00:00"},
        {"id": 2, "publisher": "SABC", "published_at": "2026-07-28T02:00:00+00:00"},
        {"id": 3, "publisher": "SA Today", "published_at": "2026-07-28T01:30:00+00:00"},
        {"id": 4, "publisher": "SA Today", "published_at": "2026-07-28T00:30:00+00:00"},
        {"id": 5, "publisher": "SA Now", "published_at": "2026-07-28T00:45:00+00:00"},
    ], 4)

    assert [item["id"] for item in sample] == [2, 3, 5, 1]


def test_manual_prewarm_script_enables_the_current_scheduler_gate():
    source = (Path(__file__).parents[1] / "scripts" / "run_authorized_hotspot_prewarm.py").read_text(encoding="utf-8")

    assert 'os.environ["HOTSPOT_HOOK_SYNC_ENABLED"] = "1"' in source
    assert "--fetch-only" in source
    assert "skipped_fetch_only" in source
    assert "if args.fetch_only:" in source
    assert "await scheduler.prewarm_authorized_hotspot_media" in source
    fetch_only_idx = source.index("if args.fetch_only:")
    prewarm_idx = source.index("await scheduler.prewarm_authorized_hotspot_media")
    assert fetch_only_idx < prewarm_idx
    assert "if not args.fetch_only:" in source
    assert "--refresh-sources" in source
    assert "if media_ids and not args.refresh_sources:" in source
    assert "skipped_for_media_ids" in source


def test_manual_prewarm_does_not_reintroduce_topic_prefilter_gate():
    source = (Path(__file__).parents[1] / "hotspot_media.py").read_text(encoding="utf-8")

    assert "PREFILTER_NOISE_TOPIC_BLOCKLIST" not in source
    assert "noise_topic_blocklist" not in source
