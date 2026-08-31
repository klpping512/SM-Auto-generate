"""Scene render contract, topic-faithful fallback, and production-state gates."""
from pathlib import Path

import pytest


def test_any_nonempty_custom_topic_builds_owned_only_contract():
    import video_topic_contract

    contract = video_topic_contract.build_topic_contract(
        "客户改地址后运单怎么重打", has_event_anchor=False,
    )
    assert contract["opening_mode"] == "owned_topic_hook"
    assert contract["requires_hotspot_fact"] is False
    assert "改地址" in contract["opening_hook"] or "运单" in contract["opening_hook"]


def test_opening_gate_accepts_user_topic_without_hotspot_hook():
    import video_topic_contract

    topic = "空运、海运、直邮：发南非到底怎么选才划算？"
    contract = video_topic_contract.build_topic_contract(topic, has_event_anchor=False)
    assert video_topic_contract.opening_scene_answers_topic(topic, contract)
    assert video_topic_contract.opening_scene_answers_topic(contract["opening_hook"], contract)
    assert not video_topic_contract.opening_scene_answers_topic("仓内随意作业。", contract)
    errors = video_topic_contract.validate_generated_topic_contract(
        {"title": contract["safe_title"], "scenes": [{"voiceover": contract["opening_hook"]}]},
        contract,
    )
    assert all("第一镜没有使用主题型开场" not in item for item in errors)


def test_deterministic_fallback_keeps_user_topic_on_scene_one():
    import app
    import video_topic_contract

    topic = "空运、海运、直邮：发南非到底怎么选才划算？"
    brief = {
        "raw_input": topic,
        "requested_topic": topic,
        "subject": topic,
        "logistics_topic": topic,
    }
    scenes = [
        {"scene_role": "owned_proof", "duration_ms": 6000, "primary_category": "warehouse"}
        for _ in range(7)
    ]
    scenes.append({
        "scene_role": "brand_cta", "evidence_type": "brand_endcard",
        "duration_ms": 3000, "render_kind": "brand_endcard",
    })
    generated = app._deterministic_formal_script(
        brief, scenes, None,
        fallback_reason="remote_model_output_invalid:validation_error",
    )
    first = generated["scenes"][0]["voiceover"]
    assert any(token in first for token in ("空运", "海运", "直邮", "南非", "怎么选"))
    assert "现场动作正在展开" not in first
    assert "风险影响仓储" not in first
    assert generated["scenes"][0]["copy_source"] == "fallback"
    assert "validation_error" in generated["scenes"][0]["copy_repair_reason"]
    contract = video_topic_contract.build_topic_contract(topic, has_event_anchor=False)
    remaining = video_topic_contract.validate_generated_topic_contract(generated, contract)
    assert all("第一镜没有使用主题型开场" not in item for item in remaining)


@pytest.mark.parametrize("reason", [
    "remote_model_output_invalid:timeout",
    "remote_model_output_invalid:json_error",
    "remote_model_output_invalid:validation_error",
])
def test_minimax_failure_reasons_use_topic_fallback(reason):
    import app

    topic = "南非清关文件不齐怎么办"
    generated = app._deterministic_formal_script(
        {"raw_input": topic, "requested_topic": topic, "subject": topic},
        [{"scene_role": "owned_proof", "duration_ms": 5000, "primary_category": "warehouse"}],
        None,
        fallback_reason=reason,
    )
    assert generated["scenes"][0]["copy_source"] == "fallback"
    assert generated["scenes"][0]["copy_repair_reason"] == reason
    assert "清关" in generated["scenes"][0]["voiceover"] or "文件" in generated["scenes"][0]["voiceover"]


def test_capacity_plan_inserts_text_cards_instead_of_hollow_video_slots():
    import video_render_contract

    plan = video_render_contract.plan_render_capacity(video_count=7, image_count=0, brand_endcard_count=1)
    assert plan["video_count"] == 7
    assert plan["text_card_count"] >= 1
    assert plan["scene_count"] == plan["video_count"] + plan["image_count"] + plan["text_card_count"] + 1


