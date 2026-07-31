from pathlib import Path


def test_normalize_scene_boundaries_splits_long_shots_and_merges_short_shots():
    from asset_processing import normalize_scene_boundaries

    ranges = normalize_scene_boundaries([0, 900, 9_500, 10_100], duration_ms=16_000)

    assert ranges[0][0] == 0
    assert ranges[-1][1] == 16_000
    assert all(2_000 <= end - start <= 8_000 for start, end in ranges)
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))


def test_manual_classification_has_priority_over_ocr_asr_and_filename():
    from asset_processing import classify_evidence

    result = classify_evidence(
        filename="德班港卡车排队.mp4",
        transcript="客户正在签收包裹",
        ocr_text="DURBAN PORT CONGESTION",
        manual={"primary_category": "customer", "tags": {"region": ["约翰内斯堡"]}},
    )

    assert result["primary_category"] == "customer"
    assert result["decision"] == "confirmed"
    assert result["evidence"][0]["source"] == "manual"
    region = next(tag for tag in result["tags"] if tag["dimension"] == "region")
    assert region["value"] == "约翰内斯堡"
    assert region["confirmed"] is True


def test_low_confidence_classification_requires_review_instead_of_fake_certainty():
    from asset_processing import classify_evidence

    result = classify_evidence(filename="IMG_0042.mp4", transcript="", ocr_text="")

    assert result["primary_category"] == "other"
    assert result["decision"] == "review_required"
    assert result["confidence"] < 0.7


def test_classification_extracts_multidimensional_tags_from_content_evidence():
    from asset_processing import classify_evidence

    result = classify_evidence(
        filename="clip.mp4",
        transcript="德班港出现卡车排队，仓库团队正在分拣货物",
        ocr_text="DURBAN CONTAINER TERMINAL",
    )

    tags = {(item["dimension"], item["value"]) for item in result["tags"]}
    assert ("region", "德班") in tags
    assert ("entity", "卡车") in tags
    assert ("action", "排队") in tags
    assert result["primary_category"] in {"warehouse", "delivery"}


def test_visible_buffalo_is_a_brand_tag_without_overriding_delivery_category():
    from asset_processing import classify_evidence

    result = classify_evidence(
        filename="IMG_6032.jpg", ocr_text="BUFFALO LOGISTICS · WE DELIVER HOPE · TRUCK",
        model_category="delivery", model_confidence=0.91,
        model_tags={"brand": ["Buffalo"], "scene": ["道路运输"], "object": ["货车"]},
    )

    assert result["primary_category"] == "delivery"
    assert ("brand", "Buffalo") in {(tag["dimension"], tag["value"]) for tag in result["tags"]}


def test_visual_tags_normalize_english_logistics_labels_to_matchable_terms():
    from asset_processing import _visual_tag_dimensions

    tags = _visual_tag_dimensions({
        "brand_tags": ["Buffalo"], "scene_tags": ["warehouse", "delivery"],
        "object_tags": ["truck", "trailer"],
    })

    assert tags["brand"] == ["Buffalo"]
    assert tags["scene"] == ["仓库作业", "道路运输"]
    assert tags["object"] == ["卡车", "拖车"]


def test_build_processing_plan_uses_one_segment_for_images():
    from asset_processing import build_processing_plan

    plan = build_processing_plan(
        {"file_type": "image", "duration": None, "width": 1080, "height": 1920},
        scene_boundaries=[],
    )

    assert plan == [{"segment_index": 0, "start_ms": 0, "end_ms": 0, "orientation": "portrait"}]


def test_process_asset_job_persists_classification_and_tags(tmp_db, tmp_path):
    from PIL import Image
    from asset_processing import process_asset_job

    static_dir = tmp_path / "static"
    image_path = static_dir / "assets/library/image/durban.jpg"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (1080, 1920), "white").save(image_path)
    asset_id = tmp_db.create_asset({
        "name": "德班港卡车排队", "filepath": "assets/library/image/durban.jpg", "file_type": "image",
        "category": "other", "duration": None, "width": 1080, "height": 1920,
        "size": image_path.stat().st_size, "thumbnail": None, "sha256": "a" * 64,
        "source": "upload", "status": "active", "created_by": None,
    })
    job_id = tmp_db.create_asset_processing_job(asset_id)

    result = process_asset_job(job_id, static_dir)

    assert result["status"] == "succeeded"
    assert tmp_db.get_asset(asset_id)["primary_category"] == "delivery"
    segment = tmp_db.list_asset_segments(asset_id=asset_id)[0]
    assert {tag["value"] for tag in segment["tags"]} >= {"德班", "卡车", "排队"}


