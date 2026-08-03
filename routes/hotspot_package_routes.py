"""Hotspot topic-package review routes (admin-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

import hotspot_package_service
from auth import require_role
from models import UserRole

router = APIRouter()


@router.get("/api/hotspot-packages")
async def list_hotspot_packages(
    query: str = "",
    source: str = "",
    event_type: str = "",
    heat_state: str = "",
    media_form: str = "",
    since: str = "",
    limit: int = 100,
    user=Depends(require_role(UserRole.ADMIN)),
):
    return hotspot_package_service.list_packages(
        query=query, source=source, event_type=event_type, heat_state=heat_state,
        media_form=media_form, since=since, limit=max(1, min(limit, 200)),
    )


@router.get("/api/hotspot-packages/{hotspot_id}")
async def get_hotspot_package_detail(hotspot_id: int, user=Depends(require_role(UserRole.ADMIN))):
    package = hotspot_package_service.get_package_detail(hotspot_id)
    if package is None:
        raise HTTPException(404, "热点专题包不存在")
    return package


@router.post("/api/hotspot-packages/{hotspot_id}/confirm")
async def confirm_hotspot_package(hotspot_id: int, user=Depends(require_role(UserRole.ADMIN))):
    package = hotspot_package_service.confirm_package(hotspot_id, user)
    if package is None:
        raise HTTPException(404, "热点专题包不存在")
    return package


@router.post("/api/hotspot-packages/{hotspot_id}/reject")
async def reject_hotspot_package(hotspot_id: int, user=Depends(require_role(UserRole.ADMIN))):
    package = hotspot_package_service.reject_package(hotspot_id, user)
    if package is None:
        raise HTTPException(404, "热点专题包不存在")
    return package


@router.post("/api/hotspot-packages/{hotspot_id}/merge")
async def merge_hotspot_signals(hotspot_id: int, signal_ids: list[int], user=Depends(require_role(UserRole.ADMIN))):
    if not signal_ids:
        raise HTTPException(422, "至少选择一条待合并信号")
    try:
        package = hotspot_package_service.merge_signals(hotspot_id, signal_ids, user)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if package is None:
        raise HTTPException(404, "热点专题包不存在")
    return package
