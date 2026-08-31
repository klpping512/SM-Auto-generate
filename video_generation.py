"""Persistent, quality-gated video generation state machine and worker shell."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import database as db
import semantic_matching
import video_renderer
import video_state
import video_topic_contract
from video_composition_policy import is_explanation_scene, source_usage_report
import video_quality.service as video_quality_service
from video_quality.schemas import VideoQualityInput
from video_quality.video_evaluator import is_recoverable_temporal_evidence_error


logger = logging.getLogger(__name__)


def _script_quality_topic(brief: dict, script: dict) -> str:
    """Return the immutable user topic, never a Hook-derived planner label."""
    contract = brief.get("topic_contract") if isinstance(brief.get("topic_contract"), dict) else {}
    return str(
        contract.get("requested_topic")
        or brief.get("requested_topic")
        or brief.get("logistics_topic")
        or script.get("title")
        or ""
    ).strip()


class PipelineStage(StrEnum):
    QUEUED = "queued"
    TOPIC_BRIEF = "topic_brief"
    HOOK_LOCKING = "hook_locking"
    SCRIPTING = "scripting"
    PROJECT_BUILDING = "project_building"
    PLANNING = "planning"
    SCRIPT_QUALITY_CHECK = "script_quality_check"
    ASSET_MATCHING = "asset_matching"
    MATCH_QUALITY_CHECK = "match_quality_check"
    PREVIEW_RENDERING = "preview_rendering"
    PREVIEW_QUALITY_CHECK = "preview_quality_check"
    FINAL_RENDERING = "final_rendering"
    FINAL_QUALITY_CHECK = "final_quality_check"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


_FORWARD_TRANSITIONS = {
    PipelineStage.QUEUED: {PipelineStage.PLANNING, PipelineStage.TOPIC_BRIEF},
    PipelineStage.TOPIC_BRIEF: {PipelineStage.HOOK_LOCKING},
    PipelineStage.HOOK_LOCKING: {PipelineStage.SCRIPTING},
    PipelineStage.SCRIPTING: {PipelineStage.PROJECT_BUILDING},
    PipelineStage.PROJECT_BUILDING: {PipelineStage.PLANNING},
    PipelineStage.PLANNING: {PipelineStage.SCRIPT_QUALITY_CHECK},
    PipelineStage.SCRIPT_QUALITY_CHECK: {PipelineStage.ASSET_MATCHING},
    PipelineStage.ASSET_MATCHING: {PipelineStage.MATCH_QUALITY_CHECK},
    PipelineStage.MATCH_QUALITY_CHECK: {PipelineStage.PREVIEW_RENDERING},
    PipelineStage.PREVIEW_RENDERING: {PipelineStage.PREVIEW_QUALITY_CHECK},
    PipelineStage.PREVIEW_QUALITY_CHECK: {
        PipelineStage.PREVIEW_RENDERING,
        PipelineStage.FINAL_RENDERING,
        PipelineStage.SUCCEEDED,
    },
    PipelineStage.FINAL_RENDERING: {PipelineStage.FINAL_QUALITY_CHECK},
    PipelineStage.FINAL_QUALITY_CHECK: {PipelineStage.SUCCEEDED},
}


@dataclass(frozen=True)
class QualityDecision:
    status: JobStatus
    stage: PipelineStage
    score: float
    issues: list[str] = field(default_factory=list)
    repair_attempts: int = 0


class GenerationCanceled(RuntimeError):
    """Raised at cooperative cancellation checkpoints."""


def validate_transition(current: PipelineStage | str, target: PipelineStage | str) -> bool:
    current_stage = PipelineStage(current)
    target_stage = PipelineStage(target)
    if target_stage not in _FORWARD_TRANSITIONS.get(current_stage, set()):
        raise ValueError(f"非法的视频生成阶段跳转：{current_stage.value} -> {target_stage.value}")
    return True


def build_idempotency_key(project_id: str, revision_id: str) -> str:
    canonical = json.dumps(
        {"project_id": project_id, "revision_id": revision_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cancellation_checkpoint(job: dict) -> None:
    if str(job.get("status")) in {JobStatus.CANCEL_REQUESTED, JobStatus.CANCELED}:
        raise GenerationCanceled("视频生成已取消")


def _score(report: dict, default: float = 0) -> float:
    try:
        return float(report.get("score", default))
    except (TypeError, ValueError):
        return default


def route_script_quality(report: dict, threshold: float = 80) -> QualityDecision:
    score = _score(report)
    issues = [str(issue) for issue in report.get("issues") or []]
    hard_failures = report.get("hard_failures") or []
    if hard_failures or score < threshold:
        if not issues:
            issues = [str(issue) for issue in hard_failures] or ["脚本质量未达到自动成片标准"]
        return QualityDecision(
            JobStatus.NEEDS_REVIEW, PipelineStage.SCRIPT_QUALITY_CHECK, score, issues
        )
    return QualityDecision(JobStatus.RUNNING, PipelineStage.ASSET_MATCHING, score)


def _normalized_copy(value: object) -> str:
    """Normalize a line enough to identify a reused template."""
    return "".join(char for char in str(value or "") if char.isalnum())


def _utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def copy_provenance_report(scenes: list[dict] | None) -> list[dict]:
    """Expose the final per-scene narration origin in every generation report."""
    rows = []
    for index, scene in enumerate(scenes or [], 1):
        source = video_state.normalize_copy_source(scene.get("copy_source"))
        rows.append({
            "scene": index,
            "scene_role": str(scene.get("scene_role") or scene.get("evidence_type") or ""),
            "source": source,
            "reason": str(scene.get("copy_repair_reason") or scene.get("repair_reason") or ""),
            "model_name": str(scene.get("model_name") or ""),
            "voiceover": str(scene.get("voiceover") or ""),
        })
    return rows


def formal_content_repetition_issues(scenes: list[dict]) -> list[str]:
    """Reject a formal video when narration or visible actions are repetitive."""
    scenes = [
        scene for scene in scenes
        if str(scene.get("evidence_type") or "") != "brand_endcard"
    ]
    voiceovers = [_normalized_copy(scene.get("voiceover")) for scene in scenes]
    issues: list[str] = []
    if any(line and voiceovers.count(line) > 1 for line in voiceovers):
        issues.append("旁白存在重复模板句，不能生成同质化成片")

    generic_checks = sum("请核对" in str(scene.get("voiceover") or "") for scene in scenes)
    if generic_checks >= 2:
        issues.append("“请核对”类泛化提醒重复出现，缺少具体物流建议")

    action_anchors = [
        _normalized_copy(scene.get("action_key") or scene.get("copy_anchor"))
        for scene in scenes
        if str(scene.get("scene_role") or "") == "owned_proof"
    ]
    if any(anchor and action_anchors.count(anchor) > 1 for anchor in action_anchors):
        issues.append("自有镜头存在重复可见动作，不能用相似叉车/拖车镜头凑时长")
    return issues


def route_match_quality(scene_reports: list[dict], threshold: float = 35) -> QualityDecision:
    """只让无可用素材等硬失败暂停；低匹配分进入带提示的内部预览。

    匹配分衡量的是画面与口播的贴合程度，不是素材是否存在。将它当作
    成片前的硬门槛会把已有的原始素材库全部挡在预览之外。发布级质量
    仍由后续的视频质检和人工审核把关。
    """
    if not scene_reports:
        return QualityDecision(
            JobStatus.NEEDS_REVIEW,
            PipelineStage.MATCH_QUALITY_CHECK,
            0,
            ["没有可用的分镜素材匹配结果"],
        )
    scores = [_score(report) for report in scene_reports]
    issues: list[str] = []
    hard_failure = False
    for index, report in enumerate(scene_reports, start=1):
        scene_number = report.get("scene", index)
        report_issues = [str(issue) for issue in report.get("issues") or []]
        if report.get("hard_failures"):
            hard_failure = True
            report_issues.extend(str(issue) for issue in report["hard_failures"])
        if _score(report) < threshold and not report_issues:
            report_issues.append(f"匹配分低于 {threshold:g} 分")
        issues.extend(f"第{scene_number}镜头：{issue}" for issue in dict.fromkeys(report_issues))
    minimum = min(scores)
    if hard_failure:
        return QualityDecision(
            JobStatus.NEEDS_REVIEW,
            PipelineStage.MATCH_QUALITY_CHECK,
            minimum,
            issues,
        )
    return QualityDecision(
        JobStatus.RUNNING,
        PipelineStage.PREVIEW_RENDERING,
        minimum,
        issues,
    )


def hotspot_evidence_gate(script: dict) -> list[str]:
    """热点跟进的成片前硬约束，防止静态拼贴冒充真实素材视频。

    When ``adaptation.adapted`` is set, owned inventory and duration floors are
    softened so thin Buffalo stock degrades the plan instead of blocking render.
    Hook absence remains a hard failure.
    """
    if str(script.get("source_type") or "") != "hotspot_followup":
        return []
    scenes = [item for item in script.get("scenes") or [] if isinstance(item, dict)]
    total_ms = sum(int(item.get("duration_ms") or round(float(item.get("duration") or 0) * 1000)) for item in scenes)
    hotspot_count = sum(item.get("evidence_type") == "hotspot_video" for item in scenes)
    owned_count = sum(item.get("evidence_type") == "owned_video" for item in scenes)
    image_ms = sum(
        int(item.get("duration_ms") or round(float(item.get("duration") or 0) * 1000))
        for item in scenes if item.get("evidence_type") == "image"
    )
    adapted = bool((script.get("adaptation") or {}).get("adapted"))
    issues = []
    if adapted:
        if total_ms < 15_000:
            issues.append(f"自适应成片过短，当前仅 {total_ms / 1000:g} 秒")
        if hotspot_count < 1:
            issues.append("至少需要 1 段已确认热点视频")
        if owned_count < 1 and not any(item.get("evidence_type") == "image" for item in scenes):
            issues.append("自适应成片至少需要 1 段 Buffalo 自有动态或图片过渡")
        if total_ms and image_ms / total_ms > 0.45:
            issues.append(f"自适应模式下静态图片占比不能超过 45%，当前为 {image_ms / total_ms:.0%}")
    else:
        if total_ms < 60_000:
            issues.append(f"热点跟进成片必须至少 60 秒，当前仅 {total_ms / 1000:g} 秒")
        if hotspot_count < 1:
            issues.append("至少需要 1 段已确认热点视频")
        if owned_count < 4:
            issues.append(f"至少 4 段 Buffalo 动态视频，当前仅 {owned_count} 段")
        if total_ms and image_ms / total_ms > 0.15:
            issues.append(f"静态图片占比不能超过 15%，当前为 {image_ms / total_ms:.0%}")
    for first, second in zip(scenes, scenes[1:]):
        if first.get("evidence_type") == second.get("evidence_type") == "image":
            issues.append("静态图片不能连续作为主体镜头")
            break
    usage = source_usage_report(scenes)
    issues.extend(usage["issues"])
    for index, scene in enumerate(scenes, 1):
        if is_explanation_scene(scene):
            issues.append(f"第{index}镜使用了已禁用的信息图、流程图或 PPT 卡片")
    return issues


def quality_decision(
    score: float,
    requested_tier: str,
    issues: list[str] | None = None,
    threshold: float = 75,
) -> dict:
    """低分内容只允许生成带水印的内部预览，发布级仍执行硬门禁。"""
    tier = "internal_preview" if requested_tier == "internal_preview" else "publish"
    publish_allowed = tier == "publish" and float(score) >= threshold
    return {
        "tier": tier,
        "score": float(score),
        "render_allowed": tier == "internal_preview" or publish_allowed,
        "publish_allowed": publish_allowed,
        "watermark": "内部测试｜素材待确认" if tier == "internal_preview" else "",
        "issues": [str(issue) for issue in issues or []],
    }


def route_preview_quality(
    report: dict,
    repair_attempts: int = 0,
    threshold: float = 80,
    max_repairs: int = 2,
) -> QualityDecision:
    score = _score(report)
    issues = [str(issue) for issue in report.get("issues") or []]
    hard_failures = [str(issue) for issue in report.get("hard_failures") or []]
    if not hard_failures and score >= threshold:
        return QualityDecision(
            JobStatus.RUNNING,
            PipelineStage.FINAL_RENDERING,
            score,
            repair_attempts=repair_attempts,
        )
    if not hard_failures and repair_attempts < max_repairs:
        return QualityDecision(
            JobStatus.RUNNING,
            PipelineStage.PREVIEW_RENDERING,
            score,
            issues or ["预览质量未达标，正在自动修复"],
            repair_attempts + 1,
        )
    return QualityDecision(
        JobStatus.NEEDS_REVIEW,
        PipelineStage.PREVIEW_QUALITY_CHECK,
        score,
        issues + hard_failures or ["预览质量未达到自动成片标准"],
        repair_attempts,
    )


def route_video_evaluation_quality(
    report: dict,
    *,
    threshold: float = 80,
) -> QualityDecision:
    try:
        score = float(report.get("overall_score", report.get("score", 0)))
    except (TypeError, ValueError):
        score = 0
    evaluation_status = str(report.get("evaluation_status") or "unavailable")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    high_issues = [item for item in issues if str(item.get("severity")) == "high"]
    critical_categories = {
        "prompt_alignment", "topic_alignment", "hook_alignment",
        "semantic_alignment", "storytelling",
    }
    critical_issues = [
        item for item in issues
        if str(item.get("category") or "") in critical_categories
        and str(item.get("severity") or "") in {"medium", "high"}
    ]
    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    try:
        prompt_alignment = float(scores.get("prompt_alignment", score))
        storytelling = float(scores.get("storytelling", score))
    except (TypeError, ValueError):
        prompt_alignment = storytelling = 0
    low_risk_categories = {
        "camera_quality", "temporal_consistency", "subtitle_audio_quality",
    }
    # A real-source video can retain small editorial notes such as handheld
    # motion or a transition preference. If every technical gate has passed,
    # the evaluator found no high/medium defect, and its score is at least 75, do not
    # make the C-end user manually approve a 50–90s delivery just because the
    # model marked those minor notes as a failed regeneration. The raw report
    # remains attached for audit; this is a product policy decision, not a
    # rewritten model score.
    low_risk_editorial_only = (
        evaluation_status == "completed"
        and score >= 75
        and bool(issues)
        and not high_issues
        and not any(str(item.get("severity")) not in {"low", ""} for item in issues)
        and all(str(item.get("category") or "") in low_risk_categories for item in issues)
        and not (report.get("technical_issues") or [])
    )
    # Technical hard gates have already passed before this function runs.
    # Medium editorial notes such as visual variety remain useful audit data,
    # but they must not strand a decodable, correctly timed, real-Hook video in
    # ``needs_review``.  Semantic/topic mismatches and every high-severity issue
    # are still blocking unless an expected contextual-Hook note was explicitly
    # recovered by ``_filter_expected_dual_library_editorial_notes``.
    noncritical_editorial_only = (
        evaluation_status == "completed"
        and score >= 70
        and bool(issues)
        and not high_issues
        and not critical_issues
        and not (report.get("technical_issues") or [])
    )
    # The evaluator's explicit clean pass is authoritative once deterministic
    # media gates have passed. Requiring an undocumented prompt score of 85
    # after the evaluator returned passed=true, score=86 and no issues created
    # the contradictory “86 分通过但 needs_review” state seen in production.
    clean_evaluator_pass = (
        evaluation_status == "completed"
        and bool(report.get("passed"))
        and score >= threshold
        and not issues
        and not high_issues
        and not critical_issues
        and not (report.get("technical_issues") or [])
        and prompt_alignment >= 75
        and storytelling >= 75
    )
    # Do not call an unavailable semantic evaluator a pass.  The pipeline
    # converts this review decision into a publication hold *after* the hard
    # technical gates have passed and still finishes the MP4.  This preserves
    # both truths: generation remains available, publication is not silently
    # approved without semantic evidence.
    if evaluation_status != "completed" and not (report.get("technical_issues") or []):
        return QualityDecision(
            JobStatus.NEEDS_REVIEW,
            PipelineStage.PREVIEW_QUALITY_CHECK,
            score,
            ["视频质检服务暂不可用，请人工检查预览"],
        )
    if clean_evaluator_pass or (
        evaluation_status == "completed"
        and bool(report.get("passed"))
        and score >= threshold
        and not high_issues
        and not critical_issues
        and prompt_alignment >= 85
        and storytelling >= 80
    ) or low_risk_editorial_only or noncritical_editorial_only or (
        evaluation_status == "completed"
        and bool(report.get("policy_recovered"))
        and bool(report.get("passed"))
        and score >= 75
        and not issues
        and not high_issues
    ):
        return QualityDecision(JobStatus.RUNNING, PipelineStage.FINAL_RENDERING, score)
    descriptions = [
        str(item.get("description") or item.get("category") or "视频质量问题")
        for item in (critical_issues or high_issues or issues)
    ]
    if not descriptions:
        descriptions = [
            "视频质检服务暂不可用，请人工检查预览"
            if evaluation_status != "completed"
            else f"成片总分低于 {threshold:g} 分，请按质检报告修正"
        ]
    return QualityDecision(
        JobStatus.NEEDS_REVIEW,
        PipelineStage.PREVIEW_QUALITY_CHECK,
        score,
        list(dict.fromkeys(descriptions)),
    )


def _filter_expected_dual_library_editorial_notes(report: dict, script: dict) -> dict:
    """Keep fixed dual-Hook openings and the brand CTA from becoming false defects."""
    if str(script.get("source_type") or "") != "topic_brief_dual_library":
        return report
    binding_mode = str((script.get("brief") or {}).get("hook_binding_mode") or "")
    contextual_binding = binding_mode in {"adjacent_logistics", "contextual_attention"}
    kept, ignored = [], []
    for issue in report.get("issues") or []:
        category = str(issue.get("category") or "")
        description = str(issue.get("description") or "")
        expected_hook_pair = category == "temporal_consistency" and "同一热点的不同片段" in description
        expected_cta = category == "storytelling" and "品牌CTA" in description and "静态图片" in description
        expected_contextual_hook = (
            contextual_binding
            and category in {"prompt_alignment", "topic_alignment", "hook_alignment", "semantic_alignment"}
            and any(term in description for term in (
                "开场", "Hook", "热点", "hook_compatibility", "节点不匹配", "跨度大",
            ))
            and not any(term in description for term in ("全片", "正文偏题", "没有回应主题"))
        )
        (ignored if expected_hook_pair or expected_cta or expected_contextual_hook else kept).append(issue)
    if not ignored:
        return report
    filtered = {**report, "issues": kept, "expected_editorial_notes": ignored}
    if not kept and not filtered.get("technical_issues"):
        filtered.update({"passed": True, "policy_recovered": True})
    return filtered


def _formal_dual_library_duration_issue(report: dict, script: dict) -> str | None:
    """Keep a C-end dual-library promise honest at the technical quality gate."""
    if str(script.get("source_type") or "") != "topic_brief_dual_library":
        return None
    if bool((script.get("adaptation") or {}).get("adapted")):
        # Adapted plans intentionally shorten; provenance already surfaces the
        # inventory compromise. Only reject trivially short renders.
        try:
            duration = float(report.get("duration"))
        except (TypeError, ValueError):
            return "成片时长未检测到，无法确认自适应交付"
        if duration < 15.0:
            return f"自适应成片仅 {duration:.1f} 秒，过短无法验收"
        return None
    try:
        duration = float(report.get("duration"))
    except (TypeError, ValueError):
        return "成片时长未检测到，无法确认 50–90 秒交付标准"
    if 50.0 <= duration <= 90.0:
        return None
    return f"成片时长 {duration:.1f} 秒，不符合 50–90 秒交付标准"



def quality_budget_job_id(job_id: str) -> str:
    """Keep a video's visual-review budget separate from its script-planning budget."""
    return f"{job_id}:video-quality"


