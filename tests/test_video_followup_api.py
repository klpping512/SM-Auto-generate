from fastapi.testclient import TestClient


def test_hotspot_event_endpoint_exposes_preview_range(tmp_db):
    import app
    import auth

    tmp_db.create_user("followup-owner", auth.hash_password("pw12345"), "editor", "followup-owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "followup-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa event", "summary": "", "source_url": "https://example.com/event",
        "publisher": "SA Today", "published_at": "2026-07-23T00:00:00Z",
        "retrieved_at": "2026-07-23T00:00:00Z", "snapshot_sha256": "followup-hotspot",
    })
    asset_id = tmp_db.create_asset({
        "name": "热点母片", "filepath": "assets/library/video/hotspot.mp4", "thumbnail": "assets/thumb.jpg",
        "file_type": "video", "category": "other", "duration": 120, "size": 10,
        "source": "youtube", "status": "active", "sha256": "f" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 12000, "end_ms": 18000,
        "title_zh": "2026-07-23｜现场事件｜SA Today", "title_en": "2026-07-23 | Scene Event | SA Today",
        "location": "cape town", "segments": [], "confidence": 0.9, "review_status": "confirmed",
    }])[0]

    response = client.get(f"/api/hotspot-events/{event['id']}", headers=headers)
    assert response.status_code == 200
    virtual = response.json()["virtual_asset"]
    assert virtual["preview_url"].endswith("assets/library/video/hotspot.mp4")
    assert virtual["preview_start_second"] == 12
    assert virtual["preview_end_second"] == 18
    assert virtual["thumbnail_url"].endswith("assets/thumb.jpg")


def test_hotspot_logistics_plan_returns_dynamic_brief_and_evidence_budget(tmp_db):
    import app
    import auth

    tmp_db.create_user("plan-owner", auth.hash_password("pw12345"), "editor", "plan-owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "plan-owner", "password": "pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa ecommerce growth", "summary": "Orders and delivery demand increased",
        "source_url": "https://example.com/ecommerce", "publisher": "SA Today",
        "published_at": "2026-07-23T00:00:00Z", "retrieved_at": "2026-07-23T00:00:00Z",
        "snapshot_sha256": "plan-hotspot",
    })
    source_asset = tmp_db.create_asset({
        "name": "热点视频", "filepath": "assets/library/video/hotspot.mp4", "thumbnail": "assets/thumb.jpg",
        "file_type": "video", "category": "other", "duration": 120, "size": 10,
        "source": "youtube", "status": "active", "sha256": "a" * 64,
    })
    event = tmp_db.replace_hotspot_event_clips(source_asset, hotspot_id, [
        {"event_index": index, "start_ms": (index - 1) * 6000, "end_ms": index * 6000,
         "title_zh": title, "title_en": title, "location": "Johannesburg", "segments": [],
         "confidence": 0.9, "review_status": "confirmed"}
        for index, title in enumerate(["电商订单增长", "配送需求增加", "仓储扩容"], 1)
    ])[0]
    for index, category in enumerate(["warehouse", "delivery", "staff", "facility", "warehouse"], 1):
        asset_id = tmp_db.create_asset({
            "name": f"Buffalo-{category}-{index}", "filepath": f"assets/library/video/{index}.mp4",
            "file_type": "video", "category": category, "duration": 10, "size": 10,
            "source": "local", "status": "active", "sha256": str(index) * 64,
        })
        tmp_db.create_asset_segment({"asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 10000,
                                     "primary_category": category, "quality_score": 0.9})

    response = client.get(f"/api/hotspot-events/{event['id']}/logistics-plan", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["brief"]["logistics_topic"] == "本地快递时效"
    assert payload["evidence_summary"]["planned_duration_ms"] == 47000
    assert payload["evidence_summary"]["cta_duration_ms"] == 3000
    assert payload["evidence_summary"]["duration_ms"] == 50000
    assert payload["evidence_summary"]["ready"] is True
    assert payload["evidence_summary"]["hotspot_video"] == 2
    assert payload["evidence_summary"]["owned_video"] >= 4
