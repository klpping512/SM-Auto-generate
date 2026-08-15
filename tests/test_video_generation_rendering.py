from pathlib import Path

import pytest


def _input_bound(command, source: Path):
    source_index = command.index(str(source))
    before_source = command[:source_index]
    start = float(before_source[before_source.index("-ss") + 1])
    duration = float(before_source[before_source.index("-t") + 1])
    return start, duration


def test_scene_command_enforces_selected_shot_start_and_end(tmp_path, monkeypatch):
    import video_renderer

    source = tmp_path / "source.mp4"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    monkeypatch.setattr(video_renderer, "_has_audio", lambda *_: False)

    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, True, wav, [], 8.0,
        tmp_path / "out.mp4", tmp_path, 0,
        source_start=6.0, source_end=12.4,
    )

    start = float(command[command.index("-ss") + 1])
    assert start == 6.0
    assert "-stream_loop" not in command
    assert command[command.index("-t") + 1] == "8.0"


def test_normalize_script_preserves_precise_range_for_hotspot_event(tmp_db):
    import video_renderer

    asset_id = tmp_db.create_asset({
        "name": "热点母片", "filepath": "assets/hotspot.mp4", "file_type": "video", "category": "other",
        "duration": 60, "size": 10, "source": "upload", "status": "active", "sha256": "e" * 64,
    })
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "交通拥堵", "source_url": "https://example.com/hook", "publisher": "SA News",
        "published_at": "2026-07-27T00:00:00Z", "retrieved_at": "2026-07-27T00:00:00Z", "snapshot_sha256": "event-range",
    })
    event = tmp_db.replace_hotspot_event_clips(asset_id, hotspot_id, [{
        "event_index": 1, "start_ms": 0, "end_ms": 40_000, "title_zh": "交通拥堵", "title_en": "Traffic",
        "segments": [], "confidence": .9, "review_status": "confirmed",
    }])[0]
    script = video_renderer.normalize_script({"scenes": [
        {"duration": 8, "voiceover": "卡车在道路上排队。", "asset_id": asset_id,
         "event_clip_id": event["id"], "asset_start_ms": 12_000, "asset_end_ms": 20_000},
        {"duration": 8, "voiceover": "交付预期需要核对。"},
        {"duration": 8, "voiceover": "仓内开始准备。"},
        {"duration": 8, "voiceover": "路线需要沟通。"},
        {"duration": 8, "voiceover": "分拣动作可见。"},
        {"duration": 8, "voiceover": "最后核对交付。"},
    ]}, {asset_id}, event_lookup={event["id"]: event}, target_duration_ms=48_000)

    assert script["scenes"][0]["asset_start_ms"] == 12_000
    assert script["scenes"][0]["asset_end_ms"] == 20_000


def test_clip_source_command_materializes_exact_range_before_looping(tmp_path):
    import video_renderer

    source = tmp_path / "source.mp4"
    output = tmp_path / "selected.mp4"
    command = video_renderer._clip_source_command(
        "ffmpeg", source, output, 6.0, 12.4,
    )

    assert command[command.index("-ss") + 1] == "6.0"
    assert command[command.index("-t") + 1] == "6.4"
    assert command[-1] == str(output)


def test_preview_and_final_commands_use_explicit_output_sizes(tmp_path, monkeypatch):
    import video_renderer

    source = tmp_path / "source.png"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    preview = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav, [], 3.0,
        tmp_path / "preview.mp4", tmp_path, 0,
        output_size=(540, 960),
    )
    final = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav, [], 3.0,
        tmp_path / "final.mp4", tmp_path, 1,
        output_size=(1080, 1920),
    )

    assert "scale=540:960" in " ".join(preview)
    assert "crop=540:960" in " ".join(preview)
    assert "scale=1080:1920" in " ".join(final)
    assert "crop=1080:1920" in " ".join(final)


def test_scene_command_adds_gentle_motion_only_when_requested(tmp_path):
    import video_renderer

    source = tmp_path / "source.png"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    moving = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav, [], 1.5,
        tmp_path / "moving.mp4", tmp_path, 0, output_size=(540, 960),
        animate_image=True,
    )
    static = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav, [], 1.5,
        tmp_path / "static.mp4", tmp_path, 1, output_size=(540, 960),
    )

    assert "zoompan=z='min(zoom+0.0007,1.035)'" in moving[moving.index("-filter_complex") + 1]
    assert "zoompan=" not in static[static.index("-filter_complex") + 1]


