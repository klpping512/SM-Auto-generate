import pytest


def test_pipeline_only_allows_declared_forward_transitions():
    from video_generation import PipelineStage, validate_transition

    assert validate_transition(PipelineStage.QUEUED, PipelineStage.PLANNING)
    assert validate_transition(PipelineStage.MATCH_QUALITY_CHECK, PipelineStage.PREVIEW_RENDERING)
    assert validate_transition(PipelineStage.PREVIEW_QUALITY_CHECK, PipelineStage.FINAL_RENDERING)
    assert validate_transition(PipelineStage.PREVIEW_QUALITY_CHECK, PipelineStage.SUCCEEDED)
    with pytest.raises(ValueError, match="非法的视频生成阶段跳转"):
        validate_transition(PipelineStage.PLANNING, PipelineStage.FINAL_RENDERING)


def test_hard_script_failure_routes_to_issue_only_review():
    from video_generation import JobStatus, PipelineStage, route_script_quality

    decision = route_script_quality(
        {"score": 91, "hard_failures": ["事实来源缺失"], "issues": ["第2段缺少来源"]}
    )

    assert decision.status is JobStatus.NEEDS_REVIEW
    assert decision.stage is PipelineStage.SCRIPT_QUALITY_CHECK
    assert decision.issues == ["第2段缺少来源"]


def test_low_match_quality_continues_to_internal_preview_with_warning():
    from video_generation import JobStatus, PipelineStage, route_match_quality

    decision = route_match_quality(
        [
            {"scene": 1, "score": 88, "hard_failures": []},
            {"scene": 2, "score": 64, "hard_failures": [], "issues": ["画面语义偏弱"]},
        ]
    )

    assert decision.status is JobStatus.RUNNING
    assert decision.stage is PipelineStage.PREVIEW_RENDERING
    assert decision.score == 64
    assert decision.issues == ["第2镜头：画面语义偏弱"]


def test_missing_local_material_still_routes_to_manual_review():
    from video_generation import JobStatus, PipelineStage, route_match_quality

    decision = route_match_quality([
        {"scene": 1, "score": 0, "hard_failures": ["没有符合约束的本地素材"]},
    ])

    assert decision.status is JobStatus.NEEDS_REVIEW
    assert decision.stage is PipelineStage.MATCH_QUALITY_CHECK
    assert decision.issues == ["第1镜头：没有符合约束的本地素材"]


def test_match_quality_continues_when_every_scene_has_a_candidate():
    from video_generation import JobStatus, PipelineStage, route_match_quality

    decision = route_match_quality(
        [
            {"scene": 1, "score": 82, "hard_failures": []},
            {"scene": 2, "score": 78, "hard_failures": []},
        ],
        threshold=75,
    )

    assert decision.status is JobStatus.RUNNING
    assert decision.stage is PipelineStage.PREVIEW_RENDERING
    assert decision.score == 78


def test_preview_quality_allows_at_most_two_automatic_repairs():
    from video_generation import JobStatus, PipelineStage, route_preview_quality

    repair = route_preview_quality(
        {"score": 68, "hard_failures": [], "issues": ["字幕遮挡主体"]},
        repair_attempts=1,
    )
    stopped = route_preview_quality(
        {"score": 68, "hard_failures": [], "issues": ["字幕遮挡主体"]},
        repair_attempts=2,
    )

    assert repair.status is JobStatus.RUNNING
    assert repair.stage is PipelineStage.PREVIEW_RENDERING
    assert repair.repair_attempts == 2
    assert stopped.status is JobStatus.NEEDS_REVIEW
    assert stopped.stage is PipelineStage.PREVIEW_QUALITY_CHECK


def test_dual_library_preview_rejects_a_silently_shortened_delivery():
    from video_generation import _formal_dual_library_duration_issue

    script = {"source_type": "topic_brief_dual_library"}

    assert _formal_dual_library_duration_issue({"duration": 34.667}, script) == (
        "成片时长 34.7 秒，不符合 50–90 秒交付标准"
    )
    assert _formal_dual_library_duration_issue({"duration": 58.0}, script) is None
    assert _formal_dual_library_duration_issue({"duration": 34.667}, {"source_type": "manual"}) is None


def test_semantic_video_quality_routes_high_issue_to_manual_review():
    from video_generation import JobStatus, PipelineStage, route_video_evaluation_quality

    decision = route_video_evaluation_quality({
        "evaluation_status": "completed",
        "overall_score": 91,
        "passed": True,
        "issues": [{"severity": "high", "description": "人物面部闪烁"}],
    })

    assert decision.status is JobStatus.NEEDS_REVIEW
    assert decision.stage is PipelineStage.PREVIEW_QUALITY_CHECK
    assert decision.issues == ["人物面部闪烁"]


