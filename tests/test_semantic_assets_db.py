import json


def _create_parent_asset(db):
    return db.create_asset({
        "name": "德班港卡车排队",
        "filepath": "assets/library/video/durban.mp4",
        "file_type": "video",
        "category": "delivery",
        "duration": 12.0,
        "width": 1080,
        "height": 1920,
        "size": 1024,
        "thumbnail": "assets/thumbnails/durban.jpg",
        "sha256": "durban-sha256",
        "source": "upload",
        "status": "active",
        "created_by": None,
    })


def test_semantic_asset_schema_is_created_idempotently(tmp_db):
    tmp_db.init_db()

    with tmp_db.get_conn() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }

    assert {
        "asset_segments",
        "tags",
        "segment_tags",
        "asset_processing_jobs",
        "inspiration_items",
        "match_sessions",
        "semantic_atoms",
        "match_candidates",
        "match_feedback",
        "segment_usage",
        "asset_segment_fts",
        "inspiration_fts",
    } <= tables


def test_segment_tags_are_normalized_and_searchable_in_chinese(tmp_db):
    asset_id = _create_parent_asset(tmp_db)
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id,
        "segment_index": 0,
        "start_ms": 0,
        "end_ms": 6000,
        "preview_path": "assets/segments/durban-0.mp4",
        "thumbnail_path": "assets/segments/durban-0.jpg",
        "transcript": "德班港清关拥堵，卡车正在排队",
        "ocr_text": "DURBAN PORT",
        "description": "南非德班港集装箱堆场",
        "primary_category": "logistics_fulfillment",
        "quality_score": 86,
        "orientation": "portrait",
        "status": "active",
        "processing_version": "v1",
    })
    tmp_db.replace_segment_tags(segment_id, [
        {"dimension": "region", "value": "德班", "confidence": 0.98, "source": "ocr"},
        {"dimension": "action", "value": "排队", "confidence": 0.91, "source": "asr"},
        {"dimension": "business_role", "value": "门槛痛点", "confidence": 1.0, "source": "manual"},
    ])

    results = tmp_db.search_asset_segments("德班港卡车排队")

    assert [item["id"] for item in results] == [segment_id]
    assert {tag["value"] for tag in results[0]["tags"]} == {"德班", "排队", "门槛痛点"}


def test_segment_primary_scene_is_not_overwritten_by_legacy_asset_category(tmp_db):
    asset_id = _create_parent_asset(tmp_db)  # legacy asset category is delivery
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 6000,
        "primary_category": "warehouse", "primary_category_source": "model",
        "processing_version": "semantic-v2-qwen-vl",
    })

    segment = tmp_db.list_asset_segments(asset_id=asset_id)[0]

    assert segment["id"] == segment_id
    assert segment["primary_category"] == "warehouse"
    assert segment["asset_category"] == "delivery"


def test_taxonomy_rebuild_queue_skips_manual_and_current_assets(tmp_db):
    legacy_id = _create_parent_asset(tmp_db)
    manual_id = tmp_db.create_asset({
        "name": "人工确认", "filepath": "assets/library/video/manual.mp4", "file_type": "video",
        "category": "staff", "duration": 3, "width": 1080, "height": 1920, "size": 1,
        "sha256": "m" * 64, "source": "upload", "status": "active", "created_by": None,
    })
    current_id = tmp_db.create_asset({
        "name": "当前版本", "filepath": "assets/library/video/current.mp4", "file_type": "video",
        "category": "warehouse", "duration": 3, "width": 1080, "height": 1920, "size": 1,
        "sha256": "c" * 64, "source": "upload", "status": "active", "created_by": None,
    })
    with tmp_db.get_conn() as conn:
        conn.execute("UPDATE assets SET primary_category_source='manual' WHERE id=?", (manual_id,))
        conn.execute("UPDATE assets SET processing_version='semantic-v3-qwen-vl-brand' WHERE id=?", (current_id,))

    queued = tmp_db.list_assets_needing_taxonomy_rebuild("semantic-v3-qwen-vl-brand", 30)

    assert [item["id"] for item in queued] == [legacy_id]


def test_brand_tag_is_independently_searchable_from_primary_delivery_category(tmp_db):
    asset_id = _create_parent_asset(tmp_db)
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 6_000,
        "description": "Buffalo 配送货车在道路运输", "ocr_text": "BUFFALO LOGISTICS",
        "primary_category": "delivery", "processing_version": "semantic-v3-qwen-vl-brand",
    })
    tmp_db.replace_segment_tags(segment_id, [{
        "dimension": "brand", "value": "Buffalo", "confidence": 0.95, "source": "ocr",
    }])

    branded = tmp_db.list_assets(category="brand")
    labels = tmp_db.list_asset_brand_tags([asset_id])

    assert [asset["id"] for asset in branded] == [asset_id]
    assert labels == {asset_id: ["Buffalo"]}


