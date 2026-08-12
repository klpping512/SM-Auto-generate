def test_mixed_news_frame_with_field_activity_is_not_deterministically_rejected():
    import hotspot_hook_selection_sop

    reason = hotspot_hook_selection_sop.obvious_rejection_reason([{
        "description": "新闻主播在左侧，右侧显示雨天道路和卡车排队。",
        "tags": [
            {"dimension": "scene", "value": "道路运输"},
            {"dimension": "object", "value": "卡车"},
        ],
    }])

    assert reason == ""


def test_anchor_only_frame_is_still_deterministically_rejected():
    import hotspot_hook_selection_sop

    reason = hotspot_hook_selection_sop.obvious_rejection_reason([{
        "description": "新闻主播在演播室播报交通新闻，画面只有字幕和台标。",
        "tags": [],
    }])

    assert reason == "仅非事件画面不能作为 Hook"
