import pytest
from adapters.base import PublishResult, PublishAdapter


def test_publish_result_to_dict_minimal():
    assert PublishResult(success=True, platform="reddit").to_dict() == {
        "success": True, "platform": "reddit"}


def test_publish_result_to_dict_full():
    r = PublishResult(success=False, platform="reddit", error="boom", output="log")
    assert r.to_dict() == {"success": False, "platform": "reddit", "error": "boom", "output": "log"}


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        PublishAdapter()


async def test_default_check_login_true():
    class Dummy(PublishAdapter):
        name = "dummy"
        async def publish(self, **kwargs):
            return PublishResult(success=True, platform="dummy")
    assert await Dummy().check_login() is True
