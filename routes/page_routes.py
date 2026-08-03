"""Static HTML page routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

CACHE_CONTROL = {"Cache-Control": "no-cache, no-store, must-revalidate"}


def create_router(static_dir: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(static_dir / "chat.html", headers=CACHE_CONTROL)

    @router.get("/{page_name}.html", response_class=HTMLResponse)
    async def page(page_name: str):
        file_path = static_dir / f"{page_name}.html"
        if not file_path.exists():
            raise HTTPException(404, f"Page '{page_name}' not found")
        return FileResponse(file_path, headers=CACHE_CONTROL)

    return router

