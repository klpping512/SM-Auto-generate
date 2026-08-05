"""清关 preparation 模式文案门禁测试：白名单正向强制、完成词全拦、准备词不误杀、作用域、放闸与 B1/B2 集成。

门禁升级为两层（黑名单 → 白名单/正向强制）：
1. whitelist_forced：非-customs 素材 × customs 节点的危险 scene 无条件用安全模板（不看文本）；
2. blacklist_fallback：其余 scene 保留完成词检测回退（防御纵深，黑名单未删）。
"""
import json

import hotspot_preview_narration as narration
import hotspot_video_planner as planner


CUSTOMS_NODES = ["清关"]

# P1 终局黑名单方案实测漏过的 6 句自然完成说法（本次升级的核心证据目标）
LEAKED_COMPLETION_PHRASES = (
    "已出关",
    "通关手续办妥",
    "顺利过关",
    "货物已顺利放行",
    "海关放行了货物",
    "已经清关完毕",
)

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
    # 升级后该危险 scene 走白名单强制（不再是黑名单命中后才回退）
    assert record["mode"] == "whitelist_forced"
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


# ---------------------------------------------------------------------------
# 门禁升级（黑名单 → 白名单/正向强制）验收用例
# ---------------------------------------------------------------------------


def test_requires_safe_customs_copy_scope_matches_blacklist_scope():
    """白名单触发范围与黑名单完全同源：同类别×同节点组合判定一致。"""
    for category in ("warehouse", "delivery", "staff", "facility"):
        for nodes in (["清关"], ["Customs"], ["关税"], ["清关", "末端"]):
            assert narration.requires_safe_customs_copy(category, nodes)
            assert narration.requires_safe_customs_copy(category.upper(), nodes)
    # 真 customs 素材、非清关节点、未知类别均不强制
    assert not narration.requires_safe_customs_copy("customs", ["清关"])
    assert not narration.requires_safe_customs_copy("warehouse", ["仓储", "末端"])
    assert not narration.requires_safe_customs_copy("brand", ["清关"])
    assert not narration.requires_safe_customs_copy("", ["清关"])
    assert not narration.requires_safe_customs_copy("warehouse", [])
    assert not narration.requires_safe_customs_copy("warehouse", None)


def test_leaked_six_phrases_are_all_whitelist_forced():
    """核心证据：P1 终局黑名单漏过的 6 句自然完成说法，现在全部被白名单
    强制替换成安全模板（mode=whitelist_forced），最终文案不含任一句原文。
    这证明白名单堵住了黑名单的 6 个漏点——不再依赖禁词枚举。"""
    for phrase in LEAKED_COMPLETION_PHRASES:
        for category in ("warehouse", "delivery"):
            scenes = [{**_warehouse_scene(), "primary_category": category}]
            generated = [{"voiceover": f"好消息：{phrase}。", "text_overlay": phrase}]
            records = narration.apply_overclaim_guard(generated, scenes, CUSTOMS_NODES)
            assert len(records) == 1, f"「{phrase}」未被强制（category={category}）"
            assert records[0]["mode"] == "whitelist_forced"
            replaced = generated[0]["voiceover"]
            assert phrase not in replaced
            assert phrase not in generated[0]["text_overlay"]
            for term in ALL_DONE_TERMS:
                assert term not in replaced
            assert replaced == records[0]["replaced_voiceover"]


def test_whitelist_forces_even_benign_copy():
    """强制不看文本：完全无害的准备式文案也被替换。这是预期行为，不是误伤——
    白名单的原理是“剥夺危险 scene 的自由文本”，代价（文案多样性下降）被刻意
    限定在借用清关上下文的少数危险 scene。"""
    scenes = [_warehouse_scene()]
    benign = "备货待清关"
    assert not narration.overclaim_completion_issues(benign, "warehouse", CUSTOMS_NODES)
    generated = [{"voiceover": benign, "text_overlay": benign}]
    records = narration.apply_overclaim_guard(generated, scenes, CUSTOMS_NODES)
    assert len(records) == 1
    assert records[0]["mode"] == "whitelist_forced"
    assert records[0]["original_voiceover"] == benign
    replaced = generated[0]["voiceover"]
    assert replaced != benign
    assert narration.requires_safe_customs_copy("warehouse", CUSTOMS_NODES)
    # 替换后的文案必为 warehouse 的某个安全模板变体
    variants = {
        planner.safe_customs_preparation_copy("warehouse", max_chars=m, min_chars=5)
        for m in (None, 12, 20, 28, 60, 150)
    }
    assert replaced in variants


def test_whitelist_does_not_touch_real_customs_or_other_nodes():
    """范围正确不误伤：真 customs 素材与非清关节点的 warehouse scene 原样保留。"""
    # 真 customs 素材：即使文案含完成词也不强制（它有权说完成）
    customs_scene = {**_warehouse_scene(), "primary_category": "customs"}
    voiceover = "你的货物已清关，可以放心安排后续。"
    generated = [{"voiceover": voiceover, "text_overlay": "已清关"}]
    records = narration.apply_overclaim_guard(generated, [customs_scene], CUSTOMS_NODES)
    assert records == []
    assert generated[0]["voiceover"] == voiceover
    # 非清关节点的 warehouse scene：模型文案原样保留
    warehouse_scene = _warehouse_scene()
    voiceover2 = "仓内备货有条不紊，异常先留在仓内。"
    generated2 = [{"voiceover": voiceover2, "text_overlay": "仓内备货"}]
    records2 = narration.apply_overclaim_guard(generated2, [warehouse_scene], ["仓储", "末端"])
    assert records2 == []
    assert generated2[0]["voiceover"] == voiceover2


