from adapters.twitter import TwitterAdapter


async def test_success(monkeypatch):
    a = TwitterAdapter()
    async def fake(url, **kw):
        assert url.endswith("/2/tweets")
        assert kw["headers"]["Authorization"] == "Bearer tok"
        assert kw["json"]["text"].startswith("hello")
        return 201, {"data": {"id": "999", "text": "hello"}}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"access_token":"tok"}'}
    r = await a.publish(platform="twitter", title="", content="hello", account=acc)
    assert r.success and r.output == "999"


async def test_missing_token():
    a = TwitterAdapter()
    r = await a.publish(platform="twitter", title="", content="hi", account={"credentials": "{}"})
    assert r.success is False and "access_token" in r.error


async def test_api_error(monkeypatch):
    a = TwitterAdapter()
    async def fake(url, **kw):
        return 403, {"detail": "Unsupported Authentication"}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"access_token":"bad"}'}
    r = await a.publish(platform="twitter", title="", content="hi", account=acc)
    assert r.success is False and "Unsupported Authentication" in r.error
