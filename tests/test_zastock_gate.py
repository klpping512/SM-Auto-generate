"""za-stock 受控开闸：匹配放行 + 文案强制安全模板。"""
from __future__ import annotations

import hotspot_preview_narration as narration
import hotspot_video_planner as planner


def _seg(
    segment_id: int,
    asset_id: int,
    category: str,
    *,
    file_type: str = "video",
    source: str | None = "upload",
    description: str = "",
    quality: float = 0.8,
    tags: list | None = None,
) -> dict:
    return {
        "id": segment_id,
        "asset_id": asset_id,
        "primary_category": category,
        "asset_file_type": file_type,
        "asset_source": source,
        "description": description or f"{category} scene",
        "quality_score": quality,
        "tags": tags or [],
        "start_ms": 0,
        "end_ms": 5_000,
    }


def _customs_brief() -> dict:
    return {
        "topic_brief_id": "zastock-gate-test",
        "logistics_topic": "清关风险",
        "logistics_nodes": ["清关"],
    }


def test_is_buffalo_usable_source_accepts_zastock():
    assert planner._is_buffalo_usable_source({"asset_source": "za_stock_license"}) is True
    assert planner._is_buffalo_usable_source({"asset_source": "mixkit_license"}) is False
    assert planner._is_buffalo_usable_source({"asset_source": "upload"}) is True


def test_owned_candidates_admits_zastock_customs():
    brief = _customs_brief()
    segments = [
        _seg(1, 866, "customs", source="za_stock_license", description="港口集装箱"),
        _seg(2, 10, "customs", source="upload", description="自有报关现场"),
        _seg(3, 11, "customs", source="mixkit_license", description="应被排除"),
    ]
    candidates = planner._owned_candidates(segments, brief)
    asset_ids = {int(item["asset_id"]) for item in candidates}
    assert 866 in asset_ids
    assert 10 in asset_ids
    assert 11 not in asset_ids


def test_zastock_scene_forced_safe_copy():
    scenes = [
        {
            "scene": 1,
            "scene_role": "owned_proof",
            "evidence_type": "owned_video",
            "duration_ms": 8_000,
            "primary_category": "customs",
            "asset_source": "za_stock_license",
            "asset_id": 866,
        },
        {
            "scene": 2,
            "scene_role": "owned_proof",
            "evidence_type": "owned_video",
            "duration_ms": 8_000,
            "primary_category": "customs",
            "asset_source": "upload",
            "asset_id": 10,
        },
    ]
    generated = [
        {"voiceover": "已清关完成，货物顺利放行。", "text_overlay": "已清关"},
        {"voiceover": "已清关完成，货物顺利放行。", "text_overlay": "已清关"},
    ]
    records = narration.apply_overclaim_guard(generated, scenes, ["清关"])
    forced = [row for row in records if row.get("mode") == "whitelist_forced"]
    assert len(forced) == 1
    assert forced[0]["asset_source"] == "za_stock_license"
    assert generated[0]["voiceover"] != "已清关完成，货物顺利放行。"
    assert generated[1]["voiceover"] == "已清关完成，货物顺利放行。"


def test_zastock_non_customs_topic_does_not_inject_customs_copy():
    scenes = [
        {
            "scene": 1,
            "scene_role": "owned_proof",
            "evidence_type": "owned_video",
            "duration_ms": 8_000,
            "primary_category": "delivery",
            "asset_source": "za_stock_license",
            "asset_id": 866,
        },
    ]
    original = "风险影响配送，Buffalo交接更可核对。"
    generated = [
        {"voiceover": original, "text_overlay": "仓配核对"},
    ]
    records = narration.apply_overclaim_guard(generated, scenes, ["仓储", "末端"])
    assert records == []
    assert generated[0]["voiceover"] == original
    for term in ("清关", "海关", "放行", "报关", "通关"):
        assert term not in generated[0]["voiceover"]


def test_zastock_diag_passed_category():
    brief = _customs_brief()
    segments = [
        _seg(1, 866, "customs", source="za_stock_license"),
        _seg(2, 867, "customs", source="za_stock_license"),
    ]
    diag = planner.diagnose_owned_matching(segments, brief)
    assert diag["funnel"]["not_licensed_stock"] == 2
    assert diag["funnel"]["category_match"] == 2
    assert diag["funnel"]["after_dedup"] == 2
    assert diag["verdict"] == "thin_but_matched"
    assert diag["category_inventory"] == {}
