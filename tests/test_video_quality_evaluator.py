import json

import pytest


def _report(evidence_frame="FRAME_0001@1.500s"):
    return {
        "overall_score": 76,
        "passed": False,
        "summary": "字幕时序需要修正",
        "technical_issues": [],
        "scores": {
            "prompt_alignment": 80,
            "visual_quality": 82,
            "character_consistency": 90,
            "product_consistency": 90,
            "temporal_consistency": 75,
            "motion_quality": 80,
            "camera_quality": 82,
            "subtitle_audio_quality": 60,
            "storytelling": 78,
            "platform_suitability": 76,
        },
        "issues": [{
            "start_second": 1.2,
            "end_second": 1.8,
            "severity": "high",
            "category": "subtitle_alignment",
            "description": "字幕提前",
            "evidence_frame": evidence_frame,
            "suggested_fix": "字幕后移",
        }],
        "regeneration": {
            "required": True,
            "revised_prompt": "字幕和口播对齐",
            "negative_prompt": "字幕提前",
            "storyboard_changes": [],
            "parameter_changes": {},
            "segments_to_regenerate": [{"start_second": 1.2, "end_second": 1.8}],
        },
    }


def test_messages_interleave_frame_ids_timestamps_and_images(tmp_path):
    from video_quality.video_evaluator import build_evaluation_messages

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    messages, frame_index = build_evaluation_messages(
        original_prompt="南非仓库履约",
        storyboard={"scenes": []},
        target_platform="抖音",
        technical_report={"status": "passed"},
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        review_stage="global",
    )

    content = messages[1]["content"]
    assert content[-2]["text"] == "FRAME_0001@1.500s｜keyframe"
    assert content[-1]["type"] == "image_url"
    assert content[-1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert frame_index[0]["frame_id"] == "FRAME_0001@1.500s"


def test_evaluator_prompt_keeps_context_images_and_brand_cta_out_of_freeze_penalties():
    from video_quality.video_evaluator import SYSTEM_PROMPT

    assert "owned_context_image" in SYSTEM_PROMPT
    assert "brand_cta" in SYSTEM_PROMPT
    assert "intentional_static_windows" in SYSTEM_PROMPT
    assert "信息图" in SYSTEM_PROMPT
    assert "各最多 3 项" in SYSTEM_PROMPT
    assert "单张关键帧不能证明冻结" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_model_overflow_is_bounded_before_strict_validation(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    payload = _report()
    payload["technical_issues"] = [
        {"category": f"technical-{index}", "description": "机器候选"}
        for index in range(6)
    ]
    payload["issues"] = [
        {
            "start_second": 1.2,
            "end_second": 1.8,
            "severity": severity,
            "category": f"content-{index}",
            "description": f"有证据的问题 {index}",
            "evidence_frame": "FRAME_0001@1.500s",
            "suggested_fix": "替换为更匹配的真实素材",
        }
        for index, severity in enumerate(("low", "medium", "high", "high", "medium", "high"))
    ]
    payload["regeneration"]["storyboard_changes"] = [
        {"change": index} for index in range(6)
    ]
    payload["regeneration"]["segments_to_regenerate"] = [
        {"start_second": 1.0, "end_second": 1.5 + index / 10}
        for index in range(6)
    ]
    calls = 0

    async def caller(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"content": json.dumps(payload, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-bounded-overflow",
        original_prompt="南非仓库履约",
        storyboard={"scenes": []},
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}, "issues": []},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 1
    assert len(report.technical_issues) == 3
    assert len(report.issues) == 3
    assert [issue.severity for issue in report.issues] == ["high", "high", "high"]
    assert len(report.regeneration.storyboard_changes) == 3
    assert len(report.regeneration.segments_to_regenerate) == 3


def test_freeze_claim_without_technical_candidate_is_retried():
    from video_quality.video_evaluator import _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    report = VideoEvaluationReport.model_validate(_report())
    report.issues[0].category = "freeze"
    report.issues[0].description = "画面冻结"
    report.issues[0].suggested_fix = "替换为未重复的真实热点 Hook"
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]

    with pytest.raises(Exception, match="无技术候选"):
        _validate_evidence(
            report, 3.0, {"scenes": [{}]}, {"issues": []},
        )


def test_queue_narration_cannot_be_rejected_from_a_single_still_frame():
    from video_quality.video_evaluator import _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    report = VideoEvaluationReport.model_validate(_report())
    report.issues[0].category = "narrative"
    report.issues[0].description = "旁白提及车辆排队，但关键帧显示车辆已停止移动。"
    report.issues[0].suggested_fix = "调整旁白时间点。"
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]

    with pytest.raises(Exception, match="单张关键帧推断"):
        _validate_evidence(report, 3.0, {"scenes": [{}]}, {"issues": []})


