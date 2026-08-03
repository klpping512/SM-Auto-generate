"""抖音发布（RPA）：cookie 登录 + 创作平台上传视频 + 填文案 + 发布。"""
import logging
from pathlib import Path

from adapters.base import PublishResult
from adapters.rpa_base import RpaAdapter

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path(__file__).resolve().parents[1] / "static" / "debug"


class DouyinAdapter(RpaAdapter):
    name = "douyin"
    CREDENTIAL_KIND = "cookie"
    login_url = "https://creator.douyin.com/"
    publish_url = "https://creator.douyin.com/creator-micro/content/upload"

    LOGGED_IN_MARK = "text=发布视频"
    UPLOAD_INPUT = "input[type='file']"
    # 抖音的文案输入框：优先 .ql-editor（Quill 编辑器），兜底 contenteditable div
    CONTENT_INPUT = ".ql-editor, div[contenteditable='true'][data-placeholder]"
    # 发布按钮：精确匹配 footer 区域的发布按钮，避开页面标题「发布视频」
    SUBMIT_BUTTON = "button.css-1igwz5m, button[class*='submit'], div[class*='footer'] button"

    def _logged_in_selector(self) -> str:
        return self.LOGGED_IN_MARK

    async def _save_debug_shot(self, page, prefix: str) -> str:
        """把当前页面截图存到 static/debug，返回可访问的相对路径。"""
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = _DEBUG_DIR / f"{prefix}.png"
            await page.screenshot(path=str(path), full_page=True)
            return f"/static/debug/{prefix}.png"
        except Exception as exc:
            logger.warning("保存调试截图失败: %s", exc)
            return ""

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
        # 在启动发布浏览器前先验证登录态。这样 Cookie 失效或运行环境缺少
        # Playwright 浏览器时，用户获得的是可操作的登录提示，而不是底层路径错误。
        if not await self.check_login(account):
            return PublishResult(
                success=False,
                platform=self.name,
                error="cookie/登录失效或浏览器未就绪，请到「账号管理」重新扫码登录抖音后再发布",
            )

        body = content
        if tags:
            body += " " + " ".join(f"#{t}" for t in tags)

        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                page = await context.new_page()
                try:
                    await page.goto(self.publish_url, timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    # 登录态判断：cookie 失效时抖音会重定向到登录页
                    if "login" in (page.url or "").lower():
                        return PublishResult(success=False, platform=self.name,
                                             error="cookie/登录失效，请到「账号管理」重新扫码登录抖音")
                    # 等待上传入口就绪
                    try:
                        await page.wait_for_selector(self.UPLOAD_INPUT, state="attached", timeout=20000)
                    except Exception:
                        if "login" in (page.url or "").lower():
                            return PublishResult(success=False, platform=self.name,
                                                 error="cookie/登录失效，请重新扫码登录抖音")
                        shot = await self._save_debug_shot(page, "dy-no-upload")
                        return PublishResult(success=False, platform=self.name,
                                             error=f"发布页未就绪（未找到上传入口）。已截图: {shot}")
                    await page.set_input_files(self.UPLOAD_INPUT, media, timeout=60000)
                    # 等视频上传/处理
                    await page.wait_for_timeout(8000)
                    # 填写文案：优先用 Quill 编辑器，兜底 contenteditable
                    await self._fill_content(page, body)
                    await page.wait_for_timeout(1500)
                    # 点击发布
                    if not await self._click_submit(page):
                        shot = await self._save_debug_shot(page, "dy-submit-fail")
                        return PublishResult(
                            success=False, platform=self.name,
                            error=f"未找到可点击的「发布」按钮。已截图: {shot}",
                        )
                    await page.wait_for_timeout(3000)
                    logger.info("抖音发布已提交: %s", title)
                    return PublishResult(success=True, platform=self.name, output="submitted")
                except Exception:
                    await self._save_debug_shot(page, "dy-error")
                    raise
                finally:
                    await browser.close()
        except Exception as e:
            logger.exception("抖音发布异常")
            return PublishResult(success=False, platform=self.name, error=str(e))

    async def fill_publish_form(self, page, *, title, content, tags=None, images=None, video=None):
        """打开抖音创作平台上传页，自动填好文案和话题标签，但**不点发布**，供人工复核后手动发布。"""
        await page.goto(self.publish_url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        # 检查登录态
        if "login" in (page.url or "").lower():
            raise RuntimeError("cookie/登录失效，请到「账号管理」重新扫码登录抖音")
        # 关闭所有弹窗（共创中心提示、位置权限、视频预览说明等）
        await self._dismiss_popups(page)
        # 等待上传入口就绪
        try:
            await page.wait_for_selector(self.UPLOAD_INPUT, state="attached", timeout=20000)
        except Exception:
            if "login" in (page.url or "").lower():
                raise RuntimeError("cookie/登录失效，请重新扫码登录抖音")
            shot = await self._save_debug_shot(page, "dy-fill-no-upload")
            raise RuntimeError(f"发布页未就绪（未找到上传入口）。已截图: {shot}")
        # 上传视频（如果有）
        media = [video] if video else (images or [])
        if media:
            await page.set_input_files(self.UPLOAD_INPUT, media, timeout=60000)
            await page.wait_for_timeout(8000)  # 等视频上传处理
            await self._dismiss_popups(page)  # 上传后可能弹新提示
        # 组合文案：标题 + 正文 + 话题标签
        caption = f"{title} {content}".strip() if title else content
        if tags:
            caption += " " + " ".join(f"#{t}" for t in tags)
        # 填写文案
        await self._fill_content(page, caption)
        logger.info("抖音手动发布：已填好内容，等待用户手动发布")

    async def _dismiss_popups(self, page):
        """关闭抖音页面上的各种弹窗（共创中心提示、位置权限、视频预览说明等）。"""
        dismiss_selectors = [
            # "我知道了" 按钮（共创中心、视频预览说明等提示弹窗）
            "button:has-text('我知道了')",
            "button:has-text('知道了')",
            "button:has-text('好的')",
            "button:has-text('关闭')",
            # 位置权限弹窗 - 点"禁止"
            "button:has-text('禁止')",
            # 通用关闭按钮（弹窗右上角 X）
            ".semi-modal-close, .modal-close, [class*='close-btn']",
            # 半透明遮罩层后面的确认按钮
            ".semi-button-content:has-text('我知道了')",
        ]
        for selector in dismiss_selectors:
            try:
                btns = page.locator(selector)
                count = await btns.count()
                for i in range(count):
                    btn = btns.nth(i)
                    if await btn.is_visible():
                        await btn.click(timeout=2000)
                        await page.wait_for_timeout(500)
                        logger.info("抖音弹窗已关闭: %s", selector)
            except Exception:
                continue

    async def _fill_content(self, page, text: str):
        """填写抖音文案输入框。多策略兜底：Quill 编辑器 → contenteditable → textarea。"""
        strategies = [
            # 策略1: Quill 编辑器（抖音常用）
            (".ql-editor", "fill"),
            # 策略2: 带 placeholder 的 contenteditable
            ("div[contenteditable='true'][data-placeholder]", "type"),
            # 策略3: 任意 contenteditable
            ("div[contenteditable='true']", "type"),
            # 策略4: textarea 兜底
            ("textarea", "fill"),
        ]
        for selector, method in strategies:
            try:
                el = page.locator(selector).first
                await el.wait_for(state="visible", timeout=5000)
                await el.click()
                if method == "fill":
                    await el.fill(text, timeout=10000)
                else:
                    # type 方式：先清空再逐字输入，触发编辑器事件
                    await page.keyboard.press("Meta+a")
                    await page.keyboard.press("Backspace")
                    await el.type(text, delay=3)
                logger.info("抖音文案已填入 (selector: %s)", selector)
                return
            except Exception:
                continue
        # 所有策略都失败
        shot = await self._save_debug_shot(page, "dy-fill-content-fail")
        raise RuntimeError(f"无法填写文案（所有输入框策略均失败）。已截图: {shot}")

    async def _click_submit(self, page) -> bool:
        """点击抖音发布按钮。多策略兜底，避开页面标题「发布视频」。"""
        candidates = [
            # 策略1: 精确匹配「发布」文字的按钮（exact 避开「发布视频」）
            page.get_by_role("button", name="发布", exact=True),
            # 策略2: footer 区域的按钮
            page.locator("div[class*='footer'] button, div[class*='bottom'] button"),
            # 策略3: 主操作按钮（通常是蓝色/紫色）
            page.locator("button.css-1igwz5m, button[class*='primary'], button[class*='submit']"),
            # 策略4: 文字精确匹配
            page.get_by_text("发布", exact=True),
        ]
        for loc in candidates:
            try:
                btn = loc.last
                await btn.wait_for(state="visible", timeout=6000)
                await btn.scroll_into_view_if_needed(timeout=3000)
                await btn.click(timeout=6000)
                logger.info("抖音发布按钮已点击")
                return True
            except Exception:
                continue
        return False
