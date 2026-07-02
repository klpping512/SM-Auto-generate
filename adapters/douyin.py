"""抖音发布（RPA）：cookie 登录 + 创作平台上传视频 + 填文案 + 发布。"""
import logging

from adapters.base import PublishResult
from adapters.rpa_base import RpaAdapter

logger = logging.getLogger(__name__)


class DouyinAdapter(RpaAdapter):
    name = "douyin"
    CREDENTIAL_KIND = "cookie"
    login_url = "https://creator.douyin.com/"
    publish_url = "https://creator.douyin.com/creator-micro/content/upload"

    LOGGED_IN_MARK = "text=发布视频"
    UPLOAD_INPUT = "input[type='file']"
    CONTENT_INPUT = "div[contenteditable='true'], textarea"
    SUBMIT_BUTTON = "button:has-text('发布')"

    def _logged_in_selector(self) -> str:
        return self.LOGGED_IN_MARK

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        if not account:
            return PublishResult(success=False, platform=self.name, error="缺少账号（无 cookie 可用）")
        media = [video] if video else (images or [])
        if not media:
            return PublishResult(success=False, platform=self.name,
                                 error="抖音必须上传视频或图文素材")
        if not await self.check_login(account):
            return PublishResult(success=False, platform=self.name,
                                 error="cookie/登录失效，请重新登录抖音账号")

        caption = f"{title} {content}".strip() if title else content
        if tags:
            caption += " " + " ".join(f"#{t}" for t in tags)

        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                try:
                    page = await context.new_page()
                    await page.goto(self.publish_url, timeout=30000)
                    await page.set_input_files(self.UPLOAD_INPUT, media, timeout=60000)
                    await page.fill(self.CONTENT_INPUT, caption, timeout=15000)
                    await page.click(self.SUBMIT_BUTTON, timeout=15000)
                    await page.wait_for_timeout(3000)
                    logger.info("抖音发布已提交: %s", title)
                    return PublishResult(success=True, platform=self.name, output="submitted")
                finally:
                    await browser.close()
        except Exception as e:
            logger.exception("抖音发布异常")
            return PublishResult(success=False, platform=self.name, error=str(e))
