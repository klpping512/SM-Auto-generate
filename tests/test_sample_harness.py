import json


def _package(tmp_db, *, with_brand: bool):
    import evidence_harness

    user_id = tmp_db.create_user(
        f"sample-owner-{int(with_brand)}", "hash", "admin", "Owner"
    )
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Durban port publishes freight operations update",
        "summary": "The official notice asks freight users to monitor operational arrangements.",
        "source_url": "https://www.gov.za/durban-freight-update",
        "publisher": "South African Government",
        "published_at": "2026-07-22T06:00:00+02:00",
        "retrieved_at": "2026-07-22T08:00:00+02:00",
        "snapshot_sha256": "sample123",
        "image_candidate_url": None,
        "status": "new",
    })
    brand_ids = []
    if with_brand:
        brand_id = tmp_db.create_brand_evidence({
            "claim": "Buffalo 可提供仓库分拣与配送节点协同。",
            "evidence_note": "经负责人确认的公开流程说明",
            "disclosure_level": "public",
        }, created_by=user_id)
        tmp_db.confirm_brand_evidence(brand_id, user_id)
        brand_ids.append(brand_id)
    package = evidence_harness.build_package(
        hotspot_id,
        created_by=user_id,
        brand_evidence_ids=brand_ids,
    )
    return package, user_id


def test_three_samples_share_claim_ids_but_use_distinct_structures(tmp_db, tmp_path):
    import sample_harness

    package, user_id = _package(tmp_db, with_brand=True)
    bundle = sample_harness.generate_bundle(
        package["id"], created_by=user_id, output_root=tmp_path
    )

    expected = set(bundle["video"]["claim_ids"])
    assert expected
    assert expected == set(bundle["carousel"]["claim_ids"])
    assert expected == set(bundle["wechat"]["claim_ids"])
    assert 5 <= len(bundle["video"]["scenes"]) <= 6
    assert 30 <= bundle["video"]["duration_target"] <= 45
    hotspot_duration = sum(
        scene["duration"]
        for scene in bundle["video"]["scenes"]
        if scene["scene_role"] in {"hotspot_hook", "fact_context", "impact_explainer"}
    )
    total_duration = sum(scene["duration"] for scene in bundle["video"]["scenes"])
    assert 0.28 <= hotspot_duration / total_duration <= 0.35
    assert 5 <= len(bundle["carousel"]["pages"]) <= 7
    assert 800 <= len(bundle["wechat"]["body"]) <= 1200
    assert bundle["manifest"]["model_usage"]["calls_used"] == 0
    assert bundle["video"]["material_status"] == "blocked"
    assert any("热点素材库" in item for item in bundle["video"]["material_gaps"])

    output = tmp_path / bundle["id"]
    assert (output / "video-script.json").is_file()
    assert (output / "carousel.json").is_file()
    assert (output / "wechat.md").is_file()
    assert (output / "manifest.json").is_file()


def test_video_material_status_ready_when_both_libraries_have_candidates(tmp_db, tmp_path):
    import sample_harness

    package, user_id = _package(tmp_db, with_brand=True)
    hotspot_id = package["hotspot_id"]

    hotspot_asset = tmp_db.create_asset({
        "name": "南非港口运输现场",
        "filepath": "assets/library/video/hotspot-port.mp4",
        "file_type": "video",
        "category": "delivery",
        "duration": 12,
        "width": 1080,
        "height": 1920,
        "size": 1024,
        "thumbnail": "assets/thumbnails/hotspot-port.jpg",
        "sha256": "hotspot-ready",
        "source": "official_news",
        "status": "active",
        "created_by": None,
    })
    tmp_db.update_asset_provenance(
        hotspot_asset,
        "https://www.gov.za/durban-freight-update",
        "Publisher permission",
        "South African Government",
        hotspot_id,
    )
    tmp_db.update_asset_semantic_state(
        hotspot_asset, "delivery", "ready", rights_status="licensed"
    )
    tmp_db.create_asset_segment({
        "asset_id": hotspot_asset,
        "segment_index": 0,
        "start_ms": 0,
        "end_ms": 8000,
        "description": "南非港口货运现场与运输安排",
        "primary_category": "delivery",
        "quality_score": 0.9,
        "orientation": "portrait",
        "status": "active",
        "processing_version": "v1",
    })

    owned_asset = tmp_db.create_asset({
        "name": "Buffalo 海外仓分拣装车",
        "filepath": "assets/library/video/owned-warehouse.mp4",
        "file_type": "video",
        "category": "warehouse",
        "duration": 20,
        "width": 1080,
        "height": 1920,
        "size": 1024,
        "thumbnail": "assets/thumbnails/owned-warehouse.jpg",
        "sha256": "owned-ready",
        "source": "local_directory",
        "status": "active",
        "created_by": None,
    })
    tmp_db.update_asset_semantic_state(owned_asset, "warehouse", "ready")
    tmp_db.create_asset_segment({
        "asset_id": owned_asset,
        "segment_index": 0,
        "start_ms": 0,
        "end_ms": 8000,
        "description": "Buffalo 团队在海外仓扫描分拣并完成装车出库",
        "primary_category": "warehouse",
        "quality_score": 0.95,
        "orientation": "portrait",
        "status": "active",
        "processing_version": "v1",
    })

    bundle = sample_harness.generate_bundle(
        package["id"], created_by=user_id, output_root=tmp_path
    )

    assert bundle["video"]["material_status"] == "ready"
    assert any("未齐" in item for item in bundle["video"]["material_gaps"])


