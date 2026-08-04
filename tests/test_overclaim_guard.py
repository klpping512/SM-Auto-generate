"""清关 preparation 模式文案门禁测试：完成词全拦、准备词不误杀、作用域、放闸与 B1/B2 集成。"""
import json

import hotspot_preview_narration as narration
import hotspot_video_planner as planner


CUSTOMS_NODES = ["清关"]

# 指令要求必须放行的准备式措辞
PREPARATION_PHRASES = (
    "备货待清关",
    "清关前发运准备",
    "等待海关放行",
    "发运前把单证备齐",
    "待通关",
    "准备清关",
    "备齐单证",
)

ALL_DONE_TERMS = narration.CUSTOMS_DONE_CLAIMS + narration.DELIVERY_DONE_CLAIMS


def test_every_completion_term_is_blocked():
    """16 个完成词在 warehouse/delivery + 清关节点组合下全部被拦。"""
    for term in ALL_DONE_TERMS:
        for category in ("warehouse", "delivery", "staff", "facility"):
            voiceover = f"你的订单{term}，可以放心。"
            issues = narration.overclaim_completion_issues(voiceover, category, CUSTOMS_NODES)
            assert issues, f"完成词「{term}」未被拦截（category={category}）"


def test_preparation_phrases_are_allowed():
    """准备式措辞全部放行（证明不误杀）。"""
    for phrase in PREPARATION_PHRASES:
        for category in ("warehouse", "delivery", "staff", "facility"):
            issues = narration.overclaim_completion_issues(phrase, category, CUSTOMS_NODES)
            assert not issues, f"准备式措辞「{phrase}」被误杀（category={category}）: {issues}"


def test_scope_real_customs_asset_is_allowed():
    """真 customs 素材有权说完成，不受此门禁约束。"""
    for term in ALL_DONE_TERMS:
        assert not narration.overclaim_completion_issues(f"{term}", "customs", CUSTOMS_NODES)


def test_scope_non_customs_node_is_allowed():
    """非清关话题不受此门禁约束。"""
    for term in ALL_DONE_TERMS:
        assert not narration.overclaim_completion_issues(f"{term}", "warehouse", ["仓储", "末端"])


def test_node_term_matching_is_case_insensitive():
    issues = narration.overclaim_completion_issues("已清关", "warehouse", ["Customs"])
    assert issues
    issues = narration.overclaim_completion_issues("已清关", "warehouse", ["关税"])
    assert issues


def test_safe_template_never_contains_completion_terms():
    """安全兜底模板本身绝不含任何完成词（所有分类 × 所有长度档位）。"""
    for category in ("warehouse", "delivery", "staff", "facility", "other"):
        for max_chars in (None, 12, 20, 28, 60):
            for min_chars in (None, 5, 12):
                copy = planner.safe_customs_preparation_copy(
                    category, max_chars=max_chars, min_chars=min_chars,
                )
                assert copy
                for term in ALL_DONE_TERMS:
                    assert term not in copy, f"模板含完成词「{term}」: {copy}"


def test_safe_template_respects_char_bounds():
    copy = planner.safe_customs_preparation_copy("warehouse", max_chars=26, min_chars=5)
    assert len("".join(copy.split())) <= 26


def _warehouse_scene(duration_ms: int = 8_000) -> dict:
    return {
        "scene": 2, "scene_role": "owned_proof", "evidence_type": "owned_video",
        "duration_ms": duration_ms, "primary_category": "warehouse",
        "asset_id": 11, "asset_segment_id": 21,
    }


def test_b1_guard_replaces_overclaim_and_records():
    """B1 语义：模型说了已清关 → 被回退成准备式 → 记录 overclaim_guard。"""
    scenes = [
        {"scene": 1, "scene_role": "hotspot_evidence", "evidence_type": "hotspot_video",
         "duration_ms": 7_000},
        _warehouse_scene(),
    ]
    generated_scenes = [
        {"voiceover": "现场正在发生：口岸拥堵。你的订单还能按计划走吗？", "text_overlay": "口岸拥堵"},
        {"voiceover": "你的货物已清关，Buffalo 仓内已经放行。", "text_overlay": "已清关"},
    ]
    records = narration.apply_overclaim_guard(generated_scenes, scenes, CUSTOMS_NODES)
    assert len(records) == 1
    record = records[0]
    assert record["scene"] == 2
    assert record["primary_category"] == "warehouse"
    assert "已清关" in record["original_voiceover"]
    # 回退后的文案：无任何完成词，且是准备式
    replaced = generated_scenes[1]["voiceover"]
    for term in ALL_DONE_TERMS:
        assert term not in replaced
        assert term not in generated_scenes[1]["text_overlay"]
    assert not narration.overclaim_completion_issues(replaced, "warehouse", CUSTOMS_NODES)
    assert record["replaced_voiceover"] == replaced
    # 热点镜头不被误动
    assert "口岸拥堵" in generated_scenes[0]["voiceover"]


