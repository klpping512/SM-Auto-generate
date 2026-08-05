"""Materialize short, low-resolution preview clips for hotspot events.

The original downloaded mother video remains the source of truth for final rendering
when hi-res upgrade is unavailable. Confirmed hooks prefer a fresh 720p range download
from the original URL so analysis-stage 480p multi-window samples never enter the final cut.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import database as db
import inspiration_assets

logger = logging.getLogger(__name__)


def remove_materialized_event_clips(static_dir: Path, asset_id: int) -> None:
    """Remove only regenerated preview proxies for one hotspot mother asset.

    Event rows are replaced atomically during curation.  Their preview files use the
    event row id in the name, so retaining them would leave rejected hooks on disk
    forever even though they no longer exist in the library.
    """
    folder = static_dir / "assets" / "hotspot-events" / str(int(asset_id))
    if folder.is_dir():
        shutil.rmtree(folder)


def _encode_preview_from_source(
    static_dir: Path,
    source: Path,
    event: dict,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[Path, Path, Path, Path]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        raise RuntimeError("未安装 FFmpeg，无法生成热点事件片段")
    if not source.is_file():
        raise FileNotFoundError(f"热点母片不存在：{source}")
    if end_ms <= start_ms:
        raise ValueError("热点事件片段时间范围无效")
    duration = (end_ms - start_ms) / 1000
    asset_id = int(event.get("asset_id") or 0)
    folder_rel = Path("assets") / "hotspot-events" / str(asset_id)
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
    return clip, thumb, clip_rel, thumb_rel


def _try_hi_res_clip(
    static_dir: Path,
    media_item: dict | None,
    event: dict,
) -> Path | None:
    """Download exact original-time 720p range for a confirmed hook. Fail soft."""
    if not media_item:
        return None
    url = str(media_item.get("original_media_url") or "").strip()
    if not url:
        return None
    start_ms = max(0, int(event.get("start_ms") or 0))
    end_ms = int(event.get("end_ms") or 0)
    if end_ms <= start_ms:
        return None
    source_type = media_item.get("platform")
    if source_type not in {"youtube", "tiktok"}:
        source_type = "other_link"
    try:
        return inspiration_assets.download_hi_res_range(
            {
                "canonical_url": url,
                "source_type": source_type,
            },
            static_dir,
            start_ms / 1000.0,
            end_ms / 1000.0,
        )
    except Exception as exc:
        logger.warning(
            "confirmed hook hi-res download failed event=%s reason=%s; fallback to analysis mother",
            event.get("id"),
            str(exc)[:180],
        )
        return None


def materialize_event_clip(
    static_dir: Path,
    asset: dict,
    event: dict,
    *,
    media_item: dict | None = None,
    sample_offsets: list[tuple[float, float]] | None = None,
) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未安装 FFmpeg，无法生成热点事件片段")
    event = {**event, "asset_id": int(asset["id"])}
    start_ms = max(0, int(event.get("start_ms") or 0))
    end_ms = int(event.get("end_ms") or 0)
    if end_ms <= start_ms:
        raise ValueError("热点事件片段时间范围无效")

    hi_res_source = _try_hi_res_clip(static_dir, media_item, event)
    if hi_res_source and hi_res_source.is_file():
        # Hi-res file is already the exact hook range — copy/re-encode as preview.
        clip, thumb, clip_rel, thumb_rel = _encode_preview_from_source(
            static_dir, hi_res_source, event, start_ms=0,
            end_ms=max(1, end_ms - start_ms),
        )
        try:
            hi_res_source.unlink(missing_ok=True)
        except OSError:
            pass
        db.update_hotspot_event_clip_media(
            int(event["id"]), clip_rel.as_posix(), thumb_rel.as_posix(), "ready", None
        )
        return {
            **event,
            "clip_path": clip_rel.as_posix(),
            "thumbnail_path": thumb_rel.as_posix(),
            "clip_status": "ready",
            "clip_source": "hi_res",
        }

    # Fallback: cut from analysis mother. Hooks store original-time stamps; when
    # the mother is a multi-window sample, convert back to analysis-local time.
    analysis_start = start_ms
    analysis_end = end_ms
    evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
    if evidence.get("analysis_start_ms") is not None and evidence.get("analysis_end_ms") is not None:
        analysis_start = int(evidence["analysis_start_ms"])
        analysis_end = int(evidence["analysis_end_ms"])
    elif sample_offsets:
        mapped_start = inspiration_assets.original_ms_to_analysis_ms(start_ms, sample_offsets)
        mapped_end = inspiration_assets.original_ms_to_analysis_ms(end_ms, sample_offsets)
        if mapped_start is None or mapped_end is None or mapped_end <= mapped_start:
            raise ValueError("Hook 时间戳落在采样窗口缝隙，无法从分析件回退切片")
        analysis_start, analysis_end = mapped_start, mapped_end

    source = static_dir / str(asset.get("filepath") or "")
    clip, thumb, clip_rel, thumb_rel = _encode_preview_from_source(
        static_dir, source, event, start_ms=analysis_start, end_ms=analysis_end,
    )
    db.update_hotspot_event_clip_media(
        int(event["id"]), clip_rel.as_posix(), thumb_rel.as_posix(), "ready", None
    )
    return {
        **event,
        "clip_path": clip_rel.as_posix(),
        "thumbnail_path": thumb_rel.as_posix(),
        "clip_status": "ready",
        "clip_source": "analysis_fallback",
    }


def materialize_event_clips(
    static_dir: Path,
    asset: dict,
    events: list[dict],
    *,
    media_item: dict | None = None,
    sample_offsets: list[tuple[float, float]] | None = None,
) -> list[dict]:
    results = []
    for event in events:
        try:
            results.append(
                materialize_event_clip(
                    static_dir,
                    asset,
                    event,
                    media_item=media_item,
                    sample_offsets=sample_offsets,
                )
            )
        except Exception as exc:
            db.update_hotspot_event_clip_media(
                int(event["id"]), None, event.get("thumbnail_path"), "failed", str(exc)[:300],
            )
            results.append({**event, "clip_status": "failed", "clip_error": str(exc)[:300]})
    return results