def collect_prior_quality_history(job: dict, *, max_depth: int = 8) -> list[dict]:
    """P3-A: walk the resume lineage (prior_job_id) and return prior quality
    evaluations in attempt order (oldest first) so decide_regeneration's
    guardrails can actually measure human-triggered reruns."""
    chain: list[dict] = []
    prior_id = job.get("prior_job_id")
    seen: set[str] = set()
    while prior_id and prior_id not in seen and len(chain) < max_depth:
        seen.add(str(prior_id))
        prior = db.get_video_generation_job(str(prior_id))
        if not prior:
            break
        evaluation = (prior.get("quality_report") or {}).get("video_evaluation") or {}
        if isinstance(evaluation, dict) and evaluation.get("overall_score") is not None:
            chain.append({
                "job_id": prior_id,
                "overall_score": evaluation.get("overall_score"),
                "passed": bool(evaluation.get("passed")),
            })
        prior_id = prior.get("prior_job_id")
    chain.reverse()
    return chain


def lease_owner_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


StageHandler = Callable[[dict], Awaitable[QualityDecision | PipelineStage | None]]


def video_generation_auto_retry_limit() -> int:
    """Return the bounded number of full-pipeline automatic retries.

    The first claim is the initial production attempt. A configured value of
    two therefore allows at most three complete attempts. Keep the hard cap
    small: deterministic reconstruction is preferable to an infinite worker
    loop when a source file is genuinely broken.
    """
    try:
        configured = int(os.environ.get("VIDEO_GENERATION_AUTO_RETRIES", "2"))
    except (TypeError, ValueError):
        configured = 2
    return max(0, min(configured, 5))


