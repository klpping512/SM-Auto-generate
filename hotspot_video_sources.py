"""低成本 YouTube 频道热点发现：只读取单视频元数据，不下载媒体。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Callable

import database as db
import hotspot_topic_packages


DEFAULT_YOUTUBE_CHANNELS = [
    # 2026-08-05 定稿：综合走量骨架 + 商业/港口垂直。不含 SA Today / SAN / SABC / Moneyweb YT。
    {"name": "eNCA", "url": "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA"},
    {"name": "Newzroom Afrika", "url": "https://www.youtube.com/@NewzroomAfrikaTV"},
    {"name": "CNBC Africa", "url": "https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ"},
    {"name": "BusinessDayTV", "url": "https://www.youtube.com/@BusinessDayTelevision"},
    # 常青实拍：最新窗常 not available，加深扫描 + -F 预检取满可物料化母片。
    {
        "name": "Transnet NPA",
        "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw",
        "evergreen": True,
        "min_downloadable": 10,
        "playlist_scan_cap": 20,
    },
]
DEFAULT_CHANNEL_VIDEO_LIMIT = 8
MAX_CHANNEL_VIDEO_LIMIT = 24
MATERIALIZATION_RETRY_HOURS = 48
NOT_AVAILABLE_MARKERS = (
    "this video is not available",
    "video is unavailable",
    "private video",
    "members-only",
)
# 每频道每轮默认读取最近 8 条（仅元数据，不下载），保证漏斗顶端有足够候选；
# 管理员批量验收可通过环境变量扩展至 24 条，以便模型有足够候选找到不同物流题材。
try:
    CHANNEL_VIDEO_LIMIT = max(1, min(MAX_CHANNEL_VIDEO_LIMIT, int(os.environ.get("HOTSPOT_YOUTUBE_CHANNEL_VIDEO_LIMIT", DEFAULT_CHANNEL_VIDEO_LIMIT))))
except ValueError:
    CHANNEL_VIDEO_LIMIT = DEFAULT_CHANNEL_VIDEO_LIMIT


def _normalize_channel(item: dict, *, inherit: dict | None = None) -> dict | None:
    name = str(item.get("name") or "").strip()
    url = str(item.get("url") or "").strip().rstrip("/")
    if not name or not url:
        return None
    if not (url.startswith("https://www.youtube.com/") or url.startswith("https://youtube.com/")):
        return None
    base = inherit or {}
    channel = {"name": name[:100], "url": url}
    evergreen = item.get("evergreen", base.get("evergreen", False))
    if evergreen:
        channel["evergreen"] = True
        try:
            channel["min_downloadable"] = max(
                1, int(item.get("min_downloadable") or base.get("min_downloadable") or 10)
            )
        except (TypeError, ValueError):
            channel["min_downloadable"] = 10
        try:
            channel["playlist_scan_cap"] = max(
                1,
                min(
                    MAX_CHANNEL_VIDEO_LIMIT,
                    int(item.get("playlist_scan_cap") or base.get("playlist_scan_cap") or 20),
                ),
            )
        except (TypeError, ValueError):
            channel["playlist_scan_cap"] = 20
    return channel


def configured_channels() -> list[dict]:
    """返回当前生效的 YouTube 频道信源。

    支持环境变量 ``SA_HOTSPOT_VIDEO_CHANNELS_JSON``（形如
    ``[{"name": "eNCA", "url": "https://www.youtube.com/@..."}]``）完全覆盖默认
    清单，语义与 ``SA_HOTSPOT_FEEDS_JSON`` 一致；未配置或配置非法时回落到
    ``DEFAULT_YOUTUBE_CHANNELS``。仅接受 HTTPS 的 youtube.com 频道地址。
    同名默认频道的 evergreen 策略在 env 未显式覆盖时自动继承。
    """
    defaults_by_name = {str(item["name"]): dict(item) for item in DEFAULT_YOUTUBE_CHANNELS}
    raw = os.environ.get("SA_HOTSPOT_VIDEO_CHANNELS_JSON", "")
    if raw.strip():
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            items = []
        channels = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            normalized = _normalize_channel(item, inherit=defaults_by_name.get(name))
            if normalized:
                channels.append(normalized)
        if channels:
            return channels
    return [dict(item) for item in DEFAULT_YOUTUBE_CHANNELS]


def is_not_available_error(message: str) -> bool:
    text = str(message or "").casefold()
    return any(marker in text for marker in NOT_AVAILABLE_MARKERS)


def retry_after_iso(hours: int = MATERIALIZATION_RETRY_HOURS) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(hours=max(1, int(hours)))).isoformat()


def _formats_command(video_url: str) -> list[str]:
    command = [sys.executable, "-m", "yt_dlp", "-F", "--no-warnings"]
    proxy = str(os.environ.get("SA_HOTSPOT_PROXY") or os.environ.get("SA_YOUTUBE_PROXY") or "").strip()
    if proxy:
        command.extend(["--proxy", proxy])
    command.append(video_url)
    return command


def probe_video_downloadable(video_url: str, *, runner: Callable = subprocess.run) -> tuple[bool, str]:
    """yt-dlp -F 预检：可列格式则视为可物料化。"""
    try:
        completed = runner(
            _formats_command(video_url),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]
    blob = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if completed.returncode == 0 and ("mp4" in blob.casefold() or "webm" in blob.casefold() or "m4a" in blob.casefold()):
        return True, ""
    return False, (completed.stderr or completed.stdout or "yt-dlp -F failed").strip()[:300]


# 三天全量 Hook 任务会逐条读取当前资讯库的单视频事实；上限只防止异常数据库
# 无限增长，不再把正常候选压缩为二十多条。
INTAKE_METADATA_LIMIT = 500


def _configured_source_authorization() -> tuple[str, str]:
    if os.environ.get("HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED", "0") == "1":
        return "authorized", "已配置频道已获企业授权，可自动下载分析；仅限已授权使用范围。"
    return "pending_review", "公开视频仅作线索，需确认授权后下载分析。"


def _published_at(entry: dict) -> str | None:
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            pass
    upload_date = str(entry.get("upload_date") or "")
    if len(upload_date) == 8 and upload_date.isdigit():
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
    return None


def _thumbnail(entry: dict) -> str | None:
    value = str(entry.get("thumbnail") or "").strip()
    if value.startswith("https://"):
        return value
    for item in reversed(entry.get("thumbnails") or []):
        value = str(item.get("url") or "").strip() if isinstance(item, dict) else ""
        if value.startswith("https://"):
            return value
    return None


def _command(channel_url: str, limit: int) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--playlist-end",
        str(limit),
        "--dump-single-json",
        "--no-warnings",
    ]
    proxy = str(os.environ.get("SA_HOTSPOT_PROXY") or os.environ.get("SA_YOUTUBE_PROXY") or "").strip()
    if proxy:
        command.extend(["--proxy", proxy])
    command.append(channel_url.rstrip("/") + "/videos")
    return command


def _metadata_command(video_url: str) -> list[str]:
    """Read one authorised video's public facts without downloading media bytes."""
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--no-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--socket-timeout",
        "20",
    ]
    proxy = str(os.environ.get("SA_HOTSPOT_PROXY") or os.environ.get("SA_YOUTUBE_PROXY") or "").strip()
    if proxy:
        command.extend(["--proxy", proxy])
    command.append(video_url)
    return command


