import io

import httpx
import pytest
from PIL import Image

import hotspot_fetcher
import hotspot_content
import truth_guard


def test_feed_parser_filters_for_south_africa_logistics():
    xml = """<rss><channel>
      <item><title>Durban port operational update</title><link>https://news.gov.za/durban</link><description>Freight operations update</description><pubDate>Tue</pubDate></item>
      <item><title>Sports result</title><link>https://news.gov.za/sport</link><description>Match report</description></item>
    </channel></rss>"""
    items = hotspot_fetcher.parse_feed(xml, {"name": "Official News"})
    assert [item["source_url"] for item in items] == ["https://news.gov.za/durban"]


def test_feed_parser_accepts_whitespace_before_xml_declaration():
    xml = " \n<?xml version='1.0'?><rss><channel><item><title>South Africa freight update</title><link>https://transport.gov.za/update</link><description>Logistics</description></item></channel></rss>"

    items = hotspot_fetcher.parse_feed(xml, {"name": "Department of Transport"})

    assert len(items) == 1


@pytest.mark.asyncio
async def test_fetcher_stores_snapshot_and_only_licensed_commons_image(tmp_db, tmp_path):
    image = Image.new("RGB", (32, 32), "blue")
    buf = io.BytesIO(); image.save(buf, "JPEG"); image_bytes = buf.getvalue()
    feed_xml = """<rss><channel><item><title>Durban port operational update</title>
      <link>https://news.gov.za/durban</link><description>South Africa freight update</description><pubDate>Tue</pubDate>
    </item></channel></rss>"""

    def handler(request: httpx.Request):
        url = str(request.url)
        if url == "https://news.gov.za/feed.xml":
            return httpx.Response(200, text=feed_xml, request=request)
        if url == "https://news.gov.za/durban":
            return httpx.Response(200, text='<html><meta property="og:image" content="https://news.gov.za/owned.jpg"><meta property="og:video" content="https://news.gov.za/report.mp4"><iframe src="https://www.youtube.com/embed/abc123def45"></iframe><body>Official snapshot</body></html>', request=request)
        if url.startswith(hotspot_fetcher.COMMONS_API):
            return httpx.Response(200, json={"query":{"pages":{"1":{"imageinfo":[{
                "url":"https://upload.wikimedia.org/licensed.jpg", "descriptionurl":"https://commons.wikimedia.org/wiki/File:Licensed.jpg",
                "mime":"image/jpeg", "extmetadata":{"LicenseShortName":{"value":"CC BY-SA 4.0"},"Artist":{"value":"Test photographer"}}
            }]}}}}, request=request)
        if url == "https://upload.wikimedia.org/licensed.jpg":
            return httpx.Response(200, content=image_bytes, request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        result = await hotspot_fetcher.fetch_hotspots(
            tmp_path, feeds=[{"name":"Official News","url":"https://news.gov.za/feed.xml","allowed_domains":["news.gov.za"]}], client=client,
        )
    finally:
        await client.aclose()

    assert result == {
        "feeds": 1, "new": 1, "updated": 0, "assets": 1, "skipped": 0,
        "errors": [], "media_errors": [],
        "packages": 1, "signals": 1, "media_candidates": 4,
        "source_health": [{"name": "Official News", "status": "ok", "items": 1, "error": ""}],
    }
    hotspot = tmp_db.list_hotspots()[0]
    assert hotspot["snapshot_sha256"]
    signals = tmp_db.list_hotspot_signals(hotspot["id"])
    assert len(signals) == 1
    assert signals[0]["source_type"] == "news"
    assert hotspot["image_candidate_url"] == "https://news.gov.za/owned.jpg"
    assert hotspot["status"] == "ready"
    asset = tmp_db.get_asset(hotspot["asset_id"])
    assert asset["source"] == "south_africa_hotspot"
    assert asset["license"] == "CC BY-SA 4.0"
    assert asset["source_url"].startswith("https://commons.wikimedia.org/")
    assert asset["attribution"] == "Test photographer"
    inspirations = tmp_db.list_inspiration_items()
    assert {item["source_role"] for item in inspirations} == {"fact_source", "supporting_visual"}
    supporting = next(item for item in inspirations if item["source_role"] == "supporting_visual")
    assert "不自动视为" in supporting["summary"]
    assert supporting["materialization_status"] == "materialized"
    media = tmp_db.list_hotspot_media(hotspot_id=hotspot["id"])
    assert {(item["media_kind"], item["platform"]) for item in media} >= {
        ("image", "direct"), ("video_link", "direct"), ("video_link", "youtube")
    }
    assert all(item["rights_tier"] in {"green", "yellow"} for item in media)
    draft = hotspot_content.compose(hotspot)
    assert "【事实速览】" in draft["body"]
    assert "【Buffalo 观点】" in draft["body"]
    assert "品牌建议不替代官方公告" in draft["body"]
    assert truth_guard.evaluate(draft["title"], draft["body"], draft["source_refs"])["status"] == "verified"
    assert draft["attachments"][0]["license"] == "CC BY-SA 4.0"


@pytest.mark.asyncio
async def test_cross_domain_feed_link_is_rejected(tmp_db, tmp_path):
    xml = """<rss><channel><item><title>South Africa logistics alert</title><link>https://evil.example/phish</link><description>Durban port</description></item></channel></rss>"""
    def handler(request):
        return httpx.Response(200, text=xml, request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await hotspot_fetcher.fetch_hotspots(tmp_path, [{"name":"Official","url":"https://news.gov.za/feed","allowed_domains":["news.gov.za"]}], client)
    finally:
        await client.aclose()
    assert result["skipped"] == 1
    assert tmp_db.list_hotspots() == []


def test_hotspot_source_admin_api_and_ssrf_validation(tmp_db):
    from fastapi.testclient import TestClient
    import app, auth
    tmp_db.create_user("sourceadmin", auth.hash_password("pw12345"), "admin", "A")
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username":"sourceadmin","password":"pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    rejected = client.post("/api/hotspot-sources", headers=headers, json={"name":"Bad","feed_url":"http://127.0.0.1/feed"})
    assert rejected.status_code == 400
    created = client.post("/api/hotspot-sources", headers=headers, json={
        "name":"Official News", "feed_url":"https://news.gov.za/feed.xml", "allowed_domains":["news.gov.za"],
    })
    assert created.status_code == 201
    sources = client.get("/api/hotspot-sources", headers=headers).json()
    assert sources[0]["allowed_domains"] == ["news.gov.za"]
    assert hotspot_fetcher.configured_feeds()[0]["url"] == "https://news.gov.za/feed.xml"


def test_hotspot_source_api_limits_enabled_sources_to_five(tmp_db):
    from fastapi.testclient import TestClient
    import app, auth

    tmp_db.create_user("limitadmin", auth.hash_password("pw12345"), "admin", "A")
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login",
        json={"username": "limitadmin", "password": "pw12345"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for index in range(5):
        response = client.post(
            "/api/hotspot-sources",
            headers=headers,
            json={
                "name": f"Official {index}",
                "feed_url": f"https://source{index}.gov.za/feed.xml",
            },
        )
        assert response.status_code == 201

    rejected = client.post(
        "/api/hotspot-sources",
        headers=headers,
        json={"name": "Sixth", "feed_url": "https://sixth.gov.za/feed.xml"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "最多启用 5 个可信源，请先停用一个现有信源"

    disabled = client.post(
        "/api/hotspot-sources",
        headers=headers,
        json={
            "name": "Standby",
            "feed_url": "https://standby.gov.za/feed.xml",
            "enabled": False,
        },
    )
    assert disabled.status_code == 201
    source_id = disabled.json()["id"]
    assert tmp_db.list_hotspot_sources()[-1]["enabled"] is False

    enable_rejected = client.put(
        f"/api/hotspot-sources/{source_id}",
        headers=headers,
        json={
            "name": "Standby",
            "feed_url": "https://standby.gov.za/feed.xml",
            "enabled": True,
        },
    )
    assert enable_rejected.status_code == 409


def test_default_sources_seed_exactly_five_without_overwriting_admin_changes(tmp_db):
    inserted = hotspot_fetcher.seed_default_sources()

    assert inserted == 5
    sources = tmp_db.list_hotspot_sources(enabled_only=True)
    assert len(sources) == 5
    assert {source["feed_url"] for source in sources} == {
        source["url"] for source in hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES
    }

    assert hotspot_fetcher.seed_default_sources() == 0
    assert len(tmp_db.list_hotspot_sources(enabled_only=True)) == 5
    assert "https://www.transport.gov.za/?feed=rss2" in {
        source["feed_url"] for source in sources
    }


def test_default_sources_replaces_broken_legacy_statssa_feed(tmp_db):
    tmp_db.create_hotspot_source(
        "Statistics South Africa",
        "https://www.statssa.gov.za/?feed=rss2",
        ["statssa.gov.za"],
        None,
        True,
    )

    hotspot_fetcher.seed_default_sources()

    sources = tmp_db.list_hotspot_sources(enabled_only=True)
    assert len(sources) == 5
    assert all("statssa.gov.za" not in source["feed_url"] for source in sources)
    assert any("transport.gov.za" in source["feed_url"] for source in sources)


@pytest.mark.asyncio
async def test_fetch_result_reports_health_per_source(tmp_db, tmp_path):
    feed_xml = """<rss><channel><item><title>Durban port update</title>
      <link>https://gov.za/durban</link><description>South Africa freight</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request):
        url = str(request.url)
        if url == "https://gov.za/feed.xml":
            return httpx.Response(200, text=feed_xml, request=request)
        if url == "https://gov.za/durban":
            return httpx.Response(200, text="Official update", request=request)
        if url.startswith(hotspot_fetcher.COMMONS_API):
            return httpx.Response(200, json={"query": {"pages": {}}}, request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await hotspot_fetcher.fetch_hotspots(
            tmp_path,
            feeds=[{
                "name": "Official",
                "url": "https://gov.za/feed.xml",
                "allowed_domains": ["gov.za"],
            }],
            client=client,
        )
    finally:
        await client.aclose()

    assert result["source_health"] == [{
        "name": "Official",
        "status": "ok",
        "items": 1,
        "error": "",
    }]


@pytest.mark.asyncio
async def test_one_failed_source_does_not_block_healthy_source(tmp_db, tmp_path):
    feed_xml = """<rss><channel><item><title>South Africa logistics update</title>
      <link>https://good.gov.za/item</link><description>Freight update</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request):
        url = str(request.url)
        if url == "https://bad.gov.za/feed.xml":
            return httpx.Response(503, request=request)
        if url == "https://good.gov.za/feed.xml":
            return httpx.Response(200, text=feed_xml, request=request)
        if url == "https://good.gov.za/item":
            return httpx.Response(200, text="Official update", request=request)
        if url.startswith(hotspot_fetcher.COMMONS_API):
            return httpx.Response(200, json={"query": {"pages": {}}}, request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await hotspot_fetcher.fetch_hotspots(
            tmp_path,
            feeds=[
                {"name": "Bad", "url": "https://bad.gov.za/feed.xml", "allowed_domains": ["bad.gov.za"]},
                {"name": "Good", "url": "https://good.gov.za/feed.xml", "allowed_domains": ["good.gov.za"]},
            ],
            client=client,
        )
    finally:
        await client.aclose()

    assert result["new"] == 1
    assert [item["status"] for item in result["source_health"]] == ["error", "ok"]


@pytest.mark.asyncio
async def test_optional_commons_failure_does_not_mark_fact_source_failed(tmp_db, tmp_path):
    feed_xml = """<rss><channel><item><title>South Africa logistics update</title>
      <link>https://gov.za/item</link><description>Durban freight update</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request):
        url = str(request.url)
        if url == "https://gov.za/feed.xml":
            return httpx.Response(200, text=feed_xml, request=request)
        if url == "https://gov.za/item":
            return httpx.Response(200, text="Official update", request=request)
        if url.startswith(hotspot_fetcher.COMMONS_API):
            return httpx.Response(403, request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await hotspot_fetcher.fetch_hotspots(
            tmp_path,
            feeds=[{"name": "Official", "url": "https://gov.za/feed.xml", "allowed_domains": ["gov.za"]}],
            client=client,
        )
    finally:
        await client.aclose()

    assert result["new"] == 1
    assert result["source_health"][0]["status"] == "ok"
    assert result["errors"] == []
    assert result["media_errors"][0]["feed"] == "Official"