def test_long_video_budget_covers_every_planned_segment_up_to_48(tmp_db, tmp_path, monkeypatch):
    import asset_processing

    static_dir = tmp_path / "static"
    source = static_dir / "assets/library/video/long.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    asset_id = tmp_db.create_asset({
        "name": "长热点母片", "filepath": "assets/library/video/long.mp4", "file_type": "video",
        "category": "other", "duration": 240, "width": 1080, "height": 1920,
        "size": 5, "thumbnail": None, "sha256": "long-video", "source": "youtube", "status": "active",
    })
    job_id = tmp_db.create_asset_processing_job(asset_id)
    plan = [{"segment_index": index, "start_ms": index * 8_000, "end_ms": (index + 1) * 8_000, "orientation": "portrait"}
            for index in range(35)]
    captured = {}
    monkeypatch.setattr(asset_processing, "detect_scene_boundaries", lambda *_args: [0])
    monkeypatch.setattr(asset_processing, "build_processing_plan", lambda *_args: plan)
    monkeypatch.setattr(asset_processing, "_make_video_preview", lambda *_args: (None, None))
    monkeypatch.setattr(asset_processing, "_transcribe", lambda *_args: "")
    monkeypatch.setattr(asset_processing, "_ocr", lambda *_args: "")
    monkeypatch.setattr(asset_processing, "_visual_analysis", lambda *_args: {})
    original_budget = asset_processing.model_router.create_budget

    def capture_budget(*args, **kwargs):
        captured.update(kwargs)
        return original_budget(*args, **kwargs)

    monkeypatch.setattr(asset_processing.model_router, "create_budget", capture_budget)

    result = asset_processing.process_asset_job(job_id, static_dir)

    assert result["segments"] == 35
    assert captured["max_calls"] == 35


def test_visual_segment_indexes_cover_both_ends_without_exceeding_remote_budget():
    from asset_processing import visual_segment_indexes

    indexes = visual_segment_indexes(180, limit=24)

    assert len(indexes) == 24
    assert min(indexes) == 0
    assert max(indexes) == 179
    assert indexes == set(sorted(indexes))


def test_high_confidence_visual_category_overrides_filename_guess():
    from asset_processing import classify_evidence

    result = classify_evidence(
        "random-upload.mp4",
        model_description="货架旁的工人正在分拣纸箱",
        model_category="warehouse",
        model_confidence=0.91,
    )

    assert result["primary_category"] == "warehouse"
    assert result["decision"] == "auto"
    assert result["confidence"] == 0.91


def test_visual_json_parser_accepts_qwen_markdown_fence():
    from asset_processing import _parse_visual_json

    assert _parse_visual_json('```json\n{"primary_category":"warehouse","confidence":0.95}\n```') == {
        "primary_category": "warehouse", "confidence": 0.95,
    }


def test_visual_model_network_failure_is_explicitly_reported(monkeypatch, tmp_path):
    from PIL import Image
    import asset_processing

    image_path = tmp_path / "truck.jpg"
    Image.new("RGB", (32, 32), "white").save(image_path)

    async def fail(*_args, **_kwargs):
        raise ConnectionError("network unavailable")

    monkeypatch.setattr(asset_processing.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(asset_processing.model_router, "call_multimodal_json", fail)

    result = asset_processing._visual_analysis("test-vision", image_path, {"start_ms": 0, "end_ms": 0})

    assert "视觉标注降级" in result["_error"]
    assert "ConnectionError" in result["_error"]


def test_scene_detection_uses_low_resolution_ffmpeg_proxy(monkeypatch, tmp_path):
    import asset_processing

    class Result:
        stderr = "[Parsed_showinfo_2] n:1 pts:7 pts_time:1.4 pos:0\n[Parsed_showinfo_2] n:2 pts:21 pts_time:4.2 pos:0"

    captured = {}
    def fake_run(command, **kwargs):
        captured["call"] = (command, kwargs)
        return Result()
    monkeypatch.setattr(asset_processing.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(asset_processing.subprocess, "run", fake_run)

    boundaries = asset_processing.detect_scene_boundaries(tmp_path / "4k-hevc.mov", duration_ms=9_000)

    command, kwargs = captured["call"]
    assert boundaries == [0, 1_400, 4_200]
    assert "fps=5,scale=320:-2" in command[command.index("-vf") + 1]
    assert kwargs["timeout"] <= 120