def test_text_card_does_not_require_asset_id():
    import video_render_contract

    scene = {"voiceover": "港口拥堵时如何改走。", "duration_ms": 3000}
    video_render_contract.materialize_text_card(scene, reason="素材耗尽", index=9)
    assert scene["render_kind"] == "text_card"
    assert scene.get("asset_id") in {None, 0}
    assert scene["text_card"]["text"]
    assert not scene.get("brand_endcard_path")
    errors = video_render_contract.validate_render_contract([scene])
    assert errors == []
    assert video_render_contract.scene_is_renderable(scene)


def test_ordinary_scene_cannot_masquerade_as_brand_endcard():
    import video_render_contract
    import video_renderer
    import video_state

    scene = {
        "scene_role": "owned_proof",
        "asset_source": "diversity_text_card",
        "voiceover": "退货反仓如何避免二次错分。",
        "duration_ms": 4000,
    }
    video_render_contract.materialize_text_card(scene, reason="去重降级", index=2)
    assert video_render_contract.infer_render_kind(scene) == "text_card"
    assert video_renderer.resolve_render_endcard_rel(scene) == ""
    assert video_renderer.scene_uses_cta_timing(scene) is False
    cta = {
        "scene_role": "brand_cta",
        "evidence_type": "brand_endcard",
        "brand_endcard_path": video_state.DEFAULT_BRAND_ENDCARD_PATH,
    }
    assert video_render_contract.infer_render_kind(cta) == "brand_endcard"
    assert video_renderer.resolve_render_endcard_rel(cta) == video_state.DEFAULT_BRAND_ENDCARD_PATH


def test_missing_tenth_scene_is_repaired_to_text_card_not_missing_asset_error():
    import video_render_contract

    scenes = [
        {
            "scene": index,
            "evidence_type": "owned_video",
            "asset_id": index,
            "asset_segment_id": index * 10,
            "duration_ms": 6000,
            "voiceover": f"仓内核对动作{index}。",
        }
        for index in range(1, 8)
    ]
    scenes.extend([
        {"scene": 8, "evidence_type": "owned_video", "asset_id": None, "duration_ms": 6000, "voiceover": "第八镜。"},
        {"scene": 9, "evidence_type": "owned_video", "asset_id": None, "duration_ms": 6000, "voiceover": "第九镜。"},
        {"scene": 10, "evidence_type": "owned_video", "asset_id": None, "duration_ms": 6000, "voiceover": "第十镜。"},
    ])
    repaired = video_render_contract.repair_scene_render_sources(scenes)
    assert repaired == 3
    assert scenes[9]["render_kind"] == "text_card"
    errors = video_render_contract.validate_render_contract(scenes)
    assert errors == []
    assert all("没有对应素材" not in item for item in errors)


def test_exhausted_inventory_can_be_all_text_cards():
    import video_render_contract

    scenes = [
        {"scene": index, "evidence_type": "owned_video", "duration_ms": 5000, "voiceover": f"节点{index}。"}
        for index in range(1, 8)
    ]
    video_render_contract.repair_scene_render_sources(scenes)
    summary = video_render_contract.contract_summary(scenes)
    assert summary["text_card_count"] == 7
    assert summary["renderable_scene_count"] == 7
    assert video_render_contract.validate_render_contract(scenes) == []


def test_mp4_present_quality_fail_is_hold_not_absent(tmp_path, monkeypatch):
    import video_state

    clip = tmp_path / "final.mp4"
    clip.write_bytes(b"not-empty")
    monkeypatch.setattr(video_state, "probe_video_artifact", lambda path: {
        "ok": bool(path), "exists": True, "readable": True, "duration_ms": 62_000, "path": str(path),
    })
    job = {
        "status": "succeeded",
        "output_path": str(clip),
        "quality_report": {"publication": {"tier": "quality_hold", "publish_allowed": False}},
    }
    assert video_state.derive_artifact_status(job) == "final"
    assert video_state.derive_quality_status(job) == "hold"
    assert video_state.result_label(job) == "quality_hold"


