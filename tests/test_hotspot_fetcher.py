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


def test_batch22_configured_sources_ignore_legacy_green_yellow_gate(monkeypatch):
    monkeypatch.setenv("HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED", "0")

    status, note = hotspot_fetcher.configured_source_rights()

    assert status == "authorized"
    assert "绿" not in note and "黄" not in note
    assert {item["name"] for item in hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES} >= {
        "Border Management Authority", "SANRAL", "Transnet National Ports Authority",
        "Transnet Port Terminals", "SAMSA", "SARS Customs Updates",
    }
    assert {item["source_kind"] for item in hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES if "source_kind" in item} == {"html_index"}


def test_html_index_parser_extracts_official_logistics_items_and_date():
    html = """
    <main>
      <article><a href="/media/road-closure">Road closure update for N3 freight route</a><span>12 August 2026</span></article>
      <article><a href="/media/careers">Careers at the agency</a></article>
      <article><a href="https://evil.example/not-allowed">Durban port operations update</a></article>
    </main>
    """

    items = hotspot_fetcher.parse_html_index(html, {
        "name": "SANRAL",
        "url": "https://www.nra.co.za/media-centre",
        "allowed_domains": ["nra.co.za"],
        "source_kind": "html_index",
    })

    assert len(items) == 1
    assert items[0]["source_url"] == "https://www.nra.co.za/media/road-closure"
    assert items[0]["published_at"].startswith("2026-08-12T00:00:00")


@pytest.mark.asyncio
async def test_fetcher_stores_snapshot_and_only_licensed_commons_image(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HOTSPOT_COMMONS_IMAGE_ENABLED", "1")
    monkeypatch.setenv("HOTSPOT_INSPIRATION_SYNC_ENABLED", "1")
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
    # 批22：RSS 文章中的视频与图片候选均进入授权库；Commons 配图仍走独立下载通道。
    assert {(item["media_kind"], item["platform"]) for item in media} == {
        ("image", "direct"), ("video_link", "direct"), ("video_link", "youtube"), ("image", "commons")
    }
    assert all(item["authorization_status"] == "authorized" for item in media)
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


def test_hotspot_source_api_limits_enabled_sources(tmp_db):
    from fastapi.testclient import TestClient
    import app, auth

    tmp_db.create_user("limitadmin", auth.hash_password("pw12345"), "admin", "A")
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login",
        json={"username": "limitadmin", "password": "pw12345"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    cap = hotspot_fetcher.MAX_ENABLED_SOURCES
    for index in range(cap):
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
        json={"name": "Overflow", "feed_url": "https://overflow.gov.za/feed.xml"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == f"最多启用 {cap} 个可信源，请先停用一个现有信源"

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


def test_default_sources_seed_all_defaults_without_overwriting_admin_changes(tmp_db):
    inserted = hotspot_fetcher.seed_default_sources()

    default_count = len(hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES)
    assert inserted == default_count
    sources = tmp_db.list_hotspot_sources(enabled_only=True)
    assert len(sources) == default_count
    assert {source["feed_url"] for source in sources} == {
        source["url"] for source in hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES
    }
    names = {source["name"] for source in sources}
    assert "Freight News" in names
    assert "South African Government" not in names
    assert "BusinessTech" not in names
    assert "The Citizen" not in names

    assert hotspot_fetcher.seed_default_sources() == 0
    assert len(tmp_db.list_hotspot_sources(enabled_only=True)) == default_count
    assert "https://www.transport.gov.za/?feed=rss2" in {
        source["feed_url"] for source in sources
    }


def test_reseed_disables_dead_sources_before_enabling_freight(tmp_db):
    """铁律 A：先停死源腾坑，再启用 Freight；启用集正好 7 个。"""
    import importlib.util
    from pathlib import Path

    # 先灌满旧默认死源占坑
    for source in [
        {"name": "SAnews", "url": "https://www.sanews.gov.za/south-africa-news-stories.xml", "domains": ["sanews.gov.za"]},
        {"name": "SARS", "url": "https://www.sars.gov.za/feed/?post_type=latest_news", "domains": ["sars.gov.za"]},
        {"name": "Department of Transport", "url": "https://www.transport.gov.za/?feed=rss2", "domains": ["transport.gov.za"]},
        {"name": "South African Government", "url": "https://www.gov.za/news-feed", "domains": ["gov.za"]},
        {"name": "South African Reserve Bank", "url": "https://www.resbank.co.za/bin/sarb/solr/publications/rss", "domains": ["resbank.co.za"]},
        {"name": "Moneyweb", "url": "https://www.moneyweb.co.za/feed/", "domains": ["moneyweb.co.za"]},
        {"name": "BusinessTech", "url": "https://businesstech.co.za/news/feed/", "domains": ["businesstech.co.za"]},
        {"name": "Daily Maverick", "url": "https://www.dailymaverick.co.za/dmrss/", "domains": ["dailymaverick.co.za"]},
        {"name": "The Citizen", "url": "https://citizen.co.za/feed/", "domains": ["citizen.co.za"]},
        {"name": "The South African", "url": "https://www.thesouthafrican.com/feed/", "domains": ["thesouthafrican.com"]},
    ]:
        tmp_db.create_hotspot_source(source["name"], source["url"], source["domains"], None, True)

    assert sum(1 for s in tmp_db.list_hotspot_sources() if s["enabled"]) == 10

    path = Path(__file__).resolve().parents[1] / "scripts" / "reseed_hotspot_sources.py"
    spec = importlib.util.spec_from_file_location("reseed_hotspot_sources", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = mod.reseed(dry_run=False)
    assert report["ok"] is True
    assert report["enabled_count"] == len(hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES)
    assert "Freight News" in report["enabled_names"]
    assert all(name not in report["enabled_names"] for name in (
        "South African Government", "South African Reserve Bank", "BusinessTech", "The Citizen",
    ))



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
    assert len(sources) == len(hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES)
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
async def test_cloudflare_challenged_source_is_blocked_not_errored(tmp_db, tmp_path):
    feed_xml = """<rss><channel><item><title>South Africa logistics update</title>
      <link>https://good.gov.za/item</link><description>Freight update</description>
    </item></channel></rss>"""

    def handler(request: httpx.Request):
        url = str(request.url)
        if url == "https://protected.co.za/feed/":
            return httpx.Response(403, headers={"server": "cloudflare", "cf-mitigated": "challenge"}, request=request)
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
                {"name": "Protected", "url": "https://protected.co.za/feed/", "allowed_domains": ["protected.co.za"]},
                {"name": "Good", "url": "https://good.gov.za/feed.xml", "allowed_domains": ["good.gov.za"]},
            ],
            client=client,
        )
    finally:
        await client.aclose()

    assert result["new"] == 1
    health_by_name = {item["name"]: item for item in result["source_health"]}
    assert health_by_name["Protected"]["status"] == "blocked"
    assert health_by_name["Good"]["status"] == "ok"
    # 被反爬拦截的源不进硬错误列表，避免 source_health 刷红
    assert result["errors"] == []
    assert result["skipped"] >= 1


@pytest.mark.asyncio
async def test_optional_commons_failure_does_not_mark_fact_source_failed(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HOTSPOT_COMMONS_IMAGE_ENABLED", "1")
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
