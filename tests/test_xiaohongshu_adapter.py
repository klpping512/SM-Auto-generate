from adapters.xiaohongshu import XiaohongshuAdapter
from adapters.base import PublishResult


def test_identity():
    a = XiaohongshuAdapter()
    assert a.name == "xiaohongshu"
    assert "xiaohongshu" in a.login_url and a._logged_in_selector()


async def test_requires_images():
    a = XiaohongshuAdapter()
    r = await a.publish(platform="xiaohongshu", title="T", content="正文",
                        images=None, account={"account_id": "x1", "credentials": "{}"})
    assert isinstance(r, PublishResult)
    assert r.success is False and "配图" in r.error


async def test_requires_account():
    a = XiaohongshuAdapter()
    r = await a.publish(platform="xiaohongshu", title="T", content="正文",
                        images=["/tmp/a.jpg"], account=None)
    assert r.success is False and "账号" in r.error


async def test_short_circuit_when_not_logged_in(monkeypatch):
    a = XiaohongshuAdapter()
    async def not_logged_in(account=None):
        return False
    monkeypatch.setattr(a, "check_login", not_logged_in)
    r = await a.publish(platform="xiaohongshu", title="T", content="正文",
                        images=["/tmp/a.jpg"], account={"account_id": "x1", "credentials": "{}"})
    assert r.success is False and ("登录" in r.error or "cookie" in r.error.lower())
