import json
import asyncio


def _admin_client(tmp_db):
    from fastapi.testclient import TestClient
    import app
    import auth

    tmp_db.create_user("hotspot-admin", auth.hash_password("pw12345"), "admin", "Admin")
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login",
        json={"username": "hotspot-admin", "password": "pw12345"},
    ).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _hotspot(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "South Africa freight update",
        "summary": "Official freight operations update.",
        "source_url": "https://www.gov.za/freight-update",
        "publisher": "South African Government",
        "published_at": "2026-07-22T08:00:00+02:00",
        "retrieved_at": "2026-07-22T09:00:00+02:00",
        "snapshot_sha256": "fetch-run-test",
    })
    return hotspot_id


def test_fetch_result_reports_packages_and_source_health(tmp_db, tmp_path):
    import httpx
    import hotspot_fetcher

    feed_xml = """<rss><channel><item><title>Durban port operational update</title><link>https://sabc.example/port</link><description>South Africa freight operations update</description><pubDate>2026-07-24T08:00:00+00:00</pubDate></item></channel></rss>"""

    def handler(request):
        if str(request.url).endswith("feed.xml"):
            return httpx.Response(200, text=feed_xml, request=request)
        if str(request.url) == "https://sabc.example/port":
            return httpx.Response(200, text="<html><body>Operations update</body></html>", request=request)
        return httpx.Response(404, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await hotspot_fetcher.fetch_hotspots(tmp_path, feeds=[{"name": "SABC", "url": "https://sabc.example/feed.xml", "allowed_domains": ["sabc.example"]}], client=client)

    result = asyncio.run(run())
    assert "packages" in result
    assert "source_health" in result
    assert result["packages"] >= 1


def test_fetch_run_is_persisted_and_status_api_reports_source_health(tmp_db, monkeypatch):
    import hotspot_fetcher

    async def fake_fetch(*args, **kwargs):
        return {
            "feeds": 2,
            "new": 3,
            "updated": 1,
            "assets": 1,
            "skipped": 0,
            "errors": [],
            "media_errors": [],
            "source_health": [
                {"name": "SAnews", "status": "ok", "items": 3, "error": ""},
                {"name": "SARS", "status": "ok", "items": 1, "error": ""},
            ],
        }

    monkeypatch.setattr(hotspot_fetcher, "fetch_hotspots", fake_fetch)
    client, headers = _admin_client(tmp_db)

    fetched = client.post("/api/hotspots/fetch", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["run"]["status"] == "succeeded"

    status = client.get("/api/hotspots/fetch-status", headers=headers)
    assert status.status_code == 200
    run = status.json()["run"]
    assert run["status"] == "succeeded"
    assert run["result"]["new"] == 3
    assert run["result"]["source_health"][0]["name"] == "SAnews"
    assert run["started_at"]
    assert run["finished_at"]


def test_interrupted_fetch_run_is_recovered_after_service_restart(tmp_db):
    run = tmp_db.create_hotspot_fetch_run()
    assert run["status"] == "running"

    recovered = tmp_db.recover_interrupted_hotspot_fetch_runs()
    latest = tmp_db.get_latest_hotspot_fetch_run()

    assert recovered == 1
    assert latest["status"] == "failed"
    assert "服务" in latest["result"]["error"]
    assert latest["finished_at"]


def test_fetch_run_distinguishes_partial_and_all_source_failures(tmp_db, monkeypatch):
    import hotspot_fetcher

    responses = [
        [
            {"name": "SAnews", "status": "ok", "items": 2, "error": ""},
            {"name": "SARS", "status": "error", "items": 0, "error": "timeout"},
        ],
        [
            {"name": "SAnews", "status": "error", "items": 0, "error": "timeout"},
            {"name": "SARS", "status": "error", "items": 0, "error": "bad feed"},
        ],
    ]

    async def fake_fetch(*args, **kwargs):
        health = responses.pop(0)
        return {
            "feeds": 2, "new": 0, "updated": 0, "assets": 0, "skipped": 0,
            "errors": [
                {"feed": item["name"], "error": item["error"]}
                for item in health if item["status"] == "error"
            ],
            "media_errors": [], "source_health": health,
        }

    monkeypatch.setattr(hotspot_fetcher, "fetch_hotspots", fake_fetch)
    client, headers = _admin_client(tmp_db)

    partial = client.post("/api/hotspots/fetch", headers=headers)
    failed = client.post("/api/hotspots/fetch", headers=headers)

    assert partial.json()["run"]["status"] == "partial"
    assert failed.json()["run"]["status"] == "failed"


def test_hotspot_detail_and_related_sample_bundle_api(tmp_db):
    hotspot_id = _hotspot(tmp_db)
    client, headers = _admin_client(tmp_db)

    detail = client.get(f"/api/hotspots/{hotspot_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == hotspot_id

    missing = client.get("/api/hotspots/999999", headers=headers)
    assert missing.status_code == 404

    user_id = tmp_db.get_user_by_username("hotspot-admin")["id"]
    package_id = "pkg-hotspot-list"
    bundle_id = "bundle-hotspot-list"
    with tmp_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO evidence_packages (id,hotspot_id,status,created_by) VALUES (?,?,?,?)",
            (package_id, hotspot_id, "ready", user_id),
        )
        conn.execute(
            """INSERT INTO sample_bundles
               (id,evidence_package_id,status,publish_allowed,quality_issues,
                video_json,carousel_json,wechat_json,manifest_json,output_dir,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                bundle_id, package_id, "needs_review", 0, "[]",
                json.dumps({"scenes": []}), json.dumps({"pages": []}),
                json.dumps({"title": "内部稿", "body": ""}), json.dumps({}),
                "/tmp/sample", user_id,
            ),
        )

    bundles = client.get(f"/api/hotspots/{hotspot_id}/sample-bundles", headers=headers)
    assert bundles.status_code == 200
    assert bundles.json()[0]["id"] == bundle_id
