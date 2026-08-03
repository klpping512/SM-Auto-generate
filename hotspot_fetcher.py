"""Trusted-feed South Africa hotspot collector with licensed Commons images."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import asyncio
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

import database as db
import media_assets
import asset_processing
import hotspot_media
import hotspot_topic_packages
import hotspot_video_sources
import hotspot_lexicon

# 线索层关键词：宽进严出。这里只决定"哪些新闻值得记一条线索"，
# 下载与使用仍由授权门禁和内置模型事实核验把关；正则真相源在 hotspot_lexicon。
KEYWORDS = hotspot_lexicon.FEED_FILTER_PATTERN
ALLOWED_LICENSE_PREFIXES = ("CC BY", "CC0", "Public domain")
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
DEFAULT_VIDEO_CHANNELS = hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS
# 允许同时启用的可信源上限：信源扩容后从 5 抬高到 12，
# 抓取仍是每 6 小时一轮的低成本元数据采集。
MAX_ENABLED_SOURCES = 12


def configured_video_channels() -> list[dict]:
    """当前生效的 YouTube 视频信源（env 覆盖，回落默认清单）。"""
    return hotspot_video_sources.configured_channels()


def configured_source_rights() -> tuple[str, str]:
    """Configured feeds can be contract-authorized at organization level."""
    if os.environ.get("HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED", "0") == "1":
        return "green", "已配置信源已获企业授权，可自动下载分析；仅限已授权使用范围。"
    return "yellow", "已发现媒体，需确认授权后下载分析。"

DEFAULT_OFFICIAL_SOURCES = [
    {
        "name": "SAnews",
        "url": "https://www.sanews.gov.za/south-africa-news-stories.xml",
        "allowed_domains": ["sanews.gov.za"],
        "purpose": "综合政务、社会事件、交通与公共服务热点",
    },
    {
        "name": "SARS",
        "url": "https://www.sars.gov.za/feed/?post_type=latest_news",
        "allowed_domains": ["sars.gov.za"],
        "purpose": "海关、边境、税务与跨境履约风险",
    },
    {
        "name": "Department of Transport",
        "url": "https://www.transport.gov.za/?feed=rss2",
        "allowed_domains": ["transport.gov.za"],
        "purpose": "道路、港口、货运与交通运行信息",
    },
    {
        "name": "South African Government",
        "url": "https://www.gov.za/news-feed",
        "allowed_domains": ["gov.za"],
        "purpose": "政府声明、部门公告和政策信息",
    },
    {
        "name": "South African Reserve Bank",
        "url": "https://www.resbank.co.za/bin/sarb/solr/publications/rss",
        "allowed_domains": ["resbank.co.za"],
        "purpose": "汇率、利率、支付与宏观金融背景",
    },
    # 以下为主流商业/综合新闻媒体信源：线索层广采，配合 KEYWORDS 过滤出
    # 物流相关条目；下载与使用仍走既有授权门禁。
    {
        "name": "Moneyweb",
        "url": "https://www.moneyweb.co.za/feed/",
        "allowed_domains": ["moneyweb.co.za"],
        "purpose": "财经与企业新闻，覆盖 Transnet、港口、燃油与供应链成本",
    },
    {
        "name": "BusinessTech",
        "url": "https://businesstech.co.za/news/feed/",
        "allowed_domains": ["businesstech.co.za"],
        "purpose": "商业与民生新闻，覆盖电商、快递、道路与基础设施",
    },
    {
        "name": "Daily Maverick",
        "url": "https://www.dailymaverick.co.za/dmrss/",
        "allowed_domains": ["dailymaverick.co.za"],
        "purpose": "深度时事报道，覆盖港口拥堵、铁路与边境口岸事件",
    },
    {
        "name": "The Citizen",
        "url": "https://citizen.co.za/feed/",
        "allowed_domains": ["citizen.co.za"],
        "purpose": "综合新闻，覆盖罢工、封路、天气等履约影响事件",
    },
    {
        "name": "The South African",
        "url": "https://www.thesouthafrican.com/feed/",
        "allowed_domains": ["thesouthafrican.com"],
        "purpose": "综合新闻与民生话题，补充 C 端视角热点",
    },
]


def seed_default_sources(created_by: int | None = None) -> int:
    """补齐默认信源；不覆盖管理员已有配置，启用总数始终不超过 MAX_ENABLED_SOURCES。"""
    existing = db.list_hotspot_sources()
    legacy_stats_url = "https://www.statssa.gov.za/?feed=rss2"
    transport = next(source for source in DEFAULT_OFFICIAL_SOURCES if source["name"] == "Department of Transport")
    legacy_stats = next((item for item in existing if item["feed_url"] == legacy_stats_url), None)
    transport_exists = any(item["feed_url"] == transport["url"] for item in existing)
    if legacy_stats and not transport_exists:
        db.update_hotspot_source(
            legacy_stats["id"],
            transport["name"],
            transport["url"],
            transport["allowed_domains"],
            legacy_stats["enabled"],
        )
        existing = db.list_hotspot_sources()
    existing_urls = {item["feed_url"] for item in existing}
    enabled_count = sum(1 for item in existing if item["enabled"])
    inserted = 0
    for source in DEFAULT_OFFICIAL_SOURCES:
        if source["url"] in existing_urls:
            continue
        enabled = enabled_count < MAX_ENABLED_SOURCES
        db.create_hotspot_source(
            source["name"],
            source["url"],
            source["allowed_domains"],
            created_by,
            enabled,
        )
        inserted += 1
        if enabled:
            enabled_count += 1
    return inserted


def configured_feeds() -> list[dict]:
    raw = os.environ.get("SA_HOTSPOT_FEEDS_JSON", "[]")
    try:
        feeds = json.loads(raw)
    except json.JSONDecodeError:
        return []
    env_feeds = [item for item in feeds if isinstance(item, dict) and item.get("name") and item.get("url")]
    if env_feeds:
        return env_feeds
    return [{"name": item["name"], "url": item["feed_url"], "allowed_domains": item["allowed_domains"]} for item in db.list_hotspot_sources(enabled_only=True)]


def _domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain.lower() or host.endswith("." + domain.lower()) for domain in allowed_domains)


def _is_bot_challenge(exc: Exception) -> bool:
    """判断异常是否为 Cloudflare 等反爬托管质询（需浏览器执行 JS，纯 HTTP 无法通过）。"""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    response = exc.response
    if response is None or response.status_code not in (403, 429, 503):
        return False
    headers = response.headers
    if "cf-mitigated" in headers:
        return True
    server = (headers.get("server") or "").lower()
    return "cloudflare" in server


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def parse_feed(xml_text: str, feed: dict) -> list[dict]:
    root = ET.fromstring(xml_text.lstrip("\ufeff \t\r\n"))
    items = []
    nodes = root.findall(".//item")
    if not nodes:  # Atom
        nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for node in nodes:
        if node.tag.endswith("entry"):
            title = _text(node, "{http://www.w3.org/2005/Atom}title")
            summary = _text(node, "{http://www.w3.org/2005/Atom}summary") or _text(node, "{http://www.w3.org/2005/Atom}content")
            link_node = node.find("{http://www.w3.org/2005/Atom}link")
            link = (link_node.get("href") or "").strip() if link_node is not None else ""
            published = _text(node, "{http://www.w3.org/2005/Atom}published") or _text(node, "{http://www.w3.org/2005/Atom}updated")
        else:
            title, link = _text(node, "title"), _text(node, "link")
            summary = _text(node, "description")
            published = _text(node, "pubDate")
        plain_summary = re.sub(r"<[^>]+>", " ", html.unescape(summary))
        if link and KEYWORDS.search(f"{title} {plain_summary}"):
            items.append({"title": title[:300], "summary": re.sub(r"\s+", " ", plain_summary).strip()[:2000], "source_url": link, "publisher": feed["name"], "published_at": published[:100]})
    return items


def _og_image(page_html: str) -> str | None:
    match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page_html, re.I)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page_html, re.I)
    return html.unescape(match.group(1)) if match else None


async def _licensed_commons_image(client: httpx.AsyncClient, query: str) -> dict | None:
    response = await client.get(COMMONS_API, params={
        "action": "query", "generator": "search", "gsrsearch": f"file:{query} South Africa",
        "gsrnamespace": 6, "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "format": "json",
    })
    response.raise_for_status()
    pages = (response.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        license_name = str((meta.get("LicenseShortName") or {}).get("value") or "")
        if not license_name.startswith(ALLOWED_LICENSE_PREFIXES):
            continue
        mime = info.get("mime") or ""
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        attribution = re.sub(r"<[^>]+>", "", str((meta.get("Artist") or {}).get("value") or (meta.get("Credit") or {}).get("value") or "Wikimedia Commons"))
        return {"url": info.get("url"), "description_url": info.get("descriptionurl") or info.get("url"), "license": license_name, "attribution": html.unescape(attribution)[:500], "mime": mime}
    return None


async def fetch_hotspots(
    static_dir: Path,
    feeds: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
    created_by: int | None = None,
    video_channels: list[dict] | None = None,
    video_limit: int | None = None,
    video_runner=None,
) -> dict:
    feeds = configured_feeds() if feeds is None else feeds
    result = {"feeds": len(feeds), "new": 0, "updated": 0, "assets": 0, "skipped": 0, "errors": [], "media_errors": [], "source_health": [], "packages": 0, "signals": 0, "media_candidates": 0}
    owns_client = client is None
    if client is None:
        _proxy = str(os.environ.get("SA_HOTSPOT_PROXY") or os.environ.get("SA_YOUTUBE_PROXY") or "").strip()
        _client_kwargs = {"timeout": 25, "follow_redirects": True, "headers": {"User-Agent": "SA-LogiFlow/3.0 (+licensed-hotspot-collector)"}}
        if _proxy:
            _client_kwargs["proxy"] = _proxy
        client = httpx.AsyncClient(**_client_kwargs)
    try:
        for feed in feeds:
            allowed_domains = feed.get("allowed_domains") or [urlparse(feed["url"]).hostname]
            health = {"name": feed.get("name", "unknown"), "status": "ok", "items": 0, "error": ""}
            try:
                feed_response = await client.get(feed["url"]); feed_response.raise_for_status()
                feed_items = parse_feed(feed_response.text, feed)
                health["items"] = len(feed_items)
                for item in feed_items:
                    if not _domain_allowed(item["source_url"], allowed_domains):
                        result["skipped"] += 1; continue
                    article_response = await client.get(item["source_url"]); article_response.raise_for_status()
                    final_url = str(article_response.url)
                    if not _domain_allowed(final_url, allowed_domains):
                        result["skipped"] += 1; continue
                    snapshot = article_response.content
                    item.update({"source_url": final_url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(), "image_candidate_url": _og_image(article_response.text)})
                    hotspot_id, created = db.upsert_hotspot(item)
                    try:
                        signal = hotspot_topic_packages.normalize_signal({
                            "hotspot_id": hotspot_id,
                            "source_name": item["publisher"],
                            "source_type": "news",
                            "external_id": final_url,
                            "title": item["title"],
                            "summary": item["summary"],
                            "source_url": final_url,
                            "published_at": item.get("published_at"),
                            "retrieved_at": item["retrieved_at"],
                            "raw_payload": {"feed": feed.get("name", ""), "source_url": final_url},
                        })
                        db.upsert_hotspot_signal(signal)
                    except Exception as signal_exc:
                        result["errors"].append({
                            "feed": feed.get("name", "unknown"),
                            "error": f"热点信号保存失败: {str(signal_exc)[:240]}",
                        })
                    media_rights_tier, media_rights_note = configured_source_rights()
                    for candidate in hotspot_media.discover_media_candidates(article_response.text, final_url):
                        db.upsert_hotspot_media({
                            **candidate,
                            "hotspot_id": hotspot_id,
                            "publisher": item["publisher"],
                            "author": item["publisher"],
                            "published_at": item.get("published_at"),
                            "rights_tier": media_rights_tier,
                            "rights_note": media_rights_note,
                            "download_status": "metadata_ready",
                            "processing_status": "not_started",
                        })
                    db.upsert_inspiration_item({
                        "source_type": "official_news",
                        "source_role": "fact_source",
                        "source_url": final_url,
                        "canonical_url": final_url,
                        "title": item["title"],
                        "summary": item["summary"],
                        "author": item["publisher"],
                        "published_at": item["published_at"],
                        "thumbnail_url": item.get("image_candidate_url"),
                        "media_kind": "article_link",
                        "rights_status": "unknown",
                        "materialization_status": "reference_only",
                        "hotspot_id": hotspot_id,
                        "created_by": created_by,
                    })
                    result["new" if created else "updated"] += 1
                    if not created:
                        continue
                    try:
                        licensed = await _licensed_commons_image(client, item["title"][:80])
                        if not licensed or not licensed.get("url"):
                            continue
                        image_response = await client.get(licensed["url"]); image_response.raise_for_status()
                        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[licensed["mime"]]
                        temp = Path(tempfile.gettempdir()) / f"hotspot-{hotspot_id}{suffix}"
                        try:
                            temp.write_bytes(image_response.content)
                            asset = media_assets.ingest_file(temp, static_dir, "other", "south_africa_hotspot", created_by, name=item["title"][:120])
                            db.update_asset_provenance(asset["id"], licensed["description_url"], licensed["license"], licensed["attribution"], hotspot_id)
                            db.link_hotspot_asset(hotspot_id, asset["id"])
                            db.update_asset_semantic_state(asset["id"], "other", "pending", rights_status="licensed")
                            db.upsert_inspiration_item({
                                "source_type": "licensed_media",
                                "source_role": "supporting_visual",
                                "source_url": licensed["description_url"],
                                "canonical_url": licensed["description_url"],
                                "title": f"开放许可补充配图：{item['title']}",
                                "summary": "按主题检索的开放许可补充画面，不自动视为该新闻事件的现场图片。",
                                "author": licensed["attribution"],
                                "thumbnail_url": "/static/" + asset["filepath"],
                                "media_kind": "image",
                                "rights_status": "licensed",
                                "license_name": licensed["license"],
                                "attribution": licensed["attribution"],
                                "rights_evidence_url": licensed["description_url"],
                                "materialization_status": "materialized",
                                "asset_id": asset["id"],
                                "hotspot_id": hotspot_id,
                                "created_by": created_by,
                            })
                            db.upsert_hotspot_media({
                                "hotspot_id": hotspot_id,
                                "media_kind": "image",
                                "platform": "commons",
                                "source_page_url": licensed["description_url"],
                                "original_media_url": licensed["url"],
                                "thumbnail_url": "/static/" + asset["filepath"],
                                "local_path": asset["filepath"],
                                "publisher": "Wikimedia Commons",
                                "author": licensed["attribution"],
                                "mime_type": licensed["mime"],
                                "rights_tier": "green",
                                "rights_note": "开放许可补充图，不自动视为新闻现场",
                                "license_name": licensed["license"],
                                "rights_evidence_url": licensed["description_url"],
                                "attribution": licensed["attribution"],
                                "download_status": "downloaded",
                                "processing_status": "ready",
                                "sha256": asset["sha256"],
                                "asset_id": asset["id"],
                            })
                            job_id = db.create_asset_processing_job(asset["id"], created_by, asset_processing.PROCESSING_VERSION)
                            await asyncio.to_thread(asset_processing.process_asset_job, job_id, static_dir)
                            result["assets"] += 1
                        finally:
                            temp.unlink(missing_ok=True)
                    except Exception as exc:
                        result["media_errors"].append({
                            "feed": feed.get("name", "unknown"),
                            "hotspot_id": hotspot_id,
                            "error": str(exc)[:300],
                        })
            except Exception as exc:
                error = str(exc)[:300]
                if _is_bot_challenge(exc):
                    # 站点被 Cloudflare 等反爬托管质询拦截（需浏览器执行 JS），
                    # 纯 HTTP 客户端无法通过。优雅降级：标记 blocked 并跳过，
                    # 不计入硬错误，避免 source_health 每次刷红。
                    health.update({"status": "blocked", "error": "反爬质询拦截（需浏览器），已跳过"})
                    result["skipped"] += 1
                else:
                    health.update({"status": "error", "error": error})
                    result["errors"].append({"feed": feed.get("name", "unknown"), "error": error})
            finally:
                result["source_health"].append(health)
        if video_channels:
            kwargs = {"runner": video_runner} if video_runner is not None else {}
            video_result = await asyncio.to_thread(
                hotspot_video_sources.fetch_youtube_channel_hotspots,
            video_channels,
            **({"limit": video_limit} if video_limit is not None else {}),
            **kwargs,
            )
            result.update({
                "video_channels": video_result["channels"],
                "video_new": video_result["new"],
                "video_updated": video_result["updated"],
                "video_media": video_result["media"],
            })
            result["errors"].extend(video_result["errors"])
            result["source_health"].extend(video_result["source_health"])
        signals = db.list_hotspot_signals(limit=500)
        packages = hotspot_topic_packages.cluster_signals(signals)
        for package in packages:
            root_id = package["signals"][0].get("hotspot_id")
            if not root_id:
                continue
            db.update_hotspot_package_metrics(
                int(root_id),
                heat_score=package["heat_score"],
                heat_state=package["heat_state"],
                event_type=package["event_type"],
                logistics_relevance=package["logistics_relevance"],
                locations=package["locations"],
                entities=package["entities"],
                package_status="new",
            )
        result["packages"] = len(packages)
        result["signals"] = len(signals)
        result["media_candidates"] = len(db.list_hotspot_media(limit=500))
    finally:
        if owns_client:
            await client.aclose()
    return result
