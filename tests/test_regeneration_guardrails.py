"""P3-A 人在环重生成 v1：护栏四态、resume 血缘持久化与诊断露出。

核心命题：人工重跑必须"可度量（Δ 分）+ 有护栏（挡得住无意义重跑）"，
且 auto_regenerate 保持 False——护栏在 manual 模式下同样生效。
"""
from fastapi.testclient import TestClient

from video_quality.regeneration_controller import BLOCKING_REASONS, decide_regeneration
from video_quality.schemas import VideoEvaluationReport


def _report(score: float, passed: bool = False, actionable: float | None = None) -> VideoEvaluationReport:
    # P3-B：可改四轴（字幕音频/提示词一致/叙事/平台适配）可用 actionable
    # 单独覆盖；缺省与总分一致。护栏四态断言不受影响。
    actionable_score = score if actionable is None else actionable
    actionable_axes = {
        "subtitle_audio_quality", "prompt_alignment",
        "storytelling", "platform_suitability",
    }
    return VideoEvaluationReport.model_validate({
        "overall_score": score,
        "passed": passed,
        "summary": "测试报告",
        "technical_issues": [],
        "scores": {key: (actionable_score if key in actionable_axes else score) for key in (
            "prompt_alignment", "visual_quality", "character_consistency",
            "product_consistency", "temporal_consistency", "motion_quality",
            "camera_quality", "subtitle_audio_quality", "storytelling",
            "platform_suitability",
        )},
        "issues": [],
        "regeneration": {
            "required": not passed, "revised_prompt": "", "negative_prompt": "",
            "storyboard_changes": [], "parameter_changes": {},
            "segments_to_regenerate": [],
        },
        "frame_index": [],
        "transcript_status": "storyboard",
    })


# ---------- 护栏四态（auto_enabled=False，人在环） ----------

def test_guardrail_no_meaningful_improvement_in_manual_mode():
    # 前序 70、本次 72（提升<3）→ 护栏生效，即使自动重生成关闭
    decision = decide_regeneration(_report(72), history=[{"overall_score": 70}], auto_enabled=False)
    assert decision["action"] == "manual_review"
    assert decision["reason"] == "no_meaningful_improvement"
    assert decision["previous_score"] == 70
    assert decision["score_delta"] == 2


def test_guardrail_maximum_attempts_reached_in_manual_mode():
    history = [{"overall_score": 68}, {"overall_score": 70}]
    decision = decide_regeneration(_report(75), history=history, auto_enabled=False)
    assert decision["reason"] == "maximum_attempts_reached"
    assert decision["attempts_used"] == 2
    assert decision["reason"] in BLOCKING_REASONS


def test_guardrail_score_declined_in_manual_mode():
    decision = decide_regeneration(_report(65), history=[{"overall_score": 70}], auto_enabled=False)
    assert decision["reason"] == "score_declined"
    assert decision["score_delta"] == -5


def test_guardrails_passed_manual_rerun_allowed():
    # 前序 70、本次 78（提升≥3、未达上限），且可改轴同样失分（加权 60 < 70）
    # → 仍是 manual_regeneration_allowed；可改轴达标的场景由 P3-B 另行拦截
    decision = decide_regeneration(
        _report(78, actionable=60), history=[{"overall_score": 70}], auto_enabled=False,
    )
    assert decision["action"] == "manual_review"
    assert decision["reason"] == "manual_regeneration_allowed"
    assert decision["score_delta"] == 8
    assert decision["weighted_actionable_score"] == 60.0


def test_no_history_keeps_legacy_disabled_semantics():
    # 可改轴也失分（加权 60 < 70）时才维持旧 disabled 语义；
    # 可改轴达标会被 P3-B 判 actionable_axes_healthy（见下方新用例）
    decision = decide_regeneration(
        _report(72, actionable=60), history=[], auto_enabled=False,
    )
    assert decision["reason"] == "automatic_regeneration_disabled"
    assert decision["previous_score"] is None
    assert decision["score_delta"] is None


def test_quality_passed_short_circuits_before_guardrails():
    decision = decide_regeneration(_report(90, passed=True), history=[{"overall_score": 50}], auto_enabled=False)
    assert decision == {"action": "none", "reason": "quality_passed", "attempts_used": 1}


def test_auto_enabled_still_regenerates_when_guardrails_pass():
    # 可改轴失分（加权 60 < 70）才值得重跑，P3-B 不误伤
    decision = decide_regeneration(
        _report(78, actionable=60), history=[{"overall_score": 70}], auto_enabled=True,
    )
    assert decision["action"] == "regenerate"
    assert decision["next_attempt"] == 2


# ---------- P3-B：可改轴加权分边缘决策（四护栏之后） ----------

def test_healthy_actionable_axes_block_rerun_even_when_guardrails_pass():
    # 护栏全过（提升 8、未达上限）但失分全在改不动的画面轴（可改轴加权 78 ≥ 70）
    # → 人工重跑同样被挡，且属于 BLOCKING_REASONS（前端禁用重生成按钮）
    decision = decide_regeneration(
        _report(78, actionable=78), history=[{"overall_score": 70}], auto_enabled=False,
    )
    assert decision["action"] == "manual_review"
    assert decision["reason"] == "actionable_axes_healthy"
    assert decision["reason"] in BLOCKING_REASONS
    assert decision["weighted_actionable_score"] == 78.0


# ---------- 血缘持久化与 history 回灌（DB + resume 端到端） ----------

