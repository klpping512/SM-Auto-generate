import json
from types import SimpleNamespace

import pytest


def _payload():
    return {
        "entries": [
            {
                "id": f"video{index:02d}abc",
                "title": f"South Africa logistics update {index}",
                "duration": 90 + index,
                "timestamp": 1784690000 + index,
                "thumbnail": f"https://i.ytimg.com/vi/video{index:02d}abc/hqdefault.jpg",
            }
            for index in range(5)
        ]
    }


def test_default_youtube_sources_are_the_approved_channels():
    import hotspot_video_sources

    assert [item["url"] for item in hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS] == [
        "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA",
        "https://www.youtube.com/@NewzroomAfrikaTV",
        "https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ",
        "https://www.youtube.com/@BusinessDayTelevision",
        "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw",
        "https://www.youtube.com/@SANRALCorporate",
        "https://www.youtube.com/user/ParliamentofRSA",
        "https://www.youtube.com/user/JusticeGOVZA",
        "https://www.youtube.com/user/GovernmentZA",
        "https://www.youtube.com/c/sabcdigitalnews",
    ]
    assert [item["name"] for item in hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS] == [
        "eNCA",
        "Newzroom Afrika",
        "CNBC Africa",
        "BusinessDayTV",
        "Transnet NPA",
        "SANRAL Corporate",
        "Parliament of RSA",
        "JusticeGOVZA",
        "GovernmentZA",
        "SABC News",
    ]
    transnet = next(item for item in hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS if item["name"] == "Transnet NPA")
    assert transnet["evergreen"] is True
    assert int(transnet["min_downloadable"]) == 10
    assert int(transnet["playlist_scan_cap"]) == 20


def test_configured_channels_env_overrides_defaults(monkeypatch):
    import hotspot_video_sources

    monkeypatch.setenv(
        "SA_HOTSPOT_VIDEO_CHANNELS_JSON",
        json.dumps([
            {"name": "eNCA", "url": "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA"},
            {"name": "bad", "url": "https://example.com/@notyoutube"},
            {"name": "", "url": "https://www.youtube.com/@nameless"},
        ]),
    )
    channels = hotspot_video_sources.configured_channels()
    assert channels == [
        {"name": "eNCA", "url": "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA", "source_class": "general_news"},
    ]


def test_configured_channels_inherits_evergreen_for_transnet(monkeypatch):
    import hotspot_video_sources

    monkeypatch.setenv(
        "SA_HOTSPOT_VIDEO_CHANNELS_JSON",
        json.dumps([
            {"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"},
        ]),
    )
    channels = hotspot_video_sources.configured_channels()
    assert channels[0]["evergreen"] is True
    assert channels[0]["min_downloadable"] == 10
    assert channels[0]["playlist_scan_cap"] == 20


def test_configured_channels_falls_back_to_defaults(monkeypatch):
    import hotspot_video_sources

    monkeypatch.delenv("SA_HOTSPOT_VIDEO_CHANNELS_JSON", raising=False)
    assert hotspot_video_sources.configured_channels() == hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS

    monkeypatch.setenv("SA_HOTSPOT_VIDEO_CHANNELS_JSON", "not json")
    assert hotspot_video_sources.configured_channels() == hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS


def test_channel_command_uses_configured_proxy_without_cookie(monkeypatch):
    import hotspot_video_sources

    monkeypatch.setenv("SA_YOUTUBE_PROXY", "http://127.0.0.1:7897")
    command = hotspot_video_sources._command(
        "https://www.youtube.com/@SAtoday", 3
    )

    assert command[command.index("--proxy") + 1] == "http://127.0.0.1:7897"
    assert not any("cookie" in value.casefold() for value in command)


