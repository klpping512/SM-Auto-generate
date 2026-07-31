def _hotspot(tmp_db, suffix="virtual"):
    return tmp_db.upsert_hotspot({
        "title": "Update", "summary": "", "source_url": f"https://example.com/{suffix}",
        "publisher": "SA Today", "published_at": "2026-07-22T00:00:00Z",
        "retrieved_at": "2026-07-23T00:00:00Z", "snapshot_sha256": f"{suffix}-hotspot",
    })[0]


def _asset(tmp_db, suffix="virtual"):
    return tmp_db.create_asset({
        "name": "母片", "filepath": f"assets/{suffix}.mp4", "file_type": "video",
        "category": "other", "duration": 30, "size": 1, "source": "youtube",
        "status": "active", "sha256": (suffix[0] * 64),
    })


def test_event_clip_has_virtual_ref_and_duration(tmp_db):
    hotspot_id, asset_id = _hotspot(tmp_db), _asset(tmp_db)
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 1000,
        "end_ms": 7000, "description": "Cape Town Transnet", "thumbnail_path": "thumb.jpg",
        "processing_version": "test",
    })
    from hotspot_event_clips import build_event_clips

    events = build_event_clips([{
        "id": segment_id, "start_ms": 1000, "end_ms": 7000,
        "transcript": "Cape Town Transnet land", "thumbnail_path": "thumb.jpg",
    }], "2026-07-22", "SA Today")
    created = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, events)
    event = tmp_db.list_hotspot_event_clips(asset_id=asset_id)[0]
    assert event["virtual_asset_id"] == f"hotspot-event-{created[0]['id']}"
    assert event["duration_ms"] == event["end_ms"] - event["start_ms"]
    assert event["library_origin"] == "hotspot_event"
    assert event["thumbnail_path"] == "thumb.jpg"


def test_event_clip_range_is_within_mother_asset(tmp_db):
    hotspot_id, asset_id = _hotspot(tmp_db, "range"), _asset(tmp_db, "range")
    from hotspot_event_clips import build_event_clips

    events = build_event_clips([{"start_ms": 0, "end_ms": 7000, "transcript": "Cape Town"}], "2026-07-22", "SA Today")
    tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, events)
    event = tmp_db.list_hotspot_event_clips(asset_id=asset_id)[0]
    asset = tmp_db.get_asset(asset_id)
    assert event["start_ms"] >= 0
    assert event["end_ms"] <= round(float(asset["duration"] or 0) * 1000)
