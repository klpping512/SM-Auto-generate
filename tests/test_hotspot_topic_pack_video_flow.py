import hotspot_logistics_planner
import hotspot_video_planner


def test_confirmed_package_creates_dynamic_video_brief_without_unrelated_media():
    package = {
        "id": 12,
        "title": "Johannesburg driver strike",
        "summary": "Drivers begin a shutdown in Johannesburg.",
        "event_type": "strike",
        "event_clips": [
            {"id": index, "asset_id": 90, "hotspot_id": 12, "title_zh": f"司机罢工现场 {index}", "clip_status": "ready"}
            for index in range(1, 4)
        ],
    }
    owned_segments = [
        {"id": index, "asset_id": index, "asset_file_type": "video", "primary_category": category,
         "asset_name": f"{category}-{index}", "start_ms": 0, "end_ms": 10000, "quality_score": 0.8}
        for index, category in enumerate(["warehouse", "delivery", "staff", "facility", "warehouse"], 1)
    ]

    brief = hotspot_logistics_planner.build_brief(package, owned_segments)
    scenes = hotspot_video_planner.build_scenes(package, owned_segments=owned_segments)

    assert brief["hotspot_type"] == "strike"
    # 默认不再用空白解释卡填充时长；没有传入自有图片时只规划真实热点和 Buffalo 视频。
    assert all(scene["source_type"] in {"hotspot_video", "owned_video"} for scene in scenes)
    assert not any(scene["source_type"] == "explanation_card" for scene in scenes)
    assert sum(scene["duration_ms"] for scene in scenes) == 49_000
