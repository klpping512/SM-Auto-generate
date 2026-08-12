"""灵感链接归一化、元数据与经授权素材化。"""
from __future__ import annotations

import datetime
import ipaddress
import math
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip() or default)
    except (TypeError, ValueError):
        return default


def analysis_height() -> int:
    return max(144, _env_int("SA_HOTSPOT_ANALYSIS_HEIGHT", 480))


def final_height() -> int:
    return max(144, _env_int("SA_HOTSPOT_FINAL_HEIGHT", 720))


def sample_window_sec() -> int:
    return max(10, _env_int("SA_HOTSPOT_SAMPLE_WINDOW_SEC", 60))


def sample_max_total_sec() -> int:
    return max(sample_window_sec(), _env_int("SA_HOTSPOT_SAMPLE_MAX_TOTAL_SEC", 300))


def single_window_sec() -> int:
    """多窗关闭时的单一连续分析窗秒数（默认 120，沿用老行为）。"""
    return max(10, _env_int("SA_HOTSPOT_SINGLE_WINDOW_SEC", 120))


def multiwindow_enabled() -> bool:
    """长视频均匀多窗采样开关。默认关闭：真修好前保持单一连续窗，避免 offsets 虚报。"""
    return str(os.environ.get("SA_HOTSPOT_MULTIWINDOW", "0")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def compute_analysis_sample_windows(duration_seconds: float) -> list[tuple[float, float]]:
    """分析档采样窗。短片不下采样窗；长视频默认单一连续窗，多窗需显式开启。

    D<=180：空列表（调用方下整片）。
    D>180 且 multiwindow 关：单一连续窗 [(0, min(SINGLE_WINDOW, D))]。
    D>180 且 multiwindow 开：均匀铺 N 个不重叠 WINDOW 秒窗口，总采样 ≤ MAX_TOTAL。
    例 D=600、WINDOW=60、MAX_TOTAL=300 → 起点 0/135/270/405/540。
    """
    duration = float(duration_seconds or 0)
    if duration <= 180:
        return []
    if not multiwindow_enabled():
        window = float(single_window_sec())
        return [(0.0, min(window, duration))]
    window = float(sample_window_sec())
    max_total = float(sample_max_total_sec())
    n = max(1, min(int(math.floor(max_total / window)), int(math.floor(duration / window))))
    if n <= 1:
        return [(0.0, min(window, duration))]
    span = n * window
    if span >= duration:
        # 窗口挤满整片：相邻紧挨
        return [(i * window, min(duration, (i + 1) * window)) for i in range(n)]
    gap = (duration - span) / (n - 1)
    starts = [i * (window + gap) for i in range(n)]
    return [(start, min(duration, start + window)) for start in starts]


def analysis_ms_to_original_ms(local_ms: int, windows: list[tuple[float, float]]) -> int:
    """把分析件内部相对毫秒换算回原片真实毫秒。"""
    if not windows:
        return max(0, int(local_ms))
    remaining = max(0, int(local_ms))
    for start_sec, end_sec in windows:
        span_ms = max(0, int(round((end_sec - start_sec) * 1000)))
        if remaining <= span_ms:
            return int(round(start_sec * 1000)) + remaining
        remaining -= span_ms
    last_start, last_end = windows[-1]
    return int(round(last_end * 1000))


def original_ms_to_analysis_ms(original_ms: int, windows: list[tuple[float, float]]) -> int | None:
    """原片真实毫秒 → 分析件相对毫秒；落在窗口缝隙则返回 None。"""
    if not windows:
        return max(0, int(original_ms))
    cursor = 0
    target = max(0, int(original_ms))
    for start_sec, end_sec in windows:
        start_ms = int(round(start_sec * 1000))
        end_ms = int(round(end_sec * 1000))
        span_ms = max(0, end_ms - start_ms)
        if start_ms <= target <= end_ms:
            return cursor + (target - start_ms)
        cursor += span_ms
    return None


def build_ytdlp_options(
    source_type: str,
    duration_seconds: float = 0,
    progress_callback=None,
    *,
    hi_res: bool = False,
    explicit_ranges: list[tuple[float, float]] | None = None,
) -> dict:
    """构建受限、可观测的视频下载参数。

    - 分析档（hi_res=False）：默认 480p；长视频默认单一连续窗（多窗需 SA_HOTSPOT_MULTIWINDOW=1）。
    - 定稿档（hi_res=True）：默认 720p；按 explicit_ranges 精确下片段，不多窗。
    """
    height = final_height() if hi_res else analysis_height()
    options = {
        "noplaylist": True,
        "max_filesize": 2 * 1024 * 1024 * 1024,
        "format": f"bv*[height<={height}]+ba/b[height<={height}]/b",
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
    # Prefer the unified hotspot proxy; empty SA_YOUTUBE_PROXY must not wipe it.
    # Fall back to local VPN HTTP port when neither variable is set.
    proxy = (
        str(
            os.environ.get("SA_HOTSPOT_PROXY")
            or os.environ.get("SA_YOUTUBE_PROXY")
            or "http://127.0.0.1:7897"
        ).strip()
        if source_type == "youtube"
        else ""
    )
    if proxy:
        options["proxy"] = proxy
    if source_type == "youtube":
        # 云端代理下 web client 偶发被 YouTube reset；Android client 可稳定
        # 返回公开元数据和可下载格式，不携带 Cookie、不绕过 DRM。
        options["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    if progress_callback:
        options["progress_hooks"] = [progress_callback]

    ranges: list[tuple[float, float]] = []
    if explicit_ranges:
        ranges = [(float(a), float(b)) for a, b in explicit_ranges if float(b) > float(a)]
    elif not hi_res:
        ranges = compute_analysis_sample_windows(duration_seconds)

    if ranges:
        from yt_dlp.utils import download_range_func
        options["download_ranges"] = download_range_func(None, ranges)
        options["force_keyframes_at_cuts"] = True
        options["_sample_offsets"] = [(float(a), float(b)) for a, b in ranges]
    else:
        options["_sample_offsets"] = []
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


def _run_ytdlp_download(
    canonical: str,
    options: dict,
    temp_dir: Path,
) -> tuple[Path, dict]:
    from yt_dlp import YoutubeDL

    options = dict(options)
    options.pop("_sample_offsets", None)
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
    return source, info


def _extract_upload_iso(info: dict) -> str | None:
    """批17：从 yt-dlp 全量信息取上传日期，统一输出 YYYY-MM-DD。"""
    upload_date = str(info.get("upload_date") or "")
    if len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    timestamp = info.get("timestamp")
    if timestamp:
        try:
            return datetime.datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            return None
    return None


def download_authorized_media(
    item: dict,
    static_dir: Path,
    created_by: int,
    progress_callback=None,
    *,
    hi_res: bool = False,
    explicit_ranges: list[tuple[float, float]] | None = None,
) -> dict:
    """不使用 Cookie、不绕过登录/DRM，下载公开且已登记授权的单条媒体。"""
    try:
        from yt_dlp import YoutubeDL  # noqa: F401
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
            hi_res=hi_res,
            explicit_ranges=explicit_ranges,
        )
        sample_offsets = list(options.get("_sample_offsets") or [])
        source, info = _run_ytdlp_download(canonical, options, temp_dir)
        asset = media_assets.ingest_file(
            source, Path(static_dir), category=item.get("primary_category") or "other",
            origin=item["source_type"], created_by=created_by, name=item.get("title") or source.stem,
        )
        db.update_asset_provenance(
            asset["id"], canonical, item["license_name"], item["attribution"], item.get("hotspot_id"),
        )
        # 批17：下载即拿到全量 info，回填父热点真实发布时间（仅当为空/1970 哨兵）
        upload_iso = _extract_upload_iso(info)
        if upload_iso and item.get("hotspot_id"):
            db.update_hotspot_published_at_if_empty(int(item["hotspot_id"]), upload_iso)
        asset = dict(asset)
        asset["sample_offsets"] = sample_offsets
        asset["download_hi_res"] = bool(hi_res)
        return asset


def download_hi_res_range(
    item: dict,
    static_dir: Path,
    start_sec: float,
    end_sec: float,
    created_by: int | None = None,
) -> Path:
    """定稿：按原片真实时间段精确下载 720p 片段，返回本地文件路径（未入库）。"""
    try:
        from yt_dlp import YoutubeDL  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("未安装 yt-dlp，无法执行已授权下载") from exc

    if float(end_sec) <= float(start_sec):
        raise ValueError("定稿时间段无效")
    canonical = normalize_url(item["canonical_url"])
    source_type = item.get("source_type") or source_type_for(canonical)
    # Pad slightly so keyframe cuts still cover the hook.
    padded_start = max(0.0, float(start_sec) - 0.25)
    padded_end = float(end_sec) + 0.25
    out_dir = Path(static_dir) / "assets" / "hotspot-events" / "_hires_tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="salogiflow-hires-", dir=out_dir) as temp_value:
        temp_dir = Path(temp_value)
        options = build_ytdlp_options(
            source_type,
            duration_seconds=0,
            hi_res=True,
            explicit_ranges=[(padded_start, padded_end)],
        )
        source, _ = _run_ytdlp_download(canonical, options, temp_dir)
        target = out_dir / f"hires-{Path(source).stem}-{int(start_sec * 1000)}-{int(end_sec * 1000)}.mp4"
        target.write_bytes(source.read_bytes())
        return target
