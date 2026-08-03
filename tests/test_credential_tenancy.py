"""Credential encryption and tenancy hardening tests."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_credential_store_round_trip(tmp_path, monkeypatch):
    import credential_store

    key_path = tmp_path / ".credential_key"
    monkeypatch.setattr(credential_store, "_key_path", lambda: key_path)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

    plaintext = json.dumps({"cookies": [{"name": "sid", "value": "secret"}]})
    stored = credential_store.encrypt_credentials(plaintext)
    assert credential_store.looks_encrypted(stored)
    assert stored != plaintext
    assert credential_store.decrypt_credentials(stored) == plaintext
    assert credential_store.decrypt_credentials(plaintext) == plaintext


def test_account_credentials_are_encrypted_at_rest(tmp_db, monkeypatch, tmp_path):
    import credential_store
    import database as db

    key_path = tmp_path / ".credential_key"
    monkeypatch.setattr(credential_store, "_key_path", lambda: key_path)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

    tmp_db.create_user("cred-owner", "hash", "editor", "Cred Owner")
    owner = tmp_db.get_user_by_username("cred-owner")
    tmp_db.create_account(
        "douyin", "测试号", "dy-secret-1",
        credentials='{"cookies":[{"name":"a","value":"b"}]}',
        owner_id=owner["id"],
    )
    accounts = tmp_db.get_accounts(owner_id=owner["id"])
    assert accounts
    assert json.loads(accounts[0]["credentials"])["cookies"][0]["value"] == "b"

    with tmp_db.get_conn() as conn:
        raw = conn.execute(
            "SELECT credentials FROM accounts WHERE account_id=?", ("dy-secret-1",),
        ).fetchone()[0]
    assert raw.startswith("enc:v1:")
    assert "cookies" not in raw


def test_publish_logs_are_scoped_to_owner(tmp_db):
    import app
    import auth

    tmp_db.create_user("pub-a", auth.hash_password("pw12345"), "editor", "A")
    tmp_db.create_user("pub-b", auth.hash_password("pw12345"), "editor", "B")
    owner_a = tmp_db.get_user_by_username("pub-a")
    owner_b = tmp_db.get_user_by_username("pub-b")
    qid = tmp_db.add_to_queue("标题A", "正文", "douyin", created_by=owner_a["id"])
    tmp_db.add_publish_log(qid, "douyin", "标题A", "published")
    qid_b = tmp_db.add_to_queue("标题B", "正文", "douyin", created_by=owner_b["id"])
    tmp_db.add_publish_log(qid_b, "douyin", "标题B", "published")

    client = TestClient(app.app)
    token_b = client.post("/api/auth/login", json={"username": "pub-b", "password": "pw12345"}).json()["access_token"]
    logs = client.get("/api/publish/logs", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert all(item["title"] == "标题B" for item in logs)


def test_prompt_template_update_requires_owner(tmp_db):
    import app
    import auth

    tmp_db.create_user("tpl-a", auth.hash_password("pw12345"), "editor", "A")
    tmp_db.create_user("tpl-b", auth.hash_password("pw12345"), "editor", "B")
    owner_a = tmp_db.get_user_by_username("tpl-a")
    tpl_id = tmp_db.create_prompt_template("模板A", "通用", "内容A", owner_a["id"])

    client = TestClient(app.app)
    token_b = client.post("/api/auth/login", json={"username": "tpl-b", "password": "pw12345"}).json()["access_token"]
    response = client.put(
        f"/api/prompt-templates/{tpl_id}",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "改", "category": "通用", "content": "内容B"},
    )
    assert response.status_code == 403


def test_scheduler_expiry_only_marks_matching_owner(tmp_db):
    import auth
    import scheduler

    tmp_db.create_user("exp-a", auth.hash_password("pw12345"), "editor", "A")
    tmp_db.create_user("exp-b", auth.hash_password("pw12345"), "editor", "B")
    owner_a = tmp_db.get_user_by_username("exp-a")
    owner_b = tmp_db.get_user_by_username("exp-b")
    tmp_db.create_account("xiaohongshu", "A号", "xhs-a", credentials="{}", owner_id=owner_a["id"])
    tmp_db.create_account("xiaohongshu", "B号", "xhs-b", credentials="{}", owner_id=owner_b["id"])

    scheduler._maybe_mark_expired("xiaohongshu", "Cookie 失效", owner_id=owner_a["id"])
    accounts = {item["account_id"]: item["status"] for item in tmp_db.get_accounts("xiaohongshu")}
    assert accounts["xhs-a"] == "expired"
    assert accounts["xhs-b"] == "active"