def test_b1_final_scenes_carry_no_overclaim_into_render():
    """端到端气密：guard 之后写入 scene 的文案（进渲染/TTS/字幕）不含完成词。"""
    scenes = [_warehouse_scene()]
    generated_scenes = [{"voiceover": "货物已送达客户，妥投完成。", "text_overlay": "已送达"}]
    narration.apply_overclaim_guard(generated_scenes, scenes, CUSTOMS_NODES)
    for scene, generated_scene in zip(scenes, generated_scenes):
        scene.update(generated_scene)
    for scene in scenes:
        copy = f"{scene.get('voiceover', '')} {scene.get('text_overlay', '')}"
        for term in ALL_DONE_TERMS:
            assert term not in copy, f"完成词「{term}」进入了渲染文案"
        # 渲染字幕由 voiceover 生成，同样不能含完成词
        from video_renderer import build_subtitle_cues
        cues = build_subtitle_cues(scene["voiceover"], 8.0)
        cue_text = "".join(cue["text"] for cue in cues)
        for term in ALL_DONE_TERMS:
            assert term not in cue_text


def test_eligible_owned_categories_opens_preparation_mode():
    brief = {"topic_brief_id": "tb-1", "logistics_nodes": ["清关"]}
    assert planner._eligible_owned_categories(brief) == {"customs", "warehouse", "delivery"}
    brief_tariff = {"topic_brief_id": "tb-2", "logistics_nodes": ["关税"]}
    assert planner._eligible_owned_categories(brief_tariff) == {"customs", "warehouse", "delivery"}
    # 非清关节点行为不变
    warehouse_brief = {"topic_brief_id": "tb-3", "logistics_nodes": ["仓储"]}
    assert planner._eligible_owned_categories(warehouse_brief) == planner.NODE_CATEGORY_RULES.get("仓储", set())


def _owned_segment(category: str, asset_id: int, segment_id: int) -> dict:
    return {
        "id": segment_id, "asset_id": asset_id,
        "asset_file_type": "video", "asset_source": "upload",
        "primary_category": category, "tags": [], "quality_score": 0.8,
        "start_ms": 0, "end_ms": 8_000, "description": f"{category} 现场",
    }


def test_owned_candidates_admits_warehouse_for_customs_brief():
    """放闸生效：customs brief 下 warehouse 素材从被拒到可用。"""
    brief = {"topic_brief_id": "tb-1", "logistics_nodes": ["清关"],
             "logistics_topic": "南非清关准备"}
    segments = [_owned_segment("warehouse", 11, 21), _owned_segment("delivery", 12, 22)]
    admitted = planner._owned_candidates(segments, brief)
    admitted_categories = {item.get("primary_category") for item in admitted}
    assert admitted_categories == {"warehouse", "delivery"}
    # 旧行为对照：仅 {"customs"} 准入时这两条都会被拒
    old_gate = {"customs"}
    old_admitted = [
        item for item in segments
        if planner._functional_categories(item) & old_gate
    ]
    assert old_admitted == []


def test_voiceover_customs_branch_uses_preparation_copy():
    """改动②：warehouse/delivery + 清关节点的规划基线是准备式安全文案。"""
    brief = {"logistics_topic": "南非清关", "logistics_nodes": ["清关"]}
    for category in ("warehouse", "delivery"):
        copy = planner._voiceover(brief, "owned_proof", 2, "", category)
        for term in ALL_DONE_TERMS:
            assert term not in copy
        assert "清关前" in copy
    # 末端既有分支不受影响
    last_mile = {"logistics_topic": "末端配送", "logistics_nodes": ["末端"]}
    copy = planner._voiceover(last_mile, "owned_proof", 1, "", "warehouse")
    assert "配送前" in copy


def test_locked_scenes_carry_primary_category():
    """B2：locked_scenes 补上 primary_category，模型可区分 warehouse 上下文。"""
    scenes = [
        {"scene_role": "owned_proof", "evidence_type": "owned_video",
         "primary_category": "warehouse", "visual": "仓内备货",
         "asset_id": 11, "duration_ms": 8_000},
    ]
    messages = narration.build_messages("清关准备", {"hotspot_title": "口岸拥堵"},
                                        scenes, [], [])
    payload = json.loads(messages[1]["content"])
    assert payload["locked_scenes"][0]["primary_category"] == "warehouse"