def test_overlay_only_overclaim_cannot_leak_past_guard():
    """对抗审查发现点：旧黑名单只替换 voiceover，字幕单独含禁词时会漏。
    白名单强制同时覆盖 voiceover 与 text_overlay，堵住该漏点。"""
    scenes = [_warehouse_scene()]
    generated = [{"voiceover": "清关前的仓内备货正在进行。", "text_overlay": "已经清关完毕"}]
    records = narration.apply_overclaim_guard(generated, scenes, CUSTOMS_NODES)
    assert records and records[0]["mode"] == "whitelist_forced"
    for term in ALL_DONE_TERMS:
        assert term not in generated[0]["voiceover"]
        assert term not in generated[0]["text_overlay"]


def test_blacklist_fallback_still_active(monkeypatch):
    """黑名单兜底仍活（防御纵深未删）：临时收窄白名单作用域使某类别不被强制，
    含完成词的文案仍被 overclaim_completion_issues 命中并回退
    （mode=blacklist_fallback）。生产路径下白名单已覆盖整个危险面，
    兜底命中预期≈0；本用例证明第二道防线的代码路径仍然可用。"""
    monkeypatch.setattr(
        narration, "BORROWED_CUSTOMS_CONTEXT",
        frozenset({"warehouse", "delivery", "facility"}),
    )
    scenes = [{**_warehouse_scene(), "primary_category": "staff"}]
    generated = [{"voiceover": "你的订单已送达，可以放心。", "text_overlay": "已送达"}]
    assert not narration.requires_safe_customs_copy("staff", CUSTOMS_NODES)
    records = narration.apply_overclaim_guard(generated, scenes, CUSTOMS_NODES)
    assert len(records) == 1
    assert records[0]["mode"] == "blacklist_fallback"
    assert records[0]["issues"]
    assert "已送达" not in generated[0]["voiceover"]


def test_b1_end_to_end_leaked_phrase_never_reaches_render():
    """B1 端到端：customs brief + 仅 warehouse 素材，模型产出“已经清关完毕”
    （P1 终局漏过的词）→ 成片该 scene 文案被强制成准备式、report 记
    whitelist_forced，写回 scene 后的渲染文案绝无任何完成型宣称。"""
    brief = {"topic_brief_id": "tb-1", "logistics_nodes": ["清关"],
             "logistics_topic": "南非清关准备"}
    # 放闸前提核实：该 brief 只允许 customs/warehouse/delivery 素材
    assert planner._eligible_owned_categories(brief) == {"customs", "warehouse", "delivery"}
    scenes = [_warehouse_scene()]
    generated = [{"voiceover": "已经清关完毕，货物开始派送。", "text_overlay": "清关完毕"}]
    # app.py:_generate_topic_brief_video 的同址调用（一字未改）
    records = narration.apply_overclaim_guard(
        generated, scenes, brief.get("logistics_nodes") or [],
    )
    assert len(records) == 1
    assert records[0]["mode"] == "whitelist_forced"
    replaced = generated[0]["voiceover"]
    assert "清关前" in replaced  # 强制成准备式
    # 模拟 B1 把生成文案写回 scene（app.py 同址逻辑）后进渲染
    for scene, generated_scene in zip(scenes, generated):
        scene.update(generated_scene)
    for scene in scenes:
        copy = f"{scene.get('voiceover', '')} {scene.get('text_overlay', '')}"
        for term in ALL_DONE_TERMS:
            assert term not in copy, f"完成词「{term}」进入了渲染文案"
        for phrase in LEAKED_COMPLETION_PHRASES:
            assert phrase not in copy, f"漏点句「{phrase}」进入了渲染文案"


def test_whitelist_diversity_cost_is_bounded_and_visible():
    """多样性代价可见：被强制的多条 scene 文案取自安全模板的有限变体。
    代价确实存在（每个类别只有有限种准备式说法），但被严格限定在
    危险 scene；变体数量在此如实断言，供总指挥知情。"""
    categories = ("warehouse", "delivery", "staff", "facility")
    scenes = [
        {**_warehouse_scene(), "scene": i + 1, "primary_category": category}
        for i, category in enumerate(categories)
    ]
    generated = [
        {"voiceover": f"模型自由发挥的文案{i}", "text_overlay": f"自由{i}"}
        for i in range(len(categories))
    ]
    records = narration.apply_overclaim_guard(generated, scenes, CUSTOMS_NODES)
    assert len(records) == len(categories)
    assert all(record["mode"] == "whitelist_forced" for record in records)
    # 每个类别的合法变体枚举（模板长句在前，按字数边界选档，最多 4 档）
    variant_pool = {
        category: {
            planner.safe_customs_preparation_copy(category, max_chars=m, min_chars=5)
            for m in (None, 12, 20, 28, 60, 150)
        }
        for category in categories
    }
    for category, item in zip(categories, generated):
        assert item["voiceover"] in variant_pool[category]
        assert item["text_overlay"] == item["voiceover"].rstrip("。")[:24]
    total_variants = sum(len(pool) for pool in variant_pool.values())
    # 如实记录：4 个危险类别共 16 个安全变体（每类 4 档）——代价有限且可见
    assert total_variants == 16
    # 同一类别的多条危险 scene 会收敛到同一变体（多样性的代价本身）
    scenes2 = [_warehouse_scene(), {**_warehouse_scene(), "scene": 3}]
    generated2 = [
        {"voiceover": "甲镜自由文案", "text_overlay": "甲"},
        {"voiceover": "乙镜自由文案", "text_overlay": "乙"},
    ]
    narration.apply_overclaim_guard(generated2, scenes2, CUSTOMS_NODES)
    assert generated2[0]["voiceover"] == generated2[1]["voiceover"]
