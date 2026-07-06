"""Validated media asset ingestion and metadata extraction."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from PIL import Image

import database as db

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
MAX_IMAGE = 15 * 1024 * 1024
MAX_VIDEO = 500 * 1024 * 1024
CATEGORIES = {"warehouse", "delivery", "customs", "brand", "other"}


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


def ingest_file(source: Path, static_dir: Path, category="other", origin="upload", created_by=None, move=False) -> dict:
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
        return existing
    category = category if category in CATEGORIES else "other"
    stored_rel = Path("assets") / "library" / file_type / f"{uuid4().hex}{ext}"
    stored = (static_dir / stored_rel).resolve()
    static_root = static_dir.resolve()
    if static_root not in stored.parents:
        raise ValueError("非法素材路径")
    stored.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source), stored)
    else:
        shutil.copy2(source, stored)
    thumb_rel = Path("assets") / "thumbnails" / f"{stored.stem}.jpg"
    try:
        _thumbnail(stored, static_dir / thumb_rel, file_type)
        asset_id = db.create_asset({
            "name": source.stem, "filepath": stored_rel.as_posix(), "file_type": file_type,
            "category": category, **meta, "size": size, "thumbnail": thumb_rel.as_posix(),
            "sha256": digest, "source": origin, "status": "active", "created_by": created_by,
        })
    except Exception:
        stored.unlink(missing_ok=True)
        (static_dir / thumb_rel).unlink(missing_ok=True)
        raise
    return db.get_asset(asset_id)


def public_asset(asset: dict) -> dict:
    item = dict(asset)
    item["url"] = "/static/" + item["filepath"]
    item["thumbnail_url"] = "/static/" + item["thumbnail"] if item.get("thumbnail") else None
    item["mime"] = mimetypes.guess_type(item["filepath"])[0]
    return item

