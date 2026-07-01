from adapters.facebook import FacebookAdapter
from adapters.base import PublishResult


async def test_success(monkeypatch):
    a = FacebookAdapter()
    async def fake(url, **kw):
        assert "/123/feed" in url
        assert kw["data"]["message"] == "hello"
        assert kw["data"]["access_token"] == "tok"
        return 200, {"id": "123_456"}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"page_id":"123","page_access_token":"tok"}'}
    r = await a.publish(platform="facebook", title="", content="hello", account=acc)
    assert isinstance(r, PublishResult)
    assert r.success and r.output == "123_456"


async def test_missing_credentials():
    a = FacebookAdapter()
    r = await a.publish(platform="facebook", title="", content="hi", account={"credentials": "{}"})
    assert r.success is False and "page_id" in r.error


async def test_api_error(monkeypatch):
    a = FacebookAdapter()
    async def fake(url, **kw):
        return 400, {"error": {"message": "Invalid OAuth token"}}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"page_id":"123","page_access_token":"bad"}'}
    r = await a.publish(platform="facebook", title="", content="hi", account=acc)
    assert r.success is False and "Invalid OAuth token" in r.error
