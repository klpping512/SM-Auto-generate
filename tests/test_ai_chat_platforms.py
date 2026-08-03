import pytest

import ai_engine
import douyin_copywriting_sop
from models import Platform


def test_normalize_hashtags_accepts_model_string_or_list():
    assert ai_engine._normalize_hashtags("#南非物流, #德班港") == ["南非物流", "德班港"]
    assert ai_engine._normalize_hashtags(["#Logistics", "SupplyChain"]) == ["Logistics", "SupplyChain"]


def test_twitter_truncation_keeps_complete_sentence():
    body = "Durban congestion may cause delays. " + "Take action now. " * 30
    shortened = ai_engine._truncate_twitter_body(body)
    assert len(shortened) <= 280
    assert shortened.endswith(".")
    assert not shortened.endswith("…")


def test_unsupported_claim_detection_catches_vague_metrics_and_fake_attribution():
    body = "官方数据显示，延误可能持续数周。"
    warnings = ai_engine._unsupported_claim_warnings(body, "请写港口拥堵提醒")
    assert "输入中未提供的具体时间或数据" in warnings
    assert "输入中未提供来源的报告或官方数据归因" in warnings


def test_operational_guard_allows_generic_support_wording_without_promise():
    assert not ai_engine._unsupported_operational_claim_warnings(
        "Buffalo 支持卖家先核对船期与通关资料，再决定是否调整发货节奏。"
    )


def test_operational_guard_allows_confirmed_brand_evidence_capability_text():
    body = "对外说明：库存实时可视。"
    assert ai_engine._unsupported_operational_claim_warnings(body)
    assert not ai_engine._unsupported_operational_claim_warnings(
        body,
        brand_evidence=[{
            "status": "confirmed",
            "claim": "库存实时可视",
            "evidence_note": "经确认的仓配能力说明",
        }],
    )


def test_unsupported_operational_claim_detection_blocks_unverified_delivery_promises():
    body = "海外仓已经在约翰内斯堡正式落地，支持一件代发，库存实时可视，优先安排查验，Beitbridge 排队不影响整体交期承诺。"

    warnings = ai_engine._unsupported_operational_claim_warnings(body)

    assert warnings
    assert "不影响整体交期承诺" in warnings[0]


def test_conservative_chat_body_has_no_service_capability_or_delivery_promise():
    body = ai_engine._conservative_chat_body("南非海外仓介绍")

    assert not ai_engine._unsupported_operational_claim_warnings(body)
    assert "承运方尚未确认的信息" in body


def test_conservative_chat_subject_uses_neutral_user_intent_not_model_sales_headline():
    assert ai_engine._conservative_chat_subject(
        "", [{"role": "user", "content": "帮我生成一个关于南非海外仓的介绍视频"}]
    ) == "南非海外仓介绍"


def test_conservative_chat_subject_keeps_a_specific_border_event_over_generic_warehouse_words():
    assert ai_engine._conservative_chat_subject(
        "Beitbridge 边境卡车排队，我这票货准备进南非海外仓"
    ) == "Beitbridge 物流提醒"
    assert ai_engine._conservative_chat_subject("Beitbridge 排队会影响交期吗？") == "Beitbridge 物流提醒"


def test_topic_keywords_preserve_road_event_entities_for_model_hook_selection():
    import app

    keywords = app._topic_keywords("R60 从 Robertson 到 Worcester 有卡车侧翻，路线怎么安排？")

    assert {"r60", "robertson", "worcester", "侧翻", "路线"} <= set(keywords)


def test_operational_guard_allows_a_user_problem_but_blocks_unverified_process_claims():
    assert not ai_engine._unsupported_operational_claim_warnings("南非卖家常问：退货难怎么办？")
    assert ai_engine._unsupported_operational_claim_warnings(
        "货物抵达后，完成清关、质检、贴标、上架，全流程标准化作业。"
    )


