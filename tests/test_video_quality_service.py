from dataclasses import replace
from pathlib import Path

import pytest

from video_quality.schemas import VideoEvaluationReport, VideoQualityInput


def _report(score=88, passed=True, severity=None, actionable=None):
    issues = []
    if severity:
        issues = [{
            "start_second": 4.0,
            "end_second": 5.0,
            "severity": severity,
            "category": "flicker",
            "description": "人物边缘短暂闪烁",
            "evidence_frame": "FRAME_0001@1.000s",
            "suggested_fix": "重新生成该片段",
        }]
    # P3-B：可改四轴可用 actionable 单独覆盖（缺省与总分一致）
    actionable_score = score if actionable is None else actionable
    return VideoEvaluationReport.model_validate({
        "overall_score": score,
        "passed": passed,
        "summary": "测试报告",
        "technical_issues": [],
        "scores": {
            "prompt_alignment": actionable_score,
            "visual_quality": score,
            "character_consistency": score,
            "product_consistency": score,
            "temporal_consistency": score,
            "motion_quality": score,
            "camera_quality": score,
            "subtitle_audio_quality": actionable_score,
            "storytelling": actionable_score,
            "platform_suitability": actionable_score,
        },
        "issues": issues,
        "regeneration": {
            "required": not passed,
            "revised_prompt": "修复闪烁" if not passed else "",
            "negative_prompt": "闪烁" if not passed else "",
            "storyboard_changes": [],
            "parameter_changes": {},
            "segments_to_regenerate": [],
        },
        "frame_index": [{
            "frame_id": "FRAME_0001@1.000s",
            "timestamp_seconds": 1.0,
            "path": "/tmp/frame.jpg",
            "reason": "uniform",
        }],
        "transcript_status": "storyboard",
    })


def _preprocessed(tmp_path):
    from video_quality.video_preprocessor import PreprocessedVideo
    from video_quality.transcript_service import TranscriptResult

    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    transcript = tmp_path / "transcript.vtt"
    transcript.write_text("WEBVTT\n", encoding="utf-8")
    return PreprocessedVideo(
        video_path=video,
        source_info={"title": "sample"},
        downloaded=False,
        metadata={"duration_seconds": 10.0, "width": 540, "height": 960},
        technical_report={"status": "passed", "metadata": {"duration_seconds": 10.0}, "issues": []},
        transcript=TranscriptResult("storyboard", [], transcript),
        frames=[{"path": str(frame), "timestamp_seconds": 1.0, "reason": "uniform"}],
        frame_meta={"selected_count": 1},
    )


@pytest.mark.asyncio
async def test_passing_scan_does_not_run_focus_review(tmp_path):
    from video_quality.service import run_quality_mvp

    calls = []

    async def evaluator(**kwargs):
        calls.append(kwargs)
        return _report()

    result = await run_quality_mvp(
        VideoQualityInput(video_source=str(tmp_path / "sample.mp4")),
        tmp_path / "run",
        job_id="passing-run",
        preprocessor=lambda *args, **kwargs: _preprocessed(tmp_path),
        evaluator=evaluator,
    )

    assert len(calls) == 1
    assert result["regeneration_decision"]["action"] == "none"
    assert (tmp_path / "run" / "manifest.json").exists()
    assert (tmp_path / "run" / "evaluation.json").exists()


@pytest.mark.asyncio
async def test_high_issue_runs_one_bounded_focus_review(tmp_path):
    from video_quality.service import run_quality_mvp

    calls = []

    async def evaluator(**kwargs):
        calls.append(kwargs)
        return _report(72 if len(calls) == 1 else 70, False, "high")

    focus_frame = tmp_path / "focus.jpg"
    focus_frame.write_bytes(b"jpeg")

    def focus_extractor(*args, **kwargs):
        assert kwargs["max_frames"] <= 40
        assert 5 <= kwargs["requested_fps"] <= 10
        return {
            "frames": [{"path": str(focus_frame), "timestamp_seconds": 4.5, "reason": "uniform"}],
            "meta": {"selected_count": 1},
        }

    result = await run_quality_mvp(
        VideoQualityInput(video_source=str(tmp_path / "sample.mp4")),
        tmp_path / "run",
        job_id="focus-run",
        preprocessor=lambda *args, **kwargs: _preprocessed(tmp_path),
        evaluator=evaluator,
        focus_extractor=focus_extractor,
    )

    assert len(calls) == 2
    assert calls[1]["review_stage"] == "focused"
    assert result["report"]["passed"] is False
    assert (tmp_path / "run" / "evaluation-stages.json").exists()


def test_risk_windows_expand_merge_and_clamp():
    from video_quality.service import build_risk_windows

    windows = build_risk_windows(
        [
            {"start_second": 0.1, "end_second": 1.0, "severity": "high"},
            {"start_second": 1.2, "end_second": 2.0, "severity": "high"},
            {"start_second": 8.0, "end_second": 9.8, "severity": "medium"},
        ],
        duration_seconds=10,
    )

    assert windows == [{"start_second": 0.0, "end_second": 2.5}]


def test_regeneration_is_manual_by_default():
    from video_quality.regeneration_controller import decide_regeneration

    # 可改轴同样失分（加权分 60 < 70）→ 维持旧的 disabled 语义；
    # 若可改轴达标则由 P3-B 判 actionable_axes_healthy（见新测试文件）。
    decision = decide_regeneration(
        _report(72, False, "high", actionable=60), history=[], auto_enabled=False,
    )

    assert decision["action"] == "manual_review"
    assert decision["reason"] == "automatic_regeneration_disabled"
    assert decision["weighted_actionable_score"] == 60.0


def test_regeneration_stops_when_score_declines_or_improves_less_than_three():
    from video_quality.regeneration_controller import decide_regeneration

    declined = decide_regeneration(
        _report(70, False, "high", actionable=60),
        history=[{"overall_score": 74}], auto_enabled=True,
    )
    flat = decide_regeneration(
        _report(76, False, "high", actionable=60),
        history=[{"overall_score": 74}], auto_enabled=True,
    )

    assert declined["action"] == "manual_review"
    assert declined["reason"] == "score_declined"
    assert flat["reason"] == "no_meaningful_improvement"


def test_prompt_optimizer_reuses_report_without_extra_model_call():
    from video_quality.prompt_optimizer import optimize_prompt

    optimized = optimize_prompt(
        VideoQualityInput(video_source="/tmp/video.mp4", original_prompt="原提示词"),
        _report(72, False, "high"),
    )

    assert optimized["required"] is True
    assert optimized["revised_prompt"] == "修复闪烁"
    assert optimized["manual_confirmation_required"] is True
