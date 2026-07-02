"""Tests for file upload API + queue attachments."""
import io
import json


def _client(tmp_db, monkeypatch):
    """Create a TestClient with the tmp_db patched in."""
    from fastapi.testclient import TestClient
    import app
    # app.db is the same module object as tmp_db; DB_PATH is already patched
    return TestClient(app.app)


def _admin_token(tmp_db, client):
    """Create an admin user and return a JWT token."""
    import auth
    tmp_db.create_user("adm", auth.hash_password("pw12345"), "admin", "A")
    resp = client.post("/api/auth/login", json={"username": "adm", "password": "pw12345"})
    return resp.json()["access_token"]


def test_upload_image(tmp_db, monkeypatch):
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    # Create a minimal valid PNG (1x1 pixel)
    from PIL import Image
    img = Image.new('RGB', (1, 1), color='red')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    r = client.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("test.png", buf, "image/png")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "image"
    assert data["filename"] == "test.png"
    assert data["url"].startswith("/static/uploads/image/")
    assert data["size"] > 0


def test_upload_rejects_bad_type(tmp_db, monkeypatch):
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("test.exe", b"bad", "application/x-executable")},
    )
    assert r.status_code == 400


def test_add_to_queue_with_attachments(tmp_db):
    tmp_db.add_to_queue(
        "t", "b", "facebook",
        attachments=[{"type": "image", "path": "uploads/image/x.png"}],
    )
    items = tmp_db.get_queue("draft", "facebook")  # filter by platform to skip seed rows
    assert len(items) == 1
    att = json.loads(items[0]["attachments"])
    assert att[0]["type"] == "image"
    assert att[0]["path"] == "uploads/image/x.png"
