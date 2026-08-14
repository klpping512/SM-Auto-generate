def test_public_hotspot_asset_is_labeled_as_hotspot_source(tmp_db):
    import media_assets

    asset_id = tmp_db.create_asset({
        "name": "热点素材",
        "filepath": "assets/library/video/hotspot.mp4",
        "file_type": "video",
        "category": "delivery",
        "primary_category": "delivery",
        "hotspot_id": 31,
        "duration": 120,
        "size": 10,
        "source": "youtube",
        "status": "active",
        "sha256": "h" * 64,
    })
    tmp_db.update_asset_provenance(asset_id, "https://example.com/hotspot", "", "SA Today", 31)

    public = media_assets.public_asset(tmp_db.get_asset(asset_id))

    assert public["library_origin"] == "hotspot"
    assert public["source_label"] == "热点素材"


def test_invalid_hi_res_range_is_rejected_before_preview_encoding(tmp_path, monkeypatch):
    import hotspot_event_media

    invalid = tmp_path / "hires-placeholder.mp4"
    invalid.write_bytes(b"\x00" * 262)
    monkeypatch.setattr(
        hotspot_event_media.inspiration_assets,
        "download_hi_res_range",
        lambda *_args, **_kwargs: invalid,
    )

    result = hotspot_event_media._try_hi_res_clip(
        tmp_path,
        {
            "id": 99,
            "original_media_url": "https://www.youtube.com/watch?v=test",
            "platform": "youtube",
        },
        {"id": 58, "start_ms": 5_600, "end_ms": 18_200},
    )

    assert result is None
    assert not invalid.exists()


def test_owned_asset_is_labeled_as_buffalo_source(tmp_db):
    import media_assets

    asset_id = tmp_db.create_asset({
        "name": "Buffalo 仓库",
        "filepath": "assets/library/video/owned.mp4",
        "file_type": "video",
        "category": "warehouse",
        "duration": 10,
        "size": 10,
        "source": "local",
        "status": "active",
        "sha256": "o" * 64,
    })

    public = media_assets.public_asset(tmp_db.get_asset(asset_id))

    assert public["library_origin"] == "owned"
    assert public["source_label"] == "Buffalo 原有素材"


def test_licensed_stock_asset_is_labeled_as_stock_source(tmp_db):
    """验收 #3 指令要求：za-stock 免版权素材显示「免版权素材」源标签。
    
    指令原文：a.source==="za_stock_license" → source_label="免版权素材"
    """
    import media_assets

    asset_id = tmp_db.create_asset({
        "name": "za_customs_pexels_11801939",
        "filepath": "assets/library/video/stock.mp4",
        "file_type": "video",
        "category": "customs",
        "primary_category": "customs",
        "duration": 10,
        "size": 10,
        "source": "za_stock_license",
        "status": "active",
        "sha256": "z" * 64,
    })

    public = media_assets.public_asset(tmp_db.get_asset(asset_id))

    assert public["library_origin"] == "owned"
    assert public["source_label"] == "免版权素材"


def test_build_event_clips_groups_short_shots_and_names_bilingually():
    from hotspot_event_clips import build_event_clips

    segments = [
        {"start_ms": 0, "end_ms": 6000, "transcript": "Cape Town Transnet land"},
        {"start_ms": 6000, "end_ms": 16000, "ocr_text": "Cape Town Western Cape"},
        {"start_ms": 16000, "end_ms": 23000, "transcript": "Kgalagadi park"},
    ]
    events = build_event_clips(segments, date="2026-07-22", source="SA Today")

    assert len(events) == 2
    assert events[0]["title_zh"] == "2026-07-22｜开普敦｜Transnet 土地事件｜SA Today"
    assert events[0]["title_en"] == "2026-07-22 | Cape Town | Transnet Land Event | SA Today"
    assert events[0]["start_ms"] == 0
    assert events[0]["end_ms"] == 16000


