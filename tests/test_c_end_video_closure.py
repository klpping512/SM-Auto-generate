from scripts.run_c_end_video_closure import (
    CEND_API_TIMEOUT_SECONDS,
    SECOND_ROUND_SELLER_SCENARIOS,
    SELLER_SCENARIOS,
    selected_seller_scenarios,
)


def test_c_end_video_closure_uses_ten_distinct_natural_seller_questions():
    assert len(SELLER_SCENARIOS) == 10
    assert len(set(SELLER_SCENARIOS)) == 10
    assert all("帮我" in question and "60 秒" in question for question in SELLER_SCENARIOS)
    assert all("热点 Hook" not in question and "event_clip" not in question for question in SELLER_SCENARIOS)


def test_c_end_video_closure_waits_for_model_planning_without_aborting_the_batch():
    assert CEND_API_TIMEOUT_SECONDS >= 180


def test_c_end_video_closure_can_rerun_only_failed_scenarios():
    assert selected_seller_scenarios(6, 5) == SELLER_SCENARIOS[5:]


def test_second_round_uses_user_provided_distinct_logistics_topics():
    assert len(SECOND_ROUND_SELLER_SCENARIOS) == 4
    assert len(set(SECOND_ROUND_SELLER_SCENARIOS)) == 4
    assert all("60 秒" in question for question in SECOND_ROUND_SELLER_SCENARIOS)
    assert selected_seller_scenarios(1, 4, SECOND_ROUND_SELLER_SCENARIOS) == SECOND_ROUND_SELLER_SCENARIOS
