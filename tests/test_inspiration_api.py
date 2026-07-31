import auth


def _login(tmp_db, username, role):
    from fastapi.testclient import TestClient
    import app

    tmp_db.create_user(username, auth.hash_password("pw12345"), role, username)
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": username, "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_team_inspiration_links_are_canonicalized_and_shared(tmp_db, monkeypatch):
    import app

    async def no_metadata(url):
        return {}

    monkeypatch.setattr(app.inspiration_assets, "fetch_oembed", no_metadata)
    client, headers = _login(tmp_db, "link-editor", "editor")
    first = client.post("/api/inspirations", headers=headers, json={
        "url": "https://youtu.be/abcDEF123?si=tracking", "title": "德班港现场",
    })
    second = client.post("/api/inspirations", headers=headers, json={
        "url": "https://www.youtube.com/watch?v=abcDEF123&utm_source=x", "title": "重复链接",
    })

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    listed = client.get("/api/inspirations?query=重复链接", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["created_by"] == tmp_db.get_user_by_username("link-editor")["id"]


def test_rights_confirmation_and_materialization_guard_are_admin_only(tmp_db, monkeypatch):
    import app

    async def no_metadata(url):
        return {}

    monkeypatch.setattr(app.inspiration_assets, "fetch_oembed", no_metadata)
    editor, editor_headers = _login(tmp_db, "rights-editor", "editor")
    item = editor.post("/api/inspirations", headers=editor_headers, json={
        "url": "https://www.tiktok.com/@brand/video/741234567890", "title": "港口短视频",
    }).json()
    rights = {
        "rights_status": "confirmed", "license_name": "作者书面授权", "attribution": "原作者",
        "rights_evidence_url": "https://example.com/permission",
    }
    assert editor.put(f"/api/inspirations/{item['id']}/rights", headers=editor_headers, json=rights).status_code == 403

    admin, admin_headers = _login(tmp_db, "rights-admin", "admin")
    assert admin.put(f"/api/inspirations/{item['id']}/rights", headers=admin_headers, json=rights).status_code == 200
    blocked = admin.post(
        f"/api/inspirations/{item['id']}/materialize", headers=admin_headers, json={"confirmed": False},
    )
    assert blocked.status_code == 400
    assert "人工确认" in blocked.json()["detail"]


def test_iol_is_stored_as_restricted_secondary_discovery_source(tmp_db, monkeypatch):
    import app

    async def no_metadata(url):
        return {}

    monkeypatch.setattr(app.inspiration_assets, "fetch_oembed", no_metadata)
    admin, headers = _login(tmp_db, "iol-admin", "admin")

    created = admin.post("/api/inspirations", headers=headers, json={
        "url": "https://iol.co.za/news/south-africa/",
        "title": "IOL South Africa News",
        "summary": "二级媒体热点发现页，事实需交叉验证",
    })

    assert created.status_code == 201
    item = created.json()
    assert item["source_type"] == "secondary_discovery"
    assert item["source_role"] == "hotspot_discovery"
    assert item["rights_status"] == "restricted"
    blocked = admin.post(
        f"/api/inspirations/{item['id']}/materialize",
        headers=headers,
        json={"confirmed": True},
    )
    assert blocked.status_code == 400
    assert "版权" in blocked.json()["detail"] or "来源类型" in blocked.json()["detail"]
