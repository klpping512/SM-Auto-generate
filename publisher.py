"""Publisher module - wraps huimei CLI for auto-publishing."""
import asyncio
import subprocess
import json
from pathlib import Path
from models import Platform

# huimei binary path
HUIMEI_BIN = "/Library/Frameworks/Python.framework/Versions/3.12/bin/huimei"

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
    """Convert SA-LogiFlow platform name to huimei platform ID."""
    return PLATFORM_MAP.get(platform)


def is_huimei_supported(platform: str) -> bool:
    """Check if platform is supported by huimei."""
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
        return {
            "success": False,
            "error": f"Platform '{platform}' not supported by huimei",
            "platform": platform,
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
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip()

        if proc.returncode == 0:
            return {
                "success": True,
                "platform": platform,
                "huimei_platform": huimei_platform,
                "output": stdout_text,
            }
        else:
            return {
                "success": False,
                "platform": platform,
                "error": stderr_text or stdout_text,
                "returncode": proc.returncode,
            }
    except asyncio.TimeoutError:
        return {"success": False, "platform": platform, "error": "Publish timeout (120s)"}
    except Exception as e:
        return {"success": False, "platform": platform, "error": str(e)}


async def publish_batch(
    title: str,
    content: str,
    platforms: list[str],
    tags: list[str] = None,
    images: list[str] = None,
    video: str = None,
) -> list[dict]:
    """Publish to multiple platforms concurrently."""
    tasks = []
    for platform in platforms:
        if is_huimei_supported(platform):
            tasks.append(publish_via_huimei(platform, title, content, tags, images, video))
        else:
            tasks.append(asyncio.coroutine(lambda p=platform: {
                "success": False,
                "platform": p,
                "error": f"Platform '{p}' requires API integration (not supported by huimei)",
            })())

    # For external platforms, return error directly
    results = []
    for platform in platforms:
        if is_huimei_supported(platform):
            result = await publish_via_huimei(platform, title, content, tags, images, video)
        else:
            result = {
                "success": False,
                "platform": platform,
                "error": f"Platform '{platform}' needs API integration (Facebook Graph API / Twitter API / Reddit API)",
            }
        results.append(result)

    return results


async def check_huimei_status() -> dict:
    """Check huimei login status."""
    try:
        proc = await asyncio.create_subprocess_exec(
            HUIMEI_BIN, "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {
            "available": True,
            "output": stdout.decode().strip(),
            "error": stderr.decode().strip() if stderr else None,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


async def list_huimei_accounts() -> dict:
    """List linked accounts."""
    try:
        proc = await asyncio.create_subprocess_exec(
            HUIMEI_BIN, "account", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {
            "success": True,
            "output": stdout.decode().strip(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
