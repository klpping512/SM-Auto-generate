"""慧媒 CLI 平台适配器，支持为同一平台明确路由到不同账号。"""
from adapters.base import PublishAdapter, PublishResult


class HuimeiAdapter(PublishAdapter):
    CREDENTIAL_KIND = "none"
    REQUIRED_CREDENTIALS = []

    def __init__(self, name: str):
        self.name = name

    async def publish(self, *, platform, title, content, tags=None, images=None,
                      video=None, account=None) -> PublishResult:
        from publisher import publish_via_huimei
        if not account:
            return PublishResult(False, platform, error="未选择发布账号")
        result = await publish_via_huimei(platform, title, content, tags, images, video,
                                          account=account.get("account_id"))
        return PublishResult(bool(result.get("success")), platform,
                             error=result.get("error"), output=result.get("output"))