def test_brand_endcard_can_use_the_same_subtle_motion_treatment(tmp_path):
    import video_renderer

    source = tmp_path / "brand.png"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, False, wav, [], 3.0,
        tmp_path / "brand.mp4", tmp_path, 1, output_size=(540, 960), animate_image=True,
    )

    assert "zoompan=z='min(zoom+0.0007,1.035)'" in command[command.index("-filter_complex") + 1]


def test_scene_render_duration_caps_silent_tail_without_cutting_speech():
    import video_renderer

    assert video_renderer.scene_render_duration(7.0, 4.16) == 4.91
    assert video_renderer.scene_render_duration(7.0, 4.16, preserve_planned_duration=True) == 7.0
    assert video_renderer.scene_render_duration(2.0, 0.96) == 1.71
    assert video_renderer.scene_render_duration(3.0, 3.84, is_brand_endcard=True) == 4.19


def test_clip_source_command_preserves_real_scene_audio_for_narration_gaps(tmp_path):
    import video_renderer

    command = video_renderer._clip_source_command(
        "ffmpeg", tmp_path / "source.mp4", tmp_path / "clip.mp4", 1.0, 4.0,
    )

    assert "-an" not in command


def test_scene_command_fits_landscape_source_without_losing_edge_information(tmp_path, monkeypatch):
    import video_renderer

    source = tmp_path / "source.mp4"; source.touch()
    wav = tmp_path / "voice.wav"; wav.touch()
    monkeypatch.setattr(video_renderer, "_has_audio", lambda *_: False)
    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", source, True, wav, [], 3.0,
        tmp_path / "out.mp4", tmp_path, 0, output_size=(540, 960),
    )

    filters = command[command.index("-filter_complex") + 1]
    assert "scale=540:960:force_original_aspect_ratio=increase" in filters
    assert "crop=540:960:exact=1" in filters
    assert "force_original_aspect_ratio=decrease" in filters
    assert "boxblur=20:1" in filters
    assert "overlay=(W-w)/2:(H-h)/2" in filters
    assert "setsar=1" in filters


def test_only_standard_nine_by_sixteen_sizes_are_renderable():
    import video_renderer

    assert video_renderer.is_standard_portrait_size((540, 960))
    assert video_renderer.is_standard_portrait_size((1080, 1920))
    assert not video_renderer.is_standard_portrait_size((1280, 720))


def test_portrait_frame_policy_preserves_source_content_and_keeps_subtitles_low():
    import video_renderer

    assert video_renderer.PORTRAIT_FRAME_POLICY == "fit_with_consistent_background"
    assert video_renderer._subtitle_safe_bottom_margin(960) == 72
    assert video_renderer._subtitle_safe_bottom_margin(1920) == 144
    assert video_renderer._subtitle_safe_bottom_margin(960, "hotspot_news") == 72


def test_all_subtitle_masks_use_the_same_full_width_safe_band(tmp_path):
    import video_renderer

    overlay = tmp_path / "news-subtitle.png"
    video_renderer._generate_text_overlay(
        "R60 现场正在处置，先核对路线。", overlay, 540, height=960,
        mask_source_lower_third=True,
    )

    from PIL import Image
    assert Image.open(overlay).size == (540, 86)


def test_transition_concat_resets_timestamps_and_crossfades_audio_video(tmp_path):
    import video_renderer

    segments = [tmp_path / f"segment-{index}.mp4" for index in range(3)]
    command = video_renderer._transition_concat_command(
        "ffmpeg", segments, [5.0, 6.0, 4.0], tmp_path / "output.mp4",
        transition_duration=0.22,
    )
    filters = command[command.index("-filter_complex") + 1]

    assert filters.count("xfade=transition=fade:duration=0.22") == 2
    assert filters.count("acrossfade=d=0.22") == 2
    assert filters.count("setpts=PTS-STARTPTS") == 6
    assert filters.count("apad=whole_dur=") == 3
    assert "atrim=duration=5.000" in filters
    assert "offset=4.780" in filters
    assert "offset=10.560" in filters
    assert "-c" not in command
    assert "libx264" in command


def test_cancel_callback_prevents_process_start(monkeypatch):
    import video_renderer

    started = []
    monkeypatch.setattr(video_renderer.subprocess, "Popen", lambda *args, **kwargs: started.append(args))

    with pytest.raises(video_renderer.RenderCanceled):
        video_renderer.run_cancelable_process("job-a", ["ffmpeg"], cancel_check=lambda: True)

    assert started == []