def test_summary_cannot_invent_freeze_without_a_detector_candidate():
    from video_quality.video_evaluator import _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    report = VideoEvaluationReport.model_validate(_report())
    report.summary = "存在技术冻结问题，建议修复。"
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]

    with pytest.raises(Exception, match="摘要把无技术候选"):
        _validate_evidence(report, 3.0, {"scenes": [{}]}, {"issues": []})


def test_camera_shake_cannot_be_inferred_from_one_keyframe_without_detector_evidence():
    from video_quality.video_evaluator import _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    report = VideoEvaluationReport.model_validate(_report())
    report.summary = "镜头有轻微晃动，影响稳定性。"
    report.issues[0].category = "camera_quality"
    report.issues[0].description = "道路画面有轻微晃动，影响观感稳定。"
    report.issues[0].suggested_fix = "使用防抖处理。"
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]

    with pytest.raises(Exception, match="镜头晃动"):
        _validate_evidence(report, 3.0, {"scenes": [{}]}, {"issues": []})


def test_final_normalization_removes_an_unsupported_camera_shake_claim():
    from video_quality.video_evaluator import _normalize_final_recoverable_output, _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    payload = _report()
    payload.update({
        "overall_score": 78,
        "passed": False,
        "summary": "镜头轻微晃动，影响稳定性。",
        "technical_issues": [],
        "issues": [{
            "start_second": 1.2,
            "end_second": 1.8,
            "severity": "medium",
            "category": "camera_quality",
            "description": "道路画面有轻微晃动。",
            "evidence_frame": "FRAME_0001@1.500s",
            "suggested_fix": "使用防抖处理。",
        }],
    })
    payload["regeneration"]["storyboard_changes"] = [{"scene": 1, "action": "增加防抖"}]
    report = VideoEvaluationReport.model_validate(payload)
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]

    assert _normalize_final_recoverable_output(report, {"scenes": [{}]}, {"issues": []}) is True
    assert report.passed is True
    assert report.issues == []
    _validate_evidence(report, 3.0, {"scenes": [{}]}, {"issues": []})


def test_only_known_temporal_evidence_errors_are_recoverable():
    from video_quality.video_evaluator import is_recoverable_temporal_evidence_error

    assert is_recoverable_temporal_evidence_error(
        "质检摘要把无技术候选支撑的冻结写为问题"
    )
    assert is_recoverable_temporal_evidence_error(
        "质检摘要把无技术候选支撑的冻结写为问题；"
        "质检摘要把无技术候选支撑的镜头晃动写为问题"
    )
    assert not is_recoverable_temporal_evidence_error("多模态模型没有返回结果")
    assert not is_recoverable_temporal_evidence_error(
        "质检摘要把无技术候选支撑的冻结写为问题；人物面部闪烁"
    )