def test_operational_guard_blocks_ownership_and_priority_claims_from_chat_copy():
    body = (
        "我们南非本地仓已提前布局——货物抵达后优先入仓、分拣、备货，"
        "最大限度缓冲通关波动。点击咨询，帮你实时查进度！"
    )

    assert ai_engine._unsupported_operational_claim_warnings(body)


def test_operational_guard_blocks_disguised_customs_and_tracking_capability_claims():
    body = (
        "SA-LogiFlow深耕南非跨境多年，海外仓已备案、清关文件预审机制成熟，"
        "本地合作报关行响应及时。评论区留言查单，专人同步最新进展。"
    )

    assert ai_engine._unsupported_operational_claim_warnings(body)


def test_operational_guard_blocks_real_time_order_tracking_cta():
    body = "点击主页，获取您的订单实时节点解读。"

    assert ai_engine._unsupported_operational_claim_warnings(body)


def test_operational_guard_blocks_customer_facing_order_lookup_and_realtime_route_copy():
    body = (
        "先看交通部官网实时路况，再去承运商系统查在途节点。"
        "评论区留言‘查节点’，我们帮你快速定位当前订单状态。"
    )

    assert ai_engine._unsupported_operational_claim_warnings(body)


def test_operational_guard_blocks_invented_public_road_updates_and_comment_template_cta():
    body = (
        "请看交通部的实时路况公告，确认 GPS 已同步最新封控数据。"
        "出发前15分钟刷新官网；评论区留言领取核查清单模板。"
    )

    assert ai_engine._unsupported_operational_claim_warnings(body)


def test_operational_guard_blocks_order_lookup_local_partner_and_priority_cta_claims():
    body = (
        "把运单号发我，我们立刻调最新通关状态，同步南非本地合作方动态。"
        "评论区留言单号，我们协助核对最新通关状态。"
    )

    assert ai_engine._unsupported_operational_claim_warnings(body)


def test_chat_preview_scenes_replace_unsupported_operational_promises():
    unsafe = [{
        "scene": 1, "duration": 5, "visual": "自营海外仓", "asset_id": 1,
        "voiceover": "订单生成后，本地仓48小时内完成出库。",
        "text_overlay": "48h内出库",
    }]

    safe = ai_engine._conservative_douyin_scenes(unsafe)

    assert safe[0]["voiceover"] == "请以订单节点和已确认的通关信息为准，提前核对入库与派送安排。"
    assert not ai_engine._unsupported_operational_claim_warnings(safe[0]["voiceover"])


def test_forced_safe_preview_replaces_every_scene_and_keeps_a_neutral_end_card():
    safe = ai_engine._conservative_douyin_scenes([
        {"scene": 1, "voiceover": "无关内容", "visual": "仓库", "text_overlay": "无关"},
        {"scene": 2, "voiceover": "解决方案", "visual": "品牌", "text_overlay": "专家"},
    ], force_all=True)

    assert safe[0]["text_overlay"] == "以订单节点为准"
    assert safe[-1]["text_overlay"] == "信息以核实为准"


def test_forced_safe_preview_keeps_the_current_logistics_topic_useful_to_a_customer():
    safe = ai_engine._conservative_douyin_scenes(
        [{"scene": index} for index in range(1, 9)],
        force_all=True, topic="Swartberg Pass 有卡车侧翻和道路劝退",
    )

    assert safe[0]["voiceover"] == "Swartberg Pass 路况提醒，先看现场能确认的情况。"
    assert safe[2]["voiceover"] == "路线要不要调整，先以承运方确认的信息为准。"
    assert safe[-1]["text_overlay"] == "先把信息理清楚"


def test_chat_system_prompt_requires_conditional_language_without_service_evidence():
    assert "不得把未提供证据的服务能力" in ai_engine.SYSTEM_PROMPT_CHAT
    assert "交期或时效保证" in ai_engine.SYSTEM_PROMPT_CHAT


