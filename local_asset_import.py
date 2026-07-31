"""受信任本地目录的零复制素材接入规则。"""
from __future__ import annotations

import os
from pathlib import Path

import media_assets


SUPPORTED_EXTS = media_assets.IMAGE_EXTS | media_assets.VIDEO_EXTS


def configured_root() -> Path:
    value = os.environ.get("LOCAL_ASSET_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return (Path.home() / "Desktop" / "视频&图片素材").resolve()


def resolve_source_path(path: Path, root: Path) -> Path:
    source = Path(path).resolve()
    allowed = Path(root).resolve()
    if source == allowed or allowed not in source.parents:
        raise ValueError("文件不在受信任素材目录内")
    if not source.is_file():
        raise ValueError("素材文件不存在")
    return source


def discover(root: Path) -> tuple[list[Path], list[Path]]:
    allowed = Path(root).resolve()
    if not allowed.is_dir():
        raise ValueError("受信任素材目录不存在")
    files = sorted(
        path for path in allowed.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )
    supported = [path for path in files if path.suffix.lower() in SUPPORTED_EXTS]
    unsupported = [path for path in files if path.suffix.lower() not in SUPPORTED_EXTS]
    return supported, unsupported


def ingest_one(path: Path, root: Path, static_dir: Path, user_id: int) -> dict:
    allowed = Path(root).resolve()
    source = resolve_source_path(path, allowed)
    return media_assets.ingest_file(
        source,
        static_dir,
        category="auto",
        origin="local_directory",
        created_by=user_id,
        import_root=allowed,
        storage_mode="hardlink",
    )
