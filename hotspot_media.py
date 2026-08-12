"""热点图片与视频候选发现、规范化和权利门禁。"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v", ".m3u8")
ARTICLE_IMAGE_LIMIT = 12
MAX_HOTSPOT_IMAGE = 10 * 1024 * 1024
# 下载前廉价预筛：只砍铁定没戏的（音乐片/超短/超长直播），宁放勿杀。
PREFILTER_TITLE_BLOCKLIST = (
    "music video",
    "official audio",
    "om music",
    "jingle",
    "anthem",
    "song",
    "lyric",
    "主题曲",
    "宣传曲",
    "片头曲",
)
PREFILTER_MIN_SEC = 8
PREFILTER_MAX_SEC = 3600
# 2026-08-06 回纳现场源新增：拦频道内噪音题材（体育/选举/法庭/娱乐圈/演播室谈话）。
# 铁律：宁放勿杀——绝不拦公路/卡车/边境/抗议/天气/港口等现场实拍词（那是 SA Now/SA Today 的金子）。
PREFILTER_NOISE_TOPIC_BLOCKLIST = (
    # 体育赛事
    "world cup", "afcon", "psl", "matchday", "rugby", "cricket", "netball",
    "tennis", "tournament", "决赛", "半决赛", "夺冠", "比分",
    # 选举/政治（现场抗议/游行除外）
    "election", "votes", "polling", "candidate", "manifesto", "ballot",
    "大选", "选情", "投票站", "竞选", "计票",
    # 法庭/听证
    "trial", "court", "hearing", "magistrate", "testimony", "听证", "庭审", "出庭", "受审",
    # 娱乐圈/颁奖
    "celebrity", "awards", "premiere", "red carpet", "红毯", "颁奖",
    # 演播室/播客（无现场 b-roll）
    "podcast", "talk show",
)
# 标题含噪音主题词时仍放行的现场线索。预筛只能降低无效下载，不能用母片
# 标题的单个主题词替代镜头审核；例如“卡车事故导致法庭外道路封锁”仍值得
# 下载分析。最终是否真实、是否与标题一致，继续由三帧视觉审核和事实审核决定。
PREFILTER_FIELD_ACTIVITY_MARKERS = (
    "road", "street", "traffic", "truck", "cargo", "container", "port", "harbour", "harbor",
    "border", "customs", "warehouse", "delivery", "logistics", "airport", "flood", "storm",
    "snow", "fire", "crash", "accident", "roadblock", "protest", "卡车", "货运", "港口", "道路",
    "边境", "海关", "仓储", "配送", "洪水", "暴雨", "降雪", "起火", "事故", "封路", "抗议",
)


def prefilter_mother_candidate(item: dict) -> tuple[bool, str]:
    """纯元数据预筛：返回 (是否下载, 跳过原因)。不发起任何网络下载。"""
    title = " ".join(
        str(item.get(field) or "")
        for field in ("intake_title", "title", "publisher")
    ).casefold()
    headline = " ".join(
        str(item.get(field) or "")
        for field in ("intake_title", "title")
    ).casefold()
    for token in PREFILTER_TITLE_BLOCKLIST:
        if token.casefold() in title:
            return False, f"title_blocklist:{token}"
    for token in PREFILTER_NOISE_TOPIC_BLOCKLIST:
        if token.casefold() in title and not any(
            marker.casefold() in headline for marker in PREFILTER_FIELD_ACTIVITY_MARKERS
        ):
            return False, f"noise_topic_blocklist:{token}"
    duration = float(item.get("duration_seconds") or 0)
    if duration > 0 and duration < PREFILTER_MIN_SEC:
        return False, f"too_short:{duration:.1f}s"
    if duration > PREFILTER_MAX_SEC:
        return False, f"too_long:{duration:.1f}s"
    return True, ""
ALLOWED_IMAGE_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
ARTICLE_IMAGE_EXCLUDE = re.compile(
    r"(?:logo|icon|avatar|banner|advert|\bads?\b|tracking|pixel|share|social|emoji|placeholder)",
    re.IGNORECASE,
)
IMAGE_SIZE_SUFFIX = re.compile(
    r"(?:-\d+x\d+|-scaled)(?=\.(?:jpe?g|png|webp)$)", re.IGNORECASE
)


def _preferred_article_image(tag, source_page_url: str) -> str | None:
    value = tag.get("data-orig-file")
    if not value and tag.get("srcset"):
        choices: list[tuple[int, str]] = []
        for part in str(tag.get("srcset") or "").split(","):
            fields = part.strip().split()
            if not fields:
                continue
            descriptor = fields[1] if len(fields) > 1 else "0w"
            width = int(descriptor[:-1]) if descriptor.endswith("w") and descriptor[:-1].isdigit() else 0
            choices.append((width, fields[0]))
        if choices:
            value = max(choices, key=lambda choice: choice[0])[1]
    value = value or tag.get("data-lazy-src") or tag.get("data-src") or tag.get("src")
    if not value:
        return None
    absolute = urljoin(source_page_url, str(value).strip())
    try:
        return _validate_public_https(absolute)
    except ValueError:
        return None


async def filter_reachable_image_candidates(
    candidates: list[dict], client: httpx.AsyncClient | None = None,
) -> tuple[list[dict], int]:
    """管理员显式发现媒体时过滤当前无法读取的外部图片，视频原样保留。"""
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": "SA-LogiFlow-HotspotMedia/1.0"},
    )
    semaphore = asyncio.Semaphore(4)

    async def url_is_reachable(url: str) -> bool:
        async with semaphore:
            response = await client.head(url)
            if response.status_code in {405, 501}:
                request = client.build_request("GET", url, headers={"Range": "bytes=0-0"})
                response = await client.send(request, stream=True)
                try:
                    response.raise_for_status()
                    _validate_public_https(str(response.url))
                    mime = (response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
                    return mime in ALLOWED_IMAGE_MIME
                finally:
                    await response.aclose()
            response.raise_for_status()
            _validate_public_https(str(response.url))
            mime = (response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
            return mime in ALLOWED_IMAGE_MIME

    async def reachable_item(item: dict) -> dict | None:
        if item.get("media_kind") != "image":
            return item
        original = str(item.get("original_media_url") or "")
        variants = list(dict.fromkeys([original, IMAGE_SIZE_SUFFIX.sub("", original)]))
        for candidate_url in variants:
            try:
                candidate_url = _validate_public_https(candidate_url)
                if not await url_is_reachable(candidate_url):
                    continue
            except (ValueError, httpx.HTTPError):
                continue
            if candidate_url == original:
                return item
            repaired = dict(item)
            repaired["original_media_url"] = candidate_url
            if not item.get("thumbnail_url") or item.get("thumbnail_url") == original:
                repaired["thumbnail_url"] = candidate_url
            return repaired
        return None

    try:
        results = await asyncio.gather(*(reachable_item(item) for item in candidates))
    finally:
        if owns_client:
            await client.aclose()
    kept = [item for item in results if item is not None]
    skipped = sum(
        1 for original, result in zip(candidates, results)
        if original.get("media_kind") == "image" and result is None
    )
    return kept, skipped


def _validate_public_https(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("仅支持无账号信息的 HTTPS 视频链接")
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise ValueError("不允许本机或内网视频链接")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ValueError("不允许本机或内网视频链接")
    return parsed.geturl()


def normalize_video_url(url: str, base_url: str | None = None) -> tuple[str, str, str | None]:
    absolute = urljoin(base_url or "", str(url or "").strip())
    _validate_public_https(absolute)
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").casefold()
    query = parse_qs(parsed.query)
    if "list" in query:
        raise ValueError("播放列表不能作为单条热点视频")
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/")[0]
        if not video_id:
            raise ValueError("YouTube 链接缺少视频 ID")
        return f"https://www.youtube.com/watch?v={video_id}", "youtube", video_id
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        path = parsed.path.rstrip("/")
        if path == "/watch":
            video_id = (query.get("v") or [""])[0]
        elif path.startswith(("/shorts/", "/embed/")):
            parts = path.strip("/").split("/")
            video_id = parts[1] if len(parts) > 1 else ""
        else:
            raise ValueError("频道或播放列表不能作为单条热点视频")
        if not video_id:
            raise ValueError("YouTube 链接缺少视频 ID")
        return f"https://www.youtube.com/watch?v={video_id}", "youtube", video_id
    if host.endswith("tiktok.com"):
        matched = re.search(r"/video/(\d+)", parsed.path)
        if not matched:
            raise ValueError("TikTok 链接必须指向单条视频")
        canonical = f"https://{host}{parsed.path.rstrip('/')}"
        return canonical, "tiktok", matched.group(1)
    if not parsed.path.casefold().endswith(VIDEO_SUFFIXES):
        raise ValueError("新闻视频直链必须是受支持的视频文件或 HLS 地址")
    return absolute, "direct", None


def validate_single_video_url(url: str) -> str:
    canonical, _, _ = normalize_video_url(url)
    return canonical


def _duration_seconds(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    matched = re.fullmatch(
        r"PT(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
        str(value or "").strip(),
        re.IGNORECASE,
    )
    if not matched:
        return None
    return (
        float(matched.group("hours") or 0) * 3600
        + float(matched.group("minutes") or 0) * 60
        + float(matched.group("seconds") or 0)
    )


def _jsonld_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "video", "subjectOf"):
            if key in value:
                yield from _jsonld_objects(value[key])


def discover_media_candidates(html: str, source_page_url: str) -> list[dict]:
    _validate_public_https(source_page_url)
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[dict] = []

    def add_image(value: str | None, thumbnail: str | None = None):
        if not value:
            return
        if sum(item["media_kind"] == "image" for item in candidates) >= ARTICLE_IMAGE_LIMIT:
            return
        absolute = urljoin(source_page_url, value)
        try:
            _validate_public_https(absolute)
        except ValueError:
            return
        candidates.append({
            "media_kind": "image", "platform": "direct", "platform_media_id": None,
            "source_page_url": source_page_url, "original_media_url": absolute,
            "embed_url": None, "thumbnail_url": urljoin(source_page_url, thumbnail) if thumbnail else absolute,
            "duration_seconds": None, "mime_type": None,
        })

    def add_video(value: str | None, thumbnail: str | None = None, duration=None, embed_url: str | None = None):
        if not value:
            return
        try:
            canonical, platform, platform_id = normalize_video_url(value, source_page_url)
        except ValueError:
            return
        candidates.append({
            "media_kind": "video_link", "platform": platform, "platform_media_id": platform_id,
            "source_page_url": source_page_url, "original_media_url": canonical,
            "embed_url": urljoin(source_page_url, embed_url) if embed_url else None,
            "thumbnail_url": urljoin(source_page_url, thumbnail) if thumbnail else None,
            "duration_seconds": _duration_seconds(duration), "mime_type": None,
        })

    for tag in soup.select('meta[property="og:image"],meta[name="og:image"]'):
        add_image(tag.get("content"))
    article_image_selectors = (
        "article img, main img, .entry-content img, .post-content img, "
        ".td-post-content img, .article-content img"
    )
    for tag in soup.select(article_image_selectors):
        if tag.find_parent(["header", "footer", "nav", "aside"]):
            continue
        value = _preferred_article_image(tag, source_page_url)
        identity = " ".join([
            str(value or ""), str(tag.get("alt") or ""), str(tag.get("id") or ""),
            " ".join(tag.get("class") or []), str(tag.get("role") or ""),
        ])
        if ARTICLE_IMAGE_EXCLUDE.search(identity):
            continue
        try:
            width = int(str(tag.get("width") or "0").rstrip("px"))
            height = int(str(tag.get("height") or "0").rstrip("px"))
        except ValueError:
            width = height = 0
        if (width and width < 120) or (height and height < 120):
            continue
        if urlparse(str(value or "")).path.casefold().endswith((".svg", ".gif")):
            continue
        add_image(value)
    for tag in soup.select('meta[property="og:video"],meta[property="og:video:url"],meta[name="og:video"]'):
        add_video(tag.get("content"))
    for tag in soup.select("video[src],video source[src]"):
        add_video(tag.get("src"))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text() or "null")
        except (TypeError, json.JSONDecodeError):
            continue
        for item in _jsonld_objects(payload):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if not any(str(value).casefold() == "videoobject" for value in types):
                continue
            thumbnail = item.get("thumbnailUrl")
            if isinstance(thumbnail, list):
                thumbnail = thumbnail[0] if thumbnail else None
            add_video(
                item.get("contentUrl") or item.get("embedUrl"),
                thumbnail=thumbnail,
                duration=item.get("duration"),
                embed_url=item.get("embedUrl"),
            )
    for frame in soup.select("iframe[src]"):
        src = frame.get("src")
        if src and "youtu" in src.casefold():
            add_video(src, embed_url=src)

    unique: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (item["media_kind"], item["original_media_url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


async def fetch_source_page(url: str) -> tuple[str, str]:
    """读取已登记热点原文；限制响应体，且不携带任何登录状态。"""
    _validate_public_https(url)
    headers = {"User-Agent": "SA-LogiFlow-HotspotMedia/1.0"}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        final_url = str(response.url)
        _validate_public_https(final_url)
        content_type = (response.headers.get("content-type") or "").casefold()
        if "html" not in content_type and content_type:
            raise ValueError("热点原文不是 HTML 页面")
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError("热点原文超过 2MB，已停止媒体发现")
        return response.text, final_url


def validate_materialization(item: dict, role: str, confirmed: bool) -> None:
    if role != "admin":
        raise PermissionError("仅管理员可将热点媒体转为本地素材")
    if not confirmed:
        raise ValueError("必须完成人工确认后才能下载热点媒体")
    if item.get("authorization_status") == "blocked":
        raise ValueError("该热点媒体已被管理员停用")
    if item.get("media_kind") not in {"video_link", "image"}:
        raise ValueError("只有尚未素材化的热点图片或单条视频链接可以下载")


def download_authorized_image(
    item: dict, static_dir: Path, created_by: int, client: httpx.Client | None = None,
) -> dict:
    """下载已完成权利确认的公开图片并进入现有素材库。"""
    import database as db
    import media_assets

    media_url = _validate_public_https(str(item.get("original_media_url") or ""))
    owns_client = client is None
    client = client or httpx.Client(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "SA-LogiFlow-HotspotMedia/1.0"},
    )
    try:
        response = client.get(media_url)
        response.raise_for_status()
        _validate_public_https(str(response.url))
        mime = (response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
        if mime not in ALLOWED_IMAGE_MIME:
            raise RuntimeError("热点图片格式必须是 JPEG、PNG 或 WebP")
        declared_size = int(response.headers.get("content-length") or 0)
        if declared_size > MAX_HOTSPOT_IMAGE or len(response.content) > MAX_HOTSPOT_IMAGE:
            raise RuntimeError("热点图片超过 10MB 限制")
        if not response.content:
            raise RuntimeError("热点图片响应为空")
        with tempfile.TemporaryDirectory(prefix="salogiflow-hotspot-image-") as temp_value:
            source = Path(temp_value) / f"hotspot{ALLOWED_IMAGE_MIME[mime]}"
            source.write_bytes(response.content)
            asset = media_assets.ingest_file(
                source,
                Path(static_dir),
                category="other",
                origin="official_news",
                created_by=created_by,
                name=item.get("publisher") or f"热点图片 {item.get('id')}",
            )
        db.update_asset_provenance(
            asset["id"],
            media_url,
            item.get("license_name") or "",
            item.get("attribution") or "",
            item.get("hotspot_id"),
        )
        return db.get_asset(asset["id"]) or asset
    finally:
        if owns_client:
            client.close()


def download_authorized_video(
    item: dict,
    static_dir,
    created_by: int,
    progress_callback=None,
    *,
    hi_res: bool = False,
    explicit_ranges: list[tuple[float, float]] | None = None,
) -> dict:
    """复用现有授权视频下载与素材入库实现，不携带 Cookie。默认走分析档（低清）。"""
    import inspiration_assets

    source_type = item.get("platform")
    if source_type not in {"youtube", "tiktok"}:
        source_type = "other_link"
    adapter_item = {
        "canonical_url": item["original_media_url"],
        "source_type": source_type,
        "primary_category": "south_africa_hotspot",
        "title": item.get("intake_title") or item.get("publisher") or f"热点视频 {item['id']}",
        "license_name": item.get("license_name") or "",
        "attribution": item.get("attribution") or "",
        "hotspot_id": item.get("hotspot_id"),
        "duration_seconds": item.get("duration_seconds") or 0,
    }
    asset = inspiration_assets.download_authorized_media(
        adapter_item,
        static_dir,
        created_by,
        progress_callback=progress_callback,
        hi_res=hi_res,
        explicit_ranges=explicit_ranges,
    )
    if asset.get("file_type") != "video":
        raise RuntimeError("下载结果不是视频文件")
    return asset
