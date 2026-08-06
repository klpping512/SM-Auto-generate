"""Publisher module - wraps huimei CLI for auto-publishing."""
import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

import database as db
import publish_readiness

logger = logging.getLogger(__name__)
UPLOAD_ROOT = (Path(__file__).parent / "static" / "uploads").resolve()
_DEBUG_SHOT_RE = re.compile(r"/static/debug/\S+\.png")


def _resolve_uploaded_media(paths: list[str] | None) -> tuple[list[str], list[str]]:
    """把数据库中的静态相对路径转换为发布工具所需的绝对路径。

    返回 (resolved, missing)：
    - resolved：uploads 目录内且磁盘存在的绝对路径
    - missing：本应在 uploads 内但磁盘找不到的原始路径（显式暴露，避免被误报成「没配图」）
    uploads 目录外的路径仍忽略（安全语义不变），不计入 missing。
    """
    resolved: list[str] = []
    missing: list[str] = []
    for raw in paths or []:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path(__file__).parent / "static" / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(UPLOAD_ROOT)
        except ValueError:
            logger.warning("忽略 uploads 目录外的附件: %s", raw)
            continue
        if candidate.is_file():
            resolved.append(str(candidate))
        else:
            logger.warning("附件缺失: %s", raw)
            missing.append(str(raw))
    return resolved, missing


def debug_screenshot_from_error(error: str | None) -> str | None:
    """从 adapter error 文案中提取 /static/debug/*.png 现场路径。"""
    if not error:
        return None
    match = _DEBUG_SHOT_RE.search(error)
    return match.group(0) if match else None


def failure_status_detail(result: dict) -> str:
    """queue status / 日志用：带 category 前缀的可读失败详情。"""
    error = result.get("error") or ""
    category = result.get("category")
    return f"{category}: {error}" if category else error

# huimei binary path: 优先环境变量，其次 PATH 查找，兜底直接用命令名
HUIMEI_BIN = os.environ.get("HUIMEI_BIN") or shutil.which("huimei") or "huimei"

# Platform mapping: SA-LogiFlow platform -> huimei platform ID
PLATFORM_MAP = {
    "xiaohongshu": "xhs",
    "douyin": "douyin",
    "tiktok": "tk",
    "bilibili": "bilibili",
    "weibo": "weibo",
    "kuaishou": "ks",
    "toutiao": "toutiao",
    "zhihu": "zhihu",
    "wechat_channels": "tencent",
    "wechat_mp": "weixingongzhonghao",
    "baijiahao": "baijiahao",
}

# Platforms not supported by huimei (need other methods)
EXTERNAL_PLATFORMS = {"facebook", "twitter", "reddit"}


def get_huimei_platform(platform: str) -> str | None:
    return PLATFORM_MAP.get(platform)


def is_huimei_supported(platform: str) -> bool:
    return platform in PLATFORM_MAP


async def publish_via_huimei(
    platform: str,
    title: str,
    content: str,
    tags: list[str] = None,
    images: list[str] = None,
    video: str = None,
    account: str = None,
) -> dict:
    """Publish content using huimei CLI."""
    huimei_platform = get_huimei_platform(platform)
    if not huimei_platform:
        return {"success": False, "error": f"Platform '{platform}' not supported by huimei", "platform": platform}

    if not shutil.which(HUIMEI_BIN):
        return {
            "success": False,
            "platform": platform,
            "error": "huimei CLI 未安装（仅影响微信系/bilibili/微博/快手/头条/知乎/百家号/tiktok；"
                     "抖音/小红书/facebook/twitter/reddit 走 RPA/API 适配器不受影响）",
        }

    cmd = [HUIMEI_BIN, "publish", "-p", huimei_platform, "-t", title, "-c", content]
    if tags:
        cmd.extend(["--tags", ",".join(tags)])
    if images:
        cmd.extend(["--images", ",".join(images)])
    if video:
        cmd.extend(["--video", video])
    if account:
        cmd.extend(["-a", account])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip()

        if proc.returncode == 0:
            logger.info("发布成功: platform=%s, output=%s", platform, stdout_text)
            return {"success": True, "platform": platform, "huimei_platform": huimei_platform, "output": stdout_text}
        else:
            logger.error("发布失败: platform=%s, error=%s", platform, stderr_text)
            return {"success": False, "platform": platform, "error": stderr_text or stdout_text, "returncode": proc.returncode}
    except asyncio.TimeoutError:
        logger.error("发布超时: platform=%s", platform)
        return {"success": False, "platform": platform, "error": "Publish timeout (120s)"}
    except Exception as e:
        logger.exception("发布异常: platform=%s", platform)
        return {"success": False, "platform": platform, "error": str(e)}


async def publish_batch(
    title: str, content: str, platforms: list[str],
    tags: list[str] = None, images: list[str] = None, video: str = None,
) -> list[dict]:
    """Publish to multiple platforms concurrently."""

    async def _publish_one(platform: str) -> dict:
        if is_huimei_supported(platform):
            return await publish_via_huimei(platform, title, content, tags, images, video)
        return {"success": False, "platform": platform, "error": f"'{platform}' needs API integration"}

    return list(await asyncio.gather(*[_publish_one(p) for p in platforms]))


async def check_huimei_status() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            HUIMEI_BIN, "status", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {"available": True, "output": stdout.decode().strip(), "error": stderr.decode().strip() if stderr else None}
    except Exception as e:
        return {"available": False, "error": str(e)}


async def list_huimei_accounts() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            HUIMEI_BIN, "account", "list", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {"success": True, "output": stdout.decode().strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def dispatch(
    platform: str, title: str, content: str,
    tags: list[str] = None, images: list[str] = None,
    video: str = None, account: dict = None, owner_id: int | None = None,
) -> dict:
    """统一发布入口：查适配器注册表并分发。返回与旧接口兼容的 dict。"""
    from adapters import get_adapter  # 延迟导入避免循环
    adapter = get_adapter(platform)
    if adapter is None:
        return {"success": False, "platform": platform, "error": f"无适配器: '{platform}'"}
    resolved_images, missing_images = _resolve_uploaded_media(images)
    if images and missing_images:
        return {
            "success": False,
            "platform": platform,
            "category": "attachment_missing",
            "error": f"附件缺失: {missing_images}",
        }
    resolved_video = None
    if video:
        resolved_videos, missing_videos = _resolve_uploaded_media([video])
        if missing_videos:
            return {
                "success": False,
                "platform": platform,
                "category": "attachment_missing",
                "error": f"附件缺失: {missing_videos}",
            }
        resolved_video = resolved_videos[0] if resolved_videos else None
    if account is None:
        # 选取凭据完整的账号：优先 active，其次任何有有效凭据的账号。
        # 不因 status=='expired' 直接排除——该状态可能被上次探测误标，真实有效性由发布时判断。
        ready_accounts = [a for a in db.get_accounts(platform, owner_id=owner_id)
                          if publish_readiness.readiness(platform, a.get("credentials"))["ready"]]
        account = next((a for a in ready_accounts if a.get("status") == "active"),
                       ready_accounts[0] if ready_accounts else None)
    result = await adapter.publish(
        platform=platform, title=title, content=content,
        tags=tags, images=resolved_images or None, video=resolved_video, account=account,
    )
    return result.to_dict()
