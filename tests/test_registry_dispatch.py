import publisher
import adapters
from adapters.base import PublishAdapter, PublishResult


class _Dummy(PublishAdapter):
    name = "dummy"
    async def publish(self, *, platform, title, content,
                      tags=None, images=None, video=None, account=None):
        return PublishResult(success=True, platform=platform, output=f"{title}:{content}")


def test_get_adapter_unknown_returns_none():
    assert adapters.get_adapter("bilibili") is None  # 本期不支持的平台


async def test_dispatch_unknown_platform():
    result = await publisher.dispatch(platform="bilibili", title="T", content="B")
    assert result["success"] is False and "bilibili" in result["error"]


async def test_dispatch_routes_to_registered_adapter(monkeypatch):
    monkeypatch.setitem(adapters.ADAPTERS, "dummy", _Dummy())
    result = await publisher.dispatch(platform="dummy", title="T", content="B")
    assert result == {"success": True, "platform": "dummy", "output": "T:B"}
