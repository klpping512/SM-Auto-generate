from pathlib import Path


def test_low_match_can_render_internal_preview_but_not_publishable():
    import video_generation

    decision = video_generation.quality_decision(
        score=42,
        requested_tier="internal_preview",
        issues=["第2镜头匹配偏弱"],
    )

    assert decision["render_allowed"] is True
    assert decision["publish_allowed"] is False
    assert decision["watermark"] == "内部测试｜素材待确认"
    assert decision["issues"] == ["第2镜头匹配偏弱"]


def test_publish_tier_still_rejects_low_match():
    import video_generation

    decision = video_generation.quality_decision(score=42, requested_tier="publish")

    assert decision["render_allowed"] is False
    assert decision["publish_allowed"] is False
    assert decision["watermark"] == ""


def test_scene_command_burns_internal_preview_watermark(tmp_path, monkeypatch):
    import video_renderer

    source = tmp_path / "source.png"
    wav = tmp_path / "voice.wav"
    source.touch()
    wav.touch()
    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav, [], 3.0,
        tmp_path / "preview.mp4", tmp_path, 0,
        output_size=(540, 960),
        watermark_text="内部测试｜素材待确认",
    )

    assert any("watermark-0.png" in value for value in command)
    assert "overlay=20:20" in " ".join(command)


def test_preview_subtitles_scale_to_preview_canvas(tmp_path):
    import video_renderer
    from PIL import Image

    source = tmp_path / "source.png"
    wav = tmp_path / "voice.wav"
    source.touch()
    wav.touch()

    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav,
        [{"text": "Customs Weekly List of Unentered Goods", "start": 0, "end": 2}],
        3.0, tmp_path / "preview.mp4", tmp_path, 0,
        output_size=(540, 960),
    )

    # 所有来源使用统一全宽字幕带，并留出移动端底部安全区。
    assert Image.open(tmp_path / "subtitle-0-0.png").size == (540, 173)
    assert "overlay=0:H-h-72" in " ".join(command)


def test_subtitle_safe_bottom_margin_scales_with_preview_and_final_height():
    import video_renderer

    assert video_renderer._subtitle_safe_bottom_margin(960) == 72
    assert video_renderer._subtitle_safe_bottom_margin(1920) == 144
    assert video_renderer._subtitle_safe_bottom_margin(720) == 54


def test_hotspot_news_subtitles_use_the_same_mask_as_owned_video(tmp_path):
    import video_renderer
    from PIL import Image

    source = tmp_path / "source.mp4"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, True, wav,
        [{"text": "热点事实说明。", "start": 0, "end": 2}],
        3.0, tmp_path / "preview.mp4", tmp_path, 0,
        output_size=(540, 960), subtitle_layout="hotspot_news",
    )

    subtitle = Image.open(tmp_path / "subtitle-0-0.png")
    # 热点和自有素材必须使用同一全宽字幕带，不能再切换第二种遮罩。
    assert subtitle.size == (540, 173)
    assert subtitle.getpixel((20, 20))[3] > 0


def test_long_unpunctuated_subtitle_is_split_instead_of_clipped():
    import video_renderer

    text = "Customs Weekly List of Unentered Goods now available for South Africa logistics teams"
    cues = video_renderer.build_subtitle_cues(text, 8.0)

    assert len(cues) >= 3
    assert max(len(cue["text"]) for cue in cues) <= 36
    assert " ".join(cue["text"] for cue in cues) == text


def test_video_project_labels_internal_preview_as_not_publishable():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "static" / "video-project.html").read_text(
        encoding="utf-8"
    )

    assert "内部测试预览" in page
    assert "不可进入发布队列" in page
    assert "publication?.publish_allowed" in page


def test_local_macos_tts_never_calls_external_model(tmp_path, monkeypatch):
    import video_renderer

    commands = []
    output = tmp_path / "voice.wav"
    monkeypatch.setattr(video_renderer.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[command.index("-o") + 1] if "-o" in command else command[-1]).touch()

    monkeypatch.setattr(video_renderer.subprocess, "run", fake_run)

    video_renderer.synthesize_local_macos("内部预览旁白", output)

    assert commands[0][0].endswith("/say")
    assert commands[1][0].endswith("/ffmpeg")
    assert all("http" not in " ".join(command) for command in commands)
