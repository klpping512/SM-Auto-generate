"""Complete bounded two-stage video quality MVP service."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

from .frame_extractor import extract_frames
from .regeneration_controller import decide_regeneration
from .schemas import VideoEvaluationReport, VideoQualityInput
from .video_evaluator import evaluate_video
from .video_preprocessor import PreprocessedVideo, preprocess_video, write_json


def build_risk_windows(
    issues: list[dict],
    *,
    duration_seconds: float,
    padding_seconds: float = 0.5,
) -> list[dict]:
    candidates = []
    for issue in issues:
        if str(issue.get("severity")) != "high":
            continue
        start = max(0.0, float(issue.get("start_second") or 0) - padding_seconds)
        end = min(duration_seconds, float(issue.get("end_second") or start) + padding_seconds)
        if end > start:
            candidates.append([start, end])
    candidates.sort()
    merged: list[list[float]] = []
    for start, end in candidates:
        if merged and start <= merged[-1][1] + 0.25:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        {"start_second": round(start, 3), "end_second": round(end, 3)}
        for start, end in merged[:3]
    ]


def _merge_reports(
    global_report: VideoEvaluationReport,
    focused_report: VideoEvaluationReport,
) -> VideoEvaluationReport:
    payload = global_report.model_dump()
    existing = {
        (item["category"], item["start_second"], item["end_second"], item["evidence_frame"])
        for item in payload["issues"]
    }
    for issue in focused_report.model_dump()["issues"]:
        key = (issue["category"], issue["start_second"], issue["end_second"], issue["evidence_frame"])
        if key not in existing:
            payload["issues"].append(issue)
    payload["frame_index"] += focused_report.model_dump()["frame_index"]
    payload["overall_score"] = min(global_report.overall_score, focused_report.overall_score)
    payload["passed"] = global_report.passed and focused_report.passed
    payload["summary"] = f"{global_report.summary}；局部复检：{focused_report.summary}"
    payload["review_stage"] = "focused"
    for field in payload["scores"]:
        payload["scores"][field] = min(
            payload["scores"][field], getattr(focused_report.scores, field)
        )
    if focused_report.regeneration.required:
        focused_regeneration = focused_report.regeneration.model_dump()
        for field in ("revised_prompt", "negative_prompt"):
            if focused_regeneration[field]:
                payload["regeneration"][field] = focused_regeneration[field]
        for field in ("storyboard_changes", "segments_to_regenerate"):
            payload["regeneration"][field] += focused_regeneration[field]
        payload["regeneration"]["parameter_changes"].update(focused_regeneration["parameter_changes"])
        payload["regeneration"]["required"] = True
    return VideoEvaluationReport.model_validate(payload)


async def run_quality_mvp(
    request: VideoQualityInput,
    output_dir: Path,
    *,
    job_id: str | None = None,
    allowed_roots: list[Path] | None = None,
    history: list[dict] | None = None,
    cancel_check=None,
    preprocessor=preprocess_video,
    evaluator=evaluate_video,
    focus_extractor=extract_frames,
) -> dict:
    run_dir = Path(output_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    identifier = job_id or f"video-quality-{uuid4().hex}"
    preprocessed: PreprocessedVideo = await asyncio.to_thread(
        preprocessor,
        request,
        run_dir,
        allowed_roots=allowed_roots,
        cancel_check=cancel_check,
    )
    # Dependency-injected preprocessors used by tests and internal callers may
    # not have written artifacts themselves; always enforce the service contract.
    write_json(run_dir / "metadata.json", preprocessed.metadata)
    write_json(run_dir / "technical-report.json", preprocessed.technical_report)
    write_json(run_dir / "frames" / "index.json", {
        "meta": preprocessed.frame_meta,
        "frames": preprocessed.frames,
    })
    transcript_target = run_dir / "transcript.vtt"
    if preprocessed.transcript.path.resolve() != transcript_target.resolve():
        transcript_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(preprocessed.transcript.path, transcript_target)

    global_report = await evaluator(
        job_id=identifier,
        original_prompt=request.original_prompt,
        storyboard=request.storyboard,
        target_platform=request.target_platform,
        technical_report=preprocessed.technical_report,
        transcript_status=preprocessed.transcript.status,
        transcript_segments=preprocessed.transcript.segments,
        frames=preprocessed.frames,
        reference_images=request.reference_images,
        review_stage="global",
    )
    windows = build_risk_windows(
        global_report.model_dump()["issues"],
        duration_seconds=float(preprocessed.metadata["duration_seconds"]),
    )
    focused_report: VideoEvaluationReport | None = None
    focus_frames: list[dict] = []
    focus_meta: list[dict] = []
    if windows:
        per_window_budget = max(1, 40 // len(windows))
        for window_index, window in enumerate(windows, 1):
            extraction = await asyncio.to_thread(
                focus_extractor,
                preprocessed.video_path,
                run_dir / "frames" / f"focus-{window_index}",
                duration_seconds=float(preprocessed.metadata["duration_seconds"]),
                mode="detailed",
                max_frames=per_window_budget,
                start_second=window["start_second"],
                end_second=window["end_second"],
                requested_fps=8,
                cancel_check=cancel_check,
            )
            for frame_index, frame in enumerate(extraction["frames"], 1):
                frame = dict(frame)
                frame["frame_id"] = (
                    f"FRAME_F{window_index:02d}_{frame_index:04d}"
                    f"@{float(frame['timestamp_seconds']):.3f}s"
                )
                focus_frames.append(frame)
            focus_meta.append({"window": window, "extraction": extraction["meta"]})
        focus_frames = focus_frames[:40]
        if focus_frames:
            focused_report = await evaluator(
                job_id=identifier,
                original_prompt=request.original_prompt,
                storyboard=request.storyboard,
                target_platform=request.target_platform,
                technical_report={
                    **preprocessed.technical_report,
                    "focused_windows": windows,
                },
                transcript_status=preprocessed.transcript.status,
                transcript_segments=preprocessed.transcript.segments,
                frames=focus_frames,
                reference_images=request.reference_images,
                review_stage="focused",
            )
    final_report = _merge_reports(global_report, focused_report) if focused_report else global_report
    decision = decide_regeneration(
        final_report,
        history=history or [],
        auto_enabled=request.auto_regenerate,
    )
    problem_segments = [
        {
            "start_second": issue.start_second,
            "end_second": issue.end_second,
            "severity": issue.severity,
            "category": issue.category,
            "description": issue.description,
            "evidence_frame": issue.evidence_frame,
            "suggested_fix": issue.suggested_fix,
        }
        for issue in final_report.issues
    ]
    write_json(run_dir / "evaluation.json", final_report.model_dump())
    write_json(run_dir / "evaluation-stages.json", {
        "global": global_report.model_dump(),
        "focused": focused_report.model_dump() if focused_report else None,
        "focused_extraction": focus_meta,
    })
    write_json(run_dir / "problem-segments.json", problem_segments)
    manifest = {
        "job_id": identifier,
        "video_source": request.video_source,
        "resolved_video_path": str(preprocessed.video_path),
        "downloaded": preprocessed.downloaded,
        "source_info": preprocessed.source_info,
        "overall_score": final_report.overall_score,
        "passed": final_report.passed,
        "issue_count": len(final_report.issues),
        "global_frame_count": len(preprocessed.frames),
        "focused_frame_count": len(focus_frames),
        "transcript_status": preprocessed.transcript.status,
        "regeneration_decision": decision,
        "artifacts": {
            "metadata": "metadata.json",
            "technical_report": "technical-report.json",
            "frame_index": "frames/index.json",
            "transcript": "transcript.vtt",
            "evaluation": "evaluation.json",
            "problem_segments": "problem-segments.json",
            "evaluation_stages": "evaluation-stages.json",
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    return {
        "run_dir": str(run_dir),
        "report": final_report.model_dump(),
        "technical_report": preprocessed.technical_report,
        "problem_segments": problem_segments,
        "regeneration_decision": decision,
        "manifest": manifest,
    }
