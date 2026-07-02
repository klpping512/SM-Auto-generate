"""小红书图文发布（RPA）：cookie 登录 + 创作平台上传图片 + 填标题正文 + 发布。"""
import logging

from adapters.base import PublishResult
from adapters.rpa_base import RpaAdapter

logger = logging.getLogger(__name__)


class XiaohongshuAdapter(RpaAdapter):
    name = "xiaohongshu"
    CREDENTIAL_KIND = "cookie"
    login_url = "https://creator.xiaohongshu.com/login"
    publish_url = "https://creator.xiaohongshu.com/publish/publish?target=image"

    LOGGED_IN_MARK = "text=发布笔记"
    UPLOAD_INPUT = "input[type='file']"
    TITLE_INPUT = "input[placeholder*='标题']"
    CONTENT_INPUT = "div[contenteditable='true']"
    SUBMIT_BUTTON = "button:has-text('发布')"

    def _logged_in_selector(self) -> str:
        return self.LOGGED_IN_MARK

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        if not account:
            return PublishResult(success=False, platform=self.name, error="缺少账号（无 cookie 可用）")
        if not images:
            return PublishResult(success=False, platform=self.name,
                                 error="小红书必须配图，images 不能为空")
        if not await self.check_login(account):
            return PublishResult(success=False, platform=self.name,
                                 error="cookie/登录失效，请重新登录小红书账号")

        body = content
        if tags:
            body += " " + " ".join(f"#{t}" for t in tags)

        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                try:
                    page = await context.new_page()
                    await page.goto(self.publish_url, timeout=30000)
                    await page.set_input_files(self.UPLOAD_INPUT, images, timeout=30000)
                    await page.fill(self.TITLE_INPUT, title or content[:20], timeout=15000)
                    await page.fill(self.CONTENT_INPUT, body, timeout=15000)
                    await page.click(self.SUBMIT_BUTTON, timeout=15000)
                    await page.wait_for_timeout(3000)
                    logger.info("小红书发布已提交: %s", title)
                    return PublishResult(success=True, platform=self.name, output="submitted")
                finally:
                    await browser.close()
        except Exception as e:
            logger.exception("小红书发布异常")
            return PublishResult(success=False, platform=self.name, error=str(e))
