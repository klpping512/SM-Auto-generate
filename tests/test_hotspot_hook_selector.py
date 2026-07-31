from hotspot_hook_selector import rank_hook_clips


def test_rank_hook_clips_prefers_short_traffic_event_with_explanation():
    clips = rank_hook_clips([
        {"id": 1, "asset_id": 8, "start_ms": 0, "end_ms": 8000, "clip_status": "ready", "confidence": .9,
         "review_status": "confirmed", "title_zh": "Musina 路段交通拥堵", "keywords": ["traffic", "congestion"]},
        {"id": 2, "asset_id": 8, "start_ms": 8000, "end_ms": 50000, "clip_status": "ready", "confidence": .2,
         "review_status": "review_required", "title_zh": "普通新闻演播室"},
    ])

    assert clips[0]["event_clip_id"] == 1
    assert clips[0]["hook_score"] > clips[1]["hook_score"]
    assert "交通" in clips[0]["content_description"]
    assert "片段时长" in clips[0]["hook_reason"]
