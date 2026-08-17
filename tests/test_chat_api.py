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


def test_chat_api_returns_editor_payload(tmp_db, monkeypatch, tmp_path):
    client, headers = _authorized_client(tmp_db)
    captured = {}
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path)

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
    assert data["outputs"][0]["attachments"]
    assert data["outputs"][1].get("attachments") is None
    assert captured["tone"] == "urgent"
    assert captured["platforms"] == ["xiaohongshu", "facebook"]
    assert len(captured["messages"]) == 3
    assert captured["messages"][-1]["content"] == "沿用刚才内容，改得更紧迫"


def test_chat_api_isolates_xiaohongshu_render_permission_error(tmp_db, monkeypatch):
    client, headers = _authorized_client(tmp_db)

    async def fake_chat_platforms(**kwargs):
        return [
            {"platform": "xiaohongshu", "title": "小红书港口提醒", "body": "请提前核对船期。", "hashtags": ["南非物流"], "content": "xhs"},
            {"platform": "douyin", "title": "抖音港口提醒", "body": "现场变化先看官方通报。", "hashtags": ["南非物流"], "content": "dy", "scenes": []},
        ]

    def boom(*_args, **_kwargs):
        raise PermissionError("[Errno 13] Permission denied: '/opt/distribution-manager/static/uploads/image/xhs.png'")

    monkeypatch.setattr(app.ai_engine, "chat_platforms", fake_chat_platforms)
    monkeypatch.setattr(app, "_render_xhs_carousel", boom)
    response = client.post("/api/ai/chat", headers=headers, json={
        "messages": [{"role": "user", "content": "生成港口预警"}],
        "platforms": ["xiaohongshu", "douyin"],
        "topic": "德班港",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["outputs"][1]["platform"] == "douyin"
    assert data["outputs"][1]["title"] == "抖音港口提醒"
    assert data["outputs"][0]["title"] == "小红书港口提醒"
    assert not data["outputs"][0].get("attachments")
    assert data["platform_errors"][0]["platform"] == "xiaohongshu"
    assert data["platform_errors"][0]["error_type"] == "permission_denied"
    assert data["platform_errors"][0]["message"] == "文案已生成，小红书配图失败，请重试配图"
    assert "/opt/distribution-manager" not in response.text
    assert "Permission denied" not in response.text


def test_chat_page_has_multi_session_history():
    from fastapi.testclient import TestClient

    response = TestClient(app.app).get("/chat.html")
    assert response.status_code == 200
    assert "历史对话" in response.text
    assert "logiflowChatSessionsV2" in response.text
    assert "switchSession" in response.text


def test_chat_to_editor_uses_versioned_multi_platform_transfer_contract():
    from fastapi.testclient import TestClient

    client = TestClient(app.app)
    chat_page = client.get("/chat.html").text
    editor_page = client.get("/editor.html").text
    transfer_module = client.get("/static/editor-transfer.js").text

    assert "editor-transfer.js" in chat_page
    assert "EditorTransfer.buildDraft" in chat_page
    assert "editor-transfer.js" in editor_page
    assert "EditorTransfer.normalizeDraft" in editor_page
    assert "version: 3" in transfer_module
    assert "video_workflow" in transfer_module
    assert "activePlatform" in transfer_module
    assert "contents" in transfer_module
    assert "refreshPreviewTabs" in editor_page
    assert "该平台未从 AI 对话导入，当前内容已保留" in editor_page
    assert "未选择主题" not in editor_page
