"""适配器注册表：platform -> adapter 实例。新增平台 = 加文件 + 在 _register_all 注册。"""
from adapters.base import PublishAdapter, PublishResult

ADAPTERS: dict[str, PublishAdapter] = {}


def _register_all():
    from adapters.facebook import FacebookAdapter
    from adapters.twitter import TwitterAdapter
    from adapters.reddit import RedditAdapter
    from adapters.xiaohongshu import XiaohongshuAdapter
    from adapters.douyin import DouyinAdapter
    from adapters.huimei import HuimeiAdapter

    ADAPTERS["facebook"] = FacebookAdapter()
    ADAPTERS["twitter"] = TwitterAdapter()
    ADAPTERS["reddit"] = RedditAdapter()
    ADAPTERS["xiaohongshu"] = XiaohongshuAdapter()
    ADAPTERS["douyin"] = DouyinAdapter()
    for platform in ("wechat_mp", "wechat_channels", "bilibili", "weibo", "kuaishou", "toutiao", "zhihu", "baijiahao", "tiktok"):
        ADAPTERS[platform] = HuimeiAdapter(platform)


_register_all()


def get_adapter(platform: str) -> PublishAdapter | None:
    return ADAPTERS.get(platform)
