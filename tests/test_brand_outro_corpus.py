from brand_outro_corpus import BRAND_OUTRO_CORPUS, select_brand_outro
from hotspot_video_planner import append_brand_endcard_scenes


def test_brand_outro_corpus_has_ten_unique_entries_and_no_legacy_default():
    assert len(BRAND_OUTRO_CORPUS) == 10
    assert len({item["id"] for item in BRAND_OUTRO_CORPUS}) == 10
    assert all("Buffalo" in item["voiceover"] for item in BRAND_OUTRO_CORPUS)
    assert all("先理清订单信息" not in item["voiceover"] for item in BRAND_OUTRO_CORPUS)


def test_outro_selection_prefers_structured_logistics_node():
    assert select_brand_outro({"logistics_nodes": ["港口", "铁路", "集装箱"]})["id"] == "port_rail_container"
    assert select_brand_outro({"logistics_nodes": ["清关"]})["id"] == "customs_clearance"
    assert select_brand_outro({"logistics_nodes": ["末端", "配送"]})["id"] == "last_mile_delivery"


def test_outro_selection_uses_topic_text_when_nodes_are_missing():
    assert select_brand_outro({"logistics_topic": "仓储与分拣效率"})["id"] == "sorting_handoff"
    assert select_brand_outro({"logistics_topic": "运输安全与异常处理"})["id"] == "safety_exception"
    assert select_brand_outro({"logistics_topic": "南非电商旺季订单增长"})["id"] == "peak_season_scale"


def test_endcard_contains_selected_outro_metadata_and_text():
    scenes = append_brand_endcard_scenes(
        [{"scene_role": "owned_proof", "duration_ms": 5_000}],
        context={"logistics_nodes": ["仓储"]},
    )
    endcard = scenes[-1]
    assert endcard["outro_id"] == "warehouse_storage"
    assert "Buffalo" in endcard["voiceover"]
    assert endcard["text_overlay"] == "Buffalo｜仓配每一步，更清楚"
    assert endcard["brand_endcard_path"].endswith("buffalo-cape-town-van.png")
