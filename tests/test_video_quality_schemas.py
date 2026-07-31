import pytest
from pydantic import ValidationError


def _valid_issue(**overrides):
    value = {
        "start_second": 1.2,
        "end_second": 1.8,
        "severity": "medium",
        "category": "subtitle_alignment",
        "description": "字幕比口播提前出现",
        "evidence_frame": "FRAME_0002@1.50s",
        "suggested_fix": "将字幕整体后移 0.3 秒",
    }
    value.update(overrides)
    return value


def _valid_report(**overrides):
    value = {
        "overall_score": 88,
        "passed": True,
        "summary": "画面与分镜基本一致",
        "technical_issues": [],
        "scores": {
            "prompt_alignment": 88,
            "visual_quality": 86,
            "character_consistency": 90,
            "product_consistency": 90,
            "temporal_consistency": 85,
            "motion_quality": 84,
            "camera_quality": 87,
            "subtitle_audio_quality": 89,
            "storytelling": 86,
            "platform_suitability": 88,
        },
        "issues": [_valid_issue()],
        "regeneration": {
            "required": False,
            "revised_prompt": "",
            "negative_prompt": "",
            "storyboard_changes": [],
            "parameter_changes": {},
            "segments_to_regenerate": [],
        },
    }
    value.update(overrides)
    return value


def test_video_quality_input_defaults_are_cost_bounded():
    from video_quality.schemas import VideoQualityInput

    parsed = VideoQualityInput(video_source="/tmp/sample.mp4")

    assert parsed.mode == "balanced"
    assert parsed.max_frames == 40
    assert parsed.auto_regenerate is False


def test_report_rejects_score_outside_zero_to_one_hundred():
    from video_quality.schemas import VideoEvaluationReport

    with pytest.raises(ValidationError):
        VideoEvaluationReport.model_validate(_valid_report(overall_score=101))


def test_report_rejects_unknown_severity():
    from video_quality.schemas import VideoEvaluationReport

    report = _valid_report()
    report["issues"] = [_valid_issue(severity="critical")]
    with pytest.raises(ValidationError):
        VideoEvaluationReport.model_validate(report)


def test_report_rejects_unbounded_regeneration_advice():
    from video_quality.schemas import VideoEvaluationReport

    report = _valid_report()
    report["regeneration"]["storyboard_changes"] = [
        {"scene": number, "action": "替换素材"} for number in range(1, 5)
    ]
    with pytest.raises(ValidationError):
        VideoEvaluationReport.model_validate(report)


def test_high_issue_fails_even_with_high_score():
    from video_quality.schemas import VideoEvaluationReport, quality_failed

    report = _valid_report(overall_score=92, passed=True)
    report["issues"] = [_valid_issue(severity="high")]
    parsed = VideoEvaluationReport.model_validate(report)

    assert quality_failed(parsed, threshold=80) is True


def test_low_score_fails_even_when_model_says_passed():
    from video_quality.schemas import VideoEvaluationReport, quality_failed

    parsed = VideoEvaluationReport.model_validate(_valid_report(overall_score=79, passed=True))

    assert quality_failed(parsed, threshold=80) is True


def test_clean_high_score_report_passes():
    from video_quality.schemas import VideoEvaluationReport, quality_failed

    parsed = VideoEvaluationReport.model_validate(
        _valid_report(overall_score=86, passed=True, issues=[])
    )

    assert quality_failed(parsed, threshold=80) is False