def test_artifact_absent_only_without_mp4(monkeypatch):
    import video_state

    monkeypatch.setattr(video_state, "probe_video_artifact", lambda path: {
        "ok": False, "exists": False, "readable": False, "duration_ms": 0, "path": path,
    })
    job = {"status": "failed", "output_path": "", "preview_path": ""}
    assert video_state.derive_artifact_status(job) == "absent"


def test_identical_plan_hash_retry_stops_after_same_repair(monkeypatch):
    import asyncio
    import video_generation

    stored = {}

    def fake_update(job_id, **kwargs):
        stored.update(kwargs)

    monkeypatch.setattr(video_generation.db, "update_video_generation_job", fake_update)
    monkeypatch.setattr(video_generation.db, "add_video_generation_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(video_generation, "video_generation_auto_retry_limit", lambda: 5)

    scenes = [{"asset_id": 1, "evidence_type": "owned_video", "duration_ms": 5000}]
    job = {
        "id": "job-1",
        "attempt": 1,
        "quality_report": {"script": {"scenes": scenes}},
    }

    async def run():
        first = await video_generation._schedule_automatic_video_retry(
            job, error_code="ValueError", error_message="第10镜没有对应素材",
            failed_stage="preview_rendering", quality_report=job["quality_report"],
        )
        job["attempt"] = 2
        job["quality_report"] = stored["quality_report"]
        second = await video_generation._schedule_automatic_video_retry(
            job, error_code="ValueError", error_message="第10镜没有对应素材",
            failed_stage="preview_rendering", quality_report=job["quality_report"],
        )
        job["attempt"] = 3
        job["quality_report"] = stored["quality_report"]
        third = await video_generation._schedule_automatic_video_retry(
            job, error_code="ValueError", error_message="第10镜没有对应素材",
            failed_stage="preview_rendering", quality_report=job["quality_report"],
        )
        return first, second, third

    first, second, third = asyncio.run(run())
    history = stored["quality_report"]["automatic_retry_history"]
    assert first is True
    assert second is True
    assert third is False
    assert history[0]["repair_action"] == "inspect_render_contract"
    assert history[1]["repair_action"] == "materialize_text_card"
    assert history[0]["plan_hash"] == history[1]["plan_hash"]


def test_preview_and_final_share_render_contract_module():
    import inspect
    import video_renderer

    source = inspect.getsource(video_renderer.render_job)
    assert "validate_render_contract" in source
    assert "repair_scene_render_sources" in source
    assert "第{index + 1}镜没有对应素材" not in source


def test_renderer_does_not_globally_replace_minimax_voiceover():
    import inspect
    import video_renderer

    source = inspect.getsource(video_renderer.normalize_script)
    assert "must never replace validated MiniMax copy" in source
    assert "风险影响仓储" not in source


def test_text_card_frame_is_a_real_image(tmp_path):
    import video_renderer

    output = tmp_path / "card.png"
    video_renderer._generate_text_card_frame("高温天气冷藏车如何预冷？", output, width=540, height=960)
    assert output.is_file()
    assert output.stat().st_size > 1000


def test_different_topics_do_not_share_text_card_signature():
    import video_render_contract
    import video_state

    def scenes_for(topic: str) -> list[dict]:
        scene = {"voiceover": topic, "duration_ms": 4000, "scene": 1}
        video_render_contract.materialize_text_card(scene, reason="exhausted", index=0)
        return [scene]

    first = video_state.scene_asset_signature(scenes_for("同城配送时效怎么比较"))
    second = video_state.scene_asset_signature(scenes_for("危险品入仓如何核对"))
    assert first != second
