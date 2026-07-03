import app


def _authorized_client(tmp_db):
    from fastapi.testclient import TestClient
    import auth

    tmp_db.create_user("chatadmin", auth.hash_password("pw12345"), "admin", "Chat Admin")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={
        "username": "chatadmin", "password": "pw12345",
    }).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_root_serves_chat_page():
    from fastapi.testclient import TestClient

    response = TestClient(app.app).get("/")
    assert response.status_code == 200
    assert "AI 对话" in response.text
    assert "buffalo_logo_header.png" not in response.text  # Logo 由公共侧栏脚本渲染


def test_chat_api_returns_editor_payload(tmp_db, monkeypatch):
    client, headers = _authorized_client(tmp_db)
    captured = {}

    async def fake_chat_platforms(**kwargs):
        captured.update(kwargs)
        return [
            {"platform": platform, "title": f"{platform} 德班港预警", "body": f"{platform} 港口拥堵，请提前规划。", "hashtags": ["南非物流", "德班港"], "content": f"{platform} content"}
            for platform in kwargs["platforms"]
        ]

    monkeypatch.setattr(app.ai_engine, "chat_platforms", fake_chat_platforms)
    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [
            {"role": "user", "content": "生成港口预警"},
            {"role": "assistant", "content": "第一版港口预警"},
            {"role": "user", "content": "沿用刚才内容，改得更紧迫"},
        ],
        "tone": "urgent",
        "length": "short",
        "platforms": ["xiaohongshu", "facebook"],
        "topic": "德班港",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "xiaohongshu 德班港预警"
    assert data["body"].startswith("xiaohongshu 港口拥堵")
    assert data["hashtags"] == ["南非物流", "德班港"]
    assert [item["platform"] for item in data["outputs"]] == ["xiaohongshu", "facebook"]
    assert data["outputs"][0]["body"] != data["outputs"][1]["body"]
    assert captured["tone"] == "urgent"
    assert captured["platforms"] == ["xiaohongshu", "facebook"]
    assert len(captured["messages"]) == 3
    assert captured["messages"][-1]["content"] == "沿用刚才内容，改得更紧迫"


def test_chat_page_has_multi_session_history():
    from fastapi.testclient import TestClient

    response = TestClient(app.app).get("/chat.html")
    assert response.status_code == 200
    assert "历史对话" in response.text
    assert "logiflowChatSessionsV2" in response.text
    assert "switchSession" in response.text
