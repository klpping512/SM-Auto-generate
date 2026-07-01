"""适配器注册表：platform -> adapter 实例。新增平台 = 加文件 + 在 _register_all 注册。"""
from adapters.base import PublishAdapter, PublishResult

ADAPTERS: dict[str, PublishAdapter] = {}


def _register_all():
    # Task 13 在此注册 5 个平台适配器：facebook/twitter/reddit/xiaohongshu/douyin
    pass


_register_all()


def get_adapter(platform: str) -> PublishAdapter | None:
    return ADAPTERS.get(platform)
