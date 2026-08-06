def test_update_account_status(tmp_db):
    tmp_db.create_account("facebook", "主页", "fb_001")
    acc = tmp_db.get_accounts("facebook")[0]
    tmp_db.update_account_status(acc["id"], "expired")
    assert tmp_db.get_accounts("facebook")[0]["status"] == "expired"


def test_update_account_credentials(tmp_db):
    tmp_db.create_account("facebook", "主页", "fb_001")
    tmp_db.update_account_credentials("fb_001", '{"page_id": "123"}')
    assert tmp_db.get_accounts("facebook")[0]["credentials"] == '{"page_id": "123"}'


def test_count_published_today_and_interval(tmp_db):
    assert tmp_db.count_published_today("reddit") == 0
    assert tmp_db.minutes_since_last_publish("reddit") is None
    tmp_db.add_publish_log(1, "reddit", "标题", "published")
    assert tmp_db.count_published_today("reddit") == 1
    mins = tmp_db.minutes_since_last_publish("reddit")
    assert mins is not None and mins < 5
    tmp_db.add_publish_log(2, "reddit", "标题2", "failed")  # 失败不计
    assert tmp_db.count_published_today("reddit") == 1
    assert tmp_db.count_published_today("twitter") == 0  # 平台隔离


def test_bump_asset_usage_increments_and_list_segments_exposes_fields(tmp_db):
    # 批13 D：usage_count 列存在、list_asset_segments 暴露新字段、bump 递增并去重
    asset_id = tmp_db.create_asset({
        "name": "仓内分拣", "filepath": "assets/library/video/wh.mp4", "file_type": "video",
        "category": "warehouse", "duration": 8.0, "width": 1080, "height": 1920,
        "size": 1024, "thumbnail": "assets/thumbnails/wh.jpg", "sha256": "wh-sha",
        "source": "upload", "status": "active", "created_by": None,
    })
    tmp_db.create_asset_segment({"asset_id": asset_id, "segment_index": 0, "start_ms": 0,
                                 "end_ms": 6000, "thumbnail_path": "assets/segments/wh-0.jpg"})
    segs = tmp_db.list_asset_segments(asset_id=asset_id)
    assert segs[0]["asset_usage_count"] == 0
    assert segs[0]["asset_last_used_at"] is None

    tmp_db.bump_asset_usage([asset_id], "2026-08-07T00:00:00+00:00")
    segs = tmp_db.list_asset_segments(asset_id=asset_id)
    assert segs[0]["asset_usage_count"] == 1
    assert segs[0]["asset_last_used_at"] == "2026-08-07T00:00:00+00:00"

    tmp_db.bump_asset_usage([asset_id, asset_id], "2026-08-08T00:00:00+00:00")  # 重复 id 去重
    segs = tmp_db.list_asset_segments(asset_id=asset_id)
    assert segs[0]["asset_usage_count"] == 2
