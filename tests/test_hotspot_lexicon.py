"""Unit tests for the unified hotspot / logistics lexicon."""
from __future__ import annotations

import hotspot_lexicon as lexicon


def test_extract_terms_keeps_chinese_tokens_for_road_congestion():
    terms = lexicon.extract_terms("Musina 附近交通拥堵")

    assert "拥堵" in terms
    assert "交通" in terms
    assert "musina" in terms


def test_extract_terms_english_path_still_works():
    terms = lexicon.extract_terms("Cape Town Transnet port congestion")

    assert "cape" in terms or "town" in terms or "capetown" in "".join(terms)
    assert "transnet" in terms
    assert "port" in terms
    assert "congestion" in terms


def test_category_profile_event_mode_tags_disruption_and_border():
    profile = lexicon.category_profile("Beitbridge 口岸清关延误，道路拥堵", mode="event")

    assert "border" in profile
    assert "disruption" in profile


def test_category_profile_topic_mode_takealot_implies_warehouse_and_last_mile():
    profile = lexicon.category_profile("Takealot 库存与配送体验怎么做", mode="topic")

    assert {"warehouse", "last_mile"} <= profile


def test_classify_event_type_merges_weather_and_strike():
    assert lexicon.classify_event_type("司机罢工导致全国停运") == "strike"
    assert lexicon.classify_event_type("暴雨洪水导致配送中断") == "weather"


def test_match_hook_terms_and_topic_keywords():
    hooks = lexicon.match_hook_terms("Musina 路段交通拥堵")
    assert "拥堵" in hooks or "交通" in hooks

    keywords = lexicon.topic_keyword_hits("R60 从 Robertson 到 Worcester 有卡车侧翻，路线怎么安排？")
    assert {"r60", "robertson", "worcester", "侧翻", "路线"} <= set(keywords)


def test_overlap_score_matches_chinese_event_to_segment():
    score = lexicon.overlap_score(
        "Musina 附近交通拥堵",
        "现场画面显示道路拥堵，卡车排队",
    )
    assert score > 0
    hits = lexicon.overlap_hits("Musina 附近交通拥堵", "现场画面显示道路拥堵，卡车排队")
    assert "拥堵" in hits


def test_event_types_cover_planner_and_package_keys():
    assert {"strike", "risk", "infrastructure", "ecommerce_growth", "weather", "policy"} <= set(lexicon.EVENT_TYPES)
    for key in lexicon.EVENT_TYPES:
        assert key in lexicon.EVENT_RELEVANCE
