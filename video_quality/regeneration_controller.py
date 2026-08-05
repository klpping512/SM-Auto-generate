"""Bounded automatic-regeneration policy; automatic mode is off by default.

P3-A: guardrails (max_attempts / score_declined / no_meaningful_improvement)
must be evaluated even while automatic regeneration stays disabled, so a
human-in-the-loop "regenerate" action can be measured and blocked instead of
spinning forever. With no history the decision keeps the legacy
``automatic_regeneration_disabled`` semantics.
"""
from __future__ import annotations

from .schemas import VideoEvaluationReport, quality_failed, weighted_actionable_score

# Reasons that must disable the human "regenerate" action outright.
# actionable_axes_healthy (P3-B): 失分全落在重跑改不动的画面轴，重生成无意义。
BLOCKING_REASONS = frozenset(
    {"maximum_attempts_reached", "no_meaningful_improvement", "actionable_axes_healthy"}
)

# 可改轴加权分≥此值，视为"重跑改善空间有限"（失分在改不动的画面轴）。
ACTIONABLE_FLOOR = 70


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
    # P3-B：可改轴加权分随每一支失败判定带出，供前端展示；它只 gate
    # "重生成 vs 人工"，不参与 pass/fail。
    base["weighted_actionable_score"] = weighted_actionable_score(report.scores)
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
    # P3-B：四道有界护栏（quality_passed/达上限/下滑/提升不足）全过后，若失分
    # 其实落在重跑改不动的画面轴（可改轴加权分达标），自动与人工重跑一并挡住。
    if base["weighted_actionable_score"] >= ACTIONABLE_FLOOR:
        return {"action": "manual_review", "reason": "actionable_axes_healthy", **base}
    if history and not auto_enabled:
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
