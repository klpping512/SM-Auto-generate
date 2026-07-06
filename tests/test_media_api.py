import io

from PIL import Image


def _client_and_token(tmp_db):
    from fastapi.testclient import TestClient
    import app, auth
    tmp_db.create_user("mediaadmin", auth.hash_password("pw12345"), "admin", "Media Admin")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "mediaadmin", "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _png():
    buffer = io.BytesIO(); Image.new("RGB", (100, 160), "red").save(buffer, "PNG"); buffer.seek(0); return buffer


def test_asset_upload_list_update_delete(tmp_db, monkeypatch, tmp_path):
    import app
    client, headers = _client_and_token(tmp_db)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)
    response = client.post("/api/assets/upload?category=warehouse", headers=headers, files={"file": ("warehouse.png", _png(), "image/png")})
    assert response.status_code == 200
    asset = response.json(); assert asset["category"] == "warehouse"
    listed = client.get("/api/assets?type=image&category=warehouse", headers=headers).json()
    assert [item["id"] for item in listed] == [asset["id"]]
    assert client.put(f"/api/assets/{asset['id']}", headers=headers, json={"name": "新名称", "category": "brand"}).status_code == 200
    assert client.delete(f"/api/assets/{asset['id']}", headers=headers).json()["status"] == "deleted"


def test_render_rejects_missing_capability(tmp_db, monkeypatch):
    import app
    client, headers = _client_and_token(tmp_db)
    monkeypatch.setattr(app.media_assets, "capabilities", lambda: {"ffmpeg": False, "ffprobe": False})
    response = client.post("/api/douyin/render", headers=headers, json={"voice": "苏打", "scenes": []})
    assert response.status_code == 503
    assert "FFmpeg" in response.json()["detail"]