def test_semantic_video_quality_passes_only_valid_high_score_report():
    from video_generation import JobStatus, PipelineStage, route_video_evaluation_quality

    decision = route_video_evaluation_quality({
        "evaluation_status": "completed",
        "overall_score": 86,
        "passed": True,
        "issues": [{"severity": "low", "description": "轻微静止"}],
    })

    assert decision.status is JobStatus.RUNNING
    assert decision.stage is PipelineStage.FINAL_RENDERING


def test_temporal_evidence_policy_recovery_can_enter_final_rendering():
    from video_generation import JobStatus, PipelineStage, route_video_evaluation_quality

    decision = route_video_evaluation_quality({
        "evaluation_status": "completed",
        "overall_score": 80,
        "passed": True,
        "issues": [],
        "technical_issues": [],
        "policy_recovered": True,
    })

    assert decision.status is JobStatus.RUNNING
    assert decision.stage is PipelineStage.FINAL_RENDERING


def test_semantic_video_quality_accepts_low_risk_editorial_notes_after_hard_gates():
    from video_generation import JobStatus, PipelineStage, route_video_evaluation_quality

    decision = route_video_evaluation_quality({
        "evaluation_status": "completed",
        "overall_score": 75,
        "passed": False,
        "technical_issues": [],
        "issues": [
            {"severity": "low", "category": "camera_quality", "description": "轻微晃动"},
            {"severity": "low", "category": "temporal_consistency", "description": "转场偏快"},
        ],
    })

    assert decision.status is JobStatus.RUNNING
    assert decision.stage is PipelineStage.FINAL_RENDERING


def test_semantic_video_quality_unavailable_never_fakes_a_pass():
    from video_generation import (
        JobStatus,
        route_video_evaluation_quality,
    )

    report = {
        "evaluation_status": "unavailable",
        "overall_score": 0,
        "passed": False,
        "issues": [],
    }
    decision = route_video_evaluation_quality(report)

    assert decision.status is JobStatus.NEEDS_REVIEW
    assert decision.issues == ["视频质检服务暂不可用，请人工检查预览"]


def test_formal_content_gate_rejects_repeated_actions_and_generic_checks():
    from video_generation import formal_content_repetition_issues

    issues = formal_content_repetition_issues([
        {"scene_role": "owned_proof", "copy_anchor": "叉车正在仓内搬运包裹。", "voiceover": "请核对订单信息。"},
        {"scene_role": "owned_proof", "copy_anchor": "叉车正在仓内搬运包裹。", "voiceover": "请核对订单信息。"},
    ])

    assert "旁白存在重复模板句，不能生成同质化成片" in issues
    assert "“请核对”类泛化提醒重复出现，缺少具体物流建议" in issues
    assert "自有镜头存在重复可见动作，不能用相似叉车/拖车镜头凑时长" in issues


def test_render_progress_is_mapped_to_the_parent_pipeline():
    from video_generation import render_progress_to_pipeline

    assert render_progress_to_pipeline(0, preview=True) == 55
    assert render_progress_to_pipeline(100, preview=True) == 70
    assert render_progress_to_pipeline(0, preview=False) == 78
    assert render_progress_to_pipeline(100, preview=False) == 95


def test_brand_endcard_path_is_safe_and_does_not_require_a_video_slot(tmp_path):
    from video_generation import resolve_brand_endcard_path

    endcard = tmp_path / "uploads" / "brand-endcards" / "cta.png"
    endcard.parent.mkdir(parents=True)
    endcard.touch()

    assert resolve_brand_endcard_path(tmp_path, {
        "evidence_type": "brand_endcard",
        "brand_endcard_path": "uploads/brand-endcards/cta.png",
    }) == endcard
    assert resolve_brand_endcard_path(tmp_path, {
        "evidence_type": "brand_endcard",
        "brand_endcard_path": "../outside.png",
    }) is None