def _resequence_retry_scenes(scenes: list[dict], attempt: int) -> list[dict]:
    """Change visual rhythm on retries while preserving grounded scene facts.

    Whole scenes move together, so narration stays attached to its source.
    The real Hook remains first and the brand close remains last.
    """
    copied = [dict(scene) for scene in (scenes or [])]
    if attempt <= 1 or len(copied) < 4:
        return copied
    opener: list[dict] = []
    if copied and (
        str(copied[0].get("evidence_type") or "") == "hotspot_video"
        or str(copied[0].get("scene_role") or "") in {"hotspot_hook", "hotspot_evidence"}
    ):
        opener = [copied.pop(0)]
    closer: list[dict] = []
    if copied and str(copied[-1].get("scene_role") or "") == "brand_close":
        closer = [copied.pop()]
    images = [scene for scene in copied if str(scene.get("evidence_type") or "") == "image"]
    dynamic = [scene for scene in copied if str(scene.get("evidence_type") or "") != "image"]
    if not images or not dynamic:
        return opener + copied + closer

    first_dynamic = dynamic.pop(0)
    if dynamic:
        offset = max(0, attempt - 2) % len(dynamic)
        dynamic = dynamic[offset:] + dynamic[:offset]
    reordered = [first_dynamic]
    while images or dynamic:
        if images:
            reordered.append(images.pop(0))
        if dynamic:
            reordered.append(dynamic.pop(0))
    result = opener + reordered + closer
    for index, scene in enumerate(result, 1):
        scene["scene"] = index
    return result


async def _schedule_automatic_video_retry(
    job: dict,
    *,
    error_code: str,
    error_message: str,
    failed_stage: str,
    quality_report: dict | None = None,
) -> bool:
    """Requeue a failed attempt from the immutable revision, if budget remains."""
    try:
        attempt = max(1, int(job.get("attempt") or 1))
    except (TypeError, ValueError):
        attempt = 1
    retry_limit = video_generation_auto_retry_limit()
    if attempt > retry_limit:
        return False

    report = dict(quality_report or job.get("quality_report") or {})
    history = list(report.get("automatic_retry_history") or [])
    history.append({
        "attempt": attempt,
        "failed_stage": failed_stage,
        "error_code": error_code,
        "error_message": error_message[:500],
    })
    report["automatic_retry_history"] = history[-5:]
    report["automatic_retry"] = {
        "scheduled": True,
        "completed_attempts": attempt,
        "remaining_retries": max(0, retry_limit - attempt + 1),
        "restart_stage": failed_stage or PipelineStage.QUEUED.value,
    }
    restart = failed_stage if failed_stage in {stage.value for stage in PipelineStage} else PipelineStage.QUEUED.value
    if restart in {PipelineStage.SUCCEEDED.value, PipelineStage.FAILED.value, PipelineStage.CANCELED.value}:
        restart = PipelineStage.QUEUED.value
    await asyncio.to_thread(
        db.update_video_generation_job,
        job["id"],
        status=JobStatus.PENDING.value,
        stage=restart,
        progress=0,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        finished_at=None,
        error_code=error_code,
        error_message=error_message[:500],
        quality_report=report,
    )
    await asyncio.to_thread(
        db.add_video_generation_event,
        job["id"],
        "automatic_retry_scheduled",
        "质量门禁未通过，已自动从确定性脚本重新生产",
        {
            "attempt": attempt,
            "retry_limit": retry_limit,
            "failed_stage": failed_stage,
            "error_code": error_code,
        },
    )
    return True


async def run_claimed_job(
    job: dict,
    owner: str,
    handlers: dict[PipelineStage, StageHandler],
    lease_seconds: int = 30,
) -> None:
    """Run injected stage handlers while persisting progress and honoring cancellation."""
    current = PipelineStage(job["stage"])
    try:
        while current not in {
            PipelineStage.SUCCEEDED,
            PipelineStage.CANCELED,
            PipelineStage.FAILED,
        }:
            latest = await asyncio.to_thread(db.get_video_generation_job, job["id"])
            if not latest:
                return
            cancellation_checkpoint(latest)
            await asyncio.to_thread(
                db.renew_video_generation_lease, job["id"], owner, lease_seconds
            )
            handler = handlers.get(current)
            if not handler:
                raise RuntimeError(f"阶段处理器未配置：{current.value}")
            result = await handler(latest)
            if isinstance(result, QualityDecision):
                # 阶段处理器可能已经写入完整报告（例如质检产物）。
                # 必须重新读取数据库后再合并 gate，否则旧的 latest 会把
                # video_evaluation、证据索引和错误原因覆盖掉。
                latest_after_stage = await asyncio.to_thread(
                    db.get_video_generation_job, job["id"]
                ) or latest
                merged_report = dict(latest_after_stage.get("quality_report") or {})
                merged_report["gate"] = {
                    "stage": result.stage.value,
                    "score": result.score,
                    "issues": result.issues,
                    "repair_attempts": result.repair_attempts,
                }
                await asyncio.to_thread(
                    db.update_video_generation_job,
                    job["id"],
                    status=result.status.value,
                    stage=result.stage.value,
                    quality_report=merged_report,
                )
                if result.status is JobStatus.NEEDS_REVIEW:
                    issue_message = "；".join(result.issues) or "质量门禁未通过"
                    latest_for_retry = await asyncio.to_thread(
                        db.get_video_generation_job, job["id"]
                    ) or latest_after_stage
                    if await _schedule_automatic_video_retry(
                        latest_for_retry,
                        error_code="QualityGateRejected",
                        error_message=issue_message,
                        failed_stage=result.stage.value,
                        quality_report=merged_report,
                    ):
                        return
                    now = _utc_now_sql()
                    await asyncio.to_thread(
                        db.update_video_generation_job,
                        job["id"],
                        status=JobStatus.FAILED.value,
                        stage=result.stage.value,
                        error_code="QualityGateExhausted",
                        error_message=issue_message[:500],
                        finished_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                    )
                    await asyncio.to_thread(
                        db.add_video_generation_event,
                        job["id"], "automatic_retry_exhausted",
                        "自动重建次数已用尽，任务已停止以防止坏片出库",
                        {"stage": result.stage.value, "issues": result.issues},
                    )
                    return
                target = result.stage
            else:
                target = PipelineStage(result) if result else current
                if target != current:
                    validate_transition(current, target)
                    if target is PipelineStage.SUCCEEDED:
                        latest_for_success = await asyncio.to_thread(
                            db.get_video_generation_job, job["id"]
                        ) or latest
                        probe = video_state.probe_video_artifact(
                            latest_for_success.get("output_path") or latest_for_success.get("preview_path")
                        )
                        now = _utc_now_sql()
                        if not probe.get("ok"):
                            await asyncio.to_thread(
                                db.update_video_generation_job,
                                job["id"], status=JobStatus.FAILED.value,
                                stage=PipelineStage.FAILED.value, finished_at=now,
                                lease_owner=None, lease_expires_at=None,
                                heartbeat_at=None, error_code="MissingArtifact",
                                error_message="任务结束但没有可读取的 MP4",
                            )
                            return
                        report = dict(latest_for_success.get("quality_report") or {})
                        publication = dict(report.get("publication") or {})
                        quality_hold = (
                            publication.get("tier") == "quality_hold"
                            or publication.get("publish_allowed") is False
                        )
                        if quality_hold:
                            publication.setdefault("tier", "quality_hold")
                            publication["publish_allowed"] = False
                            publication["manual_acceptance_required"] = True
                            report["publication"] = publication
                        await asyncio.to_thread(
                            db.update_video_generation_job,
                            job["id"], status=JobStatus.SUCCEEDED.value,
                            stage=target.value, progress=100, finished_at=now,
                            lease_owner=None, lease_expires_at=None,
                            heartbeat_at=None, error_code=None, error_message=None,
                            quality_report=report,
                        )
                        await asyncio.to_thread(
                            db.add_video_generation_event,
                            job["id"],
                            "quality_hold" if quality_hold else "succeeded",
                            "成片已生成，质量暂缓，需人工确认后才能发布" if quality_hold else "视频已通过全部质量检查",
                        )
                        return
                    await asyncio.to_thread(
                        db.update_video_generation_job,
                        job["id"], status=JobStatus.RUNNING.value, stage=target.value,
                    )
                    await asyncio.to_thread(
                        db.add_video_generation_event,
                        job["id"], "stage_changed", f"进入 {target.value}",
                        {"stage": target.value},
                    )
            current = target
    except GenerationCanceled:
        now = _utc_now_sql()
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            status=JobStatus.CANCELED.value,
            stage=PipelineStage.CANCELED.value,
            canceled_at=now,
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        await asyncio.to_thread(
            db.add_video_generation_event, job["id"], "canceled", "视频生成已停止"
        )
    except Exception as exc:
        latest = await asyncio.to_thread(db.get_video_generation_job, job["id"]) or job
        fail_stage = str(latest.get("stage") or current.value)
        if fail_stage in {
            PipelineStage.FAILED.value,
            PipelineStage.SUCCEEDED.value,
            PipelineStage.CANCELED.value,
        }:
            fail_stage = current.value
        retryable = fail_stage in {
            PipelineStage.ASSET_MATCHING.value,
            PipelineStage.MATCH_QUALITY_CHECK.value,
            PipelineStage.PREVIEW_RENDERING.value,
            PipelineStage.PREVIEW_QUALITY_CHECK.value,
            PipelineStage.FINAL_RENDERING.value,
            PipelineStage.FINAL_QUALITY_CHECK.value,
        }
        if retryable and await _schedule_automatic_video_retry(
            latest,
            error_code=type(exc).__name__,
            error_message=str(exc),
            failed_stage=fail_stage,
        ):
            return
        now = _utc_now_sql()
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            status=JobStatus.FAILED.value,
            stage=fail_stage,
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
        )
        await asyncio.to_thread(
            db.add_video_generation_event, job["id"], "failed", "视频生成失败",
            {"error": str(exc)[:500], "stage": fail_stage},
        )