def test_active_process_group_is_terminated(monkeypatch):
    import video_renderer

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return self.returncode

    process = FakeProcess()
    killed = []
    video_renderer._ACTIVE_PROCESSES["job-a"] = {process}
    monkeypatch.setattr(video_renderer.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(video_renderer.os, "killpg", lambda pgid, signal: killed.append((pgid, signal)))

    assert video_renderer.cancel_render("job-a") is True
    assert killed and killed[0][0] == 4321
    video_renderer._ACTIVE_PROCESSES.pop("job-a", None)


def test_cancel_terminates_all_active_ffmpeg_children(monkeypatch):
    import video_renderer

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

        def poll(self):
            return self.returncode

    killed = []
    video_renderer._ACTIVE_PROCESSES["job-multi"] = {FakeProcess(11), FakeProcess(22)}
    monkeypatch.setattr(video_renderer.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(video_renderer.os, "killpg", lambda pgid, signal: killed.append(pgid))

    assert video_renderer.cancel_render("job-multi") is True
    assert set(killed) == {11, 22}
    video_renderer._ACTIVE_PROCESSES.pop("job-multi", None)


def test_cleanup_stale_jobs_kills_running_render(monkeypatch):
    # 批5 #19：running 超时清理必须真杀进程组并标 canceled，
    # 防止退回“只标状态”（标 failed 时 is_canceled 不认，渲染线程照跑并覆盖成 succeeded）。
    import threading
    import time
    from datetime import datetime, timedelta, timezone

    import video_renderer

    def run_sleep():
        try:
            video_renderer.run_cancelable_process("job-stale", ["sleep", "60"], cancel_check=lambda: False)
        except Exception:
            pass  # 被杀后抛 CalledProcessError，符合预期

    worker = threading.Thread(target=run_sleep, daemon=True)
    worker.start()
    for _ in range(50):
        if video_renderer._ACTIVE_PROCESSES.get("job-stale"):
            break
        time.sleep(0.1)
    process = next(iter(video_renderer._ACTIVE_PROCESSES["job-stale"]))

    # created_at 与 DB datetime('now') 一致：UTC 无时区串。旧用例用本地 datetime.now()
    # 恰好与旧 bug 的本地时区解释互相抵消，导致测试绿但生产误杀；改用 UTC 串才真实。
    created = (
        datetime.now(timezone.utc)
        - timedelta(seconds=video_renderer.RENDER_TIMEOUT + 60)
    ).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(video_renderer.db, "get_unfinished_render_jobs", lambda: [
        {"id": "job-stale", "status": "running", "created_at": created},
    ])
    updates = []
    monkeypatch.setattr(
        video_renderer.db, "update_render_job",
        lambda job_id, **kwargs: updates.append((job_id, kwargs)),
    )

    video_renderer.cleanup_stale_jobs()

    # 进程组已被终止（SIGTERM 后 sleep 秒退，cancel_render 内部已 wait）
    assert process.poll() is not None
    # 状态标 canceled（而非 failed），is_canceled 认该状态，渲染线程必停
    assert updates == [(
        "job-stale",
        {
            "status": "canceled",
            "stage": "超时清理",
            "error": f"渲染超过 {video_renderer.RENDER_TIMEOUT} 秒自动终止",
        },
    )]
    # run_cancelable_process 的 finally 已把进程从注册表清掉
    worker.join(timeout=5)
    assert not video_renderer._ACTIVE_PROCESSES.get("job-stale")


def test_cleanup_stale_jobs_does_not_kill_fresh_running_job_utc_created_at(monkeypatch):
    # P0 回归守卫：created_at 是 DB datetime('now') 的 UTC 无时区串。修复前 naive 值被
    # .timestamp() 按进程本地时区（如 +08:00）解释，age 恒多 8 小时 → 刚创建的 running
    # 任务在首个 60s 清理周期即被误判超时杀掉。修复后按 UTC 归一，新任务 age≈0 不得被清。
    from datetime import datetime, timezone

    import video_renderer

    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # 刚创建，UTC 无时区串
    monkeypatch.setattr(video_renderer.db, "get_unfinished_render_jobs", lambda: [
        {"id": "job-fresh", "status": "running", "created_at": created},
    ])
    updates = []
    canceled = []
    monkeypatch.setattr(
        video_renderer.db, "update_render_job",
        lambda job_id, **kwargs: updates.append((job_id, kwargs)),
    )
    monkeypatch.setattr(video_renderer, "cancel_render", lambda job_id: canceled.append(job_id))

    video_renderer.cleanup_stale_jobs()

    assert canceled == []  # 未误杀
    assert updates == []   # 未改状态


def test_normalize_script_preserves_event_clip_range():
    import video_renderer

    script = video_renderer.normalize_script(
        {"duration_target_ms": 30_000, "scenes": [
            {"duration": 5, "asset_id": 298, "event_clip_id": 2},
            {"duration": 5}, {"duration": 5}, {"duration": 5},
        ]},
        {298},
        asset_lookup={298: {"id": 298, "hotspot_id": 31, "file_type": "video"}},
        event_lookup={2: {"id": 2, "asset_id": 298, "start_ms": 6000, "end_ms": 12000}},
    )
    assert script["scenes"][0]["event_clip_id"] == 2
    assert script["scenes"][0]["clip_ref"]["start_ms"] == 6000
    assert script["duration_target_ms"] == 30_000


def test_normalize_script_allows_event_clip_from_inactive_mother_asset():
    import video_renderer

    script = video_renderer.normalize_script(
        {"duration_target_ms": 30_000, "scenes": [
            {"duration": 5, "asset_id": 298, "event_clip_id": 2},
            {"duration": 5}, {"duration": 5}, {"duration": 5},
        ]},
        set(),
        asset_lookup={298: {"id": 298, "hotspot_id": 31, "file_type": "video", "duration": 120}},
        event_lookup={2: {"id": 2, "asset_id": 298, "start_ms": 6000, "end_ms": 12000}},
    )

    assert script["scenes"][0]["asset_id"] == 298
    assert script["scenes"][0]["event_clip_id"] == 2
    assert script["scenes"][0]["clip_ref"]["duration_ms"] == 6000


def test_normalize_script_repairs_silent_outro_and_rebalances_without_dropping_scenes():
    import video_renderer

    script = video_renderer.normalize_script(
        {"scenes": [
            {"duration": 6, "visual": "仓库", "voiceover": "第一段旁白"},
            {"duration": 7, "visual": "入库", "voiceover": "第二段旁白"},
            {"duration": 7, "visual": "分拣", "voiceover": "第三段旁白"},
            {"duration": 6, "visual": "运输", "voiceover": "第四段旁白"},
            {"duration": 4, "visual": "配送", "voiceover": "第五段旁白"},
            {"duration": 3, "visual": "品牌结尾", "voiceover": "", "text_overlay": "SA-LogiFlow｜南非跨境物流"},
        ]},
        set(),
        target_duration_ms=30_000,
    )

    assert len(script["scenes"]) == 6
    assert sum(scene["duration_ms"] for scene in script["scenes"]) == 30_000
    assert script["scenes"][-1]["voiceover"] == "SA-LogiFlow，南非跨境物流"
    assert script["normalization"]["auto_repaired"] is True
    assert len(script["normalization"]["actions"]) == 2


def test_normalize_script_preserves_short_owned_context_images():
    import video_renderer

    script = video_renderer.normalize_script(
        {"scenes": [
            {"duration": 5, "voiceover": "仓内核对包裹。", "asset_id": 1,
             "evidence_type": "owned_video"},
            {"duration": 2, "voiceover": "再看一张现场图片。", "asset_id": 3,
             "evidence_type": "image", "scene_role": "owned_context_image"},
            {"duration": 5, "voiceover": "准备下一步交接。", "asset_id": 2,
             "evidence_type": "owned_video"},
            {"duration": 2, "voiceover": "回到订单核对。", "asset_id": 4,
             "evidence_type": "image", "scene_role": "owned_context_image"},
            {"duration": 3, "voiceover": "最后完成收束。", "scene_role": "brand_cta"},
        ]},
        {1, 2, 3, 4},
        target_duration_ms=17_000,
    )

    image_durations = [
        scene["duration_ms"] for scene in script["scenes"]
        if scene["evidence_type"] == "image"
    ]
    assert image_durations == [2_000, 2_000]
    assert sum(scene["duration_ms"] for scene in script["scenes"]) == 17_000


def test_normalize_script_rejects_hotspot_mother_without_event_ref():
    import video_renderer

    with pytest.raises(ValueError, match="热点事件片段"):
        video_renderer.normalize_script(
            {"duration_target_ms": 30_000, "scenes": [
                {"duration": 5, "asset_id": 298},
                {"duration": 5}, {"duration": 5}, {"duration": 5},
            ]},
            {298},
            asset_lookup={298: {"id": 298, "hotspot_id": 31, "file_type": "video"}},
            event_lookup={},
        )


def test_subtitle_sync_report_covers_measured_audio_window():
    import video_renderer

    cues = video_renderer.build_subtitle_cues("第一句。第二句。", 4.2)
    report = video_renderer.subtitle_sync_report(cues, 4.2)

    assert report["passed"] is True
    assert report["audio_duration"] == 4.2
    assert report["subtitle_end"] == 4.2


def test_subtitle_sync_report_allows_short_leading_tts_silence_but_not_mid_sentence_gap():
    import video_renderer

    leading = video_renderer.subtitle_sync_report(
        [{"start": 0.34, "end": 4.2, "text": "旁白"}], 4.2,
    )
    assert leading["passed"] is True
    assert leading["leading_silence_tolerated"] == 0.34

    middle = video_renderer.subtitle_sync_report(
        [
            {"start": 0.0, "end": 1.0, "text": "第一句"},
            {"start": 1.34, "end": 4.2, "text": "第二句"},
        ], 4.2,
    )
    assert middle["passed"] is False
    assert middle["gaps"] == [0.34]


def test_tts_speedup_factor_absorbs_measured_qwen_pacing_variation_without_distortion():
    import video_renderer

    factor = video_renderer.tts_speedup_factor(7.35, 7.4)

    assert factor is not None
    assert 1.04 < factor < 1.05
    assert video_renderer.tts_speedup_factor(7.0, 7.4) is None
    # Same Qwen voice can turn a punctuation-heavy 31-character sentence into
    # 8.48s.  A 7.4s confirmed Hook remains playable once with a bounded
    # 20%-ish tempo fit, rather than failing after the user clicked generate.
    measured_factor = video_renderer.tts_speedup_factor(8.48, 7.4)
    assert measured_factor is not None
    assert 1.20 < measured_factor < 1.21
    assert video_renderer.tts_speedup_factor(9.0, 7.4) is None


def test_renderer_compacts_an_overflowing_voiceover_before_repeating_real_video():
    import video_renderer

    shortened = video_renderer.compact_voiceover_to_fit_real_video(
        "拖车驶入装车区就位，工作人员继续核对货物与发运安排。", 7.4, 5.7,
    )

    assert shortened == "拖车驶入装车区就位。"


def test_renderer_drops_opaque_vehicle_codes_before_cutting_a_chinese_action_clause():
    import video_renderer

    shortened = video_renderer.compact_voiceover_to_fit_real_video(
        "拖车CEKEMACH18驶入BUFFALOBOS装车区。", 7.4, 5.7,
    )

    assert shortened == "拖车驶入装车区。"


def _scene_filter(monkeypatch, tmp_path, dims):
    # 批13 C：按源方向构造 _scene_command 的 filter_complex 字符串
    import video_renderer
    monkeypatch.setattr(video_renderer, "_probe_dimensions", lambda ffprobe, source: dims)
    monkeypatch.setattr(video_renderer, "_has_audio", lambda ffprobe, source: False)
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"x")
    command = video_renderer._scene_command(
        "ffmpeg", "ffprobe", tmp_path / "src.mp4", True, wav, [], 5.0,
        tmp_path / "segment.mp4", tmp_path, 0, output_size=(1080, 1920),
    )
    return command[command.index("-filter_complex") + 1]


def test_scene_command_landscape_source_uses_the_same_content_preserving_policy(monkeypatch, tmp_path):
    # 横屏源和竖屏源都使用同一套完整缩放 + 统一背景画布规则。
    fc = _scene_filter(monkeypatch, tmp_path, (1920, 1080))
    assert "split=2" not in fc
    assert "boxblur=20:1" in fc
    assert "force_original_aspect_ratio=decrease" in fc
    assert "overlay=(W-w)/2:(H-h)/2" in fc
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in fc
    assert "crop=1080:1920:exact=1" in fc


def test_scene_command_portrait_source_uses_the_same_content_preserving_policy(monkeypatch, tmp_path):
    # 竖屏源也走同一套背景 + 完整缩放规则，保证不同源方向视觉一致。
    fc = _scene_filter(monkeypatch, tmp_path, (1080, 1920))
    assert "boxblur=20:1" in fc
    assert "split=2" not in fc
    assert "force_original_aspect_ratio=decrease" in fc
    assert "overlay=(W-w)/2:(H-h)/2" in fc
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in fc
    assert "crop=1080:1920:exact=1" in fc
