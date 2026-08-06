import json

import pytest


def test_cli_defaults_keep_automatic_regeneration_off(tmp_path):
    from scripts.archive.run_video_quality_mvp import build_parser, build_request

    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    args = build_parser().parse_args(["--video-source", str(video)])
    request = build_request(args)

    assert request.video_source == str(video)
    assert request.max_frames == 40
    assert request.auto_regenerate is False


def test_cli_loads_storyboard_json(tmp_path):
    from scripts.archive.run_video_quality_mvp import build_parser, build_request

    storyboard = tmp_path / "storyboard.json"
    storyboard.write_text(
        json.dumps({"title": "南非物流", "scenes": [{"duration": 5, "voiceover": "测试"}]}),
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "--video-source", str(tmp_path / "sample.mp4"),
        "--storyboard-json", str(storyboard),
    ])

    request = build_request(args)

    assert request.storyboard["scenes"][0]["voiceover"] == "测试"
    assert request.original_prompt == "南非物流"


def test_cli_requires_video_source_in_args_or_input_json():
    from scripts.archive.run_video_quality_mvp import build_parser, build_request

    args = build_parser().parse_args([])
    with pytest.raises(ValueError, match="video_source"):
        build_request(args)


def test_cli_accepts_complete_mvp_input_json(tmp_path):
    from scripts.archive.run_video_quality_mvp import build_parser, build_request

    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({
        "video_source": "/tmp/video.mp4",
        "original_prompt": "真实仓库履约",
        "storyboard": "三段分镜",
        "reference_images": [],
        "target_platform": "YouTube",
    }), encoding="utf-8")
    args = build_parser().parse_args(["--input-json", str(payload)])

    request = build_request(args)

    assert request.video_source == "/tmp/video.mp4"
    assert request.target_platform == "YouTube"
