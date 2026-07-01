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
