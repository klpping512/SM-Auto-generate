"""Materialize short, low-resolution preview clips for hotspot events.

The original downloaded mother video remains the source of truth for final rendering.
These proxy files make each event an actually previewable library item without copying
the full mother video into every card.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import database as db


def remove_materialized_event_clips(static_dir: Path, asset_id: int) -> None:
    """Remove only regenerated preview proxies for one hotspot mother asset.

    Event rows are replaced atomically during curation.  Their preview files use the
    event row id in the name, so retaining them would leave rejected hooks on disk
    forever even though they no longer exist in the library.
    """
    folder = static_dir / "assets" / "hotspot-events" / str(int(asset_id))
    if folder.is_dir():
        shutil.rmtree(folder)


def materialize_event_clip(static_dir: Path, asset: dict, event: dict) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    source = static_dir / str(asset.get("filepath") or "")
    if not ffmpeg:
        raise RuntimeError("未安装 FFmpeg，无法生成热点事件片段")
    if not source.is_file():
        raise FileNotFoundError(f"热点母片不存在：{source}")
    start_ms = max(0, int(event.get("start_ms") or 0))
    end_ms = int(event.get("end_ms") or 0)
    if end_ms <= start_ms:
        raise ValueError("热点事件片段时间范围无效")
    duration = (end_ms - start_ms) / 1000
    folder_rel = Path("assets") / "hotspot-events" / str(asset["id"])
    folder = static_dir / folder_rel
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"event-{int(event['id']):04d}"
    clip_rel = folder_rel / f"{stem}.mp4"
    thumb_rel = folder_rel / f"{stem}.jpg"
    clip = static_dir / clip_rel
    thumb = static_dir / thumb_rel
    needs_encode = not clip.exists() or clip.stat().st_size < 1024
    if not needs_encode and ffprobe:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        try:
            needs_encode = abs(float(probe.stdout.strip() or 0) - duration) > 0.35
        except ValueError:
            needs_encode = True
    if needs_encode:
        command = [
            ffmpeg, "-y", "-ss", f"{start_ms / 1000:.3f}", "-i", str(source),
            "-t", f"{duration:.3f}", "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale=720:-2", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(clip),
        ]
        subprocess.run(command, capture_output=True, text=True, timeout=180, check=True)
    if not thumb.exists() or thumb.stat().st_size < 256:
        subprocess.run(
            [ffmpeg, "-y", "-ss", "0.2", "-i", str(clip), "-frames:v", "1",
             "-vf", "scale=640:-2", str(thumb)],
            capture_output=True, text=True, timeout=60, check=True,
        )
    db.update_hotspot_event_clip_media(
        int(event["id"]), clip_rel.as_posix(), thumb_rel.as_posix(), "ready", None
    )
    return {**event, "clip_path": clip_rel.as_posix(), "thumbnail_path": thumb_rel.as_posix(), "clip_status": "ready"}


def materialize_event_clips(static_dir: Path, asset: dict, events: list[dict]) -> list[dict]:
    results = []
    for event in events:
        try:
            results.append(materialize_event_clip(static_dir, asset, event))
        except Exception as exc:
            db.update_hotspot_event_clip_media(int(event["id"]), None, event.get("thumbnail_path"), "failed", str(exc)[:300])
            results.append({**event, "clip_status": "failed", "clip_error": str(exc)[:300]})
    return results
