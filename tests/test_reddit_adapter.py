from adapters.reddit import RedditAdapter

CREDS = ('{"client_id":"ci","client_secret":"cs","refresh_token":"rt",'
         '"user_agent":"linux:com.sa.logiflow:v1 (by /u/bot)","subreddit":"test"}')


async def test_success(monkeypatch):
    a = RedditAdapter()
    async def fake_token(self, creds):
        return "tok"
    monkeypatch.setattr(RedditAdapter, "_get_access_token", fake_token)
    async def fake_post(url, **kw):
        assert url.endswith("/api/submit")
        assert kw["headers"]["Authorization"] == "Bearer tok"
        assert kw["data"]["sr"] == "test" and kw["data"]["title"] == "标题"
        return 200, {"json": {"errors": [], "data": {"url": "https://redd.it/x"}}}
    monkeypatch.setattr(a, "_post_json", fake_post)
    r = await a.publish(platform="reddit", title="标题", content="正文",
                        account={"credentials": CREDS})
    assert r.success and "redd.it" in r.output


async def test_missing_credentials():
    a = RedditAdapter()
    r = await a.publish(platform="reddit", title="t", content="b", account={"credentials": "{}"})
    assert r.success is False and "client_id" in r.error


async def test_submit_returns_errors(monkeypatch):
    a = RedditAdapter()
    monkeypatch.setattr(RedditAdapter, "_get_access_token", lambda self, creds: _ret("tok"))
    async def fake_post(url, **kw):
        return 200, {"json": {"errors": [["RATELIMIT", "too fast", "ratelimit"]]}}
    monkeypatch.setattr(a, "_post_json", fake_post)
    r = await a.publish(platform="reddit", title="t", content="b", account={"credentials": CREDS})
    assert r.success is False and "RATELIMIT" in r.error


async def _ret(v):
    return v