def test_single_video_metadata_read_is_explicitly_no_download(monkeypatch):
    import hotspot_video_sources

    monkeypatch.setenv("SA_YOUTUBE_PROXY", "http://127.0.0.1:7897")
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "title": "Warehouse staff inspect parcels before loading",
            "description": "Footage shows parcel inspection and outbound loading at a warehouse.",
            "tags": ["warehouse", "parcel", "loading"],
            "duration": 243,
            "upload_date": "20260728",
        }), stderr="")

    metadata = hotspot_video_sources.read_youtube_video_metadata(
        "https://www.youtube.com/watch?v=warehouse001", runner=runner
    )

    assert metadata["title"] == "Warehouse staff inspect parcels before loading"
    assert "parcel inspection" in metadata["summary"]
    assert metadata["duration_seconds"] == 243.0
    assert metadata["published_at"] == "2026-07-28T00:00:00+00:00"
    command = commands[0]
    assert "--skip-download" in command
    assert "--no-playlist" in command
    assert "--dump-single-json" in command
    assert command[command.index("--proxy") + 1] == "http://127.0.0.1:7897"
    assert not any(value in {"--output", "-o"} for value in command)


def test_authorized_youtube_metadata_is_persisted_for_qwen_intake(tmp_db):
    import hotspot_video_sources

    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Daily bulletin",
        "summary": "来自 SA Today 的公开视频热点。",
        "source_url": "https://www.youtube.com/watch?v=warehouse002",
        "publisher": "SA Today",
        "retrieved_at": "2026-07-28T00:00:00+00:00",
        "snapshot_sha256": "metadata-source",
    })
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "source_page_url": "https://www.youtube.com/watch?v=warehouse002",
        "original_media_url": "https://www.youtube.com/watch?v=warehouse002",
        "duration_seconds": 240,
        "rights_tier": "green",
        "download_status": "metadata_ready",
    })

    def runner(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "title": "Warehouse staff inspect parcels before loading",
            "description": "Staff check parcels, sort orders and load outbound delivery vehicles.",
            "duration": 245,
            "upload_date": "20260728",
        }), stderr="")

    rows, report = hotspot_video_sources.hydrate_youtube_intake_metadata(
        [tmp_db.get_hotspot_media(media_id)], runner=runner
    )

    assert report == {"requested": 1, "ready": 1, "cached": 0, "failed": []}
    assert rows[0]["intake_metadata_status"] == "ready"
    assert rows[0]["intake_title"] == "Warehouse staff inspect parcels before loading"
    assert "load outbound delivery vehicles" in rows[0]["intake_summary"]
    persisted = tmp_db.get_hotspot_media(media_id)
    assert persisted["duration_seconds"] == 245.0
    assert persisted["intake_metadata_checked_at"]
    assert persisted["source_class"] == "general_news"
    assert tmp_db.get_hotspot(hotspot_id)["published_at"] == "2026-07-28T00:00:00+00:00"


def test_hydrate_records_failure_reason_and_does_not_leave_row_immediately_retryable(tmp_db):
    import hotspot_video_sources

    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Missing video",
        "summary": "来自 eNCA 的公开视频热点。",
        "source_url": "https://www.youtube.com/watch?v=missing001",
        "publisher": "eNCA",
        "retrieved_at": "2026-08-17T00:00:00+00:00",
        "snapshot_sha256": "missing-video",
    })
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "source_page_url": "https://www.youtube.com/watch?v=missing001",
        "original_media_url": "https://www.youtube.com/watch?v=missing001",
        "rights_tier": "green",
        "download_status": "metadata_ready",
    })

    def runner(_command, **_kwargs):
        raise RuntimeError("This video is not available")

    rows, report = hotspot_video_sources.hydrate_youtube_intake_metadata(
        [tmp_db.get_hotspot_media(media_id)], runner=runner
    )

    assert report["failed"][0]["media_id"] == media_id
    persisted = tmp_db.get_hotspot_media(media_id)
    assert persisted["intake_metadata_status"] == "failed"
    assert persisted["failure_count"] == 1
    assert persisted["failure_reason"]
    assert persisted["retry_after"]
    assert persisted["download_status"] == "metadata_ready"
    assert hotspot_video_sources.hotspot_intake_policy.is_incremental_eligible(persisted) is False


