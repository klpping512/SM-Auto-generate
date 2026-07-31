"""Timestamped frame extraction with scene/keyframe modes and deduplication.

Algorithms in this module are adapted from bradautomates/claude-video (MIT),
commit 83da59fa78c3eee9e20f515fe75c438bb5166efd. The service error model,
cancellation, 5-10 fps focused review and bounded defaults are SA-LogiFlow
adaptations. See third_party/claude_video/LICENSE.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .process_runner import run_process


SCENE_THRESHOLD = 0.20
SCENE_MIN_FRAMES = 8
KEYFRAME_MIN_FRAMES = 4
DEDUP_SIZE = 16
DEDUP_THRESHOLD = 2.0
MAX_READ_DIMENSION = 1998
SHOWINFO_TIMESTAMP = re.compile(r"pts_time:([0-9.]+)")
MODE_ENGINES = {
    "efficient": "keyframe",
    "balanced": "scene",
    "detailed": "uniform",
}
MODE_LIMITS = {"efficient": 50, "balanced": 100, "detailed": 100}


class FrameExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractionPlan:
    mode: str
    engine: str
    fps: float
    target_frames: int
    max_frames: int
    focus: bool


def _scale_filter(resolution: int) -> str:
    return (
        f"scale=w='min({resolution},iw)':h='min({MAX_READ_DIMENSION},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def plan_extraction(
    duration_seconds: float,
    *,
    mode: str = "balanced",
    max_frames: int = 100,
    focus: bool = False,
    requested_fps: float | None = None,
) -> ExtractionPlan:
    if mode not in MODE_ENGINES:
        raise FrameExtractionError(f"未知抽帧模式：{mode}")
    cap = max(1, min(int(max_frames), MODE_LIMITS[mode]))
    duration = max(0.0, float(duration_seconds))
    if focus:
        fps = max(5.0, min(10.0, float(requested_fps or 5.0)))
        target = min(cap, max(1, int(round(duration * fps))))
    else:
        if duration <= 0:
            target = 1
        elif duration <= 30:
            target = min(cap, max(12, int(round(duration))))
        elif duration <= 60:
            target = min(cap, 40)
        elif duration <= 180:
            target = min(cap, 60)
        elif duration <= 600:
            target = min(cap, 80)
        else:
            target = cap
        fps = min(2.0, target / duration) if duration else 1.0
    return ExtractionPlan(mode, MODE_ENGINES[mode], fps, target, cap, focus)


def even_indices(count: int, selected_count: int) -> list[int]:
    if count <= 0 or selected_count <= 0:
        return []
    if selected_count >= count:
        return list(range(count))
    if selected_count == 1:
        return [0]
    return [round(index * (count - 1) / (selected_count - 1)) for index in range(selected_count)]


def frame_delta(first: bytes, second: bytes) -> float:
    if not first or len(first) != len(second):
        return float("inf")
    return sum(abs(left - right) for left, right in zip(first, second)) / len(first)


def dedupe_by_thumbnails(
    candidates: list[dict],
    thumbnails: list[bytes],
    *,
    threshold: float = DEDUP_THRESHOLD,
) -> tuple[list[dict], int]:
    if len(candidates) <= 1 or len(candidates) != len(thumbnails):
        return candidates, 0
    kept = [candidates[0]]
    last_kept = thumbnails[0]
    dropped: list[dict] = []
    for candidate, thumbnail in zip(candidates[1:], thumbnails[1:]):
        if frame_delta(thumbnail, last_kept) <= threshold:
            dropped.append(candidate)
        else:
            kept.append(candidate)
            last_kept = thumbnail
    for candidate in dropped:
        Path(candidate["path"]).unlink(missing_ok=True)
    for index, candidate in enumerate(kept):
        candidate["index"] = index
    return kept, len(dropped)


def _thumbnail_frames(paths: list[Path], timeout: float, cancel_check=None) -> list[bytes]:
    if not paths:
        return []
    match = re.match(r"(.*?)(\d+)(\.[A-Za-z0-9]+)$", paths[0].name)
    if not match:
        return []
    prefix, digits, extension = match.groups()
    pattern = str(paths[0].parent / f"{prefix}%0{len(digits)}d{extension}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    try:
        result = run_process(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-start_number", str(int(digits)), "-i", pattern,
                "-vf", f"scale={DEDUP_SIZE}:{DEDUP_SIZE},format=gray",
                "-f", "rawvideo", "-",
            ],
            timeout=timeout,
            cancel_check=cancel_check,
            text=False,
        )
    except RuntimeError:
        return []
    chunk = DEDUP_SIZE * DEDUP_SIZE
    data = result.stdout or b""
    if len(data) != chunk * len(paths):
        return []
    return [data[index * chunk:(index + 1) * chunk] for index in range(len(paths))]


def dedupe_frames(
    candidates: list[dict],
    *,
    threshold: float = DEDUP_THRESHOLD,
    timeout: float = 60,
    cancel_check=None,
) -> tuple[list[dict], int]:
    thumbnails = _thumbnail_frames(
        [Path(item["path"]) for item in candidates], timeout, cancel_check
    )
    return dedupe_by_thumbnails(candidates, thumbnails, threshold=threshold)


def _even_sample(candidates: list[dict], count: int) -> list[dict]:
    selected = [candidates[index] for index in even_indices(len(candidates), count)]
    selected_paths = {item["path"] for item in selected}
    for candidate in candidates:
        if candidate["path"] not in selected_paths:
            Path(candidate["path"]).unlink(missing_ok=True)
    for index, candidate in enumerate(selected):
        candidate["index"] = index
    return selected


def _prepare_output(output_dir: Path, prefix: str = "frame_") -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for existing in output_dir.glob(f"{prefix}*.jpg"):
        existing.unlink(missing_ok=True)


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise FrameExtractionError("缺少 FFmpeg，无法抽取视频帧")
    return executable


def _time_args(start_second: float | None, end_second: float | None) -> list[str]:
    arguments: list[str] = []
    if start_second is not None:
        arguments += ["-ss", f"{start_second:.3f}"]
    if end_second is not None:
        start = start_second or 0.0
        arguments += ["-t", f"{max(0.001, end_second - start):.3f}"]
    return arguments


def _extract_uniform(
    video_path: Path,
    output_dir: Path,
    *,
    fps: float,
    max_frames: int,
    resolution: int,
    start_second: float | None,
    end_second: float | None,
    timeout: float,
    cancel_check=None,
) -> list[dict]:
    _prepare_output(output_dir)
    command = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    command += _time_args(start_second, end_second)
    command += [
        "-i", str(video_path.resolve()),
        "-vf", f"fps={fps:.8f},{_scale_filter(resolution)}",
        "-frames:v", str(max_frames), "-q:v", "4",
        str(output_dir / "frame_%04d.jpg"),
    ]
    run_process(command, timeout=timeout, cancel_check=cancel_check)
    offset = start_second or 0.0
    return [
        {
            "index": index,
            "timestamp_seconds": round(offset + (index / fps if fps else 0), 3),
            "path": str(path),
            "reason": "uniform",
        }
        for index, path in enumerate(sorted(output_dir.glob("frame_*.jpg")))
    ]


def _extract_select_candidates(
    video_path: Path,
    output_dir: Path,
    *,
    select_filter: str,
    reason: str,
    resolution: int,
    start_second: float | None,
    end_second: float | None,
    timeout: float,
    cancel_check=None,
    keyframes_only: bool = False,
) -> list[dict]:
    _prepare_output(output_dir)
    command = [_ffmpeg(), "-hide_banner", "-loglevel", "info", "-y"]
    command += _time_args(start_second, end_second)
    if keyframes_only:
        command += ["-skip_frame", "nokey"]
    command += ["-i", str(video_path.resolve())]
    filters = f"{select_filter},{_scale_filter(resolution)},showinfo" if select_filter else f"{_scale_filter(resolution)},showinfo"
    command += [
        "-vf", filters, "-fps_mode", "vfr", "-q:v", "4",
        str(output_dir / "frame_%04d.jpg"),
    ]
    result = run_process(command, timeout=timeout, cancel_check=cancel_check)
    offset = start_second or 0.0
    timestamps = [
        round(offset + float(match.group(1)), 3)
        for match in SHOWINFO_TIMESTAMP.finditer(str(result.stderr or ""))
    ]
    candidates = []
    for index, path in enumerate(sorted(output_dir.glob("frame_*.jpg"))):
        candidates.append({
            "index": index,
            "timestamp_seconds": timestamps[index] if index < len(timestamps) else offset,
            "path": str(path),
            "reason": "first-frame" if reason == "scene-change" and index == 0 else reason,
        })
    return candidates


def extract_at_timestamps(
    video_path: Path,
    output_dir: Path,
    timestamps: list[float],
    *,
    resolution: int = 512,
    max_frames: int = 40,
    timeout: float = 120,
    cancel_check=None,
) -> list[dict]:
    _prepare_output(output_dir, "point_")
    points = sorted({max(0.0, float(value)) for value in timestamps})
    points = [points[index] for index in even_indices(len(points), min(len(points), max_frames))]
    frames: list[dict] = []
    per_frame_timeout = max(5.0, timeout / max(1, len(points)))
    for timestamp in points:
        path = output_dir / f"point_{len(frames):04d}.jpg"
        try:
            run_process(
                [
                    _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{timestamp:.3f}", "-i", str(video_path.resolve()),
                    "-frames:v", "1", "-vf", _scale_filter(resolution),
                    "-q:v", "4", str(path),
                ],
                timeout=per_frame_timeout,
                cancel_check=cancel_check,
            )
        except RuntimeError:
            continue
        if path.exists():
            frames.append({
                "index": len(frames),
                "timestamp_seconds": round(timestamp, 3),
                "path": str(path),
                "reason": "fixed-timestamp",
            })
    return frames


def extract_frames(
    video_path: Path,
    output_dir: Path,
    *,
    duration_seconds: float,
    mode: str = "balanced",
    max_frames: int = 40,
    resolution: int = 512,
    start_second: float | None = None,
    end_second: float | None = None,
    requested_fps: float | None = None,
    timeout: float = 180,
    cancel_check=None,
) -> dict:
    effective_start = start_second or 0.0
    effective_end = end_second if end_second is not None else duration_seconds
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_second is not None or end_second is not None
    plan = plan_extraction(
        effective_duration,
        mode=mode,
        max_frames=max_frames,
        focus=focused,
        requested_fps=requested_fps,
    )
    if plan.engine == "keyframe":
        candidates = _extract_select_candidates(
            video_path, output_dir, select_filter="", reason="keyframe",
            resolution=resolution, start_second=start_second, end_second=end_second,
            timeout=timeout, cancel_check=cancel_check, keyframes_only=True,
        )
        original_count = len(candidates)
        fallback = original_count < KEYFRAME_MIN_FRAMES
        if fallback:
            candidates = _extract_uniform(
                video_path, output_dir, fps=plan.fps, max_frames=plan.target_frames,
                resolution=resolution, start_second=start_second, end_second=end_second,
                timeout=timeout, cancel_check=cancel_check,
            )
            engine = "uniform"
        else:
            engine = "keyframe"
    elif plan.engine == "scene" and not focused:
        candidates = _extract_select_candidates(
            video_path, output_dir,
            select_filter=f"select='eq(n\\,0)+gt(scene\\,{SCENE_THRESHOLD})'",
            reason="scene-change", resolution=resolution,
            start_second=start_second, end_second=end_second,
            timeout=timeout, cancel_check=cancel_check,
        )
        original_count = len(candidates)
        fallback = original_count < SCENE_MIN_FRAMES
        if fallback:
            candidates = _extract_uniform(
                video_path, output_dir, fps=plan.fps, max_frames=plan.target_frames,
                resolution=resolution, start_second=start_second, end_second=end_second,
                timeout=timeout, cancel_check=cancel_check,
            )
            engine = "uniform"
        else:
            engine = "scene"
    else:
        candidates = _extract_uniform(
            video_path, output_dir, fps=plan.fps, max_frames=plan.target_frames,
            resolution=resolution, start_second=start_second, end_second=end_second,
            timeout=timeout, cancel_check=cancel_check,
        )
        original_count = len(candidates)
        fallback = False
        engine = "uniform"
    deduped, dropped = dedupe_frames(
        candidates, timeout=min(timeout, 60), cancel_check=cancel_check
    )
    selected = _even_sample(deduped, min(len(deduped), plan.max_frames))
    return {
        "frames": selected,
        "meta": {
            "mode": mode,
            "engine": engine,
            "focused": focused,
            "fps": round(plan.fps, 4),
            "candidate_count": original_count,
            "deduplicated_count": dropped,
            "selected_count": len(selected),
            "fallback": fallback,
            "start_second": round(effective_start, 3),
            "end_second": round(effective_end, 3),
        },
    }
