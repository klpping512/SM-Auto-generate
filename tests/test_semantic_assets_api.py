import auth


def _client(tmp_db, role="admin", username="semantic-user"):
    from fastapi.testclient import TestClient
    import app

    tmp_db.create_user(username, auth.hash_password("pw12345"), role, "语义测试")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": username, "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}, tmp_db.get_user_by_username(username)


def _asset_and_segment(tmp_db):
    asset_id = tmp_db.create_asset({
        "name": "德班港现场", "filepath": "assets/library/video/durban.mp4", "file_type": "video",
        "category": "delivery", "duration": 5, "width": 1080, "height": 1920, "size": 100,
        "thumbnail": "assets/thumbnails/durban.jpg", "sha256": "d" * 64,
        "source": "upload", "status": "active", "created_by": None,
    })
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 5_000,
        "description": "德班港卡车正在排队", "primary_category": "delivery",
        "quality_score": 0.9, "orientation": "portrait",
    })
    tmp_db.replace_segment_tags(segment_id, [
        {"dimension": "region", "value": "德班", "confidence": 1, "source": "manual"},
        {"dimension": "entity", "value": "卡车", "confidence": 1, "source": "manual"},
        {"dimension": "action", "value": "排队", "confidence": 1, "source": "manual"},
    ])
    return asset_id, segment_id


def test_semantic_match_api_returns_explainable_top_candidates(tmp_db):
    client, headers, _ = _client(tmp_db)
    _, segment_id = _asset_and_segment(tmp_db)

    response = client.post("/api/semantic-match", headers=headers, json={
        "scenes": [{"voiceover": "德班港卡车出现排队", "visual": "港口现场", "duration": 5}]
    })

    assert response.status_code == 201
    data = response.json()
    assert data["atoms"][0]["candidates"][0]["segment_id"] == segment_id
    assert data["atoms"][0]["candidates"][0]["reasons"]


def test_semantic_match_api_normalizes_legacy_landscape_request_to_portrait(tmp_db):
    client, headers, _ = _client(tmp_db, username="portrait-match-owner")

    response = client.post("/api/semantic-match", headers=headers, json={
        "script": "南非仓库分拣现场。", "orientation": "landscape",
    })

    assert response.status_code == 201
    assert response.json()["atoms"][0]["constraints"]["orientation"] == "portrait"


def test_match_selection_is_private_and_persists_feedback(tmp_db):
    client, headers, user = _client(tmp_db, username="match-owner")
    _, segment_id = _asset_and_segment(tmp_db)
    session = client.post("/api/semantic-match", headers=headers, json={"script": "德班港卡车排队。"}).json()
    atom_id = session["atoms"][0]["id"]

    response = client.put(
        f"/api/semantic-match/{session['id']}/atoms/{atom_id}", headers=headers,
        json={"segment_id": segment_id, "locked": True, "review_confirmed": True, "reason": "人工确认"},
    )

    assert response.status_code == 200
    stored = tmp_db.get_match_session(session["id"], created_by=user["id"])
    assert stored["atoms"][0]["selected_segment_id"] == segment_id
    with tmp_db.get_conn() as conn:
        feedback = conn.execute("SELECT * FROM match_feedback WHERE session_id=?", (session["id"],)).fetchone()
    assert feedback["reason"] == "人工确认"


def test_segment_manual_classification_requires_admin(tmp_db):
    admin_client, admin_headers, _ = _client(tmp_db, username="segment-admin")
    _, segment_id = _asset_and_segment(tmp_db)
    editor_client, editor_headers, _ = _client(tmp_db, role="editor", username="segment-editor")

    assert editor_client.put(
        f"/api/asset-segments/{segment_id}/classification", headers=editor_headers,
        json={"primary_category": "customs", "tags": []},
    ).status_code == 403
    response = admin_client.put(
        f"/api/asset-segments/{segment_id}/classification", headers=admin_headers,
        json={"primary_category": "customs", "tags": [{"dimension": "scene", "value": "海关查验"}]},
    )
    assert response.status_code == 200
    assert response.json()["primary_category"] == "customs"