def _compact_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def read_youtube_video_metadata(
    video_url: str,
    *,
    runner: Callable = subprocess.run,
) -> dict:
    """Return source-provided title/description/tags for pre-download Qwen intake."""
    completed = runner(
        _metadata_command(video_url),
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "YouTube 视频元数据读取失败").strip()[:300])
    try:
        entry = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("YouTube 视频元数据不是合法 JSON") from exc
    title = _compact_text(entry.get("title"), 300)
    if not title:
        raise RuntimeError("YouTube 视频元数据缺少标题")
    description = _compact_text(entry.get("description"), 1800)
    tags = [
        _compact_text(tag, 80)
        for tag in (entry.get("tags") or [])
        if _compact_text(tag, 80)
    ][:12]
    summary_parts = [description] if description else []
    if tags:
        summary_parts.append("标签：" + "、".join(tags))
    if not summary_parts:
        summary_parts.append(f"视频标题：{title}")
    duration = entry.get("duration")
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    return {
        "title": title,
        "summary": "\n".join(summary_parts)[:2000],
        "duration_seconds": duration_seconds,
        "thumbnail_url": _thumbnail(entry),
    }


def hydrate_youtube_intake_metadata(
    candidates: list[dict],
    *,
    runner: Callable = subprocess.run,
    limit: int = INTAKE_METADATA_LIMIT,
) -> tuple[list[dict], dict]:
    """Attach public video facts to pre-download candidates (metadata hydration).

    Candidates receive actual source-provided title/description, never guessed
    visual content, before any downstream analysis or curation runs.
    """
    hydrated: list[dict] = []
    report = {"requested": 0, "ready": 0, "cached": 0, "failed": []}
    for original in candidates:
        item = dict(original)
        if len(hydrated) >= max(1, min(int(limit), INTAKE_METADATA_LIMIT)):
            hydrated.append(item)
            continue
        if item.get("platform") != "youtube":
            hydrated.append(item)
            continue
        if str(item.get("intake_metadata_status") or "") == "ready" and (
            str(item.get("intake_title") or "").strip()
            or str(item.get("intake_summary") or "").strip()
        ):
            report["cached"] += 1
            hydrated.append(item)
            continue
        report["requested"] += 1
        try:
            metadata = read_youtube_video_metadata(str(item.get("original_media_url") or ""), runner=runner)
            updated = db.update_hotspot_media_intake_metadata(
                int(item["id"]), metadata["title"], metadata["summary"], "ready"
            ) or item
            if metadata.get("duration_seconds"):
                db.update_hotspot_media_state(int(item["id"]), duration_seconds=metadata["duration_seconds"])
                updated["duration_seconds"] = metadata["duration_seconds"]
            if metadata.get("thumbnail_url") and not updated.get("thumbnail_url"):
                db.upsert_hotspot_media({**updated, "thumbnail_url": metadata["thumbnail_url"]})
                updated["thumbnail_url"] = metadata["thumbnail_url"]
            hydrated.append(updated)
            report["ready"] += 1
        except Exception as exc:
            message = str(exc)[:220]
            db.update_hotspot_media_intake_metadata(int(item["id"]), "", "", "failed")
            item["intake_metadata_status"] = "failed"
            hydrated.append(item)
            report["failed"].append({"media_id": int(item["id"]), "error": message})
    return hydrated, report


