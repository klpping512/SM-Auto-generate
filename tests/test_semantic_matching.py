def _segment(segment_id, description, tags, quality=0.8, start=0, end=5_000):
    return {
        "id": segment_id,
        "description": description,
        "transcript": "",
        "ocr_text": "",
        "quality_score": quality,
        "start_ms": start,
        "end_ms": end,
        "tags": [
            {"dimension": dimension, "value": value, "confidence": confidence}
            for dimension, value, confidence in tags
        ],
    }


def test_build_semantic_atoms_prefers_structured_scenes_and_preserves_timeline():
    from semantic_matching import build_semantic_atoms

    atoms = build_semantic_atoms({
        "scenes": [
            {"voiceover": "德班港卡车正在排队。", "visual": "港口拥堵", "duration": 4},
            {"voiceover": "团队在仓库分拣货物。", "visual": "仓库作业", "duration": 5},
        ]
    })

    assert [atom["position"] for atom in atoms] == [0, 1]
    assert atoms[0]["duration_ms"] == 4_000
    assert atoms[0]["semantics"]["region"] == ["德班"]
    assert "排队" in atoms[0]["semantics"]["action"]


def test_region_conflict_is_a_hard_exclusion_and_matching_returns_top_three():
    from semantic_matching import extract_semantics, rank_segments

    atom = {
        "text": "德班港卡车排队拥堵",
        "duration_ms": 5_000,
        "semantics": extract_semantics("德班港卡车排队拥堵"),
        "constraints": {"region": ["德班"]},
    }
    segments = [
        _segment(1, "德班港的卡车排队", [("region", "德班", 0.95), ("entity", "卡车", 0.9), ("action", "排队", 0.9)]),
        _segment(2, "开普敦港口卡车", [("region", "开普敦", 0.95), ("entity", "卡车", 0.9)]),
        _segment(3, "德班港集装箱", [("region", "德班", 0.9), ("entity", "集装箱", 0.8)]),
        _segment(4, "南非道路运输", [("region", "南非", 0.7), ("action", "运输", 0.8)]),
        _segment(5, "普通仓库", [("scene", "仓库作业", 0.7)]),
    ]

    ranked = rank_segments(atom, segments, top_k=3)

    assert [item["segment_id"] for item in ranked][0] == 1
    assert 2 not in [item["segment_id"] for item in ranked]
    assert len(ranked) == 3
    assert any("地区" in reason for reason in ranked[0]["reasons"])


def test_duration_mismatch_and_low_evidence_are_explained_for_review():
    from semantic_matching import extract_semantics, rank_segments

    atom = {
        "text": "团队在仓库分拣",
        "duration_ms": 6_000,
        "semantics": extract_semantics("团队在仓库分拣"),
        "constraints": {},
    }
    short = _segment(8, "仓库", [("entity", "仓库", 0.6)], quality=0.3, end=1_000)

    result = rank_segments(atom, [short], top_k=3)[0]

    assert result["review_required"] is True
    assert any("时长" in reason for reason in result["reasons"])


def test_assign_candidates_avoids_reusing_the_same_segment_when_alternatives_exist():
    from semantic_matching import assign_candidates, build_semantic_atoms

    atoms = build_semantic_atoms({"scenes": [
        {"voiceover": "德班港卡车排队", "duration": 4},
        {"voiceover": "德班港集装箱装卸", "duration": 4},
    ]})
    segments = [
        _segment(1, "德班港卡车排队", [("region", "德班", 1), ("entity", "卡车", 1), ("action", "排队", 1)]),
        _segment(2, "德班港集装箱装卸", [("region", "德班", 1), ("entity", "集装箱", 1), ("action", "装卸", 1)]),
    ]

    assignments = assign_candidates(atoms, segments)

    assert assignments[0]["candidates"][0]["segment_id"] == 1
    assert assignments[1]["candidates"][0]["segment_id"] == 2


def test_video_only_assignment_excludes_static_images():
    from semantic_matching import assign_candidates, build_semantic_atoms

    atoms = build_semantic_atoms({"scenes": [{
        "voiceover": "团队在仓库分拣货物",
        "visual": "仓库分拣作业",
        "duration": 5,
    }]})
    image = {**_segment(1, "仓库分拣照片", [("scene", "仓库作业", 1)]), "asset_file_type": "image"}
    video = {**_segment(2, "仓库分拣视频", [("scene", "仓库作业", 0.8)]), "asset_file_type": "video"}

    assignments = assign_candidates(
        atoms, [image, video], top_k=3, required_file_type="video"
    )

    assert [item["segment_id"] for item in assignments[0]["candidates"]] == [2]
    assert assignments[0]["candidates"][0]["media_kind"] == "video_file"


def test_landscape_shot_safe_center_crop_does_not_force_review():
    from semantic_matching import extract_semantics, rank_segments

    atom = {
        "text": "团队在仓库分拣货物",
        "duration_ms": 4_000,
        "semantics": extract_semantics("团队在仓库分拣货物"),
        "constraints": {"orientation": "portrait"},
    }
    landscape = {
        **_segment(10, "团队在仓库分拣货物", [("entity", "团队", 1), ("scene", "仓库作业", 1)]),
        "orientation": "landscape",
        "asset_id": 100,
    }

    result = rank_segments(atom, [landscape], top_k=3)[0]

    assert result["segment_id"] == 10
    assert result["orientation_safe_crop"] is True
    assert not any("不适配裁切" in reason for reason in result["reasons"])
    assert any("可安全居中裁切" in reason for reason in result["reasons"])