def test_channel_discovery_reads_metadata_items_without_downloading(tmp_db):
    import hotspot_video_sources

    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(_payload()), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        runner=runner,
        precheck=False,
    )

    assert result["new"] == 5
    assert result["media"] == 5
    assert result["downloadable"] == 5
    assert len(result["accepted_media_ids"]) == 5
    assert result["source_health"][0]["name"] == "YouTube · SA Today"
    assert result["source_health"][0]["status"] == "ok"
    assert result["source_health"][0]["items"] == 5
    assert result["source_health"][0]["downloadable"] == 5
    command = commands[0]
    assert "--flat-playlist" in command
    assert command[command.index("--playlist-end") + 1] == str(hotspot_video_sources.CHANNEL_VIDEO_LIMIT)
    assert not any("cookie" in value.casefold() for value in command)
    assert not any(value in {"--output", "-o"} for value in command)

    hotspots = tmp_db.list_hotspots(limit=10)
    assert len(hotspots) == 5
    media = tmp_db.list_hotspot_media(media_kind="video_link", limit=10)
    assert len(media) == 5
    signals = tmp_db.list_hotspot_signals(limit=10)
    assert len(signals) == 5
    assert {signal["source_type"] for signal in signals} == {"youtube"}
    assert all(item["platform"] == "youtube" for item in media)
    assert all(item["authorization_status"] == "authorized" for item in media)
    assert all(item["download_status"] == "metadata_ready" for item in media)


def test_youtube_precheck_is_opt_in(monkeypatch):
    import hotspot_video_sources

    monkeypatch.delenv("HOTSPOT_YOUTUBE_PRECHECK", raising=False)
    assert hotspot_video_sources.youtube_precheck_enabled() is False
    monkeypatch.setenv("HOTSPOT_YOUTUBE_PRECHECK", "1")
    assert hotspot_video_sources.youtube_precheck_enabled() is True


def test_channel_discovery_allows_a_bounded_admin_backfill_batch(tmp_db):
    import hotspot_video_sources

    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(_payload()), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        runner=runner, limit=5, precheck=False,
    )

    assert result["new"] == 5
    assert commands[0][commands[0].index("--playlist-end") + 1] == "5"


def test_precheck_marks_unavailable_as_retryable_and_keeps_downloadable(tmp_db):
    import hotspot_video_sources

    entries = [
        {"id": "good01", "title": "Port dredge master", "duration": 120, "timestamp": 1784690001},
        {"id": "bad01", "title": "Just published", "duration": 60, "timestamp": 1784690002},
        {"id": "good02", "title": "Harbour crane", "duration": 90, "timestamp": 1784690003},
    ]

    def runner(command, **_kwargs):
        if "-F" in command:
            url = command[-1]
            if "bad01" in url:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="ERROR: This video is not available",
                )
            return SimpleNamespace(returncode=0, stdout="137 mp4 1080p\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": entries}), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"}],
        runner=runner,
        limit=3,
        precheck=True,
    )
    assert result["downloadable"] == 2
    assert result["retryable"] == 1
    media = tmp_db.list_hotspot_media(media_kind="video_link", limit=10)
    by_id = {item["platform_media_id"]: item for item in media}
    assert by_id["good01"]["download_status"] == "metadata_ready"
    assert by_id["bad01"]["download_status"] == "materialization_retryable"
    assert by_id["bad01"]["materialization_retryable"] == 1
    assert by_id["bad01"]["retry_after"]