def _client(tmp_db):
    import app
    import auth

    tmp_db.create_user("regen-owner", auth.hash_password("pw12345"), "editor", "Regen Owner")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username": "regen-owner", "password": "pw12345"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _seed_needs_review_job(tmp_db, user_id, score=70):
    project = tmp_db.create_video_project(
        created_by=user_id, source_type="chat",
        source_snapshot={"topic": "南非清关"}, title="清关选题", target_orientation="portrait",
    )
    revision = tmp_db.create_video_project_revision(
        project["id"], {"title": "清关选题", "voice": "冰糖", "scenes": []}, created_by=user_id,
    )
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, f"key-{revision['id']}",
    )
    tmp_db.update_video_generation_job(
        job["id"],
        status="needs_review",
        stage="preview_quality_check",
        quality_report={
            "video_evaluation": {"overall_score": score, "passed": False, "issues": []},
            "regeneration_decision": {"action": "manual_review", "reason": "automatic_regeneration_disabled"},
        },
    )
    return project, job


def test_resume_records_lineage_and_history_is_replayed(tmp_db):
    import video_generation

    client, headers = _client(tmp_db)
    user = tmp_db.get_user_by_username("regen-owner")
    project, prior_job = _seed_needs_review_job(tmp_db, user["id"], score=70)

    response = client.post(f"/api/video-generation/jobs/{prior_job['id']}/resume", headers=headers, json={})
    assert response.status_code == 200, response.text
    resumed = response.json()["job"]

    # 血缘字段落库
    assert resumed["prior_job_id"] == prior_job["id"]
    assert resumed["regen_attempt"] == 1
    assert tmp_db.get_video_generation_job(prior_job["id"])["status"] == "canceled"

    # 沿链取回前序质检报告（history 回灌的数据源）
    history = video_generation.collect_prior_quality_history(resumed)
    assert history == [{"job_id": prior_job["id"], "overall_score": 70, "passed": False}]

    # 第二次 resume：先让中间 job 也落入 needs_review 并带上自己的质检报告
    tmp_db.update_video_generation_job(
        resumed["id"],
        status="needs_review",
        quality_report={"video_evaluation": {"overall_score": 72, "passed": False, "issues": []}},
    )
    second = client.post(f"/api/video-generation/jobs/{resumed['id']}/resume", headers=headers, json={})
    second_job = second.json()["job"]
    assert second_job["regen_attempt"] == 2
    chain = video_generation.collect_prior_quality_history(second_job)
    assert [item["overall_score"] for item in chain] == [70, 72]
    assert [item["job_id"] for item in chain] == [prior_job["id"], resumed["id"]]


def test_resume_hard_cap_rejects_beyond_two_regeneration_attempts(tmp_db):
    client, headers = _client(tmp_db)
    user = tmp_db.get_user_by_username("regen-owner")
    project, job = _seed_needs_review_job(tmp_db, user["id"], score=70)
    tmp_db.update_video_generation_job(job["id"], regen_attempt=2)

    response = client.post(f"/api/video-generation/jobs/{job['id']}/resume", headers=headers, json={})
    assert response.status_code == 409
    assert "已达重生成上限" in response.json()["detail"]


def test_retry_records_lineage_for_failed_jobs(tmp_db):
    client, headers = _client(tmp_db)
    user = tmp_db.get_user_by_username("regen-owner")
    project, job = _seed_needs_review_job(tmp_db, user["id"], score=60)
    tmp_db.update_video_generation_job(job["id"], status="failed", stage="preview_quality_check")

    response = client.post(f"/api/video-generation/jobs/{job['id']}/retry", headers=headers)
    assert response.status_code == 202, response.text
    retried = response.json()["job"]

    # 与 /resume 对齐：失败重试也补血缘，护栏据此判断连续失败历史
    assert retried["id"] != job["id"]
    assert retried["prior_job_id"] == job["id"]
    assert retried["regen_attempt"] == 1


def test_job_get_exposes_diagnostics_for_frontend(tmp_db):
    client, headers = _client(tmp_db)
    user = tmp_db.get_user_by_username("regen-owner")
    project, job = _seed_needs_review_job(tmp_db, user["id"], score=72)

    response = client.get(f"/api/video-generation/jobs/{job['id']}", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    # 件 1 回归：死数据必须可被前端消费
    assert payload["quality_report"]["video_evaluation"]["overall_score"] == 72
    assert payload["quality_report"]["regeneration_decision"]["action"] == "manual_review"


def test_review_page_renders_diagnostics_and_blocks_on_guardrail():
    from pathlib import Path

    html = Path("static/video-project.html").read_text(encoding="utf-8")
    # 件 1：诊断面板露出
    assert "AI 质检诊断" in html
    assert "renderDiagnosticsPanel" in html
    assert "suggested_fix" in html
    # 件 2：按建议重生成入口（复用 resume）
    assert "按质检建议重生成" in html
    assert "resumeWithQualityFixes" in html
    # 件 3：达上限/提升不足禁用按钮
    assert "maximum_attempts_reached" in html
    assert "no_meaningful_improvement" in html
    assert "已达重生成上限" in html
    # P3-B：可改轴加权分露出 + actionable_axes_healthy 禁用重生成
    assert "weighted_actionable_score" in html
    assert "可改轴加权分" in html
    assert "actionable_axes_healthy" in html
    assert "可改轴已达标" in html
