import pytest


@pytest.mark.parametrize(("url", "expected"), [
    ("https://youtu.be/abcDEF123?si=tracking", "https://www.youtube.com/watch?v=abcDEF123"),
    ("https://www.youtube.com/watch?v=abcDEF123&utm_source=test", "https://www.youtube.com/watch?v=abcDEF123"),
    ("https://www.tiktok.com/@brand/video/741234567890?lang=en", "https://www.tiktok.com/@brand/video/741234567890"),
])
def test_normalize_inspiration_url_removes_tracking_and_deduplicates(url, expected):
    from inspiration_assets import normalize_url

    assert normalize_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://youtube.com/watch?v=abc", "https://localhost/video", "https://127.0.0.1/video",
    "https://user:secret@example.com/video",
])
def test_normalize_inspiration_url_rejects_unsafe_urls(url):
    from inspiration_assets import normalize_url

    with pytest.raises(ValueError):
        normalize_url(url)


def test_external_link_requires_admin_rights_evidence_and_explicit_confirmation():
    from inspiration_assets import validate_materialization

    item = {
        "source_type": "youtube", "rights_status": "confirmed",
        "license_name": "客户书面授权", "attribution": "原作者",
        "rights_evidence_url": "https://example.com/permission",
    }
    assert validate_materialization(item, "admin", confirmed=True) is None

    with pytest.raises(PermissionError):
        validate_materialization(item, "editor", confirmed=True)
    with pytest.raises(ValueError, match="人工确认"):
        validate_materialization(item, "admin", confirmed=False)
    with pytest.raises(ValueError, match="授权"):
        validate_materialization({**item, "license_name": ""}, "admin", confirmed=True)


def test_official_item_with_explicit_open_license_can_be_auto_materialized():
    from inspiration_assets import can_auto_materialize_official

    assert can_auto_materialize_official({
        "source_type": "official_news", "rights_status": "licensed",
        "license_name": "CC BY 4.0", "rights_evidence_url": "https://example.gov.za/license",
        "media_url": "https://example.gov.za/media/port.jpg",
    }) is True
    assert can_auto_materialize_official({
        "source_type": "official_news", "rights_status": "unknown", "media_url": "https://example.gov.za/port.jpg",
    }) is False


def test_youtube_download_options_use_proxy_progress_and_bounded_clip(monkeypatch):
    import inspiration_assets

    monkeypatch.delenv("SA_HOTSPOT_PROXY", raising=False)
    monkeypatch.setenv("SA_YOUTUBE_PROXY", "http://127.0.0.1:7897")
    events = []
    options = inspiration_assets.build_ytdlp_options(
        "youtube", duration_seconds=1368, progress_callback=events.append
    )

    assert options["proxy"] == "http://127.0.0.1:7897"
    assert options["socket_timeout"] == 20
    assert options["retries"] == 2
    assert "height<=720" in options["format"]
    assert options["download_ranges"] is not None
    assert options["progress_hooks"]
    assert "node" in options["js_runtimes"]


def test_youtube_download_options_prefer_hotspot_proxy_over_empty_youtube(monkeypatch):
    import inspiration_assets

    monkeypatch.setenv("SA_HOTSPOT_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("SA_YOUTUBE_PROXY", "")
    options = inspiration_assets.build_ytdlp_options("youtube", duration_seconds=240)
    assert options["proxy"] == "http://127.0.0.1:7897"
    assert options["download_ranges"] is not None


def test_youtube_download_keeps_full_file_for_short_clips(monkeypatch):
    import inspiration_assets

    monkeypatch.setenv("SA_HOTSPOT_PROXY", "http://127.0.0.1:7897")
    options = inspiration_assets.build_ytdlp_options("youtube", duration_seconds=120)
    assert "download_ranges" not in options
