import app


def test_scan_login_status_returns_session_state(tmp_db):
    from fastapi.testclient import TestClient
    import auth

    tmp_db.create_user("scanadmin", auth.hash_password("pw12345"), "admin", "A")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "scanadmin", "password": "pw12345",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    app.scan_login_sessions["known"] = {
        "status": "error", "account_id": 42, "platform": "xiaohongshu", "error": "network",
    }
    response = client.get("/api/accounts/42/scan-login/known", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "error", "error": "network"}

    missing = client.get("/api/accounts/7/scan-login/known", headers=headers)
    assert missing.status_code == 404
