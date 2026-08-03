import pytest

from video_clip_refs import ClipReferenceError, resolve_clip_ref
from video_duration_budget import (
    fit_scenes_to_budget,
    platform_budget_ms,
    rebalance_scenes_to_budget,
)


def test_platform_budget_defaults():
    assert platform_budget_ms("douyin") == 60_000
    assert platform_budget_ms("xiaohongshu") == 45_000
    assert platform_budget_ms("youtube") == 60_000
    assert platform_budget_ms("wechat") == 90_000


def test_budget_trims_last_scene_without_using_mother_clip():
    scenes = [
        {
            "duration": 18,
            "asset_id": 298,
            "event_clip_id": 2,
            "asset_start_ms": 6000,
            "asset_end_ms": 12000,
        },
        {"duration": 18, "asset_id": 140, "asset_segment_id": 9},
    ]
    result = fit_scenes_to_budget(scenes, 30_000)
    assert sum(item["duration_ms"] for item in result) == 30_000
    assert result[0]["event_clip_id"] == 2
    assert result[1]["duration_ms"] == 12_000


def test_budget_rejects_out_of_range_override():
    with pytest.raises(ValueError, match="15–180 秒"):
        platform_budget_ms("douyin", 10_000)


def test_rebalance_preserves_every_scene_and_hits_budget_exactly():
    scenes = [{"duration": value, "scene": index + 1} for index, value in enumerate((6, 7, 7, 6, 4, 3))]

    result = rebalance_scenes_to_budget(scenes, 30_000)

    assert len(result) == 6
    assert sum(item["duration_ms"] for item in result) == 30_000
    assert all(item["duration_ms"] >= 3_000 for item in result)
    assert result[-1]["scene"] == 6


def test_hotspot_mother_asset_without_event_ref_is_rejected():
    with pytest.raises(ClipReferenceError, match="必须选择热点事件片段"):
        resolve_clip_ref(
            {"asset_id": 298},
            {"id": 298, "hotspot_id": 31, "file_type": "video"},
            {},
        )


def test_event_clip_resolves_to_mother_range():
    result = resolve_clip_ref(
        {"asset_id": 298, "event_clip_id": 2},
        {"id": 298, "hotspot_id": 31, "file_type": "video"},
        {
            2: {
                "id": 2,
                "asset_id": 298,
                "start_ms": 6000,
                "end_ms": 12000,
                "duration_ms": 6000,
                "library_origin": "hotspot_event",
            }
        },
    )
    assert result["library_origin"] == "hotspot_event"
    assert result["start_ms"] == 6000
    assert result["end_ms"] == 12000
    assert result["duration_ms"] == 6000
