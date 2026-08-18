import hotspot_video_planner


def _warehouse_segments(count=8):
    return [
        {
            "id": index,
            "asset_id": 1000 + index,
            "asset_file_type": "video",
            "file_type": "video",
            "asset_source": "local_directory",
            "primary_category": "warehouse",
            "description": f"海外仓备货动作 {index}",
            "asset_name": f"warehouse-{index}",
            "start_ms": 0,
            "end_ms": 6500,
            "tags": [],
        }
        for index in range(1, count + 1)
    ]


def test_owned_only_uses_reviewed_operational_video_when_node_pool_is_empty():
    brief = {
        "topic_brief_id": "brief-1",
        "logistics_topic": "运输",
        "logistics_nodes": ["运输"],
        "target_duration_ms": 60_000,
    }
    scenes = hotspot_video_planner.plan_followup_scenes(
        brief,
        [],
        _warehouse_segments(),
        target_duration_ms=57_000,
        allow_adaptation=True,
        chain_mode="owned_only",
    )

    owned = [scene for scene in scenes if scene["evidence_type"] == "owned_video"]
    assert len(owned) == 8
    assert all(scene["owned_match_mode"] == "broad_operational_fallback" for scene in owned)
    assert all("热点事实" in "；".join(scene["match_reasons"]) for scene in owned)
    assert sum(int(scene["duration_ms"]) for scene in scenes) >= 50_000


def test_owned_only_does_not_use_unclassified_other_video_as_operational_fallback():
    brief = {
        "topic_brief_id": "brief-2",
        "logistics_topic": "运输",
        "logistics_nodes": ["运输"],
        "target_duration_ms": 60_000,
    }
    other = [{**item, "primary_category": "other"} for item in _warehouse_segments()]
    scenes = hotspot_video_planner.plan_followup_scenes(
        brief,
        [],
        other,
        target_duration_ms=57_000,
        allow_adaptation=True,
        chain_mode="owned_only",
    )

    assert scenes == []
