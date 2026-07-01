"""适配器注册表：platform -> adapter 实例。新增平台 = 加文件 + 在 _register_all 注册。"""
from adapters.base import PublishAdapter, PublishResult

ADAPTERS: dict[str, PublishAdapter] = {}


def _register_all():
    from adapters.facebook import FacebookAdapter
    from adapters.twitter import TwitterAdapter
    from adapters.reddit import RedditAdapter
    from adapters.xiaohongshu import XiaohongshuAdapter
    from adapters.douyin import DouyinAdapter

    ADAPTERS["facebook"] = FacebookAdapter()
    ADAPTERS["twitter"] = TwitterAdapter()
    ADAPTERS["reddit"] = RedditAdapter()
    ADAPTERS["xiaohongshu"] = XiaohongshuAdapter()
    ADAPTERS["douyin"] = DouyinAdapter()


_register_all()


def get_adapter(platform: str) -> PublishAdapter | None:
    return ADAPTERS.get(platform)
