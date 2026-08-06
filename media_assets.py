"""Validated media asset ingestion and metadata extraction."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from PIL import Image

import asset_taxonomy
import database as db

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
MAX_IMAGE = 15 * 1024 * 1024
MAX_VIDEO = 2 * 1024 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
# Back-compat aliases; canonical sets live in asset_taxonomy.
CATEGORIES = asset_taxonomy.CATEGORIES
CATEGORY_KEYWORDS = asset_taxonomy.CATEGORY_KEYWORDS


def guess_category(path: Path, import_root: Path | None = None) -> str:
    """按子目录名/文件名关键词猜测分类。子目录优先，其次文件名，未命中返回 'other'。

    path: 素材文件路径。
    import_root: 导入根目录；若提供，则只取 import_root 之下的相对路径部分参与匹配，
                 避免绝对路径里的无关目录名误命中。
    """
    try:
        rel = path.relative_to(import_root) if import_root else path
    except ValueError:
        rel = path
    parts = [p.lower() for p in rel.parts]
    # 子目录优先（去掉最后一段文件名）
    for part in parts[:-1]:
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw.lower() in part for kw in keywords):
                return cat
    # 其次文件名（不含扩展名）
    stem = parts[-1].rsplit(".", 1)[0] if parts else ""
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw.lower() in stem for kw in keywords):
            return cat
    return "other"


def capabilities() -> dict:
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def stream_upload_to_path(upload, target: Path, max_size: int, chunk_size: int = UPLOAD_CHUNK_SIZE) -> int:
    """Stream an UploadFile-like object to disk without retaining the whole file in memory."""
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise ValueError("素材超过大小限制")
                handle.write(chunk)
        if total == 0:
            raise ValueError("素材为空")
        return total
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("未安装 ffprobe，无法验证视频")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(result.stdout)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("文件中没有有效视频轨道")
    return {
        "duration": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
    }


def _thumbnail(source: Path, target: Path, file_type: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    if file_type == "image":
        with Image.open(source) as image:
            image.thumbnail((640, 640)); image.convert("RGB").save(target, "JPEG", quality=82)
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未安装 FFmpeg，无法生成视频缩略图")
    subprocess.run(
        [ffmpeg, "-y", "-ss", "0.2", "-i", str(source), "-frames:v", "1", "-vf", "scale=640:-2", str(target)],
        capture_output=True, timeout=45, check=True,
    )


def ingest_file(
    source: Path,
    static_dir: Path,
    category="other",
    origin="upload",
    created_by=None,
    move=False,
    import_root: Path | None = None,
    name: str | None = None,
    storage_mode: str = "copy",
) -> dict:
    source = source.resolve()
    ext = source.suffix.lower()
    if ext not in IMAGE_EXTS | VIDEO_EXTS:
        raise ValueError("仅支持 JPG、PNG、WebP、MP4、MOV、WebM")
    file_type = "image" if ext in IMAGE_EXTS else "video"
    size = source.stat().st_size
    if not size or size > (MAX_IMAGE if file_type == "image" else MAX_VIDEO):
        raise ValueError("素材为空或超过大小限制")
    if file_type == "image":
        with Image.open(source) as image:
            image.verify()
        with Image.open(source) as image:
            width, height = image.size
        meta = {"duration": None, "width": width, "height": height}
    else:
        meta = _probe(source)
    digest = _sha256(source)
    existing = db.get_asset_by_hash(digest)
    if existing:
        # 命中已停用（inactive）记录：用户重新上传曾删除的素材，重新激活它
        # 否则前端列表过滤 active 看不到，用户会以为"已存在却看不到"
        reactivated = existing.get("status") == "inactive"
        if reactivated:
            db.update_asset(existing["id"], name or source.stem, existing["category"], "active")
            existing = db.get_asset(existing["id"])
        existing["_dedup"] = True
        existing["_reactivated"] = bool(reactivated)
        return existing
    # auto: 按子目录名/文件名关键词智能分类；显式分类不在 CATEGORIES 时回落到 other
    if category == "auto":
        category = guess_category(source, import_root.resolve() if import_root else None)
    else:
        category = category if category in CATEGORIES else "other"
    stored_rel = Path("assets") / "library" / file_type / f"{uuid4().hex}{ext}"
    stored = (static_dir / stored_rel).resolve()
    static_root = static_dir.resolve()
    if static_root not in stored.parents:
        raise ValueError("非法素材路径")
    stored.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source), stored)
    elif storage_mode == "hardlink":
        os.link(source, stored)
    elif storage_mode == "copy":
        shutil.copy2(source, stored)
    else:
        raise ValueError("不支持的素材存储方式")
    thumb_rel = Path("assets") / "thumbnails" / f"{stored.stem}.jpg"
    try:
        _thumbnail(stored, static_dir / thumb_rel, file_type)
        asset_id = db.create_asset({
            "name": name or source.stem, "filepath": stored_rel.as_posix(), "file_type": file_type,
            "category": category, **meta, "size": size, "thumbnail": thumb_rel.as_posix(),
            "sha256": digest, "source": origin, "status": "active", "created_by": created_by,
        })
    except Exception:
        stored.unlink(missing_ok=True)
        (static_dir / thumb_rel).unlink(missing_ok=True)
        raise
    asset = db.get_asset(asset_id)
    asset["_dedup"] = False
    asset["_reactivated"] = False
    return asset


def public_asset(asset: dict) -> dict:
    item = dict(asset)
    item.pop("_dedup", None)  # 内部去重标记，不暴露给前端
    item.pop("_reactivated", None)
    item["url"] = "/static/" + item["filepath"]
    item["thumbnail_url"] = "/static/" + item["thumbnail"] if item.get("thumbnail") else None
    item["mime"] = mimetypes.guess_type(item["filepath"])[0]
    item["library_origin"] = "hotspot" if item.get("hotspot_id") else "owned"
    # 源标签三选一：热点素材 / 免版权素材 (za-stock) / Buffalo 原有素材
    if item["library_origin"] == "hotspot":
        item["source_label"] = "热点素材"
    elif str(item.get("source") or "").casefold() == "za_stock_license":
        # 批13 清洗：deprecated 的免版权素材在展示层标注已下线，不再显示为可用。
        item["source_label"] = "免版权素材（已下线）" if int(item.get("deprecated") or 0) == 1 else "免版权素材"
    else:
        item["source_label"] = "Buffalo 原有素材"
    return item
