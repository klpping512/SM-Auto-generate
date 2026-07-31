"""Resolve local videos and HTTPS video URLs with optional native captions.

The URL strategy is adapted from bradautomates/claude-video (MIT), commit
83da59fa78c3eee9e20f515fe75c438bb5166efd. See third_party/claude_video.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .process_runner import MediaProcessError, run_process


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


class VideoSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedVideo:
    video_path: Path
    subtitle_path: Path | None = None
    downloaded: bool = False
    source_info: dict = field(default_factory=dict)


def _pick_downloaded_video(work_dir: Path) -> Path | None:
    for extension in (".mp4", ".mkv", ".webm", ".mov", ".m4v"):
        candidates = sorted(work_dir.glob(f"video*{extension}"))
        if candidates:
            return candidates[0]
    return None


def _pick_subtitle(work_dir: Path) -> Path | None:
    candidates = sorted(work_dir.glob("video*.vtt"))
    if not candidates:
        return None
    preferred = [
        item for item in candidates
        if any(marker in item.name.lower() for marker in (".en.", ".en-us.", ".en-gb.", ".af."))
    ]
    return (preferred or candidates)[0]


def _read_info(work_dir: Path, source: str) -> dict:
    info_file = work_dir / "video.info.json"
    if not info_file.exists():
        return {"url": source}
    try:
        data = json.loads(info_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"url": source}
    return {
        "title": data.get("title"),
        "uploader": data.get("uploader") or data.get("channel"),
        "duration": data.get("duration"),
        "url": data.get("webpage_url") or source,
    }


def resolve_video_source(
    source: str,
    work_dir: Path,
    *,
    timeout: float = 300,
    cancel_check=None,
) -> ResolvedVideo:
    parsed = urlparse(source)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or not parsed.netloc:
            raise VideoSourceError("远程视频必须使用可访问的 HTTPS URL")
        executable = shutil.which("yt-dlp")
        if not executable:
            raise VideoSourceError("处理视频 URL 需要安装 yt-dlp；本地 MP4 不受影响")
        work_dir.mkdir(parents=True, exist_ok=True)
        command = [
            executable,
            "--no-playlist",
            "--max-downloads", "1",
            "--max-filesize", "300M",
            "-N", "4",
            "-f", "bv*[height<=720]+ba/b[height<=720]/b",
            "--merge-output-format", "mp4",
            "--write-info-json",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "en.*,af.*,zu.*,xh.*",
            "--sub-format", "vtt",
            "--convert-subs", "vtt",
            "-o", str(work_dir / "video.%(ext)s"),
            "--", source,
        ]
        error: Exception | None = None
        try:
            run_process(command, timeout=timeout, cancel_check=cancel_check)
        except MediaProcessError as exc:
            error = exc
        video_path = _pick_downloaded_video(work_dir)
        if not video_path:
            for partial in work_dir.glob("*.part"):
                partial.unlink(missing_ok=True)
            raise VideoSourceError(f"yt-dlp 下载失败：{error or '没有生成视频文件'}")
        return ResolvedVideo(
            video_path=video_path.resolve(),
            subtitle_path=_pick_subtitle(work_dir),
            downloaded=True,
            source_info=_read_info(work_dir, source),
        )

    local = Path(source).expanduser().resolve()
    if not local.exists() or not local.is_file():
        raise VideoSourceError(f"本地视频不存在：{local}")
    if local.suffix.lower() not in VIDEO_EXTENSIONS:
        raise VideoSourceError(f"不支持的视频扩展名：{local.suffix or '无扩展名'}")
    sibling_vtt = local.with_suffix(".vtt")
    return ResolvedVideo(
        video_path=local,
        subtitle_path=sibling_vtt if sibling_vtt.exists() else None,
        downloaded=False,
        source_info={"title": local.name, "url": str(local)},
    )