def test_ocr_brand_backfill_never_changes_primary_category(tmp_db):
    asset_id = _create_parent_asset(tmp_db)
    tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 6_000,
        "description": "道路运输车辆", "ocr_text": "BUFFALO LOGISTICS",
        "primary_category": "delivery", "processing_version": "legacy",
    })

    result = tmp_db.backfill_visible_brand_tags("Buffalo", ("buffalo",))
    segment = tmp_db.list_asset_segments(asset_id=asset_id)[0]

    assert result["affected_assets"] == 1
    assert segment["primary_category"] == "delivery"
    assert any(tag["dimension"] == "brand" and tag["value"] == "Buffalo" for tag in segment["tags"])


def test_inspiration_url_is_deduplicated_and_not_renderable_until_materialized(tmp_db):
    first_id, created = tmp_db.upsert_inspiration_item({
        "source_type": "youtube",
        "source_role": "creative_reference",
        "source_url": "https://www.youtube.com/watch?v=abc123",
        "canonical_url": "https://www.youtube.com/watch?v=abc123",
        "title": "Durban port truck queue",
        "summary": "South Africa logistics footage",
        "rights_status": "unknown",
        "materialization_status": "reference_only",
    })
    second_id, second_created = tmp_db.upsert_inspiration_item({
        "source_type": "youtube",
        "source_role": "creative_reference",
        "source_url": "https://youtu.be/abc123",
        "canonical_url": "https://www.youtube.com/watch?v=abc123",
        "title": "Updated title",
        "summary": "Updated description",
        "rights_status": "unknown",
        "materialization_status": "reference_only",
    })

    assert created is True
    assert second_created is False
    assert second_id == first_id
    item = tmp_db.get_inspiration_item(first_id)
    assert item["title"] == "Updated title"
    assert item["asset_id"] is None
    assert item["materialization_status"] == "reference_only"


def test_match_session_persists_atoms_candidates_and_feedback(tmp_db):
    asset_id = _create_parent_asset(tmp_db)
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id,
        "segment_index": 0,
        "start_ms": 0,
        "end_ms": 5000,
        "description": "卡车在德班港排队",
        "primary_category": "logistics_fulfillment",
        "quality_score": 80,
        "orientation": "portrait",
        "status": "active",
        "processing_version": "v1",
    })
    session_id = tmp_db.create_match_session(
        created_by=7,
        source_payload={"scenes": [{"voiceover": "德班港清关出现拥堵"}]},
    )
    atom_id = tmp_db.create_semantic_atom(session_id, {
        "position": 0,
        "text": "德班港清关出现拥堵",
        "semantics": {"entities": ["德班港"], "actions": ["排队"]},
        "duration_ms": 5000,
        "constraints": {"region": "南非"},
    })
    tmp_db.replace_match_candidates(atom_id, [{
        "segment_id": segment_id,
        "rank": 1,
        "match_score": 88,
        "reasons": ["命中德班港", "动作：排队"],
        "review_required": False,
    }])
    tmp_db.update_semantic_atom_selection(atom_id, segment_id, locked=True, review_confirmed=True)
    tmp_db.add_match_feedback(session_id, atom_id, segment_id, 7, "selected", "画面与旁白一致")

    session = tmp_db.get_match_session(session_id, created_by=7)

    assert session["atoms"][0]["selected_segment_id"] == segment_id
    assert session["atoms"][0]["locked"] is True
    assert session["atoms"][0]["candidates"][0]["reasons"] == ["命中德班港", "动作：排队"]
    assert json.loads(session["source_payload"])["scenes"][0]["voiceover"] == "德班港清关出现拥堵"


def test_asset_processing_job_tracks_progress_and_segment_listing(tmp_db):
    asset_id = _create_parent_asset(tmp_db)
    job_id = tmp_db.create_asset_processing_job(asset_id, requested_by=9)
    tmp_db.update_asset_processing_job(job_id, status="running", stage="ocr", progress=55)
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 4_000,
        "description": "德班港卡车", "quality_score": 0.9,
    })

    job = tmp_db.get_asset_processing_job(job_id)
    segments = tmp_db.list_asset_segments(asset_id=asset_id)

    assert job["status"] == "running"
    assert job["stage"] == "ocr"
    assert job["progress"] == 55
    assert segments[0]["id"] == segment_id
    assert segments[0]["asset_name"] == "德班港卡车排队"


def test_pending_asset_batch_excludes_running_jobs(tmp_db):
    asset_id = _create_parent_asset(tmp_db)
    assert [item["id"] for item in tmp_db.list_assets_needing_processing()] == [asset_id]

    job_id = tmp_db.create_asset_processing_job(asset_id)

    assert tmp_db.list_assets_needing_processing() == []
    assert tmp_db.list_pending_asset_processing_job_ids() == [job_id]

    # pending 任务重启后应保留并重新派发；只有 running 才标成 interrupted。
    assert tmp_db.recover_interrupted_asset_processing_jobs() == 0
    assert tmp_db.list_pending_asset_processing_job_ids() == [job_id]

    tmp_db.update_asset_processing_job(job_id, status="running", stage="asr_ocr", progress=40)
    assert tmp_db.recover_interrupted_asset_processing_jobs() == 1
    assert tmp_db.list_pending_asset_processing_job_ids() == []
    assert [item["id"] for item in tmp_db.list_assets_needing_processing()] == [asset_id]
