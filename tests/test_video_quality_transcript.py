def test_youtube_rolling_vtt_cues_are_collapsed(tmp_path):
    from video_quality.transcript_service import parse_vtt

    source = tmp_path / "captions.vtt"
    source.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\nSouth Africa\n\n"
        "00:00:02.000 --> 00:00:03.000\nSouth Africa logistics\n\n"
        "00:00:03.000 --> 00:00:04.000\nSouth Africa logistics\n",
        encoding="utf-8",
    )

    segments = parse_vtt(source)

    assert segments == [{"start": 1.0, "end": 4.0, "text": "South Africa logistics"}]


def test_transcript_range_keeps_overlapping_cues():
    from video_quality.transcript_service import clip_segments

    segments = [
        {"start": 1.0, "end": 3.0, "text": "A"},
        {"start": 4.0, "end": 5.0, "text": "B"},
    ]

    assert clip_segments(segments, 2.5, 4.2) == segments


def test_known_storyboard_skips_whisper(tmp_path):
    from video_quality.transcript_service import build_transcript

    called = []
    result = build_transcript(
        video_path=tmp_path / "video.mp4",
        output_path=tmp_path / "transcript.vtt",
        storyboard={"scenes": [{"duration": 4, "voiceover": "第一句"}]},
        whisper_model_path=None,
        transcriber=lambda *_: called.append(True),
    )

    assert result.status == "storyboard"
    assert result.segments[0]["start"] == 0
    assert result.segments[0]["end"] == 4
    assert called == []
    assert (tmp_path / "transcript.vtt").read_text(encoding="utf-8").startswith("WEBVTT")


def test_native_vtt_is_used_before_whisper(tmp_path):
    from video_quality.transcript_service import build_transcript

    subtitle = tmp_path / "native.vtt"
    subtitle.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nNative caption\n",
        encoding="utf-8",
    )
    called = []

    result = build_transcript(
        video_path=tmp_path / "video.mp4",
        output_path=tmp_path / "transcript.vtt",
        subtitle_path=subtitle,
        storyboard="",
        whisper_model_path="/unused/model",
        transcriber=lambda *_: called.append(True),
    )

    assert result.status == "native_subtitle"
    assert result.segments[0]["text"] == "Native caption"
    assert called == []


def test_missing_subtitles_and_model_produce_explicit_unavailable_status(tmp_path):
    from video_quality.transcript_service import build_transcript

    result = build_transcript(
        video_path=tmp_path / "video.mp4",
        output_path=tmp_path / "transcript.vtt",
        storyboard="",
        whisper_model_path=None,
    )

    assert result.status == "unavailable"
    assert result.segments == []
    assert result.warnings
    assert (tmp_path / "transcript.vtt").exists()