def _upsert_channel_video(
    *,
    name: str,
    entry: dict,
    retrieved_at: str,
    download_status: str,
    retryable: bool = False,
    retry_after: str | None = None,
    precheck_error: str = "",
) -> tuple[bool, bool, int | None]:
    """写入热点+媒体。返回 (hotspot_created, media_created, media_id)。"""
    video_id = str(entry.get("id") or "").strip()
    title = str(entry.get("title") or "").strip()
    if not video_id or not title:
        return False, False, None
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    published_at = _published_at(entry)
    thumbnail = _thumbnail(entry)
    snapshot = hashlib.sha256(json.dumps({
        "id": video_id,
        "title": title,
        "duration": entry.get("duration"),
        "published_at": published_at,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    hotspot_id, created = db.upsert_hotspot({
        "title": title[:300],
        "summary": f"来自 {name} 的公开视频热点。下载和使用前仍需逐条确认权利。",
        "source_url": video_url,
        "publisher": name,
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "snapshot_sha256": snapshot,
        "image_candidate_url": thumbnail,
        "status": "new",
    })
    try:
        signal = hotspot_topic_packages.normalize_signal({
            "hotspot_id": hotspot_id,
            "source_name": name,
            "source_type": "youtube",
            "external_id": video_id,
            "title": title,
            "summary": f"来自 {name} 的公开视频热点。",
            "source_url": video_url,
            "published_at": published_at,
            "retrieved_at": retrieved_at,
            "metrics": {"media_count": 1, "video_growth": entry.get("view_count", 0)},
            "raw_payload": entry,
        })
        db.upsert_hotspot_signal(signal)
    except Exception:
        pass
    authorization_status, rights_note = _configured_source_authorization()
    detail = ""
    if retryable:
        detail = f"可重试：最新窗暂不可下载，将于 {retry_after or ''} 后重试。{precheck_error}"[:300]
    media_id, media_created = db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "platform_media_id": video_id,
        "source_page_url": video_url,
        "original_media_url": video_url,
        "thumbnail_url": thumbnail,
        "publisher": name,
        "author": str(entry.get("channel") or entry.get("uploader") or name)[:300],
        "published_at": published_at,
        "duration_seconds": entry.get("duration"),
        "authorization_status": authorization_status,
        "rights_note": rights_note,
        "download_status": download_status,
        "processing_status": "not_started",
        "progress_detail": detail or None,
        "error_message": (precheck_error[:500] if retryable else None),
        "materialization_retryable": 1 if retryable else 0,
        "retry_after": retry_after if retryable else None,
    })
    # upsert 对已存在行不改 download_status；抓取硬化必须回写状态与 retry 字段。
    # 但不得把已进入下载/分析管线的母片降级回 metadata_ready（定时重抓会撞上预热）。
    if media_id:
        current = db.get_hotspot_media(int(media_id)) or {}
        cur_dl = str(current.get("download_status") or "")
        cur_proc = str(current.get("processing_status") or "")
        in_pipeline = cur_dl in {"downloaded", "downloading", "pending"} or (
            bool(current.get("asset_id"))
            and cur_proc in {"ready", "processing", "processing_failed", "failed"}
        )
        if in_pipeline:
            if not retryable and int(current.get("materialization_retryable") or 0) == 1:
                db.update_hotspot_media_state(
                    int(media_id),
                    materialization_retryable=0,
                    retry_after=None,
                )
            return created, bool(media_created), int(media_id)
        state = {
            "download_status": download_status,
            "progress_detail": detail or None,
            "error_message": (precheck_error[:500] if retryable else None),
            "materialization_retryable": 1 if retryable else 0,
            "retry_after": retry_after if retryable else None,
        }
        if not retryable:
            state["error_message"] = None
            state["retry_after"] = None
        db.update_hotspot_media_state(int(media_id), **state)
    return created, bool(media_created), media_id


def fetch_youtube_channel_hotspots(
    channels: list[dict] | None = None,
    *,
    runner: Callable = subprocess.run,
    limit: int | None = None,
    precheck: bool = True,
) -> dict:
    channels = list(channels or configured_channels())
    requested_limit = CHANNEL_VIDEO_LIMIT if limit is None else limit
    default_limit = max(1, min(int(requested_limit), MAX_CHANNEL_VIDEO_LIMIT))
    result = {
        "channels": len(channels),
        "new": 0,
        "updated": 0,
        "media": 0,
        "downloadable": 0,
        "retryable": 0,
        "errors": [],
        "source_health": [],
        "accepted_media_ids": [],
    }
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for channel in channels:
        name = str(channel.get("name") or "YouTube").strip()
        channel_url = str(channel.get("url") or "").strip()
        health_name = f"YouTube · {name}"
        health = {
            "name": health_name, "status": "ok", "items": 0, "downloadable": 0,
            "retryable": 0, "scanned": 0, "error": "",
        }
        evergreen = bool(channel.get("evergreen"))
        scan_cap = int(channel.get("playlist_scan_cap") or default_limit) if evergreen else default_limit
        scan_cap = max(1, min(scan_cap, MAX_CHANNEL_VIDEO_LIMIT))
        target_downloadable = int(channel.get("min_downloadable") or scan_cap) if evergreen else default_limit
        target_downloadable = max(1, min(target_downloadable, scan_cap))
        try:
            completed = runner(
                _command(channel_url, scan_cap),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "YouTube 频道读取失败").strip()[:300])
            payload = json.loads(completed.stdout or "{}")
            entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
            downloadable_count = 0
            for entry in entries[:scan_cap]:
                health["scanned"] += 1
                video_id = str(entry.get("id") or "").strip()
                title = str(entry.get("title") or "").strip()
                if not video_id or not title:
                    continue
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                ok, probe_error = (True, "")
                if precheck:
                    ok, probe_error = probe_video_downloadable(video_url, runner=runner)
                if ok:
                    created, media_created, media_id = _upsert_channel_video(
                        name=name, entry=entry, retrieved_at=retrieved_at,
                        download_status="metadata_ready",
                    )
                    result["new" if created else "updated"] += 1
                    result["media"] += int(media_created)
                    result["downloadable"] += 1
                    health["items"] += 1
                    health["downloadable"] += 1
                    downloadable_count += 1
                    if media_id:
                        result["accepted_media_ids"].append(int(media_id))
                    if downloadable_count >= target_downloadable:
                        break
                    continue
                # 不可下载：不进可物料化母片池，记 retryable 延后重试（突发新闻台同样适用）。
                if is_not_available_error(probe_error) or precheck:
                    created, media_created, _media_id = _upsert_channel_video(
                        name=name, entry=entry, retrieved_at=retrieved_at,
                        download_status="materialization_retryable",
                        retryable=True,
                        retry_after=retry_after_iso(),
                        precheck_error=probe_error or "yt-dlp -F unavailable",
                    )
                    result["new" if created else "updated"] += 1
                    result["media"] += int(media_created)
                    result["retryable"] += 1
                    health["retryable"] += 1
                    # evergreen：跳过继续往深处取；非 evergreen：仍计入扫描但不算 downloadable
                    continue
            if evergreen and downloadable_count < target_downloadable:
                health["error"] = (
                    f"常青频道仅取得 {downloadable_count}/{target_downloadable} 条可物料化"
                    f"（扫描 {health['scanned']}，retryable {health['retryable']}）"
                )
        except Exception as exc:
            error = str(exc)[:300]
            health.update({"status": "error", "error": error})
            result["errors"].append({"feed": health_name, "error": error})
        result["source_health"].append(health)
    return result
