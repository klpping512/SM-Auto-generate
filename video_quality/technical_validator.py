"""FFprobe/FFmpeg technical validation with timestamped evidence."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .process_runner import MediaProcessError, run_process


class TechnicalValidationError(RuntimeError):
    pass


BLACK_RE = re.compile(
    r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
)
FREEZE_START_RE = re.compile(r"freeze_start:\s*(?P<value>[0-9.]+)")
FREEZE_END_RE = re.compile(r"freeze_end:\s*(?P<value>[0-9.]+)")
FREEZE_DURATION_RE = re.compile(r"freeze_duration:\s*(?P<value>[0-9.]+)")
SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<value>[0-9.]+)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+).*?silence_duration:\s*(?P<duration>[0-9.]+)"
)


def parse_frame_rate(value: str | int | float | None) -> float:
    if value in (None, ""):
        return 0.0
    text = str(value)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_detection_output(stderr: str) -> list[dict]:
    issues: list[dict] = []
    for match in BLACK_RE.finditer(stderr):
        duration = float(match.group("duration"))
        issues.append({
            "category": "black_frame",
            "severity": "high" if duration >= 1 else "medium",
            "start_second": round(float(match.group("start")), 3),
            "end_second": round(float(match.group("end")), 3),
            "duration_seconds": round(duration, 3),
            "description": "检测到连续黑帧",
        })

    freeze_start: float | None = None
    freeze_duration: float | None = None
    for line in stderr.splitlines():
        if match := FREEZE_START_RE.search(line):
            freeze_start = float(match.group("value"))
        if match := FREEZE_DURATION_RE.search(line):
            freeze_duration = float(match.group("value"))
        if match := FREEZE_END_RE.search(line):
            end = float(match.group("value"))
            if freeze_start is not None:
                duration = freeze_duration if freeze_duration is not None else end - freeze_start
                issues.append({
                    "category": "freeze",
                    "severity": "high" if duration >= 5 else "medium",
                    "start_second": round(freeze_start, 3),
                    "end_second": round(end, 3),
                    "duration_seconds": round(duration, 3),
                    "description": "检测到冻结或长时间静止画面，需结合分镜判断是否有意使用静态素材",
                })
            freeze_start = None
            freeze_duration = None

    silence_start: float | None = None
    for line in stderr.splitlines():
        if match := SILENCE_START_RE.search(line):
            silence_start = float(match.group("value"))
        if match := SILENCE_END_RE.search(line):
            end = float(match.group("end"))
            duration = float(match.group("duration"))
            start = silence_start if silence_start is not None else max(0.0, end - duration)
            issues.append({
                "category": "silence",
                "severity": "medium" if duration >= 5 else "low",
                "start_second": round(start, 3),
                "end_second": round(end, 3),
                "duration_seconds": round(duration, 3),
                "description": "检测到连续静音",
            })
            silence_start = None
    return sorted(issues, key=lambda item: (item["start_second"], item["category"]))


def _metadata(ffprobe: str, video_path: Path, timeout: float, cancel_check=None) -> dict:
    try:
        result = run_process(
            [
                ffprobe, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(video_path),
            ],
            timeout=timeout,
            cancel_check=cancel_check,
        )
    except MediaProcessError as exc:
        raise TechnicalValidationError(f"ffprobe 检测失败：{exc}") from exc
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise TechnicalValidationError("ffprobe 返回了无效 JSON") from exc
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        raise TechnicalValidationError("ffprobe 未检测到视频轨道")
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise TechnicalValidationError("ffprobe 未检测到有效视频时长")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    return {
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 4) if height else 0,
        "frame_rate": round(parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")), 3),
        "video_codec": video.get("codec_name") or "",
        "pixel_format": video.get("pix_fmt") or "",
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name") or "",
        "audio_channels": int((audio or {}).get("channels") or 0),
        "audio_sample_rate": int((audio or {}).get("sample_rate") or 0),
        "size_bytes": int(fmt.get("size") or video_path.stat().st_size),
        "format_name": fmt.get("format_name") or "",
    }


def validate_video(
    video_path: Path,
    *,
    timeout: float = 180,
    cancel_check=None,
) -> dict:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise TechnicalValidationError("缺少 FFmpeg/ffprobe，无法执行视频技术检测")
    path = Path(video_path).resolve()
    metadata = _metadata(ffprobe, path, min(timeout, 30), cancel_check)
    try:
        run_process(
            [ffmpeg, "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"],
            timeout=timeout,
            cancel_check=cancel_check,
        )
    except MediaProcessError as exc:
        raise TechnicalValidationError(f"视频完整解码失败：{exc}") from exc

    filters = "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=1.5"
    command = [ffmpeg, "-hide_banner", "-v", "info", "-i", str(path), "-vf", filters]
    if metadata["has_audio"]:
        # 两秒以内的镜头衔接可保留自然呼吸；超过 2.2 秒才视为需要修复的真静音。
        command += ["-af", "silencedetect=noise=-45dB:d=2.2"]
    command += ["-f", "null", "-"]
    try:
        detection = run_process(command, timeout=timeout, cancel_check=cancel_check)
        detection_stderr = str(detection.stderr or "")
    except MediaProcessError as exc:
        raise TechnicalValidationError(f"视频技术滤镜检测失败：{exc}") from exc
    issues = parse_detection_output(detection_stderr)
    if not metadata["has_audio"]:
        issues.insert(0, {
            "category": "missing_audio",
            "severity": "high",
            "start_second": 0,
            "end_second": metadata["duration_seconds"],
            "duration_seconds": metadata["duration_seconds"],
            "description": "视频没有音轨",
        })
    return {
        "status": "review" if issues else "passed",
        "decode_ok": True,
        "metadata": metadata,
        "issues": issues,
    }
