from adapters.douyin import DouyinAdapter
from adapters.base import PublishResult


def test_identity():
    a = DouyinAdapter()
    assert a.name == "douyin"
    assert "douyin" in a.login_url and a._logged_in_selector()


async def test_requires_media():
    a = DouyinAdapter()
    r = await a.publish(platform="douyin", title="T", content="正文",
                        video=None, images=None, account={"account_id": "d1", "credentials": "{}"})
    assert isinstance(r, PublishResult)
    assert r.success is False and ("视频" in r.error or "图文" in r.error)


async def test_requires_account():
    a = DouyinAdapter()
    r = await a.publish(platform="douyin", title="T", content="正文",
                        video="/tmp/v.mp4", account=None)
    assert r.success is False and "账号" in r.error


async def test_short_circuit_when_not_logged_in(monkeypatch):
    a = DouyinAdapter()
    async def not_logged_in(account=None):
        return False
    monkeypatch.setattr(a, "check_login", not_logged_in)
    r = await a.publish(platform="douyin", title="T", content="正文",
                        video="/tmp/v.mp4", account={"account_id": "d1", "credentials": "{}"})
    assert r.success is False and ("登录" in r.error or "cookie" in r.error.lower())
