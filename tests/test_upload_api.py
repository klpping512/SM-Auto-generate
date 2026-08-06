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


def test_upload_rejects_mismatched_extension(tmp_db, monkeypatch):
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.post(
        "/api/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("payload.html", b"not really png", "image/png")},
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

    tmp_db.update_queue_attachments(items[0]["id"], [{"type": "image", "path": "uploads/image/generated.png"}])
    updated = tmp_db.get_queue_item_by_id(items[0]["id"])
    assert json.loads(updated["attachments"])[0]["path"] == "uploads/image/generated.png"


def test_generate_fallback_returns_xhs_publishable_images(tmp_db, monkeypatch, tmp_path):
    import app
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    async def _no_chat(*_args, **_kwargs):
        raise RuntimeError("聊天模型未配置：测试强制走 fallback")

    monkeypatch.setattr(app.ai_engine, "_complete_json_messages", _no_chat)

    response = client.post(
        "/api/generate",
        headers={"Authorization": f"Bearer {tok}"},
        json={"topic": "德班港提醒", "platforms": ["xiaohongshu"]},
    )

    assert response.status_code == 200
    content = response.json()["contents"][0]
    assert len(content["image_pages"]) >= 5
    assert len(content["attachments"]) == len(content["image_pages"])
    assert all(item["template_version"] == "buffalo-reference-v5" for item in content["attachments"])
    assert all((tmp_path / item["path"]).exists() for item in content["attachments"])


def test_legacy_xhs_queue_submission_auto_generates_images(tmp_db, monkeypatch, tmp_path):
    import app
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

    response = client.post(
        "/api/queue",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "PAT 注册三步走", "body": "第一步：准备资料\n\n第二步：提交申请", "platforms": ["xiaohongshu"], "attachments": []},
    )

    assert response.status_code == 200
    item = tmp_db.get_queue(platform="xiaohongshu")[-1]
    attachments = json.loads(item["attachments"])
    assert len(attachments) >= 3
    assert all((tmp_path / asset["path"]).exists() for asset in attachments)


def test_douyin_queue_requires_video(tmp_db, monkeypatch):
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    response = client.post(
        "/api/queue", headers={"Authorization": f"Bearer {tok}"},
        json={"title": "抖音稿", "body": "正文", "platforms": ["douyin"], "attachments": []},
    )
    assert response.status_code == 400
    assert "MP4" in response.json()["detail"]
