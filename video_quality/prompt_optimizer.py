"""Turn an evidence-backed report into the next generation input without a model call."""
from __future__ import annotations

from .schemas import VideoEvaluationReport, VideoQualityInput, quality_failed


def optimize_prompt(
    request: VideoQualityInput,
    report: VideoEvaluationReport,
    *,
    threshold: float = 80,
) -> dict:
    failed = quality_failed(report, threshold)
    regeneration = report.regeneration
    revised = regeneration.revised_prompt.strip()
    if failed and not revised:
        fixes = "；".join(issue.suggested_fix for issue in report.issues[:6])
        revised = f"{request.original_prompt}\n质检修正要求：{fixes}".strip()
    return {
        "required": failed,
        "original_prompt": request.original_prompt,
        "revised_prompt": revised if failed else request.original_prompt,
        "negative_prompt": regeneration.negative_prompt if failed else "",
        "storyboard_changes": regeneration.storyboard_changes if failed else [],
        "parameter_changes": regeneration.parameter_changes if failed else {},
        "segments_to_regenerate": regeneration.segments_to_regenerate if failed else [],
        "manual_confirmation_required": failed,
        "source_score": report.overall_score,
    }