def test_unexplained_segment_does_not_become_a_generic_hotspot_event():
    from hotspot_event_clips import build_event_clips

    events = build_event_clips([{"start_ms": 0, "end_ms": 7000}], date="2026-07-22", source="SA Today")

    assert events == []


def test_source_title_can_name_an_event_only_when_analysed_text_corroborates_it():
    from hotspot_event_clips import build_event_clips

    events = build_event_clips(
        [{"start_ms": 0, "end_ms": 7000, "ocr_text": "Traffic congestion near Musina due to screening"}],
        date="2026-07-22", source="SABC", source_title="Traffic congestion near Musina due to screening",
    )

    assert events[0]["title_zh"] == "Traffic congestion near Musina due to screening｜现场片段 1"
    assert events[0]["review_status"] == "review_required"


def test_event_records_persist_without_copying_video(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa update", "summary": "", "source_url": "https://news.gov.za/1",
        "publisher": "SA Today", "published_at": "2026-07-22T00:00:00Z",
        "retrieved_at": "2026-07-23T00:00:00Z", "snapshot_sha256": "event-hotspot",
    })
    asset_id = tmp_db.create_asset({
        "name": "热点视频", "filepath": "assets/library/video/event.mp4", "file_type": "video",
        "category": "other", "duration": 23, "size": 10, "source": "youtube",
        "status": "active", "sha256": "event-asset" * 8,
    })
    segments = [
        {"asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 8000,
         "transcript": "Cape Town Transnet land", "processing_version": "test"},
        {"asset_id": asset_id, "segment_index": 1, "start_ms": 8000, "end_ms": 18000,
         "ocr_text": "Cape Town Western Cape", "processing_version": "test"},
    ]
    segment_ids = [tmp_db.create_asset_segment(item) for item in segments]
    from hotspot_event_clips import build_event_clips
    events = build_event_clips([dict(item, id=segment_id) for item, segment_id in zip(segments, segment_ids)], "2026-07-22", "SA Today")

    created = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, events)

    assert len(created) == 1
    assert tmp_db.list_hotspot_event_clips(asset_id=asset_id)[0]["title_zh"]
    assert tmp_db.get_asset(asset_id)["filepath"] == "assets/library/video/event.mp4"


def test_event_matching_separates_hotspot_and_owned_candidates():
    from hotspot_event_matching import match_event

    event = {"hotspot_id": 31, "title_zh": "开普敦土地事件", "location": "cape town", "keywords": ["transnet"]}
    segments = [
        {"id": 1, "asset_hotspot_id": 31, "description": "Cape Town Transnet", "asset_rights_status": "licensed", "asset_file_type": "video"},
        {"id": 2, "asset_hotspot_id": None, "description": "Buffalo delivery Cape Town", "asset_rights_status": "confirmed", "asset_file_type": "video"},
    ]

    result = match_event(event, segments)

    assert all(item["library_origin"] == "hotspot" for item in result["hotspot_candidates"])
    assert all(item["library_origin"] == "owned" for item in result["owned_candidates"])
    assert len(result["hotspot_candidates"]) <= 3
    assert len(result["owned_candidates"]) <= 3


def test_event_matching_supports_chinese_only_hook_titles():
    from hotspot_event_matching import match_event

    event = {"hotspot_id": 9, "title_zh": "Musina 附近交通拥堵", "location": "", "keywords": []}
    segments = [
        {
            "id": 1, "asset_hotspot_id": 9,
            "description": "现场画面显示道路拥堵，卡车排队",
            "asset_rights_status": "licensed", "asset_file_type": "video",
        },
        {
            "id": 2, "asset_hotspot_id": None,
            "description": "Buffalo 仓库货架分拣",
            "asset_rights_status": "confirmed", "asset_file_type": "video",
        },
    ]

    result = match_event(event, segments)

    assert result["hotspot_candidates"]
    assert "拥堵" in " ".join(result["hotspot_candidates"][0]["match_reasons"])
    assert result["owned_candidates"] == []
