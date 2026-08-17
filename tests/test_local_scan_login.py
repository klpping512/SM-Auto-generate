import json

import app
import auth


def test_local_scan_handoff_saves_cookies_once(tmp_db):
    from fastapi.testclient import TestClient

    tmp_db.create_user("localadmin", auth.hash_password("pw12345"), "admin", "A")
    tmp_db.create_account("xiaohongshu", "测试账号", "xhs-local")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "localadmin", "password": "pw12345",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    account = tmp_db.get_accounts()[0]

    try:
        response = client.post(f"/api/accounts/{account['id']}/local-scan-login", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "local"
        assert data["login_url"].startswith("https://")
        assert data["complete_path"].endswith("/complete")

        complete = client.post(
            f"/api/accounts/{account['id']}/local-scan-login/{data['session_id']}/complete",
            json={
                "handoff_token": data["handoff_token"],
                "cookies": [{"name": "sid", "value": "abc", "domain": ".xiaohongshu.com"}],
            },
        )
        assert complete.status_code == 200
        assert complete.json() == {"ok": True, "status": "success"}
        assert json.loads(tmp_db.get_accounts()[0]["credentials"])["cookies"][0]["name"] == "sid"

        replay = client.post(
            f"/api/accounts/{account['id']}/local-scan-login/{data['session_id']}/complete",
            json={"handoff_token": data["handoff_token"], "cookies": []},
        )
        assert replay.status_code == 404
    finally:
        app.local_scan_handoffs.clear()
        app.scan_login_sessions.pop(data["session_id"], None) if "data" in locals() else None
