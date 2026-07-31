"""Bounded automatic-regeneration policy; automatic mode is off by default."""
from __future__ import annotations

from .schemas import VideoEvaluationReport, quality_failed


def decide_regeneration(
    report: VideoEvaluationReport,
    *,
    history: list[dict],
    auto_enabled: bool = False,
    threshold: float = 80,
    max_attempts: int = 2,
    minimum_improvement: float = 3,
) -> dict:
    if not quality_failed(report, threshold):
        return {"action": "none", "reason": "quality_passed", "attempts_used": len(history)}
    if not auto_enabled:
        return {
            "action": "manual_review",
            "reason": "automatic_regeneration_disabled",
            "attempts_used": len(history),
        }
    if len(history) >= max_attempts:
        return {"action": "manual_review", "reason": "maximum_attempts_reached", "attempts_used": len(history)}
    if history:
        previous = float(history[-1].get("overall_score") or 0)
        if report.overall_score < previous:
            return {"action": "manual_review", "reason": "score_declined", "attempts_used": len(history)}
        if report.overall_score - previous < minimum_improvement:
            return {
                "action": "manual_review",
                "reason": "no_meaningful_improvement",
                "attempts_used": len(history),
            }
    return {
        "action": "regenerate",
        "reason": "quality_below_threshold",
        "attempts_used": len(history),
        "next_attempt": len(history) + 1,
    }
