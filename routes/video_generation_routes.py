"""Video project and generation job routes."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response

import database as db
import media_retention
import video_generation
import video_quality.service as video_quality_service
import video_renderer
from auth import get_current_user, require_role
from models import (
    Platform,
    UserRole,
    VideoGenerationManualReviewRequest,
    VideoGenerationRequest,
    VideoGenerationResumeRequest,
    VideoProjectCreateRequest,
    VideoProjectRevisionRequest,
    VideoQualityRequest,
    VideoProjectEnqueueRequest,
)
from video_clip_refs import ClipReferenceError, resolve_clip_ref
from video_quality.schemas import VideoQualityInput
from video_quality.video_evaluator import EvaluationResponseError
from video_quality.video_preprocessor import VideoPreprocessingError

_MANUAL_PREVIEW_TECHNICAL_CHECKS = (
    "expected_resolution",
    "has_audio",
    "duration_aligned",
    "has_timed_subtitles",
    "no_repeated_source_or_range",
    "final_subtitle_timeline_aligned",
    "transition_audio_video_sync",
)
_MANUAL_PREVIEW_REVIEW_CHECKS = (
    "hook_authentic",
    "no_repeat",
    "image_transitions",
    "subtitle_visibility",
    "audio_video_sync",
    "cta_natural",
)


def _validate_video_payload_clip_refs(payload: dict | None) -> None:
    """Reject direct hotspot mother references before they reach a generation job."""
    if not isinstance(payload, dict):
        return
    scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    if not scenes:
        return
    assets = {int(item["id"]): item for item in db.list_assets(status="active")}
    events = {int(item["id"]): item for item in db.list_hotspot_event_clips()}
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or not scene.get("asset_id"):
            continue
        try:
            asset_id = int(scene["asset_id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"第{index}个分镜：素材 ID 无效") from exc
        asset = assets.get(asset_id)
        if not asset:
            continue
        try:
            resolve_clip_ref(scene, asset, events)
        except (ClipReferenceError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"第{index}个分镜：{exc}") from exc


def _is_manual_reviewable_preview(job: dict, report: dict) -> bool:
    """Only expose a human decision after the preview's technical gates pass."""
    if not (job.get("preview_path") or job.get("output_path")):
        return False
    publication = report.get("publication") or {}
    if job.get("status") == "succeeded" and publication.get("review_mode") == "manual_preview":
        return True
    technical_report = report.get("preview_quality") or {}
    technical_checks = technical_report.get("checks") or {}
    return (
        job.get("status") == "needs_review"
        and job.get("stage") == "preview_quality_check"
        and technical_report.get("status") == "passed"
        and all(technical_checks.get(name) is True for name in _MANUAL_PREVIEW_TECHNICAL_CHECKS)
    )