def test_douyin_copywriting_sop_keeps_peer_voice_without_turning_style_into_a_service_claim():
    planner = douyin_copywriting_sop.prompt_for_video_planner()
    chat = douyin_copywriting_sop.prompt_for_chat_douyin()

    assert douyin_copywriting_sop.metadata() == {
        "id": "south-africa-logistics-douyin-copy-style", "version": "v2",
    }
    assert "一线物流同行" in planner
    assert "不得照抄" in planner
    assert "发生什么—要核对什么—画面里能看到什么" in planner
    assert "服务承诺门禁" in chat


def test_non_chat_douyin_fallback_uses_the_same_safe_peer_style():
    content = ai_engine._fallback_content(
        Platform.DOUYIN, "南非海外仓介绍", "custom",
    )

    visible_text = " ".join([
        content.title,
        content.body,
        *(scene["voiceover"] for scene in content.scenes),
    ])
    assert content.title == "南非海外仓介绍｜先核实再安排"
    assert content.hashtags == ["南非物流", "信息核实"]
    assert "全链路" not in visible_text
    assert not ai_engine._unsupported_operational_claim_warnings(visible_text)
    assert content.scenes[-1]["text_overlay"] == "先把信息理清楚"


@pytest.mark.asyncio
async def test_chat_douyin_prompt_applies_team_copywriting_sop(monkeypatch):
    captured = {}

    async def fake_complete(messages, *, max_tokens, prompt_version):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        captured["prompt_version"] = prompt_version
        return (
            '{"title":"南非海外仓怎么了解","body":"想做南非仓配，先把自己的订单节点问清楚。",'
            '"hashtags":["南非物流"],"scenes":[]}'
        )

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setattr(ai_engine, "_complete_json_messages", fake_complete)
    await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "帮我生成一个关于南非海外仓的介绍视频"}],
        platforms=["douyin"], topic="南非海外仓介绍",
    )

    system = captured["messages"][0]["content"]
    assert "同行式沟通" in system
    assert "服务承诺门禁" in system


def test_platform_format_detection_flags_script_markers_in_douyin_caption():
    # 抖音 body 现在是发布文案：正常种草文案不该报警，含脚本标记才报警
    assert ai_engine._platform_format_warnings("douyin", "普通种草文案，关注我们获取物流干货") == []
    assert ai_engine._platform_format_warnings("douyin", "【画面】港口\n【口播】注意拥堵")


def test_douyin_scene_normalization_falls_back_to_publishable_timeline():
    scenes = ai_engine._normalize_douyin_scenes([], "德班港提醒")
    assert len(scenes) == 8
    assert 50 <= sum(scene["duration"] for scene in scenes) <= 65
    assert all(scene["asset_id"] is None for scene in scenes)


@pytest.mark.asyncio
async def test_content_generation_uses_mimo_chat_json_api(monkeypatch):
    captured = {}

    async def fake_complete(messages, *, max_tokens, prompt_version):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        captured["prompt_version"] = prompt_version
        return '{"title":"T","body":"B","hashtags":[]}'

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setattr(ai_engine, "_complete_json_messages", fake_complete)
    result = await ai_engine.generate_content("主题", "custom", [Platform.FACEBOOK])
    assert result[0].title == "T"
    assert captured["prompt_version"] == "ai-generate-content-mimo-v1"
    assert captured["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_chat_platforms_return_distinct_platform_native_outputs(monkeypatch):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.setattr(ai_engine, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(ai_engine, "chat_model_available", lambda: False)

    outputs = await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "生成德班港拥堵预警"}],
        platforms=["xiaohongshu", "douyin", "twitter", "facebook"],
        topic="德班港拥堵",
    )
    by_platform = {item["platform"]: item for item in outputs}

    assert list(by_platform) == ["xiaohongshu", "douyin", "twitter", "facebook"]
    assert len({item["body"] for item in outputs}) == 4
    # 抖音 body 是发布文案，不含脚本标记；分镜脚本在 scenes 里
    assert "【画面】" not in by_platform["douyin"]["body"]
    assert "【口播】" not in by_platform["douyin"]["body"]
    assert by_platform["douyin"]["scenes"]
    assert "Update:" in by_platform["twitter"]["body"]
    assert "提醒：" in by_platform["facebook"]["body"]
    assert "先把信息核实清楚：" in by_platform["xiaohongshu"]["body"]
    assert all(
        not ai_engine._unsupported_operational_claim_warnings(item["body"])
        for item in outputs
    )


