"""Strict input and output contracts for video quality evaluation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoQualityInput(StrictModel):
    video_source: str = Field(min_length=1)
    original_prompt: str = ""
    storyboard: dict[str, Any] | list[Any] | str = ""
    reference_images: list[str] = Field(default_factory=list, max_length=10)
    target_platform: str = "抖音"
    mode: Literal["efficient", "balanced", "detailed"] = "balanced"
    max_frames: int = Field(default=40, ge=1, le=100)
    auto_regenerate: bool = False


class QualityScores(StrictModel):
    prompt_alignment: float = Field(ge=0, le=100)
    visual_quality: float = Field(ge=0, le=100)
    character_consistency: float = Field(ge=0, le=100)
    product_consistency: float = Field(ge=0, le=100)
    temporal_consistency: float = Field(ge=0, le=100)
    motion_quality: float = Field(ge=0, le=100)
    camera_quality: float = Field(ge=0, le=100)
    subtitle_audio_quality: float = Field(ge=0, le=100)
    storytelling: float = Field(ge=0, le=100)
    platform_suitability: float = Field(ge=0, le=100)


class EvaluationIssue(StrictModel):
    start_second: float = Field(ge=0)
    end_second: float = Field(ge=0)
    severity: Literal["low", "medium", "high"]
    category: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1_000)
    evidence_frame: str = Field(default="", max_length=200)
    suggested_fix: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_second < self.start_second:
            raise ValueError("问题结束时间不能早于开始时间")
        return self


class RegenerationPlan(StrictModel):
    required: bool
    revised_prompt: str = Field(default="", max_length=500)
    negative_prompt: str = Field(default="", max_length=500)
    storyboard_changes: list[Any] = Field(default_factory=list, max_length=3)
    parameter_changes: dict[str, Any] = Field(default_factory=dict, max_length=5)
    segments_to_regenerate: list[Any] = Field(default_factory=list, max_length=3)


class VideoEvaluationReport(StrictModel):
    overall_score: float = Field(ge=0, le=100)
    passed: bool
    summary: str = Field(min_length=1, max_length=500)
    technical_issues: list[Any] = Field(default_factory=list, max_length=3)
    scores: QualityScores
    issues: list[EvaluationIssue] = Field(default_factory=list, max_length=3)
    regeneration: RegenerationPlan
    evaluation_status: Literal["completed", "unavailable", "failed"] = "completed"
    review_stage: Literal["global", "focused", "technical_only"] = "global"
    frame_index: list[dict[str, Any]] = Field(default_factory=list)
    transcript_status: str = "unknown"


def quality_failed(report: VideoEvaluationReport, threshold: float = 80) -> bool:
    """Apply product policy rather than trusting the model's boolean alone."""
    return bool(
        report.evaluation_status != "completed"
        or not report.passed
        or report.overall_score < threshold
        or any(issue.severity == "high" for issue in report.issues)
    )


# P3-B 可改轴权重表：本管线是 FFmpeg 拼真实素材、无文生视频，重生成只能改善这
# 四轴；画面六轴（visual/character/product/temporal/motion/camera）权重=0，
# 失分靠重跑救不了。权重和必须=1.0，与 overall_score 同量纲。
ACTIONABLE_AXIS_WEIGHTS = {
    "subtitle_audio_quality": 0.35,  # 字幕/音画，能重烧，最可控
    "prompt_alignment":       0.30,  # 脚本↔画面对齐，能改脚本/重选素材
    "storytelling":           0.20,  # 叙事，能改口播/顺序
    "platform_suitability":   0.15,  # 平台适配（时长/画幅/字幕）
}


def weighted_actionable_score(scores: QualityScores) -> float:
    """只对"重生成改得动"的四轴做加权，产出 0-100 的辅助分。

    纯函数、不读 overall_score、不改任何门禁；仅供 decide_regeneration
    判"重生成是否值得"（与 overall_score 并存，不替换）。
    """
    total = 0.0
    for axis, weight in ACTIONABLE_AXIS_WEIGHTS.items():
        total += float(getattr(scores, axis)) * weight
    return round(total, 2)
