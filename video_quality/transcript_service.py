"""Timestamped transcript service with known-script and native-caption priority.

The VTT parser and rolling-caption deduplication are adapted from
bradautomates/claude-video (MIT), commit
83da59fa78c3eee9e20f515fe75c438bb5166efd.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class TranscriptResult:
    status: str
    segments: list[dict]
    path: Path
    warnings: list[str] = field(default_factory=list)


def _seconds(hours: str, minutes: str, seconds: str, milliseconds: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def _dedupe(segments: list[dict]) -> list[dict]:
    output: list[dict] = []
    for segment in segments:
        if output and segment["text"] == output[-1]["text"]:
            output[-1]["end"] = segment["end"]
            continue
        if output and segment["text"].startswith(output[-1]["text"] + " "):
            output[-1]["text"] = segment["text"]
            output[-1]["end"] = segment["end"]
            continue
        output.append(dict(segment))
    return output


def parse_vtt(path: Path) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    segments: list[dict] = []
    index = 0
    while index < len(lines):
        match = TIMESTAMP_RE.search(lines[index])
        if not match:
            index += 1
            continue
        start = _seconds(*match.groups()[:4])
        end = _seconds(*match.groups()[4:])
        index += 1
        cue_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            cleaned = TAG_RE.sub("", lines[index]).strip()
            if cleaned:
                cue_lines.append(cleaned)
            index += 1
        text = " ".join(cue_lines).strip()
        if text:
            segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        index += 1
    return _dedupe(segments)


def clip_segments(
    segments: list[dict],
    start_second: float | None,
    end_second: float | None,
) -> list[dict]:
    if start_second is None and end_second is None:
        return segments
    lower = start_second if start_second is not None else float("-inf")
    upper = end_second if end_second is not None else float("inf")
    return [segment for segment in segments if segment["end"] >= lower and segment["start"] <= upper]


def _vtt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_vtt(segments: list[dict], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["WEBVTT", ""]
    for index, segment in enumerate(segments, 1):
        lines += [
            str(index),
            f"{_vtt_time(float(segment['start']))} --> {_vtt_time(float(segment['end']))}",
            str(segment["text"]).replace("\n", " ").strip(),
            "",
        ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _storyboard_segments(storyboard: dict | list | str) -> list[dict]:
    value = storyboard
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    scenes = value.get("scenes") if isinstance(value, dict) else value
    if not isinstance(scenes, list):
        return []
    cursor = 0.0
    segments: list[dict] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        try:
            duration = max(0.1, float(scene.get("duration") or scene.get("duration_seconds") or 0))
        except (TypeError, ValueError):
            continue
        text = str(scene.get("subtitle") or scene.get("voiceover") or "").strip()
        if text:
            segments.append({
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "text": text,
            })
        cursor += duration
    return segments


def _local_whisper(video_path: Path, model_path: str) -> list[dict]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_path, device="cpu", compute_type="int8")
    source_segments, _ = model.transcribe(str(video_path), language=None, vad_filter=True)
    return [
        {
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": str(segment.text).strip(),
        }
        for segment in source_segments
        if str(segment.text).strip()
    ]


def build_transcript(
    *,
    video_path: Path,
    output_path: Path,
    storyboard: dict | list | str = "",
    subtitle_path: Path | None = None,
    whisper_model_path: str | None = None,
    transcriber: Callable[[Path, str], list[dict]] | None = None,
) -> TranscriptResult:
    known = _storyboard_segments(storyboard)
    if known:
        write_vtt(known, output_path)
        return TranscriptResult("storyboard", known, output_path)

    if subtitle_path and Path(subtitle_path).exists():
        native = parse_vtt(Path(subtitle_path))
        if native:
            write_vtt(native, output_path)
            return TranscriptResult("native_subtitle", native, output_path)

    model_path = whisper_model_path or os.environ.get("ASSET_ASR_MODEL_PATH")
    warnings: list[str] = []
    segments: list[dict] = []
    if model_path and Path(model_path).exists():
        try:
            segments = (transcriber or _local_whisper)(Path(video_path), str(model_path))
        except Exception as exc:
            warnings.append(f"本地 Whisper 转写失败：{str(exc)[:300]}")
    else:
        warnings.append("没有项目字幕、平台字幕或可用的本地 Whisper 模型")
    write_vtt(segments, output_path)
    return TranscriptResult(
        "whisper" if segments else "unavailable",
        segments,
        output_path,
        warnings,
    )
