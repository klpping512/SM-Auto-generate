import auth


def _hotspot(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Durban port publishes freight operations update",
        "summary": "The port published an operational notice for freight users.",
        "source_url": "https://www.gov.za/durban-freight-update",
        "publisher": "South African Government",
        "published_at": "2026-07-22T06:00:00+02:00",
        "retrieved_at": "2026-07-22T08:00:00+02:00",
        "snapshot_sha256": "abc123",
        "image_candidate_url": None,
        "status": "new",
    })
    return hotspot_id


def test_build_evidence_package_keeps_external_and_brand_claims_separate(tmp_db):
    import evidence_harness

    user_id = tmp_db.create_user("evidence-owner", "hash", "admin", "Owner")
    package = evidence_harness.build_package(_hotspot(tmp_db), created_by=user_id)

    assert package["fact_claims"]
    assert package["fact_claims"][0]["source_url"] == "https://www.gov.za/durban-freight-update"
    assert package["fact_claims"][0]["claim"] != package["fact_claims"][0]["excerpt"]
    assert package["brand_claims"] == []
    assert package["status"] == "needs_brand_evidence"


def test_fact_claims_require_independent_source_excerpt(tmp_db):
    import evidence_harness

    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Same text only",
        "summary": "Same text only",
        "source_url": "https://www.gov.za/same",
        "publisher": "South African Government",
        "published_at": "2026-07-22T06:00:00+02:00",
        "retrieved_at": "2026-07-22T08:00:00+02:00",
        "snapshot_sha256": "same-only",
        "status": "new",
    })
    package = evidence_harness.build_package(hotspot_id, created_by=None)
    assert package["fact_claims"] == []
    assert package["status"] == "needs_fact_review"


def test_unconfirmed_brand_claim_cannot_enter_publishable_package(tmp_db):
    import evidence_harness

    user_id = tmp_db.create_user("brand-owner", "hash", "admin", "Owner")
    claim_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 可提供仓库分拣与配送节点协同。",
        "evidence_note": "内部流程资料待负责人确认",
        "disclosure_level": "public",
    }, created_by=user_id)

    package = evidence_harness.build_package(
        _hotspot(tmp_db),
        created_by=user_id,
        brand_evidence_ids=[claim_id],
    )

    assert package["brand_claims"] == []
    assert package["status"] == "needs_brand_evidence"


def test_confirmed_public_brand_claim_can_enter_package(tmp_db):
    import evidence_harness

    user_id = tmp_db.create_user("brand-reviewer", "hash", "admin", "Reviewer")
    claim_id = tmp_db.create_brand_evidence({
        "claim": "Buffalo 可提供仓库分拣与配送节点协同。",
        "evidence_note": "经确认的对外服务流程说明",
        "disclosure_level": "public",
    }, created_by=user_id)
    tmp_db.confirm_brand_evidence(claim_id, user_id)

    package = evidence_harness.build_package(
        _hotspot(tmp_db),
        created_by=user_id,
        brand_evidence_ids=[claim_id],
    )

    assert [claim["claim"] for claim in package["brand_claims"]] == [
        "Buffalo 可提供仓库分拣与配送节点协同。"
    ]
    assert package["status"] == "ready"


def test_evidence_package_and_brand_evidence_admin_api(tmp_db):
    from fastapi.testclient import TestClient
    import app

    user_id = tmp_db.create_user(
        "evidence-admin", auth.hash_password("pw12345"), "admin", "Admin"
    )
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login",
        json={"username": "evidence-admin", "password": "pw12345"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    brand = client.post("/api/brand-evidence", headers=headers, json={
        "claim": "Buffalo 可提供仓库分拣与配送节点协同。",
        "evidence_note": "经确认的对外服务流程说明",
        "disclosure_level": "public",
    })
    assert brand.status_code == 201
    brand_id = brand.json()["id"]
    confirmed = client.put(
        f"/api/brand-evidence/{brand_id}/confirm",
        headers=headers,
        json={"status": "confirmed"},
    )
    assert confirmed.status_code == 200

    package_response = client.post(
        f"/api/hotspots/{_hotspot(tmp_db)}/evidence-package",
        headers=headers,
        json={"brand_evidence_ids": [brand_id]},
    )
    assert package_response.status_code == 201
    package = package_response.json()
    assert package["status"] == "ready"
    fetched = client.get(
        f"/api/evidence-packages/{package['id']}", headers=headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == package["id"]
