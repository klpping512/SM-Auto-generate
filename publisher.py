"""Publisher module - wraps huimei CLI for auto-publishing."""
import asyncio
import logging
import os
import shutil

logger = logging.getLogger(__name__)

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
    video: str = None, account: dict = None,
) -> dict:
    """统一发布入口：查适配器注册表并分发。返回与旧接口兼容的 dict。"""
    from adapters import get_adapter  # 延迟导入避免循环
    adapter = get_adapter(platform)
    if adapter is None:
        return {"success": False, "platform": platform, "error": f"无适配器: '{platform}'"}
    result = await adapter.publish(
        platform=platform, title=title, content=content,
        tags=tags, images=images, video=video, account=account,
    )
    return result.to_dict()