def test_missing_brand_evidence_removes_performance_promises(tmp_db, tmp_path):
    import sample_harness

    package, user_id = _package(tmp_db, with_brand=False)
    bundle = sample_harness.generate_bundle(
        package["id"], created_by=user_id, output_root=tmp_path
    )

    serialized = json.dumps(bundle, ensure_ascii=False)
    assert "保证" not in serialized
    assert "48小时" not in serialized
    assert bundle["publish_allowed"] is False
    assert "品牌证据" in "".join(bundle["quality_issues"])


def test_weak_candidate_is_not_treated_as_a_good_scene_match(tmp_db, tmp_path):
    import sample_harness

    asset_id = tmp_db.create_asset({
        "name": "无关室内画面", "filepath": "assets/library/video/weak.mp4",
        "file_type": "video", "category": "other", "duration": 12,
        "width": 1080, "height": 1920, "size": 1024,
        "thumbnail": "assets/thumbnails/weak.jpg", "sha256": "weak-match",
        "source": "local_directory", "status": "active", "created_by": None,
    })
    tmp_db.create_asset_segment({
        "asset_id": asset_id, "segment_index": 0, "start_ms": 0, "end_ms": 9000,
        "description": "室内静态墙面", "primary_category": "other",
        "quality_score": 0.5, "orientation": "portrait", "status": "active",
        "processing_version": "v1",
    })
    package, user_id = _package(tmp_db, with_brand=True)

    bundle = sample_harness.generate_bundle(
        package["id"], created_by=user_id, output_root=tmp_path
    )

    assert any(scene.get("match_review_required") for scene in bundle["video"]["scenes"])
    assert "低于质量门槛" in "".join(bundle["quality_issues"])


def test_sample_bundle_api_returns_persisted_bundle(tmp_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app, auth, sample_harness

    monkeypatch.setattr(sample_harness, "DEFAULT_OUTPUT_ROOT", tmp_path)
    package, _ = _package(tmp_db, with_brand=True)
    tmp_db.create_user("bundle-admin", auth.hash_password("pw12345"), "admin", "Admin")
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": "bundle-admin", "password": "pw12345"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        f"/api/evidence-packages/{package['id']}/sample-bundle", headers=headers
    )
    assert created.status_code == 201
    bundle_id = created.json()["id"]
    fetched = client.get(f"/api/sample-bundles/{bundle_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == bundle_id


def test_video_sample_writes_selected_segment_boundaries(tmp_db, tmp_path):
    import sample_harness

    asset_id = tmp_db.create_asset({
        "name": "南非仓库分拣",
        "filepath": "assets/library/video/warehouse.mp4",
        "file_type": "video",
        "category": "warehouse",
        "duration": 12,
        "width": 1080,
        "height": 1920,
        "size": 1024,
        "thumbnail": "assets/thumbnails/warehouse.jpg",
        "sha256": "sample-warehouse",
        "source": "local_directory",
        "status": "active",
        "created_by": None,
    })
    segment_id = tmp_db.create_asset_segment({
        "asset_id": asset_id,
        "segment_index": 0,
        "start_ms": 1200,
        "end_ms": 8200,
        "description": "南非仓库工作人员扫描并分拣包裹",
        "primary_category": "warehouse",
        "quality_score": 0.92,
        "orientation": "portrait",
        "status": "active",
        "processing_version": "v1",
    })
    package, user_id = _package(tmp_db, with_brand=True)

    bundle = sample_harness.generate_bundle(
        package["id"], created_by=user_id, output_root=tmp_path
    )
    selected = next(scene for scene in bundle["video"]["scenes"] if scene.get("asset_id"))

    assert selected["asset_id"] == asset_id
    assert selected["asset_segment_id"] == segment_id
    assert selected["asset_start_ms"] == 1200
    assert selected["asset_end_ms"] == 8200
    assert selected["candidates"][0]["asset_id"] == asset_id
    assert selected["candidates"][0]["start_ms"] == 1200
    assert selected["candidates"][0]["end_ms"] == 8200
    assert selected["candidates"][0]["reasons"]
