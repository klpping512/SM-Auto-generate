def test_hotspot_topic_package_fields_and_signals(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Johannesburg driver strike",
        "summary": "Drivers announce a shutdown.",
        "source_url": "https://sabc.example/strike",
        "publisher": "SABC",
        "published_at": "2026-07-24T07:00:00+00:00",
        "retrieved_at": "2026-07-24T07:05:00+00:00",
        "snapshot_sha256": "a" * 64,
    })

    tmp_db.update_hotspot_package_metrics(
        hotspot_id,
        heat_score=82,
        heat_state="rising",
        event_type="strike",
        logistics_relevance=91,
        locations=["Johannesburg"],
        entities=["driver"],
        package_status="new",
    )
    signal_id, created = tmp_db.upsert_hotspot_signal({
        "hotspot_id": hotspot_id,
        "source_name": "SABC",
        "source_type": "news",
        "external_id": "sabc-1",
        "title": "Drivers announce a shutdown",
        "summary": "Johannesburg drivers announce a national shutdown.",
        "source_url": "https://sabc.example/strike",
        "published_at": "2026-07-24T07:00:00+00:00",
        "retrieved_at": "2026-07-24T07:05:00+00:00",
        "metrics": {"cross_platform": 2},
        "raw_payload": {"id": "sabc-1"},
    })

    assert created is True
    assert tmp_db.get_hotspot(hotspot_id)["heat_score"] == 82
    assert tmp_db.list_hotspot_signals(hotspot_id)[0]["id"] == signal_id
    package = tmp_db.get_hotspot_package(hotspot_id)
    assert package["locations"] == ["Johannesburg"]
    assert package["entities"] == ["driver"]
    assert package["signal_count"] == 1
    assert package["media_count"] == 0
