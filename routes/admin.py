"""管理员后台 API 端点 - SA-LogiFlow v3.0."""
import asyncio
import json as _json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

import database as db
import media_retention
import scheduler
from auth import require_role
from models import UserRole
import os

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)


@router.post("/media-cleanup")
async def manual_trigger_cleanup_media(admin: dict = Depends(require_role(UserRole.ADMIN))):
    """手动触发媒体清理任务。"""
    try:
        logger.info("管理员 %s 手动触发媒体清理任务", admin.get("username"))
        
        report = await scheduler.cleanup_media_retention()
        
        return {
            "status": "success",
            "message": "媒体清理任务已执行",
            "report": report,
        }
    except Exception as e:
        logger.exception("手动触发媒体清理失败：%s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清理任务执行失败：{str(e)[:200]}",
        )


@router.post("/hotspot-hook-library/cleanup")
async def manual_trigger_hotspot_hook_cleanup(admin: dict = Depends(require_role(UserRole.ADMIN)), dry_run: bool = False):
    """手动触发热点 Hook 库清理任务。"""
    try:
        logger.info("管理员 %s 手动触发热点 Hook 库清理", admin.get("username"))
        
        retention_days = max(3, int(os.environ.get("HOTSPOT_HOOK_RETENTION_DAYS", "10")))
        protect_days = max(0, int(os.environ.get("HOTSPOT_HOOK_PROTECT_DAYS", "3")))
        
        # P4: 异步调用避免阻塞
        report = await asyncio.to_thread(
            media_retention.cleanup_hotspot_hook_library,
            static_dir=Path(__file__).with_name("static"),
            retention_days=retention_days,
            protect_days=protect_days,
            dry_run=dry_run,
        )
        
        return {
            "status": "success",
            "message": "热点 Hook 库清理任务已执行",
            "report": report,
        }
    except Exception as e:
        logger.exception("手动触发热点 Hook 库清理失败：%s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清理任务执行失败：{str(e)[:200]}",
        )


@router.get("/status")
async def get_system_status(admin: dict = Depends(require_role(UserRole.ADMIN))):
    """获取系统运行状态和健康检查。"""
    try:
        storage = media_retention.storage_summary(Path(__file__).with_name("static"))
        
        from datetime import datetime, timezone
        current_time = datetime.now(timezone.utc)
        
        return {
            "status": "running",
            "timestamp": current_time.isoformat(),
            "storage": storage,
        }
    except Exception as e:
        logger.exception("获取系统状态失败：%s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统状态失败：{str(e)[:200]}",
        )
