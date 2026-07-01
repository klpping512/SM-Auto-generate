"""Playwright RPA 适配器基类：cookie 登录态存取 + 登录骨架。"""
import json
import logging

import database as db
from adapters.base import PublishAdapter

logger = logging.getLogger(__name__)


def parse_cookies(credentials: str | None) -> list[dict]:
    if not credentials:
        return []
    try:
        return json.loads(credentials).get("cookies", []) or []
    except (json.JSONDecodeError, AttributeError):
        return []


def build_credentials(cookies: list[dict]) -> str:
    return json.dumps({"cookies": cookies}, ensure_ascii=False)


class RpaAdapter(PublishAdapter):
    name = ""
    login_url = ""
    headless = True

    def _logged_in_selector(self) -> str:
        raise NotImplementedError

    async def _new_context(self, playwright, account: dict | None):
        browser = await playwright.chromium.launch(headless=self.headless)
        context = await browser.new_context()
        cookies = parse_cookies((account or {}).get("credentials"))
        if cookies:
            await context.add_cookies(cookies)
        return browser, context

    async def check_login(self, account: dict | None = None) -> bool:
        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                try:
                    page = await context.new_page()
                    await page.goto(self.login_url, timeout=30000)
                    return await page.query_selector(self._logged_in_selector()) is not None
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("check_login 异常: platform=%s, err=%s", self.name, e)
            return False

    async def save_login(self, account: dict, context) -> None:
        cookies = await context.cookies()
        db.update_account_credentials(account["account_id"], build_credentials(cookies))
        logger.info("已保存登录 cookie: platform=%s, account=%s", self.name, account["account_id"])
