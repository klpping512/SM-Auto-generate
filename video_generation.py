"""Persistent, quality-gated video generation state machine and worker shell."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import database as db
import semantic_matching
import video_renderer
from video_composition_policy import is_explanation_scene, source_usage_report
import video_quality.service as video_quality_service
from video_quality.schemas import VideoQualityInput
from video_quality.video_evaluator import is_recoverable_temporal_evidence_error


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


def formal_content_repetition_issues(scenes: list[dict]) -> list[str]:
    """Reject a formal video when narration or visible actions are repetitive."""
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
    low_risk_categories = {
        "camera_quality", "temporal_consistency", "subtitle_audio_quality",
    }
    # A real-source video can retain small editorial notes such as handheld
    # motion or a transition preference. If every technical gate has passed,
    # Qwen found no high/medium defect, and its score is at least 75, do not
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
    if (
        evaluation_status == "completed"
        and bool(report.get("passed"))
        and score >= threshold
        and not high_issues
    ) or low_risk_editorial_only or (
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
        for item in (high_issues or issues)
    ]
    if not descriptions:
        descriptions = [
            "Qwen 视频质检不可用，请人工检查预览"
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
    kept, ignored = [], []
    for issue in report.get("issues") or []:
        category = str(issue.get("category") or "")
        description = str(issue.get("description") or "")
        expected_hook_pair = category == "temporal_consistency" and "同一热点的不同片段" in description
        expected_cta = category == "storytelling" and "品牌CTA" in description and "静态图片" in description
        (ignored if expected_hook_pair or expected_cta else kept).append(issue)
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
                # 阶段处理器可能已经写入完整报告（例如 Qwen 质检产物）。
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
                    await asyncio.to_thread(
                        db.add_video_generation_event,
                        job["id"], "needs_review", "质量门禁需要人工确认",
                        {"stage": result.stage.value, "issues": result.issues},
                    )
                    return
                target = result.stage
            else:
                target = PipelineStage(result) if result else current
                if target != current:
                    validate_transition(current, target)
                    if target is PipelineStage.SUCCEEDED:
                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        await asyncio.to_thread(
                            db.update_video_generation_job,
                            job["id"], status=JobStatus.SUCCEEDED.value,
                            stage=target.value, progress=100, finished_at=now,
                            lease_owner=None, lease_expires_at=None,
                        )
                        await asyncio.to_thread(
                            db.add_video_generation_event,
                            job["id"], "succeeded", "视频已通过全部质量检查",
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
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        await asyncio.to_thread(
            db.update_video_generation_job,
            job["id"],
            status=JobStatus.FAILED.value,
            stage=PipelineStage.FAILED.value,
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
            lease_owner=None,
            lease_expires_at=None,
        )
        await asyncio.to_thread(
            db.add_video_generation_event, job["id"], "failed", "视频生成失败",
            {"error": str(exc)[:500]},
        )


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
            planning_payload["tts_provider"] = snapshot.get("tts_provider") or "mimo"
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
        planning_payload["scenes"] = planning_scenes
        try:
            script = video_renderer.normalize_script(
                planning_payload,
                asset_ids,
                asset_lookup=asset_lookup,
                event_lookup=event_lookup,
                platform=str((project or {}).get("platform") or "douyin"),
                target_duration_ms=int((project or {}).get("target_duration_ms") or 60_000),
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
        issues = list(report.get("planning_issues") or [])
        hard_failures = []
        target_ms = int(script.get("duration_target_ms") or 60_000)
        adapted = bool((script.get("adaptation") or {}).get("adapted"))
        min_scenes, max_scenes = video_renderer.formal_scene_bounds(target_ms, adapted=adapted)
        if not min_scenes <= len(scenes) <= max_scenes:
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
                if not (4 <= len(scenes) <= video_renderer.FORMAL_MAX_SCENES):
                    hard_failures.append(
                        f"自适应双素材成片需要 4–"
                        f"{video_renderer.FORMAL_MAX_SCENES} 个完整分镜"
                    )
            elif not (
                video_renderer.FORMAL_MIN_SCENES
                <= len(scenes)
                <= video_renderer.FORMAL_MAX_SCENES
            ):
                hard_failures.append(
                    f"正式双素材成片需要 {video_renderer.FORMAL_MIN_SCENES}–"
                    f"{video_renderer.FORMAL_MAX_SCENES} 个完整分镜"
                )
        empty_voiceovers = [index + 1 for index, scene in enumerate(scenes) if not str(scene.get("voiceover") or "").strip()]
        if empty_voiceovers:
            hard_failures.append("存在无旁白分镜：" + "、".join(map(str, empty_voiceovers)))
        infographic_scenes = [index + 1 for index, scene in enumerate(scenes) if is_explanation_scene(scene)]
        if infographic_scenes:
            hard_failures.append(
                "信息图、流程图和 PPT 卡片已禁用：第" + "、".join(map(str, infographic_scenes)) + "镜"
            )
        source_usage = source_usage_report(scenes)
        if not source_usage["passed"]:
            hard_failures.extend(source_usage["issues"])
        if str(script.get("source_type") or "") == "topic_brief_dual_library":
            hard_failures.extend(formal_content_repetition_issues(scenes))
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
        # 自动视频只使用动态视频片段。图片不会在素材不足时静默顶替，
        # 否则生成结果会变成静态幻灯片且用户无法发现来源问题。
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
        assignment_by_scene = {
            scene_index: assignments[index] if index < len(assignments) else {"candidates": []}
            for index, scene_index in enumerate(matchable_indexes)
        }
        scene_reports = []
        used_owned_asset_ids: set[int] = set()
        used_segment_ids: set[int] = set()
        used_image_asset_ids: set[int] = set()
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
            if is_explanation_scene(scene):
                scene_reports.append({
                    "scene": index + 1, "score": 0,
                    "hard_failures": ["信息图、流程图和 PPT 卡片已禁用"], "issues": [],
                })
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
                        "library_origin": "owned",
                    })
                continue
            assignment = assignment_by_scene.get(index, {"candidates": []})
            candidates = assignment.get("candidates") or []
            selected = None
            segment = None
            for candidate in candidates:
                candidate_segment = await asyncio.to_thread(db.get_asset_segment, candidate["segment_id"])
                if not candidate_segment:
                    continue
                asset_id = int(candidate_segment.get("asset_id") or 0)
                segment_id = int(candidate_segment.get("id") or 0)
                if asset_id in used_owned_asset_ids or segment_id in used_segment_ids:
                    continue
                selected, segment = candidate, candidate_segment
                break
            if not selected or not segment:
                scene_reports.append({
                    "scene": index + 1, "score": 0,
                    "hard_failures": ["没有未被使用且符合约束的本地素材"],
                    "issues": ["请补充未使用的热点 Hook、Buffalo 自有视频或自有图片；禁止重复真实视频"],
                })
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
            })
            scene_reports.append({
                "scene": index + 1,
                "score": selected["match_score"],
                "hard_failures": [],
                "issues": ["匹配证据偏弱"] if selected.get("review_required") else [],
            })
        script["scenes"] = scenes
        usage = source_usage_report(scenes)
        if not usage["passed"]:
            scene_reports.append({
                "scene": "全片", "score": 0, "hard_failures": usage["issues"], "issues": [],
            })
        report.update({"script": script, "matches": assignments, "match_scenes": scene_reports,
                       "source_usage": usage})
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
            script.get("tts_provider") or snapshot.get("tts_provider") or "mimo"
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
        report["preview_quality" if preview else "final_quality"] = rendered.get("quality_report") or {}
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
        })
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
                        "summary": "Qwen 视频质检未完成",
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
