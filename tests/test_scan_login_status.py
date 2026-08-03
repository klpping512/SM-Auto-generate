import app


def test_scan_login_status_returns_session_state(tmp_db):
    from fastapi.testclient import TestClient
    import auth

    user_id = tmp_db.create_user("scanadmin", auth.hash_password("pw12345"), "admin", "A")
    tmp_db.create_account("xiaohongshu", "扫码账号", "scan-account", owner_id=user_id)
    account_id = tmp_db.get_accounts(owner_id=user_id)[0]["id"]
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "scanadmin", "password": "pw12345",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    app.scan_login_sessions["known"] = {
        "status": "error", "account_id": account_id, "platform": "xiaohongshu", "error": "network",
    }
    response = client.get(f"/api/accounts/{account_id}/scan-login/known", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "error", "error": "network"}

    missing = client.get(f"/api/accounts/{account_id + 1}/scan-login/known", headers=headers)
    assert missing.status_code == 404