@pytest.mark.asyncio
async def test_repeated_unsupported_camera_shake_summary_is_normalized_after_retry(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0
    invalid = _report()
    invalid.update({
        "overall_score": 78,
        "passed": False,
        "summary": "镜头有轻微晃动，影响稳定性。",
        "technical_issues": [],
        "issues": [],
    })
    invalid["regeneration"].update({
        "required": True,
        "storyboard_changes": [{"scene": 1, "action": "增加防抖"}],
    })

    async def caller(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"content": json.dumps(invalid, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-repeated-camera-summary",
        original_prompt="Beitbridge 边境卡车排队",
        storyboard={"scenes": [{}]},
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 2
    assert report.passed is True
    assert report.summary == "未发现有充分证据支持的重大质量问题。"
    assert report.issues == []


def test_cta_static_detector_hit_cannot_justify_a_freeze_summary_or_fix():
    from video_quality.video_evaluator import _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    report = VideoEvaluationReport.model_validate(_report())
    report.summary = "存在技术冻结问题，建议修复。"
    report.regeneration.storyboard_changes = [{"scene": 1, "action": "避免长时间静止"}]
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]
    storyboard = {
        "scenes": [{"evidence_type": "brand_endcard", "scene_role": "brand_cta", "duration": 2}],
        "render_timeline": [{"scene": 1, "start": 1.0, "end": 2.0}],
    }
    technical = {"issues": [{"category": "freeze", "start_second": 1.1, "end_second": 1.9}]}

    with pytest.raises(Exception, match="静态图片窗口"):
        _validate_evidence(report, 3.0, storyboard, technical)


def test_final_normalization_recovers_when_unsupported_freeze_is_the_only_failure():
    from video_quality.video_evaluator import _normalize_final_recoverable_output, _validate_evidence
    from video_quality.schemas import VideoEvaluationReport

    payload = _report()
    payload.update({
        "overall_score": 75,
        "passed": False,
        "summary": "存在技术冻结问题，建议修复。",
        "issues": [],
        "technical_issues": [{
            "start_second": 1.0,
            "end_second": 2.0,
            "category": "freeze",
            "description": "疑似冻结画面",
            "suggested_fix": "替换为动态镜头",
        }],
    })
    payload["regeneration"]["storyboard_changes"] = [{"scene": 1, "action": "减少静止"}]
    report = VideoEvaluationReport.model_validate(payload)
    report.frame_index = [{"frame_id": "FRAME_0001@1.500s"}]

    assert _normalize_final_recoverable_output(report, {"scenes": [{}]}, {"issues": []}) is True
    assert report.passed is True
    assert report.overall_score == 80
    assert report.technical_issues == []
    assert report.issues == []
    _validate_evidence(report, 3.0, {"scenes": [{}]}, {"issues": []})


def test_intentional_static_windows_use_actual_render_timeline():
    from video_quality.video_evaluator import intentional_static_windows

    windows = intentional_static_windows({
        "scenes": [
            {"evidence_type": "owned_video", "duration": 5},
            {"evidence_type": "image", "scene_role": "owned_context_image", "duration": 2},
            {"evidence_type": "brand_endcard", "scene_role": "brand_cta", "duration": 3},
        ],
        "render_timeline": [
            {"scene": 1, "start": 0, "end": 4.5},
            {"scene": 2, "start": 4.28, "end": 5.9},
            {"scene": 3, "start": 5.68, "end": 9.1},
        ],
    })

    assert windows == [
        {"scene": 2, "start_second": 4.28, "end_second": 5.9, "kind": "owned_context_image"},
        {"scene": 3, "start_second": 5.68, "end_second": 9.1, "kind": "brand_cta"},
    ]


@pytest.mark.asyncio
async def test_invalid_scene_reference_is_retried_before_accepting_report(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0
    messages = []

    invalid = _report()
    invalid["issues"][0].update({
        "severity": "low",
        "description": "场景 19 的字幕需要核对。",
        "suggested_fix": "调整场景 19 的字幕。",
    })
    invalid["regeneration"]["storyboard_changes"] = [{"scene": 19, "action": "修复"}]

    async def caller(*args, **kwargs):
        nonlocal calls
        calls += 1
        messages.append(args[2])
        payload = invalid if calls == 1 else _report()
        return {"content": json.dumps(payload, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-scene-ref",
        original_prompt="南非仓库履约",
        storyboard={"scenes": [{}, {}]},
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 2
    assert report.issues[0].description == "字幕提前"
    assert "invalid_core" in messages[1][1]["content"][-1]["text"]
    assert "场景 19" in messages[1][1]["content"][-1]["text"]


@pytest.mark.asyncio
async def test_freeze_inside_intentional_static_window_is_retried(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0
    invalid = _report()
    invalid["technical_issues"] = [{
        "category": "freeze", "start_second": 1.1, "end_second": 1.9,
    }]

    async def caller(*args, **kwargs):
        nonlocal calls
        calls += 1
        payload = invalid if calls == 1 else _report()
        return {"content": json.dumps(payload, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-static-window",
        original_prompt="南非仓库履约",
        storyboard={
            "scenes": [{"evidence_type": "image", "scene_role": "owned_context_image", "duration": 2}],
            "render_timeline": [{"scene": 1, "start": 1.0, "end": 2.0}],
        },
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 2
    assert report.technical_issues == []


@pytest.mark.asyncio
async def test_repeated_static_window_and_text_card_conflicts_are_normalized_after_retry(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0
    invalid = _report()
    invalid["technical_issues"] = [{
        "category": "freeze", "start_second": 1.1, "end_second": 1.9,
    }]
    invalid["issues"][0].update({
        "category": "visual_quality",
        "description": "画面细节需要关注。",
        "suggested_fix": "增加文字说明卡。",
    })

    async def caller(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"content": json.dumps(invalid, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-recoverable-final",
        original_prompt="南非仓库履约",
        storyboard={
            "scenes": [{"evidence_type": "image", "scene_role": "owned_context_image", "duration": 2}],
            "render_timeline": [{"scene": 1, "start": 1.0, "end": 2.0}],
        },
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 2
    assert report.technical_issues == []
    assert report.issues[0].suggested_fix.startswith("替换为未重复的真实热点 Hook")


@pytest.mark.asyncio
async def test_repeated_renderer_sync_only_subtitle_issue_is_normalized_after_retry(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0
    invalid = _report()
    invalid["issues"][0]["category"] = "subtitle_audio_mismatch"

    async def caller(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"content": json.dumps(invalid, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-subtitle-contract-final",
        original_prompt="南非仓库履约",
        storyboard={
            "scenes": [{"evidence_type": "owned_video", "duration": 2}],
            "renderer_contract": {"subtitle_audio_sync": {"passed": True}},
        },
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 2
    assert report.issues == []


@pytest.mark.asyncio
async def test_issue_window_is_anchored_to_its_canonical_evidence_frame(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    payload = _report("FRAME_0001@9.999s")
    payload["issues"][0].update({"start_second": 8.0, "end_second": 9.0})

    async def caller(*args, **kwargs):
        return {"content": json.dumps(payload, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-window-anchor",
        original_prompt="南非仓库履约",
        storyboard={"scenes": []},
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert report.issues[0].evidence_frame == "FRAME_0001@1.500s"
    assert (report.issues[0].start_second, report.issues[0].end_second) == (1.0, 2.0)


@pytest.mark.asyncio
async def test_renderer_subtitle_contract_retries_spurious_subtitle_mismatch(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0
    invalid = _report()
    invalid["issues"][0]["category"] = "subtitle_audio_mismatch"
    valid = _report()
    valid["issues"][0].update({
        "category": "visual_quality",
        "description": "画面细节需要关注。",
        "suggested_fix": "复核该镜头画面细节。",
    })

    async def caller(*args, **kwargs):
        nonlocal calls
        calls += 1
        payload = invalid if calls == 1 else valid
        return {"content": json.dumps(payload, ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-subtitle-contract",
        original_prompt="南非仓库履约",
        storyboard={
            "scenes": [{}],
            "renderer_contract": {"subtitle_audio_sync": {"passed": True}},
        },
        target_platform="抖音",
        technical_report={"metadata": {"duration_seconds": 3}},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert calls == 2
    assert report.issues[0].category == "visual_quality"


@pytest.mark.asyncio
async def test_fenced_json_is_parsed_and_audit_evidence_is_attached(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = []

    async def caller(*args, **kwargs):
        calls.append(kwargs)
        return {"content": "```json\n" + json.dumps(_report(), ensure_ascii=False) + "\n```", "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test",
        original_prompt="南非仓库履约",
        storyboard={"scenes": []},
        target_platform="抖音",
        technical_report={"status": "passed"},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert report.review_stage == "global"
    assert report.frame_index[0]["frame_id"] == "FRAME_0001@1.500s"
    assert report.issues[0].evidence_frame == "FRAME_0001@1.500s"
    assert len(calls) == 1


def test_parser_repairs_only_a_missing_final_json_container_closer():
    from video_quality.video_evaluator import parse_json_content

    parsed = parse_json_content('{"overall_score": 80, "scores": {"visual_quality": 80}')

    assert parsed["overall_score"] == 80
    assert parsed["scores"]["visual_quality"] == 80


def test_parser_does_not_guess_an_unterminated_json_string():
    from video_quality.video_evaluator import EvaluationResponseError, parse_json_content

    with pytest.raises(EvaluationResponseError, match="无法解析"):
        parse_json_content('{"summary": "未完成}')


@pytest.mark.asyncio
async def test_evidence_frame_number_is_canonicalized_when_qwen_reestimates_timestamp(tmp_path):
    from video_quality.video_evaluator import evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")

    async def caller(*args, **kwargs):
        return {"content": json.dumps(_report("FRAME_0001@9.999s"), ensure_ascii=False), "usage": {}}

    report = await evaluate_video(
        job_id="evaluation-test-frame-number",
        original_prompt="南非仓库履约",
        storyboard={"scenes": []},
        target_platform="抖音",
        technical_report={"status": "passed"},
        transcript_status="storyboard",
        transcript_segments=[],
        frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
        reference_images=[],
        caller=caller,
    )

    assert report.issues[0].evidence_frame == "FRAME_0001@1.500s"


@pytest.mark.asyncio
async def test_unknown_evidence_frame_is_retried_once_then_rejected(tmp_path):
    from video_quality.video_evaluator import EvaluationResponseError, evaluate_video

    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    calls = 0

    async def caller(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"content": json.dumps(_report("FRAME_9999@99.000s"), ensure_ascii=False), "usage": {}}

    with pytest.raises(EvaluationResponseError, match="证据帧"):
        await evaluate_video(
            job_id="evaluation-test-invalid",
            original_prompt="测试",
            storyboard={},
            target_platform="抖音",
            technical_report={},
            transcript_status="unavailable",
            transcript_segments=[],
            frames=[{"path": str(image), "timestamp_seconds": 1.5, "reason": "keyframe"}],
            reference_images=[],
            caller=caller,
        )

    assert calls == 2
