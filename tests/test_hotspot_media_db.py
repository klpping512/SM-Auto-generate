from datetime import datetime, timezone


def _hotspot(source_url: str = "https://news.gov.za/story") -> dict:
    return {
        "title": "Port operations update",
        "summary": "Operations continue in Durban.",
        "source_url": source_url,
        "publisher": "SAnews",
        "published_at": "2026-07-22T08:00:00+00:00",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": "snapshot-v1",
        "image_candidate_url": None,
    }


def test_hotspot_translation_cache_round_trip(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(_hotspot())

    tmp_db.update_hotspot_translation(
        hotspot_id,
        title_zh="南非港口运营动态",
        summary_zh="德班港继续运营。",
        snapshot_sha256="snapshot-v1",
        model="mimo-v2.5",
    )

    item = tmp_db.get_hotspot(hotspot_id)
    assert item["title_zh"] == "南非港口运营动态"
    assert item["summary_zh"] == "德班港继续运营。"
    assert item["translation_status"] == "ready"
    assert item["translation_snapshot_sha256"] == "snapshot-v1"
    assert item["translation_model"] == "mimo-v2.5"
    assert item["translated_at"]


def test_upsert_hotspot_preserves_published_at_when_refresh_omits_it(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(_hotspot())
    assert tmp_db.get_hotspot(hotspot_id)["published_at"] == "2026-07-22T08:00:00+00:00"

    refreshed = _hotspot()
    refreshed["published_at"] = None
    refreshed["snapshot_sha256"] = "snapshot-v2"
    refreshed["title"] = "Port operations update (refreshed)"
    returned_id, created = tmp_db.upsert_hotspot(refreshed)
    assert returned_id == hotspot_id
    assert created is False
    item = tmp_db.get_hotspot(hotspot_id)
    assert item["published_at"] == "2026-07-22T08:00:00+00:00"
    assert item["title"] == "Port operations update (refreshed)"


def test_changed_hotspot_snapshot_invalidates_cached_translation(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(_hotspot())
    tmp_db.update_hotspot_translation(
        hotspot_id, "旧中文标题", "旧中文摘要", "snapshot-v1", "mimo-v2.5"
    )
    changed = _hotspot()
    changed["snapshot_sha256"] = "snapshot-v2"

    returned_id, created = tmp_db.upsert_hotspot(changed)

    item = tmp_db.get_hotspot(returned_id)
    assert created is False
    assert item["translation_status"] == "stale"
    assert item["title_zh"] == "旧中文标题"


def test_hotspot_media_round_trip_deduplicates_by_hotspot_and_url(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(_hotspot())
    payload = {
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": "abc123def45",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://www.youtube.com/watch?v=abc123def45",
        "thumbnail_url": "https://i.ytimg.com/vi/abc123def45/hqdefault.jpg",
        "publisher": "SABC Digital News",
        "author": "SABC Digital News",
        "rights_tier": "yellow",
        "download_status": "metadata_ready",
        "processing_status": "not_started",
    }

    media_id, created = tmp_db.upsert_hotspot_media(payload)
    duplicate_id, duplicate_created = tmp_db.upsert_hotspot_media(payload)

    assert created is True
    assert duplicate_created is False
    assert duplicate_id == media_id
    item = tmp_db.get_hotspot_media(media_id)
    assert item["media_kind"] == "video_link"
    assert item["platform"] == "youtube"
    assert item["rights_tier"] == "yellow"
    assert tmp_db.list_hotspot_media(hotspot_id=hotspot_id, media_kind="video_link") == [item]


def test_hotspot_media_rights_confirmation_is_independent_from_download(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(_hotspot())
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "direct",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://cdn.news.gov.za/story.mp4",
        "rights_tier": "yellow",
    })

    tmp_db.update_hotspot_media_rights(
        media_id,
        rights_tier="yellow",
        rights_note="已获得内部新闻型宣传授权",
        license_name="Publisher permission",
        attribution="SAnews",
        rights_evidence_url="https://news.gov.za/permissions/story",
        confirmed_by=7,
    )

    item = tmp_db.get_hotspot_media(media_id)
    assert item["confirmed_by"] == 7
    assert item["confirmed_at"]
    assert item["download_status"] == "discovered"


def test_init_db_migrates_existing_hotspot_media_before_creating_authorization_index(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(_hotspot())
    media_id, _ = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "direct",
        "source_page_url": "https://news.gov.za/story",
        "original_media_url": "https://cdn.news.gov.za/legacy.mp4",
        "rights_tier": "green",
    })
    with tmp_db.get_conn() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_hotspot_media_authorization")
        conn.execute("ALTER TABLE hotspot_media DROP COLUMN authorization_status")

    tmp_db.init_db()

    item = tmp_db.get_hotspot_media(media_id)
    assert item["authorization_status"] == "authorized"
    assert tmp_db.list_hotspot_media(authorization_status="authorized") == [item]
