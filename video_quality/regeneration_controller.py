"""Bounded automatic-regeneration policy; automatic mode is off by default.

P3-A: guardrails (max_attempts / score_declined / no_meaningful_improvement)
must be evaluated even while automatic regeneration stays disabled, so a
human-in-the-loop "regenerate" action can be measured and blocked instead of
spinning forever. With no history the decision keeps the legacy
``automatic_regeneration_disabled`` semantics.
"""
from __future__ import annotations

from .schemas import VideoEvaluationReport, quality_failed

# Reasons that must disable the human "regenerate" action outright.
BLOCKING_REASONS = frozenset({"maximum_attempts_reached", "no_meaningful_improvement"})


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
    previous_score = float(history[-1].get("overall_score") or 0) if history else None
    base = {
        "attempts_used": len(history),
        "previous_score": previous_score,
        "score_delta": (
            round(float(report.overall_score) - previous_score, 2)
            if previous_score is not None else None
        ),
    }
    # Guardrails run before the auto/manual split so human-triggered reruns are
    # bounded too; an empty history keeps the original short-circuit.
    if history:
        if len(history) >= max_attempts:
            return {"action": "manual_review", "reason": "maximum_attempts_reached", **base}
        if report.overall_score < previous_score:
            return {"action": "manual_review", "reason": "score_declined", **base}
        if report.overall_score - previous_score < minimum_improvement:
            return {
                "action": "manual_review",
                "reason": "no_meaningful_improvement",
                **base,
            }
        if not auto_enabled:
            # Guardrails passed but the loop stays human-triggered: the action
            # now reflects "rerun is allowed" instead of an idle disabled note.
            return {
                "action": "manual_review",
                "reason": "manual_regeneration_allowed",
                **base,
            }
    if not auto_enabled:
        return {
            "action": "manual_review",
            "reason": "automatic_regeneration_disabled",
            **base,
        }
    return {
        "action": "regenerate",
        "reason": "quality_below_threshold",
        "next_attempt": len(history) + 1,
        **base,
    }