def test_landscape_shot_with_edge_risk_requires_review():
    from semantic_matching import extract_semantics, rank_segments

    atom = {
        "text": "团队在仓库分拣货物",
        "duration_ms": 4_000,
        "semantics": extract_semantics("团队在仓库分拣货物"),
        "constraints": {"orientation": "portrait"},
    }
    landscape = {
        **_segment(
            11,
            "团队在仓库分拣货物",
            [("entity", "团队", 1), ("scene", "仓库作业", 1), ("composition", "edge_risk_both", 0.9)],
        ),
        "orientation": "landscape",
        "asset_id": 101,
    }

    result = rank_segments(atom, [landscape], top_k=3)[0]

    assert result["review_required"] is True
    assert any("不适配裁切" in reason for reason in result["reasons"])


def test_rank_segments_diversifies_by_source_asset_within_topk():
    from semantic_matching import extract_semantics, rank_segments

    atom = {
        "text": "仓库作业卡车分拣",
        "duration_ms": 4_000,
        "semantics": extract_semantics("仓库作业卡车分拣"),
        "constraints": {},
    }
    segments = [
        {**_segment(1, "仓库作业卡车分拣", [("scene", "仓库作业", 1), ("entity", "卡车", 1)]), "asset_id": 7},
        {**_segment(2, "仓库作业卡车装卸", [("scene", "仓库作业", 0.95), ("entity", "卡车", 0.9)]), "asset_id": 7},
        {**_segment(3, "仓库作业集装箱", [("scene", "仓库作业", 0.9), ("entity", "集装箱", 0.9)]), "asset_id": 8},
        {**_segment(4, "仓库分拣团队", [("scene", "仓库作业", 0.85), ("entity", "团队", 0.8)]), "asset_id": 9},
    ]

    ranked = rank_segments(atom, segments, top_k=3)

    assert len(ranked) == 3
    assert [item["asset_id"] for item in ranked] == [7, 8, 9] or (
        ranked[0]["asset_id"] == 7 and set(item["asset_id"] for item in ranked) == {7, 8, 9}
    )
    assert ranked[0]["segment_id"] == 1
    assert 2 not in [item["segment_id"] for item in ranked]


def test_assign_candidates_globally_uses_distinct_source_assets():
    from semantic_matching import assign_candidates, build_semantic_atoms

    atoms = build_semantic_atoms({"scenes": [
        {"voiceover": "仓库分拣货物", "visual": "仓库作业", "duration": 4, "scene_role": "brand_proof"},
        {"voiceover": "道路运输配送", "visual": "道路运输", "duration": 4, "scene_role": "brand_proof"},
        {"voiceover": "仓库装卸集装箱", "visual": "仓库作业", "duration": 4, "scene_role": "brand_proof"},
        {"voiceover": "团队清关处理", "visual": "清关", "duration": 4, "scene_role": "brand_proof"},
        {"voiceover": "配送车辆出库", "visual": "道路运输", "duration": 4, "scene_role": "brand_proof"},
        {"voiceover": "海外仓作业现场", "visual": "仓库作业", "duration": 4, "scene_role": "brand_proof"},
    ]})
    segments = []
    for asset_id in range(1, 7):
        segments.append({
            **_segment(
                asset_id,
                f"Buffalo 履约现场 {asset_id}",
                [("brand", "Buffalo", 1), ("scene", "仓库作业", 0.9)],
                quality=0.9,
                end=8_000,
            ),
            "asset_id": asset_id,
            "primary_category": "warehouse",
            "asset_source": "buffalo_library",
            "asset_file_type": "video",
        })

    assignments = assign_candidates(atoms, segments, top_k=10, required_file_type="video")
    preferred = [item["candidates"][0]["asset_id"] for item in assignments if item["candidates"]]

    assert len(preferred) == 6
    assert len(set(preferred)) == 6


def test_owned_scene_role_and_asset_category_are_strong_match_evidence():
    from semantic_matching import build_semantic_atoms, rank_segments

    atoms = build_semantic_atoms({"scenes": [{
        "scene_role": "brand_proof",
        "visual": "Buffalo 物流履约现场",
        "voiceover": "面对复杂环境，Buffalo 依然保持稳定履约。",
        "duration": 6,
    }]})
    segment = {
        **_segment(11, "海外仓作业现场", [("brand", "Buffalo", 0.95)], quality=0.85, end=12_000),
        "primary_category": "warehouse",
        "asset_hotspot_id": None,
    }

    result = rank_segments(atoms[0], [segment], top_k=1)[0]

    assert result["match_score"] >= 75
    assert any("分镜职责匹配" in reason for reason in result["reasons"])
    assert result["review_required"] is False


def test_brand_proof_rejects_generic_logistics_and_prefers_visible_buffalo():
    from semantic_matching import build_semantic_atoms, rank_segments

    atom = build_semantic_atoms({"scenes": [{
        "scene_role": "brand_proof", "visual": "Buffalo 配送车履约", "duration": 5,
    }]})[0]
    generic = {**_segment(21, "道路上的配送货车", [("scene", "道路运输", 1)]),
               "primary_category": "delivery", "asset_hotspot_id": None}
    branded = {**_segment(22, "Buffalo 配送车在道路运输", [("brand", "Buffalo", 1), ("scene", "道路运输", 1)]),
               "primary_category": "delivery", "asset_hotspot_id": None}

    ranked = rank_segments(atom, [generic, branded], top_k=3)

    assert [item["segment_id"] for item in ranked] == [22]
    assert any("品牌露出匹配" in reason for reason in ranked[0]["reasons"])
