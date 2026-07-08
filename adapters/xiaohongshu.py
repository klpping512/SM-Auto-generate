"""小红书图文发布（RPA）：cookie 登录 + 创作平台上传图片 + 填标题正文 + 发布。"""
import logging
from pathlib import Path

from adapters.base import PublishResult
from adapters.rpa_base import RpaAdapter

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path(__file__).resolve().parents[1] / "static" / "debug"


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
                    await page.wait_for_timeout(2500)  # 等 SPA 客户端重定向/渲染
                    # 登录态判断以发布页为准（去掉脆弱的独立 check_login 预检）：
                    # cookie 失效时小红书会把发布页重定向到登录页。
                    if "login" in (page.url or "").lower():
                        return PublishResult(success=False, platform=self.name,
                                             error="cookie/登录失效，请到「账号管理」重新扫码登录小红书")
                    # 上传输入框是发布页就绪的可靠标志；文件 input 是隐藏元素，
                    # 必须用 state="attached"（存在即可），不能等 visible，否则永远超时。
                    try:
                        await page.wait_for_selector(self.UPLOAD_INPUT, state="attached", timeout=20000)
                    except Exception:
                        if "login" in (page.url or "").lower():
                            return PublishResult(success=False, platform=self.name,
                                                 error="cookie/登录失效，请重新扫码登录小红书")
                        shot = await self._save_debug_shot(page, "xhs-no-upload")
                        return PublishResult(success=False, platform=self.name,
                                             error=f"发布页未就绪（未找到上传入口）。已截图: {shot}")
                    await page.set_input_files(self.UPLOAD_INPUT, images, timeout=30000)
                    # 图片需先上传完成，发布按钮才可点；等待标题输入框出现（发布表单就绪）后再多留缓冲。
                    await page.wait_for_selector(self.TITLE_INPUT, timeout=30000)
                    await page.wait_for_timeout(4000)
                    await page.fill(self.TITLE_INPUT, title or content[:20], timeout=15000)
                    await page.fill(self.CONTENT_INPUT, body, timeout=15000)
                    await page.wait_for_timeout(1500)

                    if not await self._click_submit(page):
                        shot = await self._save_debug_shot(page, "xhs-submit-fail")
                        return PublishResult(
                            success=False, platform=self.name,
                            error=f"未找到可点击的「发布」按钮（图片可能仍在上传或页面结构变化）。已截图: {shot}",
                        )
                    await page.wait_for_timeout(3000)
                    logger.info("小红书发布已提交: %s", title)
                    return PublishResult(success=True, platform=self.name, output="submitted")
                except Exception:
                    await self._save_debug_shot(page, "xhs-error")
                    raise
                finally:
                    await browser.close()
        except Exception as e:
            logger.exception("小红书发布异常")
            return PublishResult(success=False, platform=self.name, error=str(e))

    async def fill_publish_form(self, page, *, title, content, tags=None, images=None):
        """打开发布页并自动填好（标题/正文/图片/话题），但**不点发布**，供人工复核后手动发布。
        话题用逐字输入 + 回车选中，触发小红书话题识别，生成真正的蓝色话题。异常直接抛出。"""
        await page.goto(self.publish_url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        if "login" in (page.url or "").lower():
            raise RuntimeError("cookie/登录失效，请到「账号管理」重新扫码登录小红书")
        await page.wait_for_selector(self.UPLOAD_INPUT, state="attached", timeout=20000)
        if images:
            await page.set_input_files(self.UPLOAD_INPUT, images, timeout=30000)
        await page.wait_for_selector(self.TITLE_INPUT, timeout=30000)
        await page.wait_for_timeout(4000)
        await page.fill(self.TITLE_INPUT, title or (content or "")[:20], timeout=15000)
        editor = page.locator(self.CONTENT_INPUT).first
        await editor.click()
        await editor.type(content or "", delay=3)
        # 逐个添加话题：输入 #标签 触发下拉，稍等后回车选中第一个，生成蓝色话题
        for t in (tags or []):
            tag = str(t).lstrip("#").strip()
            if not tag:
                continue
            try:
                await editor.type(" #" + tag, delay=40)
                await page.wait_for_timeout(1200)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(400)
            except Exception:
                continue

    async def _click_submit(self, page) -> bool:
        """点击发布按钮。小红书的「发布」是自定义 div（非 <button>），
        故以精确文案定位为主，多策略兜底；用 exact=True 避开「发布笔记」。"""
        candidates = [
            page.get_by_text("发布", exact=True),                                   # 主：任意元素精确「发布」
            page.locator("button:has-text('发布'), [class*='publish']:has-text('发布')"),
            page.locator(".el-button--primary, .submit, .footer, [class*='btn']").filter(has_text="发布"),
            page.get_by_role("button", name="发布", exact=True),
        ]
        for loc in candidates:
            try:
                btn = loc.last
                await btn.wait_for(state="visible", timeout=6000)
                await btn.scroll_into_view_if_needed(timeout=3000)
                await btn.click(timeout=6000)
                logger.info("小红书发布按钮已点击")
                return True
            except Exception:
                continue
        return False

    async def _save_debug_shot(self, page, prefix: str) -> str:
        """把当前页面截图存到 static/debug，返回可访问的相对路径，便于排查选择器。"""
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = _DEBUG_DIR / f"{prefix}.png"
            await page.screenshot(path=str(path), full_page=True)
            return f"/static/debug/{prefix}.png"
        except Exception as exc:
            logger.warning("保存调试截图失败: %s", exc)
            return ""