def test_evergreen_channel_scans_full_playlist_cap(tmp_db):
    import hotspot_video_sources

    # 前 4 条不可下，后 4 条可下；evergreen 必须扫完 scan_cap=8，不能在凑满 min_downloadable 后提前停。
    entries = [
        {"id": f"v{i:02d}", "title": f"clip {i}", "duration": 80, "timestamp": 1784690000 + i}
        for i in range(8)
    ]
    unavailable = {f"v{i:02d}" for i in range(4)}

    def runner(command, **_kwargs):
        if "-F" in command:
            url = command[-1]
            vid = url.rsplit("=", 1)[-1]
            if vid in unavailable:
                return SimpleNamespace(returncode=1, stdout="", stderr="This video is not available")
            return SimpleNamespace(returncode=0, stdout="22 mp4\n", stderr="")
        assert command[command.index("--playlist-end") + 1] == "8"
        return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": entries}), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{
            "name": "Transnet NPA",
            "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw",
            "evergreen": True,
            "min_downloadable": 3,
            "playlist_scan_cap": 8,
        }],
        runner=runner,
        precheck=True,
    )
    assert result["downloadable"] == 4
    assert result["retryable"] == 4
    assert result["source_health"][0]["scanned"] == 8


def test_non_evergreen_channel_stops_at_limit(tmp_db):
    import hotspot_video_sources

    entries = [
        {"id": f"n{i:02d}", "title": f"news {i}", "duration": 80, "timestamp": 1784690000 + i}
        for i in range(6)
    ]

    def runner(command, **_kwargs):
        if "-F" in command:
            return SimpleNamespace(returncode=0, stdout="22 mp4\n", stderr="")
        assert command[command.index("--playlist-end") + 1] == "3"
        return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": entries}), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{
            "name": "CNBC Africa",
            "url": "https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ",
        }],
        runner=runner,
        limit=3,
        precheck=True,
    )
    assert result["downloadable"] == 3
    assert result["source_health"][0]["scanned"] == 3


def test_refetch_does_not_downgrade_downloaded_mother(tmp_db):
    import hotspot_video_sources

    entries = [
        {"id": "kept01", "title": "Port ops", "duration": 90, "timestamp": 1784690001},
    ]

    def runner(command, **_kwargs):
        if "-F" in command:
            return SimpleNamespace(returncode=0, stdout="22 mp4\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"entries": entries}), stderr="")

    hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"}],
        runner=runner, limit=1, precheck=True,
    )
    media = tmp_db.list_hotspot_media(media_kind="video_link", limit=5)[0]
    tmp_db.update_hotspot_media_state(
        media["id"],
        download_status="downloaded",
        processing_status="ready",
        progress_detail="已分析",
    )
    hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"}],
        runner=runner, limit=1, precheck=True,
    )
    refreshed = tmp_db.get_hotspot_media(media["id"])
    assert refreshed["download_status"] == "downloaded"
    assert refreshed["processing_status"] == "ready"



def test_channel_failure_is_isolated_in_health_result(tmp_db):
    import hotspot_video_sources

    def runner(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="channel unavailable")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        runner=runner,
    )

    assert result["new"] == 0
    assert result["errors"] == [
        {"feed": "YouTube · SA Today", "error": "channel unavailable"}
    ]
    assert result["source_health"][0]["status"] == "error"
    assert tmp_db.list_hotspots() == []


@pytest.mark.asyncio
async def test_main_hotspot_fetch_combines_youtube_channel_results(tmp_db, tmp_path, monkeypatch):
    import hotspot_fetcher

    monkeypatch.setenv("HOTSPOT_YOUTUBE_PRECHECK", "1")

    def runner(command, **kwargs):
        if "-F" in command:
            return SimpleNamespace(returncode=0, stdout="22 mp4 720p\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(_payload()), stderr="")

    result = await hotspot_fetcher.fetch_hotspots(
        tmp_path,
        feeds=[],
        video_channels=[{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        video_runner=runner,
    )

    assert result["video_channels"] == 1
    assert result["video_new"] == 5
    assert result["video_media"] == 5
    assert result["source_health"][0]["name"] == "YouTube · SA Today"