def create_router(static_dir: Path | Callable[[], Path]) -> APIRouter:
    router = APIRouter()

    def current_static_dir() -> Path:
        return static_dir() if callable(static_dir) else static_dir

    @router.post("/api/video-projects", status_code=201)
    async def create_video_project(
        body: VideoProjectCreateRequest,
        user=Depends(get_current_user),
    ):
        _validate_video_payload_clip_refs(body.revision)
        target_orientation = "portrait"
        project = db.create_video_project(
            created_by=user["id"],
            source_type=body.source_type,
            source_snapshot=body.source_snapshot,
            title=body.title,
            platform=body.platform,
            target_duration_ms=body.target_duration_ms,
            target_orientation=target_orientation,
        )
        if body.revision is not None:
            db.create_video_project_revision(project["id"], body.revision, user["id"])
        db.add_audit_log(
            user["id"], user["username"], "create_video_project", target=project["id"]
        )
        return db.get_video_project(project["id"], created_by=user["id"])

    @router.get("/api/video-projects")
    async def list_video_projects(limit: int = 50, user=Depends(get_current_user)):
        return db.list_video_projects(user["id"], limit=limit)

    @router.get("/api/video-projects/{project_id}")
    async def get_video_project(project_id: str, user=Depends(get_current_user)):
        project = db.get_video_project(project_id, created_by=user["id"])
        if not project:
            raise HTTPException(404, "视频项目不存在")
        active_job_id = project.get("active_job_id")
        if active_job_id:
            active_job = db.get_video_generation_job(active_job_id, created_by=user["id"])
            if active_job:
                if active_job.get("preview_path"):
                    active_job["preview_url"] = "/static/" + active_job["preview_path"]
                if active_job.get("output_path"):
                    active_job["output_url"] = "/static/" + active_job["output_path"]
                project["active_job"] = active_job
        return project

    @router.put("/api/video-projects/{project_id}/revision")
    async def update_video_project_revision(
        project_id: str,
        body: VideoProjectRevisionRequest,
        user=Depends(get_current_user),
    ):
        if not db.get_video_project(project_id, created_by=user["id"]):
            raise HTTPException(404, "视频项目不存在")
        _validate_video_payload_clip_refs(body.payload)
        revision = db.create_video_project_revision(project_id, body.payload, user["id"])
        db.add_audit_log(
            user["id"], user["username"], "revise_video_project", target=project_id,
            detail=f"revision={revision['revision_no']}",
        )
        return revision

    @router.post("/api/video-projects/{project_id}/generate")
    async def generate_video_project(
        project_id: str,
        body: VideoGenerationRequest | None = None,
        user=Depends(get_current_user),
    ):
        project = db.get_video_project(project_id, created_by=user["id"])
        if not project:
            raise HTTPException(404, "视频项目不存在")
        capacity = media_retention.disk_guard(current_static_dir())
        if capacity.get("blocked"):
            raise HTTPException(
                507,
                f"服务器磁盘剩余仅 {capacity.get('free_percent', 0)}%，已暂停新视频生成，请先清理或扩容",
            )
        revision = project.get("current_revision")
        if not revision:
            raise HTTPException(409, "请先保存视频脚本和分镜")
        idempotency_key = (
            body.idempotency_key if body and body.idempotency_key
            else video_generation.build_idempotency_key(project_id, revision["id"])
        )
        job, created = db.create_or_get_video_generation_job(
            project_id, revision["id"], user["id"], idempotency_key
        )
        if created:
            db.add_video_generation_event(job["id"], "job_created", "任务已进入生成队列")
        return Response(
            content=json.dumps({"job": job, "created": created}, ensure_ascii=False),
            media_type="application/json",
            status_code=202 if created else 200,
        )

    @router.get("/api/video-generation/jobs/active")
    async def get_active_video_generation_jobs(user=Depends(get_current_user)):
        return db.list_active_video_generation_jobs(user["id"])

    @router.get("/api/video-generation/jobs/{job_id}")
    async def get_video_generation_job(job_id: str, user=Depends(get_current_user)):
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        job["events"] = db.list_video_generation_events(job_id)
        if job.get("preview_path"):
            job["preview_url"] = "/static/" + job["preview_path"]
        if job.get("output_path"):
            job["output_url"] = "/static/" + job["output_path"]
        return job

    @router.post("/api/video-generation/jobs/{job_id}/cancel")
    async def cancel_video_generation_job(job_id: str, user=Depends(get_current_user)):
        job = db.request_video_generation_cancel(job_id, user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        video_renderer.cancel_render(job_id)
        return job

    @router.post("/api/video-generation/jobs/{job_id}/output-pin")
    async def pin_video_generation_output(job_id: str, user=Depends(get_current_user)):
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        return db.set_video_output_pinned(job_id, True)

    @router.post("/api/video-generation/jobs/{job_id}/output-unpin")
    async def unpin_video_generation_output(job_id: str, user=Depends(get_current_user)):
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        return db.set_video_output_pinned(job_id, False)

    @router.post("/api/video-generation/jobs/{job_id}/manual-review")
    async def review_manual_preview(
        job_id: str,
        body: VideoGenerationManualReviewRequest,
        user=Depends(get_current_user),
    ):
        """记录用户对内部预览的人工验收，绝不改变发布权限。"""
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        report = dict(job.get("quality_report") or {})
        publication = dict(report.get("publication") or {})
        if not _is_manual_reviewable_preview(job, report):
            raise HTTPException(409, "只有技术检查通过且待人工判断的预览可以提交验收结果")

        technical_report = report.get("preview_quality") or {}
        technical_checks = technical_report.get("checks") or {}
        failed_technical_checks = [
            name for name in _MANUAL_PREVIEW_TECHNICAL_CHECKS
            if technical_checks.get(name) is not True
        ]
        if body.action == "accept":
            if technical_report.get("status") != "passed" or failed_technical_checks:
                detail = "、".join(failed_technical_checks) or "预览技术检查"
                raise HTTPException(409, f"技术检查未通过，不能确认人工验收：{detail}")
            missing_review_checks = [
                name for name in _MANUAL_PREVIEW_REVIEW_CHECKS
                if body.checklist.get(name) is not True
            ]
            if missing_review_checks:
                raise HTTPException(422, "请逐项确认 Hook、重复素材、转场、字幕、音画同步和 CTA")

        reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        review_status = "accepted" if body.action == "accept" else "rejected"
        manual_review = {
            "status": review_status,
            "reviewed_at": reviewed_at,
            "reviewer": {"id": user["id"], "username": user["username"]},
            "checklist": {
                name: body.checklist.get(name) is True
                for name in _MANUAL_PREVIEW_REVIEW_CHECKS
            },
            "note": body.note.strip(),
            "preview_path": job.get("preview_path") or job.get("output_path"),
            "technical_checks": {
                name: technical_checks.get(name) is True
                for name in _MANUAL_PREVIEW_TECHNICAL_CHECKS
            },
        }
        report["manual_review"] = manual_review
        publication.update({
            "tier": publication.get("tier") or "manual_review",
            "publish_allowed": False,
            "manual_acceptance_required": review_status != "accepted",
            "manual_acceptance_status": review_status,
            "review_mode": "manual_preview",
        })
        report["publication"] = publication
        update_fields = {"quality_report": report}
        if review_status == "accepted" and job.get("status") == "needs_review":
            update_fields["stage"] = "manual_accepted"
        updated = db.update_video_generation_job(job_id, **update_fields)
        event_type = f"manual_preview_{review_status}"
        event_message = (
            "内部预览已完成人工验收（仅记录，不发布）"
            if review_status == "accepted" else "内部预览已退回重做"
        )
        db.add_video_generation_event(
            job_id, event_type, event_message,
            {
                "reviewer": user["username"],
                "note": manual_review["note"],
                "checklist": manual_review["checklist"],
            },
        )
        db.add_audit_log(
            user["id"], user["username"], event_type, target=job_id,
            detail="仅内部预览，不触发发布",
        )
        return {"job": updated, "manual_review": manual_review}

    @router.post("/api/video-generation/jobs/{job_id}/manual-finalize", status_code=202)
    async def finalize_manually_accepted_preview(job_id: str, user=Depends(get_current_user)):
        """Queue a high-resolution render only after an explicit human acceptance."""
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        report = dict(job.get("quality_report") or {})
        publication = dict(report.get("publication") or {})
        manual_review = report.get("manual_review") or {}
        if not (
            job.get("status") == "needs_review"
            and job.get("stage") == "manual_accepted"
            and manual_review.get("status") == "accepted"
            and publication.get("publish_allowed") is False
        ):
            raise HTTPException(409, "只有已记录且未发布的人工验收预览可以生成高清成片")
        updated = db.update_video_generation_job(
            job_id,
            status="pending",
            stage="final_rendering",
            progress=78,
            lease_owner=None,
            lease_expires_at=None,
            error_code=None,
            error_message=None,
            quality_report=report,
        )
        db.add_video_generation_event(
            job_id, "manual_final_render_requested", "人工验收后已进入高清成片渲染（不发布）",
            {"reviewer": (manual_review.get("reviewer") or {}).get("username", "")},
        )
        db.add_audit_log(
            user["id"], user["username"], "manual_final_render_requested", target=job_id,
            detail="人工验收后渲染高清成片；不发布",
        )
        return {"job": updated}

    @router.post("/api/video-generation/jobs/{job_id}/resume")
    async def resume_video_generation_job(
        job_id: str,
        body: VideoGenerationResumeRequest | None = None,
        user=Depends(get_current_user),
    ):
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        if job["status"] != "needs_review":
            raise HTTPException(409, "只有待确认任务可以继续生成")
        if int(job.get("regen_attempt") or 0) >= 2:
            raise HTTPException(409, "已达重生成上限（2 次），请人工评审脚本或另建项目")
        project = db.get_video_project(job["project_id"], created_by=user["id"])
        payload = body.payload if body and body.payload is not None else project["current_revision"]["payload"]
        revision = db.create_video_project_revision(project["id"], payload, user["id"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.update_video_generation_job(
            job_id, status="canceled", stage="canceled", canceled_at=now, finished_at=now
        )
        resumed, _ = db.create_or_get_video_generation_job(
            project["id"], revision["id"], user["id"],
            video_generation.build_idempotency_key(project["id"], revision["id"]),
        )
        # P3-A 血缘：新 job 指向前序 job，预览质检据此回灌 history 使护栏生效
        resumed = db.update_video_generation_job(
            resumed["id"],
            prior_job_id=job_id,
            regen_attempt=int(job.get("regen_attempt") or 0) + 1,
        ) or resumed
        db.add_video_generation_event(resumed["id"], "job_resumed", "修改已保存，重新进入质量检查")
        return {"job": resumed, "revision": revision}

    @router.post("/api/video-generation/jobs/{job_id}/retry", status_code=202)
    async def retry_video_generation_job(job_id: str, user=Depends(get_current_user)):
        job = db.get_video_generation_job(job_id, created_by=user["id"])
        if not job:
            raise HTTPException(404, "视频生成任务不存在")
        if job["status"] not in ("failed", "canceled"):
            raise HTTPException(409, "当前任务不能重试")
        retried, _ = db.create_or_get_video_generation_job(
            job["project_id"], job["revision_id"], user["id"], job["idempotency_key"]
        )
        # 与 /resume 对齐：失败重试也补血缘，护栏据此判断连续失败历史
        retried = db.update_video_generation_job(
            retried["id"],
            prior_job_id=job_id,
            regen_attempt=int(job.get("regen_attempt") or 0) + 1,
        ) or retried
        db.add_video_generation_event(retried["id"], "job_retried", "任务已重新进入队列")
        return {"job": retried}

    @router.post("/api/video-quality/evaluate")
    async def evaluate_video_quality(
        body: VideoQualityRequest,
        user=Depends(require_role(UserRole.ADMIN)),
    ):
        source = body.video_source.strip()
        resolved_static_dir = current_static_dir()
        if source.startswith("/static/"):
            source = str(resolved_static_dir / source.removeprefix("/static/"))
        request = VideoQualityInput.model_validate({**body.model_dump(), "video_source": source})
        run_id = uuid4().hex
        output_dir = resolved_static_dir / "uploads" / "video-quality" / f"manual-{run_id}"
        try:
            result = await video_quality_service.run_quality_mvp(
                request,
                output_dir,
                job_id=f"manual-video-quality-{run_id}",
                allowed_roots=[resolved_static_dir],
            )
        except VideoPreprocessingError as exc:
            raise HTTPException(422, str(exc)) from exc
        except EvaluationResponseError as exc:
            raise HTTPException(502, f"质检结果不可用：{exc}") from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        db.add_audit_log(
            user["id"], user["username"], "evaluate_video_quality",
            target=run_id,
            detail=f"score={result['report']['overall_score']},passed={result['report']['passed']}",
        )
        return result

    # P3: 发布队列入队入口
    @router.post("/api/video-projects/{project_id}/enqueue")
    async def enqueue_video_project(
        project_id: str,
        request: VideoProjectEnqueueRequest,
        user=Depends(get_current_user),
    ) -> dict:
        """将已完成渲染的视频项目加入发布队列（立即或定时）。"""
        project = db.get_video_project(project_id, created_by=user["id"])
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        job = db.get_video_generation_job(project.get("active_job_id"))
        if not job or job.get("status") != "succeeded" or not job.get("output_path"):
            raise HTTPException(status_code=400, detail="成片尚未就绪，无法入队")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scheduled_at = request.scheduled_at or now_str
        platforms = request.platforms or [project.get("platform", "douyin")]
        valid_platforms = {p.value for p in Platform}
        for platform in platforms:
            if platform not in valid_platforms:
                raise HTTPException(status_code=400, detail=f"不支持的平台：{platform}")

        created_ids = []
        for platform in platforms:
            target_ids = request.account_targets.get(platform) or [None]
            for target_id in target_ids:
                if target_id is not None:
                    account = db.get_account(target_id)
                    if not account or account.get("owner_id") != user["id"]:
                        raise HTTPException(status_code=403, detail="不能操作其他用户的账号")
                    if account.get("platform") != platform:
                        raise HTTPException(status_code=400, detail="目标账号与发布平台不匹配")
                queue_id = db.add_to_queue(
                    title=request.title or project.get("title") or "",
                    body="",
                    platform=platform,
                    scheduled_at=scheduled_at,
                    status="queued",
                    created_by=user["id"],
                    attachments=[{"type": "video", "path": job["output_path"]}],
                    target_account_id=target_id,
                )
                created_ids.append(queue_id)

        db.add_audit_log(
            user["id"], user["username"], "enqueue_video_project",
            target=project_id, detail=json.dumps({"queue_ids": created_ids}),
        )
        return {"status": "queued", "queue_ids": created_ids, "message": f"已入队 {len(created_ids)} 条"}

    return router
