import publisher
import adapters
from adapters.base import PublishAdapter, PublishResult


class _Dummy(PublishAdapter):
    name = "dummy"
    async def publish(self, *, platform, title, content,
                      tags=None, images=None, video=None, account=None):
        return PublishResult(success=True, platform=platform, output=f"{title}:{content}")


class _AccountDummy(PublishAdapter):
    name = "account_dummy"
    CREDENTIAL_KIND = "token"
    REQUIRED_CREDENTIALS = ["token"]

    async def publish(self, *, platform, title, content,
                      tags=None, images=None, video=None, account=None):
        return PublishResult(success=account is not None, platform=platform,
                             output=(account or {}).get("account_id"))


def test_get_adapter_unknown_returns_none():
    assert adapters.get_adapter("unknown") is None


async def test_dispatch_unknown_platform():
    result = await publisher.dispatch(platform="unknown", title="T", content="B")
    assert result["success"] is False and "unknown" in result["error"]


async def test_dispatch_routes_to_registered_adapter(monkeypatch):
    monkeypatch.setitem(adapters.ADAPTERS, "dummy", _Dummy())
    result = await publisher.dispatch(platform="dummy", title="T", content="B")
    assert result == {"success": True, "platform": "dummy", "output": "T:B"}


async def test_dispatch_loads_ready_account_from_database(monkeypatch):
    monkeypatch.setitem(adapters.ADAPTERS, "account_dummy", _AccountDummy())
    monkeypatch.setattr(publisher.db, "get_accounts", lambda platform, owner_id=None: [
        {"account_id": "broken", "platform": platform, "status": "active", "credentials": "{}"},
        {"account_id": "ready", "platform": platform, "status": "active", "credentials": '{"token":"ok"}'},
    ])
    result = await publisher.dispatch(platform="account_dummy", title="T", content="B")
    assert result == {"success": True, "platform": "account_dummy", "output": "ready"}


def test_core_and_huimei_adapters_registered():
    from adapters.facebook import FacebookAdapter
    from adapters.twitter import TwitterAdapter
    from adapters.reddit import RedditAdapter
    from adapters.xiaohongshu import XiaohongshuAdapter
    from adapters.douyin import DouyinAdapter
    assert isinstance(adapters.get_adapter("facebook"), FacebookAdapter)
    assert isinstance(adapters.get_adapter("twitter"), TwitterAdapter)
    assert isinstance(adapters.get_adapter("reddit"), RedditAdapter)
    assert isinstance(adapters.get_adapter("xiaohongshu"), XiaohongshuAdapter)
    assert isinstance(adapters.get_adapter("douyin"), DouyinAdapter)
    assert adapters.get_adapter("wechat_mp").name == "wechat_mp"
    assert adapters.get_adapter("bilibili").name == "bilibili"
