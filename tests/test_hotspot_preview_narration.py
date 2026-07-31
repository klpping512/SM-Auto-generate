import pytest


def test_qwen_narration_requires_the_exact_locked_scene_count():
    import hotspot_preview_narration as narration

    content = '{"title":"题目","angle":"角度","scenes":[{"voiceover":"第一镜必须有足够完整的事实说明。","text_overlay":"事实"}]}'
    with pytest.raises(ValueError, match="2 个锁定分镜"):
        narration.parse_narration(content, 2)


def test_qwen_narration_keeps_only_copy_fields():
    import hotspot_preview_narration as narration

    content = (
        '{"title":"道路变化如何影响履约","angle":"从现场到准备",'
        '"scenes":[{"voiceover":"现场画面说明道路受阻，订单需要重新核对交付安排。","text_overlay":"道路受阻"},'
        '{"voiceover":"仓内可见的检查与分拣动作，用来说明准备而非承诺结果。","text_overlay":"检查与分拣"}]}'
    )
    parsed = narration.parse_narration(content, 2)

    assert parsed["title"] == "道路变化如何影响履约"
    assert parsed["scenes"][0] == {
        "voiceover": "现场画面说明道路受阻，订单需要重新核对交付安排。",
        "text_overlay": "道路受阻",
    }


def test_narration_uses_model_voiceover_when_model_omits_only_the_overlay():
    import hotspot_preview_narration as narration

    parsed = narration.parse_narration(
        '{"title":"题目","angle":"角度","scenes":[{"voiceover":"先核对订单状态。","text_overlay":""}]}',
        [{"duration_ms": 4_000}],
    )

    assert parsed["scenes"][0]["text_overlay"] == "先核对订单状态。"


def test_narration_can_use_model_short_overlay_for_two_second_context_image():
    import hotspot_preview_narration as narration

    parsed = narration.parse_narration(
        '{"title":"题目","angle":"角度","scenes":[{"voiceover":"这是一段超过图片时长的完整说明。","text_overlay":"核对批次"}]}',
        [{"evidence_type": "image", "duration_ms": 2_000}],
    )

    assert parsed["scenes"][0]["voiceover"] == "核对批次"


def test_narration_prompt_exposes_hotspot_facts_and_locked_assets():
    import hotspot_preview_narration as narration

    messages = narration.build_messages(
        "道路异常", {"hotspot_title": "货车侧翻", "brand_claims": ["可见动作"]},
        [{"scene_role": "hotspot_evidence", "evidence_type": "hotspot_video", "event_clip_id": 8, "asset_id": 30, "visual": "侧翻货车"}],
        [{"id": 8, "title_zh": "货车侧翻", "evidence": {"what_happened": "救援作业"}}],
        [{"id": "brand:1", "excerpt": "仓内检查"}],
    )
    payload = messages[1]["content"]

    assert "救援作业" in payload
    assert '"asset_id": 30' in payload
    assert "南非物流抖音文案 SOP" in messages[0]["content"]
    assert "一线物流同行" in messages[0]["content"]


def test_narration_rejects_copy_that_cannot_fit_the_locked_real_video():
    import hotspot_preview_narration as narration

    content = (
        '{"title":"题目","angle":"角度","scenes":['
        '{"voiceover":"这段旁白故意写得很长很长，不能塞进三秒真实视频。","text_overlay":"过长"}]}'
    )
    with pytest.raises(ValueError, match="镜头时长预算"):
        narration.parse_narration(content, [{"duration_ms": 3_000}])


def test_narration_rejects_overlong_copy_for_short_legacy_card_scene():
    import hotspot_preview_narration as narration

    content = (
        '{"title":"题目","angle":"角度","scenes":['
        '{"voiceover":"路线变化会影响到站节奏，因此卖家需要提前核对订单节点与客户预期。","text_overlay":"路线影响"}]}'
    )
    with pytest.raises(ValueError, match="镜头时长预算"):
        narration.parse_narration(content, [{
            "scene_role": "logistics_explainer", "evidence_type": "explanation_card", "duration_ms": 3_000,
        }])


def test_narration_prompt_marks_context_images_as_non_evidence():
    import hotspot_preview_narration as narration

    messages = narration.build_messages(
        "道路异常", {"hotspot_title": "货车侧翻"},
        [{"scene_role": "owned_context_image", "evidence_type": "image",
          "asset_id": 71, "duration_ms": 2_000, "visual": "Buffalo 配送车"}], [], [],
    )

    assert "voiceover_max_chars" in messages[1]["content"]
    assert "Buffalo 自有图片" in messages[0]["content"]
    assert "条件性问题或建议" in messages[1]["content"]


def test_critic_requires_true_and_no_issues_for_a_pass():
    import hotspot_preview_narration as narration

    assert narration.parse_critique('{"approved":true,"issues":[]}') == (True, [])
    assert narration.parse_critique('{"approved":true,"issues":["超出热点事实"]}') == (False, ["超出热点事实"])


def test_evidence_gate_blocks_oil_price_to_surcharge_leap():
    import hotspot_preview_narration as narration

    proposal = {"scenes": [{
        "voiceover": "国际能源扰动可能让跨境物流燃油附加费产生波动。",
        "text_overlay": "附加费波动",
    }]}
    related_events = [{
        "title_zh": "红海局势推高国际油价",
        "evidence": {"what_happened": "国际油价突破 100 美元"},
    }]

    issues = narration.deterministic_evidence_issues(proposal, related_events)

    assert len(issues) == 1
    assert "燃油附加费" in issues[0]


def test_evidence_gate_allows_quote_check_question_for_oil_hook():
    import hotspot_preview_narration as narration

    proposal = {"scenes": [{
        "voiceover": "卖家可在发货前核对实际报价与路线计划。",
        "text_overlay": "核对报价与计划",
    }]}
    related_events = [{
        "title_zh": "红海局势推高国际油价",
        "evidence": {"what_happened": "国际油价突破 100 美元"},
    }]

    assert narration.deterministic_evidence_issues(proposal, related_events) == []