def _scene_is_locked_visual(scene: dict) -> bool:
    evidence = str(scene.get("evidence_type") or "")
    role = str(scene.get("scene_role") or "")
    return (
        evidence == "brand_endcard"
        or role in {"brand_endcard", "brand_close", "cta"}
        or bool(scene.get("event_clip_id"))
        or bool(scene.get("brand_endcard_fallback"))
    )


def diversify_repeated_asset_sequence(
    scenes: list[dict],
    recent_signatures: list[str],
    *,
    alternate_segments: dict[int, list[dict]] | None = None,
) -> dict:
    """Rematch or explicitly degrade when the owned sequence repeats a recent job.

    ``alternate_segments`` maps scene index to candidate dicts with
    ``asset_id`` / ``asset_segment_id`` / optional timing and score fields.
    """
    recent = {item for item in recent_signatures if item}
    signature = video_state.scene_asset_signature(scenes)
    result = {
        "signature": signature,
        "rematch_applied": False,
        "strategy": None,
        "inventory_limited": False,
        "quality_hold": False,
        "notes": [],
    }
    if not signature or signature not in recent:
        return result

    used_assets: set[int] = set()
    used_segments: set[int] = set()
    for scene in scenes:
        try:
            asset_id = int(scene.get("asset_id") or 0)
        except (TypeError, ValueError):
            asset_id = 0
        try:
            segment_id = int(scene.get("asset_segment_id") or 0)
        except (TypeError, ValueError):
            segment_id = 0
        if asset_id > 0:
            used_assets.add(asset_id)
        if segment_id > 0:
            used_segments.add(segment_id)

    for index, scene in enumerate(scenes):
        if _scene_is_locked_visual(scene):
            continue
        current_segment = 0
        try:
            current_segment = int(scene.get("asset_segment_id") or 0)
        except (TypeError, ValueError):
            current_segment = 0
        for alt in (alternate_segments or {}).get(index) or []:
            try:
                alt_asset = int(alt.get("asset_id") or 0)
                alt_segment = int(alt.get("asset_segment_id") or alt.get("segment_id") or 0)
            except (TypeError, ValueError):
                continue
            if alt_asset <= 0 or alt_segment <= 0:
                continue
            if alt_segment == current_segment:
                continue
            if alt_segment in used_segments or alt_asset in used_assets:
                continue
            old_asset = 0
            try:
                old_asset = int(scene.get("asset_id") or 0)
            except (TypeError, ValueError):
                old_asset = 0
            used_assets.discard(old_asset)
            used_segments.discard(current_segment)
            scene.update({
                "asset_id": alt_asset,
                "asset_segment_id": alt_segment,
                "asset_start_ms": alt.get("asset_start_ms", alt.get("start_ms", scene.get("asset_start_ms"))),
                "asset_end_ms": alt.get("asset_end_ms", alt.get("end_ms", scene.get("asset_end_ms"))),
                "match_score": alt.get("match_score", scene.get("match_score")),
                "match_reasons": alt.get("reasons") or alt.get("match_reasons") or ["近 20 条序列重复，已更换未使用镜头"],
                "asset_source": alt.get("asset_source") or scene.get("asset_source") or "owned_rematch",
            })
            used_assets.add(alt_asset)
            used_segments.add(alt_segment)
            current_segment = alt_segment
            result["rematch_applied"] = True
            result["strategy"] = "alternate_segment"
            signature = video_state.scene_asset_signature(scenes)
            if signature not in recent:
                result["signature"] = signature
                result["notes"].append("近 20 条完整素材序列重复，已重新选片")
                return result
            break

    for index in range(len(scenes) - 1, -1, -1):
        scene = scenes[index]
        if _scene_is_locked_visual(scene):
            continue
        _apply_text_card_fallback(scene, index, "完整序列与近 20 条重复，已强制替换为文字卡")
        scene["asset_source"] = "diversity_text_card"
        result["rematch_applied"] = True
        result["strategy"] = "text_card_degrade"
        result["inventory_limited"] = True
        signature = video_state.scene_asset_signature(scenes)
        result["signature"] = signature
        if signature not in recent:
            result["notes"].append("近 20 条序列重复且无可用替代镜头，已降级为文字卡")
            return result

    result["inventory_limited"] = True
    result["quality_hold"] = True
    result["signature"] = video_state.scene_asset_signature(scenes)
    result["notes"].append("库存无法给出不同素材序列，进入质量暂缓，禁止按相同画面出片")
    return result


def _apply_text_card_fallback(scene: dict, index: int, reason: str) -> dict:
    """Last-resort visual: keep a renderable brand/text card instead of failing the job."""
    scene.update({
        "asset_id": None,
        "asset_segment_id": None,
        "event_clip_id": None,
        "evidence_type": "brand_endcard",
        "scene_role": scene.get("scene_role") or "owned_context_image",
        "asset_source": "text_card_fallback",
        "brand_endcard_fallback": True,
        "match_score": 40,
        "match_reasons": [reason],
        "cooldown": False,
        "usage_count": 0,
    })
    return {
        "scene": index + 1,
        "score": 40,
        "hard_failures": [],
        "issues": [reason],
        "library_origin": "text_card_fallback",
        "asset_id": None,
        "usage_count": 0,
        "cooldown": False,
    }


def _asset_is_cooled(asset_id, usage_counts: dict[str, int], *, limit: int = 3) -> bool:
    key = str(asset_id or "").strip()
    if not key:
        return False
    return int(usage_counts.get(key) or 0) >= limit


_STAGE_PROGRESS = {
    PipelineStage.QUEUED: 0,
    PipelineStage.TOPIC_BRIEF: 4,
    PipelineStage.HOOK_LOCKING: 7,
    PipelineStage.SCRIPTING: 10,
    PipelineStage.PROJECT_BUILDING: 14,
    PipelineStage.PLANNING: 16,
    PipelineStage.SCRIPT_QUALITY_CHECK: 18,
    PipelineStage.ASSET_MATCHING: 30,
    PipelineStage.MATCH_QUALITY_CHECK: 45,
    PipelineStage.PREVIEW_RENDERING: 55,
    PipelineStage.PREVIEW_QUALITY_CHECK: 70,
    PipelineStage.FINAL_RENDERING: 78,
    PipelineStage.FINAL_QUALITY_CHECK: 95,
}


def render_progress_to_pipeline(render_progress: int | float | None, preview: bool) -> int:
    """将渲染子任务进度映射到外层视频流水线，避免项目页长期停在一个百分比。"""
    try:
        nested = max(0, min(100, int(render_progress or 0)))
    except (TypeError, ValueError):
        nested = 0
    start = _STAGE_PROGRESS[PipelineStage.PREVIEW_RENDERING if preview else PipelineStage.FINAL_RENDERING]
    end = _STAGE_PROGRESS[PipelineStage.PREVIEW_QUALITY_CHECK if preview else PipelineStage.FINAL_QUALITY_CHECK]
    return start + round((end - start) * nested / 100)


def _new_job_cancel_requested(job_id: str) -> bool:
    job = db.get_video_generation_job(job_id)
    return bool(job and job.get("status") in {"cancel_requested", "canceled"})


