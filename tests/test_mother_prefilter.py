"""母片下载前元数据预筛。"""


def test_prefilter_skips_om_music_video():
    import hotspot_media

    ok, reason = hotspot_media.prefilter_mother_candidate({
        "title": "TNPA OM Music video",
        "duration_seconds": 210,
        "publisher": "Transnet NPA",
    })
    assert ok is False
    assert "title_blocklist" in reason
    assert "om music" in reason.casefold() or "music video" in reason.casefold()


def test_prefilter_allows_port_operations_title():
    import hotspot_media

    ok, reason = hotspot_media.prefilter_mother_candidate({
        "intake_title": "Dredge Master working at Port of Durban",
        "duration_seconds": 420,
        "publisher": "Transnet NPA",
    })
    assert ok is True
    assert reason == ""


def test_prefilter_skips_too_short_and_too_long():
    import hotspot_media

    ok_short, reason_short = hotspot_media.prefilter_mother_candidate({
        "title": "Port clip", "duration_seconds": 5, "publisher": "eNCA",
    })
    ok_long, reason_long = hotspot_media.prefilter_mother_candidate({
        "title": "Port live stream", "duration_seconds": 4000, "publisher": "eNCA",
    })
    assert ok_short is False and "too_short" in reason_short
    assert ok_long is False and "too_long" in reason_long
