import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.twitter import TwitterAdapter

SAMPLE_CREDS = {
    "client_id": "client-id",
    "access_token": "at",
    "token_obtained_at": time.time(),
    "expires_in": 7200,
}


async def test_success(monkeypatch):
    """OAuth 2.0 Bearer token + 正常响应 → success。"""
    a = TwitterAdapter()

    fake_resp = MagicMock()
    fake_resp.status_code = 201
    fake_resp.json.return_value = {"data": {"id": "999", "text": "hello"}}

    mock_client = AsyncMock()
    mock_client.post.return_value = fake_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("adapters.twitter.httpx.AsyncClient", return_value=mock_client):
        acc = {"credentials": json.dumps(SAMPLE_CREDS)}
        r = await a.publish(platform="twitter", title="", content="hello", account=acc)

    assert r.success and r.output == "999"
    # 验证 Authorization header 使用 OAuth 2.0 Bearer token
    call_args = mock_client.post.call_args
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer at"


async def test_missing_credentials():
    """缺凭据 → 报错。"""
    a = TwitterAdapter()
    r = await a.publish(platform="twitter", title="", content="hi",
                        account={"credentials": "{}"})
    assert r.success is False and "client_id" in r.error


async def test_api_error():
    """API 返回错误 → 透传错误信息。"""
    a = TwitterAdapter()

    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.json.return_value = {"detail": "Unsupported Authentication"}

    mock_client = AsyncMock()
    mock_client.post.return_value = fake_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("adapters.twitter.httpx.AsyncClient", return_value=mock_client):
        acc = {"credentials": json.dumps(SAMPLE_CREDS)}
        r = await a.publish(platform="twitter", title="", content="hi", account=acc)

    assert r.success is False and "Unsupported Authentication" in r.error
