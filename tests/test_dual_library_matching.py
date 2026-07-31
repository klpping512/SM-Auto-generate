def _segment(
    segment_id: int,
    asset_id: int,
    description: str,
    *,
    hotspot_id=None,
    rights_status="licensed",
    start_ms=0,
    end_ms=5_000,
):
    return {
        "id": segment_id,
        "asset_id": asset_id,
        "description": description,
        "transcript": "",
        "ocr_text": "",
        "quality_score": 0.9,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "orientation": "portrait",
        "asset_hotspot_id": hotspot_id,
        "asset_rights_status": rights_status,
        "asset_file_type": "video",
        "asset_source_url": "https://news.gov.za/story" if hotspot_id else None,
        "asset_attribution": "SAnews" if hotspot_id else None,
        "tags": [
            {"dimension": "region", "value": "南非", "confidence": 1},
            {"dimension": "scene", "value": "仓库作业", "confidence": 1},
        ],
    }


def test_build_atoms_preserves_scene_role_and_hotspot_context():
    from semantic_matching import build_semantic_atoms

    atoms = build_semantic_atoms({
        "hotspot_id": 18,
        "scenes": [
            {
                "voiceover": "南非港口发布最新消息",
                "visual": "新闻现场",
                "duration": 4,
                "scene_role": "hotspot_hook",
            },
            {
                "voiceover": "Buffalo 团队正在分拣",
                "visual": "自有仓库",
                "duration": 6,
                "scene_role": "brand_proof",
            },
        ],
    })

    assert atoms[0]["constraints"]["scene_role"] == "hotspot_hook"
    assert atoms[0]["constraints"]["hotspot_id"] == 18
    assert atoms[1]["constraints"]["scene_role"] == "brand_proof"


def test_scene_role_hard_filters_current_hotspot_and_owned_segments():
    from semantic_matching import extract_semantics, rank_segments

    segments = [
        _segment(1, 101, "南非港口现场", hotspot_id=18),
        _segment(2, 102, "另一个热点现场", hotspot_id=19),
        _segment(3, 103, "权利未确认的热点", hotspot_id=18, rights_status="unknown"),
        _segment(4, 104, "Buffalo 自有仓库分拣", hotspot_id=None),
    ]
    hotspot_atom = {
        "text": "南非港口最新现场",
        "duration_ms": 4_000,
        "semantics": extract_semantics("南非港口最新现场"),
        "constraints": {"scene_role": "hotspot_hook", "hotspot_id": 18},
    }
    brand_atom = {
        "text": "Buffalo 自有仓库分拣",
        "duration_ms": 4_000,
        "semantics": extract_semantics("Buffalo 自有仓库分拣"),
        "constraints": {"scene_role": "brand_proof", "hotspot_id": 18},
    }

    hotspot_candidates = rank_segments(hotspot_atom, segments, top_k=3)
    brand_candidates = rank_segments(brand_atom, segments, top_k=3)

    assert [item["segment_id"] for item in hotspot_candidates] == [1]
    assert hotspot_candidates[0]["library_origin"] == "hotspot"
    assert hotspot_candidates[0]["hotspot_id"] == 18
    assert hotspot_candidates[0]["media_kind"] == "video_file"
    assert [item["segment_id"] for item in brand_candidates] == [4]
    assert brand_candidates[0]["library_origin"] == "owned"


def test_hotspot_top_three_contains_at_most_one_segment_per_asset():
    from semantic_matching import extract_semantics, rank_segments

    atom = {
        "text": "南非港口现场",
        "duration_ms": 3_000,
        "semantics": extract_semantics("南非港口现场"),
        "constraints": {"scene_role": "fact_context", "hotspot_id": 18},
    }
    segments = [
        _segment(1, 201, "南非港口全景", hotspot_id=18, start_ms=0, end_ms=4_000),
        _segment(2, 201, "南非港口车辆", hotspot_id=18, start_ms=4_000, end_ms=8_000),
        _segment(3, 202, "南非港口集装箱", hotspot_id=18),
        _segment(4, 203, "南非港口道路", hotspot_id=18),
    ]

    candidates = rank_segments(atom, segments, top_k=3)

    assert len(candidates) == 3
    assert len({item["asset_id"] for item in candidates}) == 3

