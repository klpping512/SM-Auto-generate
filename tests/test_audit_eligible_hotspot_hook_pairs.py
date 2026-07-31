from scripts.audit_eligible_hotspot_hook_pairs import eligible_hook_pairs


def _event(event_id, start_ms, end_ms, *, asset_id=9, hotspot_id=3, identity="r60-rollover"):
    return {
        "id": event_id, "asset_id": asset_id, "hotspot_id": hotspot_id,
        "start_ms": start_ms, "end_ms": end_ms, "title_zh": "R60 货车侧翻处置",
        "review_status": "confirmed", "clip_status": "ready", "clip_path": f"hooks/{event_id}.mp4",
        "evidence": {
            "event_identity": identity, "what_happened": "救援人员正在处置侧翻货车。",
            "hook_reason": "现场处置画面清晰", "logistics_question": "路线是否需要调整？",
        },
    }


def test_audit_keeps_only_one_same_event_non_overlapping_pair():
    pairs = eligible_hook_pairs([
        _event(1, 0, 6_000), _event(2, 6_000, 12_000),
        _event(3, 2_000, 7_000, identity="another-event"),
    ])

    assert pairs == [{
        "hotspot_id": 3, "asset_id": 9, "event_identity": "r60-rollover",
        "hook_ids": [1, 2], "title": "R60 货车侧翻处置",
        "what_happened": "救援人员正在处置侧翻货车。", "logistics_question": "路线是否需要调整？",
    }]


def test_audit_rejects_overlapping_hooks_even_when_the_event_identity_matches():
    assert eligible_hook_pairs([
        _event(1, 0, 6_000), _event(2, 5_500, 12_000),
    ]) == []
