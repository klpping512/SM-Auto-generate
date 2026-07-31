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


def test_default_youtube_sources_are_the_three_approved_channels():
    import hotspot_video_sources

    assert [item["url"] for item in hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS] == [
        "https://www.youtube.com/@SAtoday",
        "https://www.youtube.com/@SouthAfricaNow1",
        "https://www.youtube.com/@sabcdigitalnews",
    ]


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
        }), stderr="")

    metadata = hotspot_video_sources.read_youtube_video_metadata(
        "https://www.youtube.com/watch?v=warehouse001", runner=runner
    )

    assert metadata["title"] == "Warehouse staff inspect parcels before loading"
    assert "parcel inspection" in metadata["summary"]
    assert metadata["duration_seconds"] == 243.0
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


def test_channel_discovery_reads_three_metadata_items_without_downloading(tmp_db):
    import hotspot_video_sources

    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(_payload()), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        runner=runner,
    )

    assert result["new"] == 3
    assert result["media"] == 3
    assert result["source_health"] == [
        {"name": "YouTube · SA Today", "status": "ok", "items": 3, "error": ""}
    ]
    command = commands[0]
    assert "--flat-playlist" in command
    assert command[command.index("--playlist-end") + 1] == "3"
    assert not any("cookie" in value.casefold() for value in command)
    assert not any(value in {"--output", "-o"} for value in command)

    hotspots = tmp_db.list_hotspots(limit=10)
    assert len(hotspots) == 3
    media = tmp_db.list_hotspot_media(media_kind="video_link", limit=10)
    assert len(media) == 3
    signals = tmp_db.list_hotspot_signals(limit=10)
    assert len(signals) == 3
    assert {signal["source_type"] for signal in signals} == {"youtube"}
    assert all(item["platform"] == "youtube" for item in media)
    assert all(item["rights_tier"] == "yellow" for item in media)
    assert all(item["download_status"] == "metadata_ready" for item in media)


def test_channel_discovery_allows_a_bounded_admin_backfill_batch(tmp_db):
    import hotspot_video_sources

    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(_payload()), stderr="")

    result = hotspot_video_sources.fetch_youtube_channel_hotspots(
        [{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        runner=runner, limit=5,
    )

    assert result["new"] == 5
    assert commands[0][commands[0].index("--playlist-end") + 1] == "5"


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
async def test_main_hotspot_fetch_combines_youtube_channel_results(tmp_db, tmp_path):
    import hotspot_fetcher

    def runner(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(_payload()), stderr="")

    result = await hotspot_fetcher.fetch_hotspots(
        tmp_path,
        feeds=[],
        video_channels=[{"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"}],
        video_runner=runner,
    )

    assert result["video_channels"] == 1
    assert result["video_new"] == 3
    assert result["video_media"] == 3
    assert result["source_health"][0]["name"] == "YouTube · SA Today"
