from pathlib import Path

import pytest


def test_local_source_is_resolved_without_copy(tmp_path):
    from video_quality.source_resolver import resolve_video_source

    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    result = resolve_video_source(str(source), tmp_path / "work")

    assert result.video_path == source.resolve()
    assert result.downloaded is False


def test_remote_source_requires_https(tmp_path):
    from video_quality.source_resolver import VideoSourceError, resolve_video_source

    with pytest.raises(VideoSourceError, match="HTTPS"):
        resolve_video_source("http://example.com/sample.mp4", tmp_path)


def test_url_source_reports_missing_ytdlp(tmp_path, monkeypatch):
    import video_quality.source_resolver as resolver

    monkeypatch.setattr(resolver.shutil, "which", lambda name: None)
    with pytest.raises(resolver.VideoSourceError, match="yt-dlp"):
        resolver.resolve_video_source("https://example.com/sample.mp4", tmp_path)


def test_process_runner_honors_preexisting_cancel_request():
    from video_quality.process_runner import MediaProcessCanceled, run_process

    with pytest.raises(MediaProcessCanceled):
        run_process(["/usr/bin/true"], timeout=1, cancel_check=lambda: True)


def test_process_runner_terminates_on_timeout():
    from video_quality.process_runner import MediaProcessTimeout, run_process

    with pytest.raises(MediaProcessTimeout):
        run_process(["/bin/sleep", "2"], timeout=0.05)


def test_ffprobe_fraction_is_parsed():
    from video_quality.technical_validator import parse_frame_rate

    assert parse_frame_rate("30000/1001") == pytest.approx(29.970, rel=1e-3)
    assert parse_frame_rate("0/0") == 0


def test_detection_output_becomes_timestamped_issues():
    from video_quality.technical_validator import parse_detection_output

    stderr = "\n".join(
        [
            "[blackdetect] black_start:1.2 black_end:2.0 black_duration:0.8",
            "[freezedetect] lavfi.freezedetect.freeze_start: 5.4",
            "[freezedetect] lavfi.freezedetect.freeze_duration: 2.1",
            "[freezedetect] lavfi.freezedetect.freeze_end: 7.5",
            "[silencedetect] silence_start: 9.0",
            "[silencedetect] silence_end: 12.2 | silence_duration: 3.2",
        ]
    )
    issues = parse_detection_output(stderr)

    assert [issue["category"] for issue in issues] == ["black_frame", "freeze", "silence"]
    assert issues[1]["start_second"] == 5.4
    assert issues[1]["end_second"] == 7.5


def test_corrupt_video_fails_before_semantic_review(tmp_path):
    from video_quality.technical_validator import TechnicalValidationError, validate_video

    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"not-a-video")

    with pytest.raises(TechnicalValidationError, match="ffprobe"):
        validate_video(source)