@pytest.mark.asyncio
async def test_chat_model_failure_uses_the_same_safe_fallback_for_a_generic_warehouse_request(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "test-key")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("temporary outage")

    monkeypatch.setattr(ai_engine, "_complete_json_messages", boom)
    output = (await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "帮我生成一个关于南非海外仓的介绍视频"}],
        platforms=["douyin"],
    ))[0]

    assert output["title"] == "南非海外仓介绍｜信息待核实"
    assert output["source"] == "safe_fallback"
    assert not ai_engine._unsupported_operational_claim_warnings(output["body"])
    assert "全链路" not in output["body"]
    assert output["hashtags"] == ["南非物流", "信息核实"]
    assert output["duration_target"] == 60
    assert output["scenes"][-1]["text_overlay"] == "先把信息理清楚"


@pytest.mark.asyncio
async def test_chat_model_operational_claims_are_replaced_before_the_video_button(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    unsafe = {
        "title": "Beitbridge边境排队？南非清关时效这样看",
        "body": "把运单号发我，我们立刻调最新通关状态，同步南非本地合作方动态。",
        "hashtags": ["南非物流"],
        "scenes": [{"scene": 1, "duration": 5, "voiceover": "马上私信单号，优先为您核查进度。", "text_overlay": "实时进展"}],
    }

    async def fake_complete(*_args, **_kwargs):
        return __import__("json").dumps(unsafe, ensure_ascii=False)

    monkeypatch.setattr(ai_engine, "_complete_json_messages", fake_complete)
    output = (await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "Beitbridge 边境排队会不会影响到货？"}],
        platforms=["douyin"], topic="Beitbridge 边境排队",
    ))[0]

    assert output["title"] == "Beitbridge边境排队？南非清关时效这样看"
    assert not ai_engine._unsupported_operational_claim_warnings(output["body"])
    assert output["hashtags"] == ["南非物流"]
    assert output["source"] == "model_sanitized"
    assert output["duration_target"] == 60
    assert len(output["scenes"]) == 8


@pytest.mark.asyncio
async def test_chat_sanitizer_keeps_useful_copy_and_removes_only_unsafe_sentence(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    draft = {
        "title": "从0到1进入南非市场，先拆这四个物流节点",
        "body": "进入南非市场，不是先找一句口号，而是先拆清运输、入库、分拣和末端交付。评论区留言单号，我们帮你查实时进度。",
        "hashtags": ["南非市场", "查进度"],
        "scenes": [
            {"scene": index, "duration": 8, "visual": f"物流节点{index}", "voiceover": f"第{index}步先核对画面里能确认的物流动作。", "text_overlay": f"节点{index}"}
            for index in range(1, 9)
        ],
    }

    async def fake_complete(*_args, **_kwargs):
        return __import__("json").dumps(draft, ensure_ascii=False)

    monkeypatch.setattr(ai_engine, "_complete_json_messages", fake_complete)
    output = (await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "请围绕从0到1开拓南非市场创作一篇内容"}],
        platforms=["douyin"], topic="从0到1开拓南非市场",
    ))[0]

    assert "运输、入库、分拣和末端交付" in output["body"]
    assert "查实时进度" not in output["body"]
    assert output["title"] == draft["title"]
    assert output["source"] == "model_sanitized"
