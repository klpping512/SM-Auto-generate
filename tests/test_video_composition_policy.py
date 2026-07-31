from video_composition_policy import (
    is_explanation_scene,
    source_usage_report,
    subtitle_timeline_report,
)


def test_source_usage_rejects_duplicate_segment_and_buffalo_mother():
    report = source_usage_report([
        {"asset_id": 10, "asset_segment_id": 101, "asset_start_ms": 0, "asset_end_ms": 6_000, "evidence_type": "owned_video"},
        {"asset_id": 10, "asset_segment_id": 101, "asset_start_ms": 0, "asset_end_ms": 6_000, "evidence_type": "owned_video"},
    ])

    assert report["passed"] is False
    assert any("asset_segment_id 101" in item for item in report["issues"])
    assert any("Buffalo 原始视频 10" in item for item in report["issues"])


def test_source_usage_allows_only_two_distinct_non_overlapping_hooks_per_parent():
    passed = source_usage_report([
        {"asset_id": 20, "event_clip_id": 1, "asset_start_ms": 0, "asset_end_ms": 5_000, "evidence_type": "hotspot_video"},
        {"asset_id": 20, "event_clip_id": 2, "asset_start_ms": 7_000, "asset_end_ms": 12_000, "evidence_type": "hotspot_video"},
    ])
    failed = source_usage_report([
        {"asset_id": 20, "event_clip_id": 1, "asset_start_ms": 0, "asset_end_ms": 5_000, "evidence_type": "hotspot_video"},
        {"asset_id": 20, "event_clip_id": 2, "asset_start_ms": 4_000, "asset_end_ms": 9_000, "evidence_type": "hotspot_video"},
        {"asset_id": 20, "event_clip_id": 3, "asset_start_ms": 10_000, "asset_end_ms": 15_000, "evidence_type": "hotspot_video"},
    ])

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert any("最多允许 2 个" in item for item in failed["issues"])
    assert any("时间范围重叠" in item for item in failed["issues"])


def test_source_usage_rejects_reused_context_image():
    report = source_usage_report([
        {"asset_id": 71, "evidence_type": "image", "duration_ms": 2_000},
        {"asset_id": 71, "evidence_type": "image", "duration_ms": 2_000},
    ])

    assert report["passed"] is False
    assert any("静态图片 71" in item for item in report["issues"])


def test_final_subtitle_timeline_accounts_for_crossfades():
    report = subtitle_timeline_report([
        {"render_duration": 4, "sync": {"passed": True, "audio_duration": 3.5, "subtitle_end": 3.5}},
        {"render_duration": 5, "sync": {"passed": True, "audio_duration": 4.5, "subtitle_end": 4.5}},
    ], final_duration=8.78, transition_duration=.22)

    assert report["passed"] is True
    assert report["timeline"][-1]["end"] == 8.78


def test_legacy_infographic_scene_is_identified_for_hard_rejection():
    assert is_explanation_scene({
        "scene_role": "logistics_explainer", "evidence_type": "explanation_card",
    }) is True
