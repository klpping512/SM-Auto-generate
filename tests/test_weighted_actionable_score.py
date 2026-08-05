"""P3-B 可改轴加权分：纯函数正确性 + decide_regeneration 边缘决策。

核心命题：本管线是 FFmpeg 拼真实素材、无文生视频，重生成只能改善四轴
（subtitle_audio_quality / prompt_alignment / storytelling / platform_suitability）。
加权分只对这四轴打分，用来 gate「重生成 vs 人工」，与 overall_score 并存、
绝不替换——pass/fail 语义零变化。
"""
from video_quality.regeneration_controller import (
    ACTIONABLE_FLOOR,
    BLOCKING_REASONS,
    decide_regeneration,
)
from video_quality.schemas import (
    ACTIONABLE_AXIS_WEIGHTS,
    QualityScores,
    VideoEvaluationReport,
    weighted_actionable_score,
)

ACTIONABLE_AXES = tuple(ACTIONABLE_AXIS_WEIGHTS)
VISUAL_AXES = (
    "visual_quality", "character_consistency", "product_consistency",
    "temporal_consistency", "motion_quality", "camera_quality",
)


def _scores(actionable: dict[str, float], visual: float = 100) -> QualityScores:
    return QualityScores.model_validate({
        **{axis: visual for axis in VISUAL_AXES},
        **{axis: 60 for axis in ACTIONABLE_AXES},
        **actionable,
    })


def _report(
    overall: float,
    actionable: dict[str, float],
    *,
    passed: bool = False,
    visual: float = 100,
) -> VideoEvaluationReport:
    scores = _scores(actionable, visual)
    return VideoEvaluationReport.model_validate({
        "overall_score": overall,
        "passed": passed,
        "summary": "测试报告",
        "technical_issues": [],
        "scores": scores.model_dump(),
        "issues": [],
        "regeneration": {
            "required": not passed, "revised_prompt": "", "negative_prompt": "",
            "storyboard_changes": [], "parameter_changes": {},
            "segments_to_regenerate": [],
        },
        "frame_index": [],
        "transcript_status": "storyboard",
    })


# ---------- 权重表红线 ----------

def test_weight_table_is_the_four_actionable_axes_summing_to_one():
    assert set(ACTIONABLE_AXIS_WEIGHTS) == {
        "subtitle_audio_quality", "prompt_alignment",
        "storytelling", "platform_suitability",
    }
    assert round(sum(ACTIONABLE_AXIS_WEIGHTS.values()), 10) == 1.0
    # 画面六轴不在权重表 == 权重为 0，不得掺入
    assert not (set(ACTIONABLE_AXIS_WEIGHTS) & set(VISUAL_AXES))


# ---------- 加权分算对，且与画面轴无关 ----------

def test_weighted_score_matches_manual_computation():
    scores = _scores({
        "subtitle_audio_quality": 80, "prompt_alignment": 70,
        "storytelling": 60, "platform_suitability": 50,
    }, visual=100)
    expected = 0.35 * 80 + 0.30 * 70 + 0.20 * 60 + 0.15 * 50  # = 68.5（指令文档笔误写 68.0）
    assert weighted_actionable_score(scores) == expected == 68.5


def test_weighted_score_ignores_visual_axes():
    actionable = {
        "subtitle_audio_quality": 80, "prompt_alignment": 70,
        "storytelling": 60, "platform_suitability": 50,
    }
    assert weighted_actionable_score(_scores(actionable, visual=100)) == 68.5
    # 画面六轴全部砸到 0，加权分纹丝不动
    assert weighted_actionable_score(_scores(actionable, visual=0)) == 68.5


# ---------- 可改轴健康 → 不浪费重跑 ----------

def test_healthy_actionable_axes_route_to_manual_review():
    # overall=76 < 80 触发失败；无 history；可改轴加权分 = 75.75 ≥ 70
    report = _report(76, {
        "subtitle_audio_quality": 85, "prompt_alignment": 80,
        "storytelling": 65, "platform_suitability": 60,
    }, visual=40)
    decision = decide_regeneration(report, history=[], auto_enabled=False)
    assert decision["action"] == "manual_review"
    assert decision["reason"] == "actionable_axes_healthy"
    assert decision["weighted_actionable_score"] == 75.75
    assert decision["reason"] in BLOCKING_REASONS


def test_floor_boundary_inclusive():
    # 加权分恰好 = 70 → 同样判 actionable_axes_healthy（≥ 为界）
    report = _report(76, {axis: 70 for axis in ACTIONABLE_AXES})
    decision = decide_regeneration(report, history=[], auto_enabled=False)
    assert decision["reason"] == "actionable_axes_healthy"
    assert ACTIONABLE_FLOOR == 70


def test_healthy_actionable_axes_block_manual_rerun_with_history():
    # 有 history、护栏全过（提升 8 分、未达上限），但可改轴达标 →
    # 人工重跑同样被挡（不浪费一次无意义重跑）
    report = _report(78, {axis: 80 for axis in ACTIONABLE_AXES}, visual=30)
    decision = decide_regeneration(
        report, history=[{"overall_score": 70}], auto_enabled=False,
    )
    assert decision["reason"] == "actionable_axes_healthy"


# ---------- 可改轴烂 → 维持 P3-A 原判定，不误伤 ----------

def test_low_actionable_score_keeps_regenerate_when_auto_enabled():
    report = _report(70, {axis: 50 for axis in ACTIONABLE_AXES})
    decision = decide_regeneration(report, history=[], auto_enabled=True)
    assert decision["action"] == "regenerate"
    assert decision["reason"] == "quality_below_threshold"
    assert decision["weighted_actionable_score"] == 50.0


def test_low_actionable_score_keeps_legacy_disabled_without_history():
    report = _report(72, {axis: 60 for axis in ACTIONABLE_AXES})
    decision = decide_regeneration(report, history=[], auto_enabled=False)
    assert decision["action"] == "manual_review"
    assert decision["reason"] == "automatic_regeneration_disabled"
    assert decision["weighted_actionable_score"] == 60.0


def test_weighted_score_is_carried_on_guardrail_branches_too():
    # 护栏命中时加权分只透传展示，不抢判定
    report = _report(75, {axis: 90 for axis in ACTIONABLE_AXES})
    decision = decide_regeneration(
        report, history=[{"overall_score": 68}, {"overall_score": 70}],
        auto_enabled=False,
    )
    assert decision["reason"] == "maximum_attempts_reached"
    assert decision["weighted_actionable_score"] == 90.0
