"""发布适配器接口契约。所有平台适配器实现同一协议。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PublishResult:
    success: bool
    platform: str
    error: str | None = None
    output: str | None = None
    # 结构化失败分类：login_expired/no_images/timeout/selector_failed/
    # page_not_ready/attachment_missing/no_account/unknown；成功或旧适配器为 None
    category: str | None = None

    def to_dict(self) -> dict:
        d = {"success": self.success, "platform": self.platform}
        if self.error is not None:
            d["error"] = self.error
        if self.output is not None:
            d["output"] = self.output
        if self.category is not None:
            d["category"] = self.category
        return d


class PublishAdapter(ABC):
    """一个平台（或一组平台）的发布实现。"""

    name: str = ""
    # 该适配器发布所需的凭据字段（供前端表单 + 就绪度判定，单一来源）
    REQUIRED_CREDENTIALS: list[str] = []
    # "token"=手填字段；"cookie"=靠扫码登录写入 cookies
    CREDENTIAL_KIND: str = "token"

    @abstractmethod
    async def publish(
        self, *, platform: str, title: str, content: str,
        tags: list[str] | None = None, images: list[str] | None = None,
        video: str | None = None, account: dict | None = None,
    ) -> PublishResult:
        ...

    async def check_login(self, account: dict | None = None) -> bool:
        """默认恒为已登录（无状态/Token 型）。RPA 子类覆写。"""
        return True