def resolve_brand_endcard_path(static_dir: Path, scene: dict) -> Path | None:
    """Return a safe existing CTA endcard path, without treating it as a video slot."""
    if str(scene.get("evidence_type") or "") != "brand_endcard":
        return None
    relative_path = str(scene.get("brand_endcard_path") or "").strip()
    if not relative_path:
        return None
    root = static_dir.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def build_default_handlers(static_dir: Path) -> dict[PipelineStage, StageHandler]:
    """Build the production pipeline using immutable revisions and existing render tools."""

    async def queued(job: dict):
        return PipelineStage.PLANNING

    async def planning(job: dict):
        revision = await asyncio.to_thread(
            db.get_video_project_revision, job["revision_id"], job["created_by"]
        )
        if not revision:
            raise RuntimeError("视频项目修订不存在")
        payload = revision["payload"]
        project = await asyncio.to_thread(
            db.get_video_project, job["project_id"], job["created_by"]
        )
        assets = await asyncio.to_thread(db.list_assets, None, None, None, "active")
        asset_ids = {asset["id"] for asset in assets}
        # 普通素材仍只允许 active；事件短片需要读取 inactive 的热点母片路径，
        # 但只通过 event_clip_id 使用指定时间范围，绝不会把母片全文带入成片。
        all_assets = await asyncio.to_thread(db.list_assets, None, None, None, None)
        asset_lookup = {int(asset["id"]): asset for asset in all_assets}
        events = await asyncio.to_thread(db.list_hotspot_event_clips)
        event_lookup = {int(event["id"]): event for event in events}
        issues: list[str] = []
        # 兼容旧版“视频跟进”项目：旧草稿没有保存分镜职责，也没有把
        # 首段事件片段写回 revision。这里在规划阶段补齐，避免用户只能
        # 删除项目重做，且不会改写原始 revision。
        planning_payload = dict(payload or {})
        planning_payload["source_type"] = (project or {}).get("source_type")
        snapshot = (project or {}).get("source_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except json.JSONDecodeError:
                snapshot = {}
        if not planning_payload.get("tts_provider"):
            planning_payload["tts_provider"] = (
                snapshot.get("tts_provider") or os.environ.get("TTS_PROVIDER", "mimo")
            )
        if not planning_payload.get("voice"):
            planning_payload["voice"] = snapshot.get("voice") or video_renderer.MIMO_TTS_VOICE
        planning_scenes = [dict(scene) for scene in planning_payload.get("scenes") or []]
        if (project or {}).get("source_type") == "hotspot_followup":
            hotspot_event_id = snapshot.get("hotspot_event_id")
            event = event_lookup.get(int(hotspot_event_id)) if hotspot_event_id else None
            for index, scene in enumerate(planning_scenes):
                if not scene.get("scene_role"):
                    scene["scene_role"] = (
                        "hotspot_hook" if index == 0 else
                        "brand_close" if index == len(planning_scenes) - 1 else
                        "brand_proof"
                    )
            if planning_scenes and event and not planning_scenes[0].get("event_clip_id"):
                planning_scenes[0].update({
                    "asset_id": event["asset_id"],
                    "event_clip_id": event["id"],
                    "asset_start_ms": event["start_ms"],
                    "asset_end_ms": event["end_ms"],
                })
        planning_scenes = _resequence_retry_scenes(
            planning_scenes,
            max(1, int(job.get("attempt") or 1)),
        )
        planning_payload["scenes"] = planning_scenes
        try:
            script = video_renderer.normalize_script(
                planning_payload,
                asset_ids,
                asset_lookup=asset_lookup,
                event_lookup=event_lookup,
                platform=str((project or {}).get("platform") or "douyin"),
                target_duration_ms=video_renderer.resolve_formal_video_target_ms(
                    project=project,
                    snapshot=snapshot if isinstance(snapshot, dict) else {},
                    payload=planning_payload,
                ),
            )
        except (ValueError, TypeError) as exc:
            script = payload
            issues.append(str(exc))
        report = dict(job.get("quality_report") or {})
        report.update({
            "script": script,
            "planning_issues": issues,
            "automatic_adjustments": (script.get("normalization") or {}).get("actions") or [],
        })
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"], progress=_STAGE_PROGRESS[PipelineStage.PLANNING],
            quality_report=report,
        )
        return PipelineStage.SCRIPT_QUALITY_CHECK

    async def script_quality(job: dict):
        report = dict(job.get("quality_report") or {})
        script = report.get("script") or {}
        scenes = script.get("scenes") if isinstance(script.get("scenes"), list) else []
        # 品牌 CTA 是固定结尾，不是内容分镜；它不能把 10 个有效内容镜头
        # 推成 11 个而触发“分镜过多”门禁。
        content_scenes = [
            scene for scene in scenes
            if str(scene.get("evidence_type") or "") != "brand_endcard"
        ]
        issues = list(report.get("planning_issues") or [])
        hard_failures = []
        target_ms = int(script.get("duration_target_ms") or 60_000)
        adapted = bool((script.get("adaptation") or {}).get("adapted"))
        min_scenes, max_scenes = video_renderer.formal_scene_bounds(target_ms, adapted=adapted)
        if not min_scenes <= len(content_scenes) <= max_scenes:
            hard_failures.append(f"当前时长需要 {min_scenes}–{max_scenes} 个完整分镜")
        if str(script.get("source_type") or "") == "topic_brief_dual_library":
            if adapted:
                if target_ms < 15_000 or target_ms > video_renderer.FORMAL_MAX_DURATION_MS:
                    hard_failures.append("自适应双素材成片时长超出可验收范围")
            elif not (
                video_renderer.FORMAL_MIN_DURATION_MS
                <= target_ms
                <= video_renderer.FORMAL_MAX_DURATION_MS
            ):
                hard_failures.append("正式双素材成片必须在 50–90 秒之间")
            if adapted:
                if not (4 <= len(content_scenes) <= video_renderer.FORMAL_MAX_SCENES):
                    hard_failures.append(
                        f"自适应双素材成片需要 4–"
                        f"{video_renderer.FORMAL_MAX_SCENES} 个完整分镜"
                    )
            elif not (
                video_renderer.FORMAL_MIN_SCENES
                <= len(content_scenes)
                <= video_renderer.FORMAL_MAX_SCENES
            ):
                hard_failures.append(
                    f"正式双素材成片需要 {video_renderer.FORMAL_MIN_SCENES}–"
                    f"{video_renderer.FORMAL_MAX_SCENES} 个完整分镜"
                )
        empty_voiceovers = [index + 1 for index, scene in enumerate(scenes) if not str(scene.get("voiceover") or "").strip()]
        if empty_voiceovers:
            for index in empty_voiceovers:
                scene = scenes[index - 1]
                scene["voiceover"] = video_state.fallback_voiceover_for_role(
                    scene, str(job.get("project_id") or script.get("title") or ""), index,
                )
                scene["copy_source"] = "fallback"
                scene["repair_reason"] = f"第{index}镜旁白为空，已使用确定性镜头模板"
                issues.append(scene["repair_reason"])
            script["scenes"] = scenes
            report["script"] = script
        infographic_scenes = [index + 1 for index, scene in enumerate(scenes) if is_explanation_scene(scene)]
        if infographic_scenes:
            issues.append(
                "信息图镜头将降级为文字卡/品牌兜底：第" + "、".join(map(str, infographic_scenes)) + "镜"
            )
        source_usage = source_usage_report(scenes)
        if not source_usage["passed"]:
            issues.extend(source_usage["issues"])
        if str(script.get("source_type") or "") == "topic_brief_dual_library":
            hard_failures.extend(formal_content_repetition_issues(content_scenes))
            brief = script.get("brief") if isinstance(script.get("brief"), dict) else {}
            contract = brief.get("topic_contract") if isinstance(brief.get("topic_contract"), dict) else {}
            if contract:
                topic = _script_quality_topic(brief, script)
                hook_event_ids = brief.get("approved_hook_event_ids") or []
                hook_events = []
                for event_id in hook_event_ids:
                    try:
                        event = db.get_hotspot_event_clip(int(event_id))
                    except (TypeError, ValueError):
                        event = None
                    if event:
                        hook_events.append(event)
                contract = video_topic_contract.build_topic_contract(
                    topic, has_event_anchor=bool(hook_events),
                )
                repaired_title = video_topic_contract.ensure_title_satisfies_contract(
                    str(script.get("title") or ""), contract,
                )
                if repaired_title and repaired_title != str(script.get("title") or ""):
                    script["title"] = repaired_title
                    report["script"] = script
                    await asyncio.to_thread(
                        db.update_video_generation_job, job["id"], quality_report=report,
                    )
                hook_binding_mode = str(brief.get("hook_binding_mode") or "exact")
                compatibility_issues = video_topic_contract.topic_hook_compatibility_issues(
                    topic, hook_events,
                )
                if hook_binding_mode == "exact" and compatibility_issues:
                    hook_binding_mode = "contextual_attention"
                    brief["hook_binding_mode"] = hook_binding_mode
                    script["brief"] = brief
                    chat_generation = dict(report.get("chat_generation") or {})
                    chat_generation["hook_binding_mode"] = hook_binding_mode
                    chat_generation["hook_binding_demoted"] = True
                    chat_generation["hook_binding_demote_reason"] = compatibility_issues[:2]
                    report["chat_generation"] = chat_generation
                    report["script"] = script
                    await asyncio.to_thread(
                        db.update_video_generation_job, job["id"], quality_report=report,
                    )
                contract_errors = video_topic_contract.validate_generated_topic_contract(script, contract)
                sentence_errors = video_topic_contract.incomplete_sentence_issues(script)
                opening_errors = [
                    error for error in contract_errors if "主题型开场" in str(error)
                ]
                if opening_errors and (script.get("scenes") or []):
                    opening_hook = str(contract.get("opening_hook") or "").strip()
                    if opening_hook:
                        if opening_hook[-1] not in "。！？；":
                            opening_hook = opening_hook.rstrip("，,") + "。"
                        first_scene = dict(script["scenes"][0])
                        first_scene["voiceover"] = opening_hook
                        first_scene["text_overlay"] = opening_hook.rstrip("。！？；")[:24]
                        first_scene["copy_source"] = "policy_repair"
                        first_scene["copy_repair_reason"] = "owned_topic_opening"
                        script["scenes"][0] = first_scene
                        report["script"] = script
                        await asyncio.to_thread(
                            db.update_video_generation_job, job["id"], quality_report=report,
                        )
                        contract_errors = video_topic_contract.validate_generated_topic_contract(
                            script, contract,
                        )
                recoverable = contract_errors and all(
                    str(error).startswith("标题缺少主题要素") for error in contract_errors
                )
                if recoverable:
                    script["title"] = repaired_title or str(contract.get("safe_title") or topic)
                    report["script"] = script
                    await asyncio.to_thread(
                        db.update_video_generation_job, job["id"], quality_report=report,
                    )
                    contract_errors = video_topic_contract.validate_generated_topic_contract(
                        script, contract,
                    )
                if sentence_errors:
                    script, repair_notes = video_state.repair_incomplete_scenes(
                        script, seed=str(job.get("id") or job.get("project_id") or topic),
                    )
                    report["script"] = script
                    issues.extend(repair_notes or sentence_errors)
                    remaining_sentences = video_topic_contract.incomplete_sentence_issues(script)
                    if remaining_sentences:
                        issues.extend(remaining_sentences)
                hard_failures.extend(contract_errors)
                if contract.get("opening_mode") == "owned_topic_hook" and hook_binding_mode == "exact":
                    if any(str(scene.get("evidence_type") or "") == "hotspot_video" for scene in content_scenes):
                        hard_failures.append("非事件主题禁止使用热点新闻画面")
                    if not content_scenes or str(content_scenes[0].get("scene_role") or "") != "topic_hook":
                        hard_failures.append("非事件主题第一镜必须是主题型开场")
        if hard_failures:
            logger.warning(
                "脚本质量门禁未通过: job=%s project=%s failures=%s",
                job.get("id"),
                job.get("project_id"),
                hard_failures,
            )
        issues.extend(hard_failures)
        score = max(0, 100 - len(issues) * 30)
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"],
            progress=_STAGE_PROGRESS[PipelineStage.SCRIPT_QUALITY_CHECK],
        )
        return route_script_quality(
            {"score": score, "hard_failures": hard_failures, "issues": issues}
        )

    async def asset_matching(job: dict):
        report = dict(job.get("quality_report") or {})
        script = dict(report.get("script") or {})
        scenes = [dict(scene) for scene in script.get("scenes") or []]
        matchable_indexes = [
            index for index, scene in enumerate(scenes)
            if not scene.get("event_clip_id")
            and not (scene.get("asset_id") and scene.get("asset_segment_id"))
            and str(scene.get("evidence_type") or "") != "image"
            and str(scene.get("evidence_type") or "") != "brand_endcard"
        ]
        payload = {
            "scenes": [scenes[index] for index in matchable_indexes],
            "orientation": "portrait",
        }
        atoms = semantic_matching.build_semantic_atoms(payload)
        # Prefer dynamic Buffalo clips.  When the inventory cannot provide a
        # unique video segment, a unique owned still is the deterministic
        # availability fallback explicitly allowed by the production policy.
        segments = [
            item for item in await asyncio.to_thread(db.list_asset_segments, None, "active", 1000)
            if not item.get("asset_hotspot_id") and item.get("asset_file_type") == "video"
        ]
        assignments = semantic_matching.assign_candidates(
            atoms,
            segments,
            top_k=10,
            required_file_type="video",
        )
        owned_images = [
            item for item in await asyncio.to_thread(
                db.list_assets, "image", None, None, "active"
            )
            if not item.get("hotspot_id")
        ]
        assignment_by_scene = {
            scene_index: assignments[index] if index < len(assignments) else {"candidates": []}
            for index, scene_index in enumerate(matchable_indexes)
        }
        scene_reports = []
        used_owned_asset_ids: set[int] = set()
        used_segment_ids: set[int] = set()
        used_image_asset_ids: set[int] = set()
        recent_signatures = await asyncio.to_thread(db.list_recent_succeeded_asset_signatures, 20)
        usage_counts = await asyncio.to_thread(db.recent_asset_usage_counts, 10)
        inventory_limited = False
        last_evidence_type = ""
        for index, scene in enumerate(scenes):
            if str(scene.get("evidence_type") or "") == "brand_endcard":
                if resolve_brand_endcard_path(static_dir, scene):
                    scene_reports.append({
                        "scene": index + 1, "score": 100, "hard_failures": [], "issues": [],
                        "library_origin": "brand_endcard",
                    })
                else:
                    scene_reports.append({
                        "scene": index + 1, "score": 0,
                        "hard_failures": ["品牌 CTA 结尾图不存在或路径不安全"], "issues": [],
                    })
                continue
            if is_explanation_scene(scene) and str(scene.get("asset_source") or "") != "text_card_fallback":
                scene_reports.append(_apply_text_card_fallback(
                    scene, index, "信息图镜头已降级为文字卡/品牌兜底",
                ))
                last_evidence_type = "brand_endcard"
                continue
            if scene.get("evidence_type") == "image":
                asset_id = int(scene.get("asset_id") or 0)
                asset = await asyncio.to_thread(db.get_asset, asset_id) if asset_id else None
                if not asset or asset.get("file_type") != "image" or asset.get("hotspot_id"):
                    scene_reports.append({
                        "scene": index + 1, "score": 0,
                        "hard_failures": ["上下文图片不存在、不是自有图片，或未经热点 Hook 确认"], "issues": [],
                    })
                elif asset_id in used_image_asset_ids:
                    scene_reports.append({
                        "scene": index + 1, "score": 0,
                        "hard_failures": ["同一张 Buffalo 静态图片在全片重复使用"], "issues": [],
                    })
                else:
                    used_image_asset_ids.add(asset_id)
                    scene_reports.append({
                        "scene": index + 1, "score": 100, "hard_failures": [], "issues": [],
                        "library_origin": "owned_context_image",
                    })
                continue
            # 已由用户选择的热点事件片段不能被通用候选匹配覆盖。
            if scene.get("event_clip_id"):
                event = await asyncio.to_thread(
                    db.get_hotspot_event_clip, int(scene["event_clip_id"])
                )
                if not event or int(event.get("asset_id") or 0) != int(scene.get("asset_id") or 0):
                    scene_reports.append({
                        "scene": index + 1, "score": 0,
                        "hard_failures": ["热点事件片段与母片不匹配"], "issues": [],
                    })
                else:
                    event_start, event_end = int(event["start_ms"]), int(event["end_ms"])
                    requested_start = int(scene.get("asset_start_ms") or event_start)
                    requested_end = int(scene.get("asset_end_ms") or event_end)
                    if event_start <= requested_start < requested_end <= event_end:
                        selected_start, selected_end = requested_start, requested_end
                    else:
                        selected_start, selected_end = event_start, event_end
                    scene.update({
                        "asset_start_ms": selected_start,
                        "asset_end_ms": selected_end,
                        "clip_ref": {
                            "library_origin": "hotspot_event",
                            "asset_id": event["asset_id"],
                            "event_clip_id": event["id"],
                            "start_ms": selected_start,
                            "end_ms": selected_end,
                            "duration_ms": selected_end - selected_start,
                        },
                    })
                    scene_reports.append({
                        "scene": index + 1, "score": 100,
                        "hard_failures": [], "issues": [], "library_origin": "hotspot_event",
                    })
                continue
            if scene.get("asset_id") and scene.get("asset_segment_id"):
                segment = await asyncio.to_thread(db.get_asset_segment, int(scene["asset_segment_id"]))
                asset_id = int(scene.get("asset_id") or 0)
                if not segment or int(segment.get("asset_id") or 0) != asset_id:
                    scene_reports.append({
                        "scene": index + 1, "score": 0,
                        "hard_failures": ["已规划的 Buffalo 镜头不存在或不属于该素材"], "issues": [],
                    })
                elif asset_id in used_owned_asset_ids or int(segment["id"]) in used_segment_ids:
                    scene_reports.append({
                        "scene": index + 1, "score": 0,
                        "hard_failures": ["Buffalo 原始视频或 asset_segment_id 在全片重复使用"], "issues": [],
                    })
                else:
                    used_owned_asset_ids.add(asset_id)
                    used_segment_ids.add(int(segment["id"]))
                    scene_reports.append({
                        "scene": index + 1, "score": 100, "hard_failures": [], "issues": [],
                        "library_origin": "za_stock" if str(scene.get("asset_source") or "") == "za_stock_license" else "owned",
                    })
                continue
            assignment = assignment_by_scene.get(index, {"candidates": []})
            candidates = assignment.get("candidates") or []
            selected = None
            segment = None
            cooled_skipped = 0
            for candidate in candidates:
                candidate_segment = await asyncio.to_thread(db.get_asset_segment, candidate["segment_id"])
                if not candidate_segment:
                    continue
                asset_id = int(candidate_segment.get("asset_id") or 0)
                segment_id = int(candidate_segment.get("id") or 0)
                if asset_id in used_owned_asset_ids or segment_id in used_segment_ids:
                    continue
                if _asset_is_cooled(asset_id, usage_counts):
                    cooled_skipped += 1
                    continue
                selected, segment = candidate, candidate_segment
                break
            if not selected or not segment:
                fallback_image = next(
                    (
                        item for item in owned_images
                        if int(item.get("id") or 0) not in used_image_asset_ids
                        and not _asset_is_cooled(item.get("id"), usage_counts)
                    ),
                    None,
                )
                if not fallback_image:
                    fallback_image = next(
                        (
                            item for item in owned_images
                            if int(item.get("id") or 0) not in used_image_asset_ids
                        ),
                        None,
                    )
                    if fallback_image:
                        inventory_limited = True
                if fallback_image:
                    image_id = int(fallback_image["id"])
                    used_image_asset_ids.add(image_id)
                    scene.update({
                        "asset_id": image_id,
                        "asset_segment_id": None,
                        "asset_start_ms": 0,
                        "asset_end_ms": int(scene.get("duration_ms") or 6_000),
                        "evidence_type": "image",
                        "scene_role": "owned_context_image",
                        "asset_source": "owned_image_fallback",
                        "match_score": 70,
                        "match_reasons": ["动态素材不足，使用唯一 Buffalo 自有图片兜底"],
                        "usage_count": usage_counts.get(str(image_id), 0),
                        "cooldown": _asset_is_cooled(image_id, usage_counts),
                    })
                    scene_reports.append({
                        "scene": index + 1, "score": 70,
                        "hard_failures": [],
                        "issues": ["动态素材不足，已自动使用 Buffalo 自有图片"],
                        "library_origin": "owned_context_image",
                        "asset_id": image_id,
                        "usage_count": usage_counts.get(str(image_id), 0),
                        "cooldown": _asset_is_cooled(image_id, usage_counts),
                    })
                    last_evidence_type = "image"
                else:
                    inventory_limited = True
                    scene_reports.append(_apply_text_card_fallback(
                        scene, index,
                        "相关视频和图片均不可用，已使用文字卡/品牌兜底" + (
                            f"；跳过 {cooled_skipped} 个冷却素材" if cooled_skipped else ""
                        ),
                    ))
                    last_evidence_type = "brand_endcard"
                continue
            if last_evidence_type == "video" and str(segment.get("asset_file_type") or "video") == "video":
                # Prefer type rotation when a still image remains.
                rotated = next(
                    (
                        item for item in owned_images
                        if int(item.get("id") or 0) not in used_image_asset_ids
                        and not _asset_is_cooled(item.get("id"), usage_counts)
                    ),
                    None,
                )
                if rotated:
                    image_id = int(rotated["id"])
                    used_image_asset_ids.add(image_id)
                    scene.update({
                        "asset_id": image_id,
                        "asset_segment_id": None,
                        "evidence_type": "image",
                        "scene_role": "owned_context_image",
                        "asset_source": "type_rotation",
                        "match_score": 72,
                        "usage_count": usage_counts.get(str(image_id), 0),
                        "cooldown": False,
                    })
                    scene_reports.append({
                        "scene": index + 1, "score": 72, "hard_failures": [],
                        "issues": ["为避免连续视频镜头，已轮换为图片"],
                        "library_origin": "owned_context_image",
                        "asset_id": image_id,
                        "usage_count": usage_counts.get(str(image_id), 0),
                        "cooldown": False,
                    })
                    last_evidence_type = "image"
                    continue
            used_owned_asset_ids.add(int(segment["asset_id"]))
            used_segment_ids.add(int(segment["id"]))
            scene.update({
                "asset_id": segment["asset_id"],
                "asset_segment_id": segment["id"],
                "asset_start_ms": segment["start_ms"],
                "asset_end_ms": segment["end_ms"],
                "match_score": selected["match_score"],
                "match_reasons": selected["reasons"],
                "usage_count": usage_counts.get(str(segment["asset_id"]), 0),
                "cooldown": _asset_is_cooled(segment["asset_id"], usage_counts),
            })
            scene_reports.append({
                "scene": index + 1,
                "score": selected["match_score"],
                "hard_failures": [],
                "issues": ["匹配证据偏弱"] if selected.get("review_required") else [],
                "asset_id": segment["asset_id"],
                "usage_count": usage_counts.get(str(segment["asset_id"]), 0),
                "cooldown": _asset_is_cooled(segment["asset_id"], usage_counts),
            })
            last_evidence_type = "video"
        script["scenes"] = scenes
        signature = video_state.scene_asset_signature(scenes)
        rematch = {
            "signature": signature,
            "rematch_applied": False,
            "strategy": None,
            "inventory_limited": False,
            "quality_hold": False,
            "notes": [],
        }
        if signature and signature in recent_signatures:
            alternate_segments: dict[int, list[dict]] = {}
            for index, scene in enumerate(scenes):
                assignment = assignment_by_scene.get(index, {"candidates": []})
                alts = []
                for candidate in assignment.get("candidates") or []:
                    candidate_segment = await asyncio.to_thread(
                        db.get_asset_segment, candidate["segment_id"]
                    )
                    if not candidate_segment:
                        continue
                    alts.append({
                        "asset_id": candidate_segment.get("asset_id"),
                        "asset_segment_id": candidate_segment.get("id"),
                        "asset_start_ms": candidate_segment.get("start_ms"),
                        "asset_end_ms": candidate_segment.get("end_ms"),
                        "match_score": candidate.get("match_score"),
                        "reasons": candidate.get("reasons"),
                    })
                if alts:
                    alternate_segments[index] = alts
            rematch = diversify_repeated_asset_sequence(
                scenes, recent_signatures, alternate_segments=alternate_segments,
            )
            signature = rematch["signature"]
            script["scenes"] = scenes
            inventory_limited = inventory_limited or bool(rematch.get("inventory_limited"))
            for note in rematch.get("notes") or []:
                scene_reports.append({
                    "scene": "全片",
                    "score": 40 if rematch.get("quality_hold") else 70,
                    "hard_failures": [],
                    "issues": [note],
                })
            if rematch.get("quality_hold"):
                report["quality_hold_reason"] = "asset_sequence_exhausted"
        usage = source_usage_report(scenes)
        if not usage["passed"]:
            # Repeated source is a quality hold, not a reason to discard an already
            # matched timeline. The renderer still has a concrete shot list.
            scene_reports.append({
                "scene": "全片", "score": 40, "hard_failures": [],
                "issues": usage["issues"],
            })
            inventory_limited = True
        report.update({
            "script": script,
            "matches": assignments,
            "match_scenes": scene_reports,
            "source_usage": usage,
            "copy_provenance": copy_provenance_report(scenes),
            "scene_asset_signature": signature,
            "asset_diversity": {
                "signature": signature,
                "recent_signature_hit": bool(
                    rematch.get("quality_hold")
                    or (signature and signature in recent_signatures and not rematch.get("rematch_applied"))
                ),
                "rematch_applied": bool(rematch.get("rematch_applied")),
                "rematch_strategy": rematch.get("strategy"),
                "inventory_limited": inventory_limited,
                "quality_hold": bool(rematch.get("quality_hold")),
                "notes": rematch.get("notes") or [],
                "scenes": [
                    {
                        "scene": item.get("scene"),
                        "asset_id": item.get("asset_id"),
                        "library_origin": item.get("library_origin"),
                        "usage_count": item.get("usage_count"),
                        "cooldown": item.get("cooldown"),
                    }
                    for item in scene_reports if item.get("scene") != "全片"
                ],
            },
        })
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"],
            progress=_STAGE_PROGRESS[PipelineStage.ASSET_MATCHING], quality_report=report,
        )
        return PipelineStage.MATCH_QUALITY_CHECK

    async def match_quality(job: dict):
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"],
            progress=_STAGE_PROGRESS[PipelineStage.MATCH_QUALITY_CHECK],
        )
        report = dict(job.get("quality_report") or {})
        scene_reports = report.get("match_scenes") or []
        script = dict(report.get("script") or {})
        evidence_issues = hotspot_evidence_gate(script)
        usage = source_usage_report(script.get("scenes") or [])
        evidence_issues.extend(usage["issues"])
        report["evidence_gate"] = {
            "passed": not evidence_issues,
            "issues": list(dict.fromkeys(evidence_issues)),
        }
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"], quality_report=report
        )
        if evidence_issues:
            return QualityDecision(
                JobStatus.NEEDS_REVIEW,
                PipelineStage.MATCH_QUALITY_CHECK,
                min((_score(item) for item in scene_reports), default=0),
                list(dict.fromkeys(evidence_issues)),
            )
        if script.get("tier") == "internal_preview":
            scores = [_score(item) for item in scene_reports] or [0]
            issues = []
            for index, item in enumerate(scene_reports, 1):
                for issue in item.get("hard_failures") or item.get("issues") or []:
                    issues.append(f"第{item.get('scene', index)}镜头：{issue}")
            publication = quality_decision(
                min(scores), "internal_preview", list(dict.fromkeys(issues))
            )
            script["watermark"] = publication["watermark"]
            report.update({"script": script, "publication": publication})
            await asyncio.to_thread(
                db.update_video_generation_job,
                job["id"],
                quality_report=report,
            )
            return QualityDecision(
                JobStatus.RUNNING,
                PipelineStage.PREVIEW_RENDERING,
                publication["score"],
                publication["issues"],
            )
        decision = route_match_quality(scene_reports)
        return decision

    async def render_version(job: dict, preview: bool):
        report = dict(job.get("quality_report") or {})
        script = dict(report.get("script") or {})
        project = await asyncio.to_thread(
            db.get_video_project, job["project_id"], job["created_by"]
        )
        snapshot = (project or {}).get("source_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except json.JSONDecodeError:
                snapshot = {}
        requested_provider = str(
            script.get("tts_provider")
            or snapshot.get("tts_provider")
            or os.environ.get("TTS_PROVIDER", "mimo")
        )
        requested_voice = str(script.get("voice") or snapshot.get("voice") or "")
        try:
            tts_provider, voice = video_renderer.resolve_tts_selection(
                requested_provider, requested_voice, strict=True,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        script["tts_provider"] = tts_provider
        script["voice"] = voice
        report["script"] = script
        legacy = await asyncio.to_thread(db.get_render_job, job["id"])
        if not legacy:
            await asyncio.to_thread(db.create_render_job, job["id"], script, voice, job["created_by"])
        else:
            await asyncio.to_thread(
                db.update_render_job, job["id"], status="pending", progress=0,
                stage="等待渲染", error=None, output_path=None, voice=voice, script=script,
            )
        output_size = (540, 960) if preview else (1080, 1920)
        output_name = f"preview-{job['id']}.mp4" if preview else f"douyin-{job['id']}.mp4"
        render_task = asyncio.create_task(asyncio.to_thread(
            video_renderer.render_job, job["id"], static_dir,
            lambda: _new_job_cancel_requested(job["id"]), output_size, output_name,
            tts_provider, preview,
        ))
        last_reported_progress = -1
        polls_since_renew = 0
        while not render_task.done():
            # 轮询间隔拉长、且仅在进度真的变化/需要续约时才写库：渲染期间
            # 高频短写会在 WAL 模式下触发频繁 checkpoint，进而造成
            # "database is locked"。续约周期（下面每 5 轮一次，约 10s）
            # 仍远小于 30s 租约，不会导致任务被其他 worker 抢占。
            await asyncio.sleep(2.0)
            rendered_snapshot = await asyncio.to_thread(db.get_render_job, job["id"])
            if not rendered_snapshot:
                continue
            polls_since_renew += 1
            current_progress = int(rendered_snapshot.get("progress") or 0)
            progress_changed = current_progress != last_reported_progress
            should_renew = polls_since_renew >= 5
            if not progress_changed and not should_renew:
                continue
            latest_job = await asyncio.to_thread(db.get_video_generation_job, job["id"])
            if progress_changed:
                progress_report = dict((latest_job or job).get("quality_report") or {})
                progress_report["render_progress"] = {
                    "stage": rendered_snapshot.get("stage") or "正在渲染",
                    "progress": current_progress,
                    "preview": preview,
                }
                await asyncio.to_thread(
                    db.update_video_generation_job,
                    job["id"],
                    progress=render_progress_to_pipeline(rendered_snapshot.get("progress"), preview),
                    quality_report=progress_report,
                )
                last_reported_progress = current_progress
            # 渲染可能远超默认 30 秒租约；定期续约以避免其他 worker 重复领取任务。
            if should_renew:
                lease_owner = (latest_job or {}).get("lease_owner")
                if lease_owner:
                    await asyncio.to_thread(db.renew_video_generation_lease, job["id"], lease_owner, 30)
                polls_since_renew = 0
        await render_task
        rendered = await asyncio.to_thread(db.get_render_job, job["id"])
        if rendered.get("status") == "canceled":
            raise GenerationCanceled("视频生成已取消")
        if rendered.get("status") != "succeeded":
            raise RuntimeError(rendered.get("error") or "视频渲染失败")
        path_field = "preview_path" if preview else "output_path"
        latest_job = await asyncio.to_thread(db.get_video_generation_job, job["id"])
        report = dict((latest_job or job).get("quality_report") or {})
        report.pop("render_progress", None)
        rendered_quality = dict(rendered.get("quality_report") or {})
        copy_provenance = report.get("copy_provenance") or copy_provenance_report(
            script.get("scenes") or []
        )
        rendered_quality["copy_provenance"] = copy_provenance
        report["copy_provenance"] = copy_provenance
        report["preview_quality" if preview else "final_quality"] = rendered_quality
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"],
            **{
                path_field: rendered["output_path"],
                "quality_report": report,
                "progress": _STAGE_PROGRESS[
                    PipelineStage.PREVIEW_RENDERING if preview else PipelineStage.FINAL_RENDERING
                ],
            },
        )

    async def preview_rendering(job: dict):
        await render_version(job, preview=True)
        return PipelineStage.PREVIEW_QUALITY_CHECK

    async def preview_quality(job: dict):
        full_report = dict(job.get("quality_report") or {})
        report = full_report.get("preview_quality") or {}
        prior_gate = full_report.get("gate") if isinstance(full_report.get("gate"), dict) else {}
        try:
            repair_attempts = max(0, int(prior_gate.get("repair_attempts") or 0))
        except (TypeError, ValueError):
            repair_attempts = 0
        checks = report.get("checks") or {}
        issues = [name for name, passed in checks.items() if not passed]
        script = full_report.get("script") or {}
        formal_duration_issue = _formal_dual_library_duration_issue(report, script)
        if formal_duration_issue:
            issues.append(formal_duration_issue)
        technical_decision = route_preview_quality({
            "score": 100 if report.get("status") == "passed" else 0,
            "hard_failures": issues,
            "issues": issues,
        }, repair_attempts=repair_attempts)
        if not (
            technical_decision.status is JobStatus.RUNNING
            and technical_decision.stage is PipelineStage.FINAL_RENDERING
        ):
            return technical_decision

        semantic_decision = technical_decision
        if os.environ.get("VIDEO_QUALITY_ENABLED", "1").strip().lower() not in {"0", "false", "off"}:
            project = await asyncio.to_thread(db.get_video_project, job["project_id"])
            snapshot = (project or {}).get("source_snapshot") or {}
            if isinstance(snapshot, str):
                try:
                    snapshot = json.loads(snapshot)
                except json.JSONDecodeError:
                    snapshot = {}
            original_prompt = str(
                snapshot.get("instruction")
                or snapshot.get("topic")
                or script.get("title")
                or (project or {}).get("title")
                or ""
            )
            quality_storyboard = {
                **script,
                "render_timeline": report.get("render_timeline")
                or (report.get("final_subtitle_timeline") or {}).get("timeline")
                or [],
                "renderer_contract": {
                    "subtitle_audio_sync": report.get("audio_sync") or {},
                    "transition_audio_video_sync": report.get("transition_audio_sync") or {},
                },
            }
            quality_request = VideoQualityInput(
                video_source=str(static_dir / str(job.get("preview_path") or "")),
                original_prompt=original_prompt,
                storyboard=quality_storyboard,
                reference_images=[str(item) for item in script.get("reference_images") or []],
                target_platform=str((project or {}).get("platform") or "douyin"),
                mode="balanced",
                # Deterministic per-scene checks already cover the full
                # timeline. Twenty-four global frames leave budget for a
                # focused review of any real risk window.
                max_frames=24,
                auto_regenerate=False,
            )
            try:
                # P3-A: resume 重生成携带前序质检历史，护栏（达上限/下滑/提升不足）生效
                quality_history = await asyncio.to_thread(
                    collect_prior_quality_history, job,
                ) if job.get("prior_job_id") else []
                result = await video_quality_service.run_quality_mvp(
                    quality_request,
                    static_dir / "uploads" / "video-quality" / job["id"],
                    # Script planning and visual review use different models and
                    # token profiles. Keep their budgets isolated while retaining
                    # one auditable quality budget for both global/focused passes.
                    job_id=quality_budget_job_id(job["id"]),
                    allowed_roots=[static_dir],
                    history=quality_history,
                    cancel_check=lambda: _new_job_cancel_requested(job["id"]),
                )
                evaluation_report = _filter_expected_dual_library_editorial_notes(
                    result["report"], script,
                )
                full_report["video_evaluation"] = evaluation_report
                full_report["video_technical"] = result.get("technical_report") or {}
                full_report["video_quality_artifacts"] = result["manifest"]["artifacts"]
                full_report["regeneration_decision"] = result["regeneration_decision"]
                semantic_decision = route_video_evaluation_quality(evaluation_report)
            except GenerationCanceled:
                raise
            except Exception as exc:
                if is_recoverable_temporal_evidence_error(exc):
                    # The evaluator supplied no valid visual defect, only a temporal
                    # assertion that its own detector evidence rejects. Preview hard
                    # gates have already passed above, so keep the recovery explicit
                    # and auditable instead of silently demoting a valid delivery.
                    recovered = {
                        "evaluation_status": "completed",
                        "overall_score": 80,
                        "passed": True,
                        "summary": "已忽略无技术候选支撑的时间序列质检误报；预览技术门禁通过。",
                        "issues": [],
                        "technical_issues": [],
                        "policy_recovered": True,
                        "recovered_error": str(exc)[:500],
                    }
                    full_report["video_evaluation"] = recovered
                    semantic_decision = route_video_evaluation_quality(recovered)
                else:
                    unavailable = {
                        "evaluation_status": "unavailable",
                        "overall_score": 0,
                        "passed": False,
                        "summary": "视频质检未完成（服务暂不可用）",
                        "issues": [],
                        "error": str(exc)[:500],
                    }
                    full_report["video_evaluation"] = unavailable
                    semantic_decision = route_video_evaluation_quality(unavailable)
            await asyncio.to_thread(
                db.update_video_generation_job,
                job["id"],
                quality_report=full_report,
            )
        evaluation_report = full_report.get("video_evaluation") or {}
        internal_preview = script.get("tier") == "internal_preview"
        if internal_preview and semantic_decision.status is JobStatus.RUNNING:
            publication = dict(full_report.get("publication") or {})
            publication.update({
                "tier": "internal_preview",
                "publish_allowed": False,
                "technical_preview_passed": True,
                "semantic_preview_passed": semantic_decision.status is JobStatus.RUNNING,
                "review_mode": "internal_preview",
            })
            full_report["publication"] = publication
            await asyncio.to_thread(
                db.update_video_generation_job,
                job["id"],
                output_path=job.get("preview_path"),
                quality_report=full_report,
            )
            return PipelineStage.SUCCEEDED
        if semantic_decision.status is JobStatus.NEEDS_REVIEW:
            # Semantic review is important for publication, but it runs after
            # the deterministic preview gates have already proved that the MP4
            # is decodable, audible, timed and source-safe.  Do not discard that
            # valid production or loop the same immutable revision.  Finish the
            # high-resolution render and expose an explicit publication hold.
            publication = dict(full_report.get("publication") or {})
            publication.update({
                "tier": "quality_hold",
                "publish_allowed": False,
                "manual_acceptance_required": True,
                "review_mode": "manual_preview",
                "technical_preview_passed": True,
                "semantic_preview_passed": False,
                "semantic_issues": list(semantic_decision.issues),
            })
            full_report["publication"] = publication
            await asyncio.to_thread(
                db.update_video_generation_job,
                job["id"],
                quality_report=full_report,
            )
            return PipelineStage.FINAL_RENDERING
        return semantic_decision

    async def final_rendering(job: dict):
        await render_version(job, preview=False)
        return PipelineStage.FINAL_QUALITY_CHECK

    async def final_quality(job: dict):
        report = (job.get("quality_report") or {}).get("final_quality") or {}
        checks = report.get("checks") or {}
        required_final_checks = (
            "no_repeated_source_or_range",
            "final_subtitle_timeline_aligned",
            "transition_audio_video_sync",
            "tts_audio_unique",
        )
        missing_or_failed = [name for name in required_final_checks if checks.get(name) is not True]
        if report.get("status") != "passed" or missing_or_failed:
            detail = "、".join(missing_or_failed) or "基础技术检查"
            raise RuntimeError(f"最终成片未通过质量检查：{detail}")
        await asyncio.to_thread(
            db.update_video_generation_job, job["id"],
            progress=_STAGE_PROGRESS[PipelineStage.FINAL_QUALITY_CHECK],
        )
        return PipelineStage.SUCCEEDED

    return {
        PipelineStage.QUEUED: queued,
        PipelineStage.PLANNING: planning,
        PipelineStage.SCRIPT_QUALITY_CHECK: script_quality,
        PipelineStage.ASSET_MATCHING: asset_matching,
        PipelineStage.MATCH_QUALITY_CHECK: match_quality,
        PipelineStage.PREVIEW_RENDERING: preview_rendering,
        PipelineStage.PREVIEW_QUALITY_CHECK: preview_quality,
        PipelineStage.FINAL_RENDERING: final_rendering,
        PipelineStage.FINAL_QUALITY_CHECK: final_quality,
    }


async def worker_loop(
    stop_event: asyncio.Event,
    handlers: dict[PipelineStage, StageHandler],
    poll_seconds: float = 0.5,
    lease_seconds: int = 30,
) -> None:
    owner = lease_owner_identity()
    logger.info("视频生成 worker 已启动 owner=%s", owner)
    await asyncio.to_thread(db.recover_expired_video_generation_jobs)
    while not stop_event.is_set():
        job = await asyncio.to_thread(db.claim_next_video_generation_job, owner, lease_seconds)
        if job:
            await run_claimed_job(job, owner, handlers, lease_seconds)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass
