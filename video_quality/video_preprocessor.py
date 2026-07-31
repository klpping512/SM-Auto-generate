"""Unified local/URL video preprocessing orchestration."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .frame_extractor import extract_frames
from .schemas import VideoQualityInput
from .source_resolver import ResolvedVideo, resolve_video_source
from .technical_validator import TechnicalValidationError, validate_video
from .transcript_service import TranscriptResult, build_transcript


MAX_VIDEO_BYTES = 300 * 1024 * 1024
MAX_VIDEO_SECONDS = 600


class VideoPreprocessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreprocessedVideo:
    video_path: Path
    source_info: dict
    downloaded: bool
    metadata: dict
    technical_report: dict
    transcript: TranscriptResult
    frames: list[dict]
    frame_meta: dict


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def _local_path_allowed(source: str, allowed_roots: list[Path] | None) -> bool:
    if not allowed_roots or "://" in source:
        return True
    candidate = Path(source).expanduser().resolve()
    return any(candidate == root.resolve() or candidate.is_relative_to(root.resolve()) for root in allowed_roots)


def preprocess_video(
    request: VideoQualityInput,
    run_dir: Path,
    *,
    allowed_roots: list[Path] | None = None,
    cancel_check=None,
) -> PreprocessedVideo:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if not _local_path_allowed(request.video_source, allowed_roots):
        raise VideoPreprocessingError("API 只允许质检项目 static 目录内的本地视频")
    resolved: ResolvedVideo = resolve_video_source(
        request.video_source,
        run_dir / "source",
        cancel_check=cancel_check,
    )
    if resolved.video_path.stat().st_size > MAX_VIDEO_BYTES:
        raise VideoPreprocessingError("视频超过 300 MB，已阻止自动质检")
    try:
        technical = validate_video(resolved.video_path, cancel_check=cancel_check)
    except TechnicalValidationError as exc:
        write_json(run_dir / "failure.json", {"stage": "technical_validation", "error": str(exc)})
        raise VideoPreprocessingError(str(exc)) from exc
    metadata = technical["metadata"]
    if metadata["duration_seconds"] > MAX_VIDEO_SECONDS:
        raise VideoPreprocessingError("视频超过 10 分钟，已阻止自动质检以控制成本")
    transcript = build_transcript(
        video_path=resolved.video_path,
        output_path=run_dir / "transcript.vtt",
        storyboard=request.storyboard,
        subtitle_path=resolved.subtitle_path,
    )
    frame_result = extract_frames(
        resolved.video_path,
        run_dir / "frames",
        duration_seconds=metadata["duration_seconds"],
        mode=request.mode,
        max_frames=request.max_frames,
        cancel_check=cancel_check,
    )
    write_json(run_dir / "metadata.json", metadata)
    write_json(run_dir / "technical-report.json", technical)
    write_json(run_dir / "frames" / "index.json", {
        "meta": frame_result["meta"],
        "frames": frame_result["frames"],
    })
    write_json(run_dir / "input.json", request.model_dump())
    return PreprocessedVideo(
        video_path=resolved.video_path,
        source_info=resolved.source_info,
        downloaded=resolved.downloaded,
        metadata=metadata,
        technical_report=technical,
        transcript=transcript,
        frames=frame_result["frames"],
        frame_meta=frame_result["meta"],
    )