@pytest.mark.asyncio
async def test_run_claimed_job_preserves_report_written_inside_stage(monkeypatch):
    from video_generation import JobStatus, PipelineStage, QualityDecision, run_claimed_job

    state = {
        "id": "job-report-1", "status": "running", "stage": "queued",
        "quality_report": {"before_stage": True},
    }

    def get_job(_job_id):
        return dict(state)

    def update_job(_job_id, **kwargs):
        if "quality_report" in kwargs:
            state["quality_report"] = kwargs["quality_report"]
        state.update({key: value for key, value in kwargs.items() if key != "quality_report"})
        return dict(state)

    monkeypatch.setattr("database.get_video_generation_job", get_job)
    monkeypatch.setattr("database.update_video_generation_job", update_job)
    monkeypatch.setattr("database.renew_video_generation_lease", lambda *args, **kwargs: True)
    monkeypatch.setattr("database.add_video_generation_event", lambda *args, **kwargs: {})

    async def queued_stage(_job):
        state["quality_report"] = {"video_evaluation": {"evaluation_status": "unavailable", "error": "timeout"}}
        return QualityDecision(JobStatus.RUNNING, PipelineStage.SUCCEEDED, 86)

    await run_claimed_job(state, "owner", {PipelineStage.QUEUED: queued_stage})

    assert state["quality_report"]["video_evaluation"]["error"] == "timeout"


def test_hotspot_followup_evidence_gate_requires_sixty_seconds_and_dynamic_video():
    from video_generation import hotspot_evidence_gate

    issues = hotspot_evidence_gate({
        "source_type": "hotspot_followup",
        "duration_target_ms": 60_000,
        "scenes": [
            {"duration_ms": 30_000, "evidence_type": "hotspot_video"},
            {"duration_ms": 30_000, "evidence_type": "image"},
        ],
    })

    assert not any("热点视频" in issue for issue in issues)
    assert any("至少 4 段 Buffalo 动态视频" in issue for issue in issues)
    assert any("静态图片占比" in issue for issue in issues)


def test_hotspot_followup_evidence_gate_softens_owned_floor_when_adapted():
    from video_generation import hotspot_evidence_gate

    issues = hotspot_evidence_gate({
        "source_type": "hotspot_followup",
        "adaptation": {"adapted": True, "strategies": ["reduce_owned_requirement"]},
        "duration_target_ms": 30_000,
        "scenes": [
            {"duration_ms": 10_000, "evidence_type": "hotspot_video"},
            {"duration_ms": 10_000, "evidence_type": "owned_video"},
            {"duration_ms": 8_000, "evidence_type": "owned_video"},
            {"duration_ms": 2_000, "evidence_type": "image"},
        ],
    })

    assert not any("至少 4 段 Buffalo 动态视频" in issue for issue in issues)
    assert not any("必须至少 60 秒" in issue for issue in issues)


def test_hotspot_followup_evidence_gate_rejects_legacy_infographic_scene():
    from video_generation import hotspot_evidence_gate

    issues = hotspot_evidence_gate({
        "source_type": "hotspot_followup",
        "scenes": [
            {"duration_ms": 10_000, "evidence_type": "hotspot_video"},
            {"duration_ms": 10_000, "evidence_type": "hotspot_video"},
            {"duration_ms": 10_000, "evidence_type": "owned_video"},
            {"duration_ms": 10_000, "evidence_type": "owned_video"},
            {"duration_ms": 10_000, "evidence_type": "owned_video"},
            {"duration_ms": 10_000, "evidence_type": "owned_video"},
            {"duration_ms": 5_000, "scene_role": "logistics_explainer", "evidence_type": "explanation_card"},
        ],
    })

    assert any("已禁用" in issue for issue in issues)


def test_describe_plan_adaptation_marks_thin_owned_inventory():
    from hotspot_video_planner import describe_plan_adaptation

    adaptation = describe_plan_adaptation([
        {"evidence_type": "hotspot_video", "duration_ms": 7000},
        {"evidence_type": "owned_video", "duration_ms": 7000},
        {"evidence_type": "image", "duration_ms": 2000},
    ])

    assert adaptation["adapted"] is True
    assert "reduce_owned_requirement" in adaptation["strategies"]
    assert "use_owned_images_as_bridges" in adaptation["strategies"]


@pytest.mark.parametrize(
    "stage",
    [
        "queued", "planning", "script_quality_check", "asset_matching",
        "match_quality_check", "preview_rendering", "preview_quality_check",
        "final_rendering", "final_quality_check",
    ],
)
def test_cancel_checkpoint_stops_every_pipeline_stage(stage):
    from video_generation import GenerationCanceled, cancellation_checkpoint

    with pytest.raises(GenerationCanceled):
        cancellation_checkpoint({"status": "cancel_requested", "stage": stage})


def test_idempotency_key_is_deterministic_and_revision_scoped():
    from video_generation import build_idempotency_key

    first = build_idempotency_key("project-a", "revision-1")
    repeated = build_idempotency_key("project-a", "revision-1")
    changed = build_idempotency_key("project-a", "revision-2")

    assert first == repeated
    assert first != changed


def test_video_quality_uses_a_budget_distinct_from_script_planning():
    from video_generation import quality_budget_job_id

    assert quality_budget_job_id("video-job-1") == "video-job-1:video-quality"
