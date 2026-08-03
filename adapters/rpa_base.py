"""Playwright RPA 适配器基类：cookie 登录态存取 + 登录骨架。"""
import json
import logging
import os
from urllib.parse import unquote, urlparse

import database as db
from adapters.base import PublishAdapter

logger = logging.getLogger(__name__)


def playwright_proxy_from_env(environ: dict | None = None) -> dict | None:
    """把常见代理环境变量转换为 Playwright 的 proxy 配置。"""
    env = environ if environ is not None else os.environ
    raw = next((env.get(k) for k in (
        "RPA_PROXY", "rpa_proxy", "HTTPS_PROXY", "https_proxy",
        "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
    ) if env.get(k)), "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if not parsed.hostname:
        return None
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def browser_launch_options(*, headless: bool, use_proxy: bool = True) -> dict:
    options = {
        "headless": headless,
        "args": [
            # 反检测
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            # GPU + 视频解码（解决抖音/小红书视频预览黑屏）
            "--enable-gpu",
            "--ignore-gpu-blocklist",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--enable-hardware-overlays",
            "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-webgl",
            "--autoplay-policy=no-user-gesture-required",
        ],
    }
    # 国内站点（小红书/抖音）必须直连；若走 Clash 等国外代理会被掐断（ERR_CONNECTION_CLOSED）。
    if use_proxy:
        proxy = playwright_proxy_from_env()
        if proxy:
            options["proxy"] = proxy
    return options


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
    use_proxy = False  # RPA 目标多为国内站，默认直连；如需代理的平台可在子类置 True

    def _logged_in_selector(self) -> str:
        raise NotImplementedError

    async def _new_context(self, playwright, account: dict | None):
        browser = await playwright.chromium.launch(**browser_launch_options(headless=self.headless, use_proxy=self.use_proxy))
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        # 隐藏 webdriver 标记，绕过抖音反爬检测
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            window.chrome = {runtime: {}};
        """)
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
                    # 目标站点多为 SPA：goto 后会有客户端重定向/异步渲染，
                    # 必须 wait_for_selector 等登录态标记出现，不能用 query_selector 立即判断。
                    await page.goto(self.login_url, timeout=30000, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_selector(self._logged_in_selector(), timeout=20000)
                        return True
                    except Exception:
                        # 兜底：登录态下访问 /login 通常会被重定向离开登录页；
                        # 若最终 URL 不再停留在登录页，视为已登录（应对 SPA 文案/结构变化）。
                        await page.wait_for_timeout(1500)
                        return "login" not in (page.url or "").lower()
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("check_login 异常: platform=%s, err=%s", self.name, e)
            return False

    async def save_login(self, account: dict, context) -> None:
        cookies = await context.cookies()
        db.update_account_credentials(account["account_id"], build_credentials(cookies))
        logger.info("已保存登录 cookie: platform=%s, account=%s", self.name, account["account_id"])
