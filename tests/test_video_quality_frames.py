from pathlib import Path

import pytest


def test_full_scan_budget_targets_forty_frames_for_one_minute_video():
    from video_quality.frame_extractor import plan_extraction

    plan = plan_extraction(46.8, mode="balanced", max_frames=40)

    assert plan.target_frames == 40
    assert plan.fps == pytest.approx(40 / 46.8)


def test_even_sampling_always_keeps_first_and_last():
    from video_quality.frame_extractor import even_indices

    selected = even_indices(11, 4)

    assert selected[0] == 0
    assert selected[-1] == 10
    assert len(selected) == 4


def test_mean_pixel_delta_detects_near_duplicates():
    from video_quality.frame_extractor import frame_delta

    assert frame_delta(bytes([10, 10]), bytes([11, 11])) == 1
    assert frame_delta(bytes([10]), bytes([10, 10])) == float("inf")


def test_dedup_compares_against_last_kept_frame_and_deletes_dropped(tmp_path):
    from video_quality.frame_extractor import dedupe_by_thumbnails

    paths = [tmp_path / f"frame_{index:04d}.jpg" for index in range(3)]
    for path in paths:
        path.write_bytes(b"x")
    candidates = [
        {"index": index, "path": str(path), "timestamp_seconds": float(index), "reason": "uniform"}
        for index, path in enumerate(paths)
    ]
    thumbnails = [bytes([10, 10]), bytes([11, 11]), bytes([14, 14])]

    kept, dropped = dedupe_by_thumbnails(candidates, thumbnails, threshold=2)

    assert dropped == 1
    assert [item["timestamp_seconds"] for item in kept] == [0.0, 2.0]
    assert not paths[1].exists()
    assert [item["index"] for item in kept] == [0, 1]


@pytest.mark.parametrize(
    ("mode", "engine"),
    [("efficient", "keyframe"), ("balanced", "scene"), ("detailed", "uniform")],
)
def test_three_modes_have_explicit_engines(mode, engine):
    from video_quality.frame_extractor import plan_extraction

    assert plan_extraction(30, mode=mode).engine == engine


def test_focus_mode_uses_requested_density_but_obeys_cap():
    from video_quality.frame_extractor import plan_extraction

    plan = plan_extraction(
        4.0,
        mode="detailed",
        focus=True,
        requested_fps=8,
        max_frames=20,
    )

    assert plan.fps == 8
    assert plan.target_frames == 20


def test_focus_density_is_limited_to_ten_fps():
    from video_quality.frame_extractor import plan_extraction

    assert plan_extraction(2, mode="detailed", focus=True, requested_fps=20).fps == 10
