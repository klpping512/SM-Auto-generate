"""灵感链接归一化、元数据与经授权素材化。"""
from __future__ import annotations

import ipaddress
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


TRACKING_PARAMS = {"fbclid", "gclid", "si", "feature", "ref", "source", "share_app_id"}


def _validate_public_https(url: str):
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("仅支持无账号信息的 HTTPS 链接")
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise ValueError("不允许本地链接")
    try:
        address = ipaddress.ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("不允许内网或本机链接")
    except ValueError as exc:
        if "不允许" in str(exc):
            raise


def normalize_url(url: str) -> str:
    raw = str(url or "").strip()
    _validate_public_https(raw)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = parse_qs(parsed.query, keep_blank_values=False)
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = path.strip("/").split("/")[0]
        if not video_id:
            raise ValueError("YouTube 链接缺少视频 ID")
        return f"https://www.youtube.com/watch?v={video_id}"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path == "/watch":
            video_id = (query.get("v") or [""])[0]
        elif path.startswith(("/shorts/", "/embed/")):
            video_id = path.strip("/").split("/")[1]
        else:
            video_id = ""
        if not video_id:
            raise ValueError("YouTube 链接缺少视频 ID")
        return f"https://www.youtube.com/watch?v={video_id}"
    if host.endswith("tiktok.com"):
        return urlunparse(("https", host, path.rstrip("/") or "/", "", "", ""))
    clean_query = {
        key: values for key, values in query.items()
        if key.casefold() not in TRACKING_PARAMS and not key.casefold().startswith("utm_")
    }
    return urlunparse(("https", host, path.rstrip("/") or "/", "", urlencode(clean_query, doseq=True), ""))


def source_type_for(url: str) -> str:
    host = (urlparse(normalize_url(url)).hostname or "").casefold()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("tiktok.com"):
        return "tiktok"
    if host == "iol.co.za" or host.endswith(".iol.co.za"):
        return "secondary_discovery"
    return "official_news" if host.endswith((".gov.za", ".org.za")) else "other_link"


def validate_materialization(item: dict, role: str, confirmed: bool) -> None:
    if role != "admin":
        raise PermissionError("仅管理员可将外部链接转为本地素材")
    if not confirmed:
        raise ValueError("必须完成人工确认后才能下载")
    if item.get("source_type") not in {"youtube", "tiktok", "official_news", "other_link"}:
        raise ValueError("来源类型不支持素材化")
    if item.get("rights_status") not in {"confirmed", "licensed"}:
        raise ValueError("版权状态未确认")
    if not all(str(item.get(field) or "").strip() for field in ("license_name", "attribution", "rights_evidence_url")):
        raise ValueError("必须填写授权名称、署名与授权证据链接")
    _validate_public_https(str(item["rights_evidence_url"]))


def can_auto_materialize_official(item: dict) -> bool:
    try:
        required = bool(
            item.get("source_type") == "official_news"
            and item.get("rights_status") == "licensed"
            and str(item.get("license_name") or "").strip()
            and str(item.get("media_url") or "").strip()
            and str(item.get("rights_evidence_url") or "").strip()
        )
        if not required:
            return False
        _validate_public_https(str(item["media_url"]))
        _validate_public_https(str(item["rights_evidence_url"]))
        return True
    except ValueError:
        return False


def build_ytdlp_options(
    source_type: str,
    duration_seconds: float = 0,
    progress_callback=None,
) -> dict:
    """构建受限、可观测的视频下载参数，避免整片长视频占满磁盘。"""
    options = {
        "noplaylist": True,
        "max_filesize": 2 * 1024 * 1024 * 1024,
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": None,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "js_runtimes": {"node": {}},
    }
    proxy = os.environ.get(
        "SA_YOUTUBE_PROXY", "http://127.0.0.1:7897"
    ).strip() if source_type == "youtube" else ""
    if proxy:
        options["proxy"] = proxy
    if progress_callback:
        options["progress_hooks"] = [progress_callback]
    if float(duration_seconds or 0) > 600:
        from yt_dlp.utils import download_range_func
        options["download_ranges"] = download_range_func(None, [(0, 120)])
        options["force_keyframes_at_cuts"] = True
    return options


async def fetch_oembed(url: str) -> dict:
    """只访问平台公开 oEmbed 端点，不携带 Cookie 或登录态。"""
    import httpx

    canonical = normalize_url(url)
    source_type = source_type_for(canonical)
    if source_type == "youtube":
        endpoint = "https://www.youtube.com/oembed"
    elif source_type == "tiktok":
        endpoint = "https://www.tiktok.com/oembed"
    else:
        return {}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        response = await client.get(endpoint, params={"url": canonical, "format": "json"})
        response.raise_for_status()
        data = response.json()
    return {
        "title": str(data.get("title") or "")[:300],
        "author": str(data.get("author_name") or "")[:300],
        "thumbnail_url": str(data.get("thumbnail_url") or "")[:1_000] or None,
    }


def download_authorized_media(
    item: dict,
    static_dir: Path,
    created_by: int,
    progress_callback=None,
) -> dict:
    """不使用 Cookie、不绕过登录/DRM，下载公开且已登记授权的单条媒体。"""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("未安装 yt-dlp，无法执行已授权下载") from exc
    import database as db
    import media_assets

    canonical = normalize_url(item["canonical_url"])
    with tempfile.TemporaryDirectory(prefix="salogiflow-authorized-") as temp_value:
        temp_dir = Path(temp_value)
        options = build_ytdlp_options(
            item.get("source_type") or "other_link",
            item.get("duration_seconds") or 0,
            progress_callback,
        )
        options["outtmpl"] = str(temp_dir / "%(id)s.%(ext)s")
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(canonical, download=True)
            requested = info.get("requested_downloads") or []
            candidates = [Path(entry.get("filepath")) for entry in requested if entry.get("filepath")]
            if info.get("_filename"):
                candidates.append(Path(info["_filename"]))
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            source = next((path for path in temp_dir.iterdir() if path.is_file()), None)
        if source is None:
            raise RuntimeError("平台未返回可用媒体文件")
        asset = media_assets.ingest_file(
            source, Path(static_dir), category=item.get("primary_category") or "other",
            origin=item["source_type"], created_by=created_by, name=item.get("title") or source.stem,
        )
        db.update_asset_provenance(
            asset["id"], canonical, item["license_name"], item["attribution"], item.get("hotspot_id"),
        )
        return asset
