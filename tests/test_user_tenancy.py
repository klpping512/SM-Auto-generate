"""Registration and per-user account/content isolation."""
from fastapi.testclient import TestClient

import app


def _signup_login(client, username, display_name):
    signup = client.post("/api/auth/signup", json={
        "username": username, "password": "strong-pass-123", "display_name": display_name,
        "role": "admin",  # must be ignored/rejected by the public schema
    })
    assert signup.status_code == 201
    login = client.post("/api/auth/login", json={"username": username, "password": "strong-pass-123"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_signup_is_editor_and_accounts_are_isolated(tmp_db):
    client = TestClient(app.app)
    alice = _signup_login(client, "alice", "Alice")
    bob = _signup_login(client, "bob", "Bob")

    assert tmp_db.get_user_by_username("alice")["role"] == "editor"
    created = client.post("/api/accounts", headers=alice, json={
        "platform": "wechat_mp", "name": "Alice 公众号", "account_id": "alice-wechat",
    })
    assert created.status_code == 200
    assert len(client.get("/api/accounts", headers=alice).json()) == 1
    assert client.get("/api/accounts", headers=bob).json() == []

    account_id = client.get("/api/accounts", headers=alice).json()[0]["id"]
    assert client.delete(f"/api/accounts/{account_id}", headers=bob).status_code == 403


def test_queue_and_dashboard_are_scoped_to_creator(tmp_db):
    client = TestClient(app.app)
    alice = _signup_login(client, "alice2", "Alice")
    bob = _signup_login(client, "bob2", "Bob")

    response = client.post("/api/queue", headers=alice, json={
        "title": "Alice 内容", "body": "真实内容", "platforms": ["facebook"],
    })
    assert response.status_code == 200
    assert len(client.get("/api/queue", headers=alice).json()) == 1
    assert client.get("/api/queue", headers=bob).json() == []
    assert client.get("/api/dashboard", headers=alice).json()["queue_stats"]["total"] == 1
    bob_dashboard = client.get("/api/dashboard", headers=bob).json()
    assert bob_dashboard["queue_stats"]["total"] == 0
    assert bob_dashboard["recent_activity"] == []

    item_id = client.get("/api/queue", headers=alice).json()[0]["id"]
    assert client.delete(f"/api/queue/{item_id}", headers=bob).status_code == 403


def test_one_wechat_article_can_route_to_multiple_owned_accounts(tmp_db):
    client = TestClient(app.app)
    alice = _signup_login(client, "matrixalice", "Alice")
    bob = _signup_login(client, "matrixbob", "Bob")
    for account_id in ("wechat-a", "wechat-b"):
        assert client.post("/api/accounts", headers=alice, json={
            "platform": "wechat_mp", "name": account_id, "account_id": account_id,
        }).status_code == 200
    alice_accounts = client.get("/api/accounts?platform=wechat_mp", headers=alice).json()
    response = client.post("/api/queue", headers=alice, json={
        "title": "矩阵文章", "body": "同一篇内容分别进入两个公众号任务。", "platforms": ["wechat_mp"],
        "account_targets": {"wechat_mp": [item["id"] for item in alice_accounts]},
    })
    assert response.status_code == 200
    assert response.json()["added"] == 2
    queued = client.get("/api/queue", headers=alice).json()
    assert {item["target_account_name"] for item in queued} == {"wechat-a", "wechat-b"}

    foreign = client.post("/api/accounts", headers=bob, json={
        "platform": "wechat_mp", "name": "bob-wechat", "account_id": "bob-wechat",
    })
    assert foreign.status_code == 200
    bob_id = client.get("/api/accounts", headers=bob).json()[0]["id"]
    assert client.post("/api/queue", headers=alice, json={
        "title": "越权", "body": "不能路由到别人的账号。", "platforms": ["wechat_mp"],
        "account_targets": {"wechat_mp": [bob_id]},
    }).status_code == 403


def test_dashboard_team_metrics_are_auditable_and_role_scoped(tmp_db):
    import auth
    client = TestClient(app.app)
    alice = _signup_login(client, "metricalice", "Alice")
    _signup_login(client, "metricbob", "Bob")
    alice_id = tmp_db.get_user_by_username("metricalice")["id"]
    published_id = tmp_db.add_to_queue("干货", "正文", "wechat_mp", status="published", created_by=alice_id)
    tmp_db.add_to_queue("待核实", "今日发生事件", "wechat_mp", status="pending_review",
                        created_by=alice_id, verification_status="needs_evidence")
    own = client.get("/api/dashboard", headers=alice).json()["team_performance"]
    assert [item["username"] for item in own] == ["metricalice"]
    assert own[0]["published"] == 1
    assert own[0]["pain_points"] == [{"type": "evidence_blocked", "label": "证据不足", "count": 1}]

    tmp_db.create_user("metricsadmin", auth.hash_password("pw123456"), "admin", "Admin")
    token = client.post("/api/auth/login", json={"username": "metricsadmin", "password": "pw123456"}).json()["access_token"]
    team = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"}).json()["team_performance"]
    assert {item["username"] for item in team} >= {"metricalice", "metricbob", "metricsadmin"}


def test_truth_gate_blocks_current_event_until_evidence_is_mapped(tmp_db):
    client = TestClient(app.app)
    headers = _signup_login(client, "facteditor", "事实编辑")
    created = client.post("/api/queue", headers=headers, json={
        "title": "德班港最新动态",
        "body": "Transnet 今日宣布德班港部分作业延误。",
        "platforms": ["facebook"],
    })
    assert created.status_code == 200
    assert created.json()["verification"]["status"] == "needs_evidence"
    item_id = client.get("/api/queue", headers=headers).json()[0]["id"]
    blocked = client.put(f"/api/queue/{item_id}/status", headers=headers, json={"status": "queued"})
    assert blocked.status_code == 409

    evidence = [{
        "claim": "德班港最新动态",
        "url": "https://example.org/port-update",
        "source_title": "Port operational update",
        "publisher": "Transnet",
        "excerpt": "Operations at one terminal are delayed.",
    }, {
        "claim": "Transnet 今日宣布德班港部分作业延误",
        "url": "https://example.org/port-update",
        "source_title": "Port operational update",
        "publisher": "Transnet",
        "excerpt": "Operations at one terminal are delayed.",
    }]
    verified = client.put(f"/api/queue/{item_id}/evidence", headers=headers, json={"source_refs": evidence})
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    assert client.put(f"/api/queue/{item_id}/status", headers=headers, json={"status": "queued"}).status_code == 200
