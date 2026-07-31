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
    {"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"},
    {"name": "South Africa Now", "url": "https://www.youtube.com/@SouthAfricaNow1"},
    {"name": "SABC Digital News", "url": "https://www.youtube.com/@sabcdigitalnews"},
]
DEFAULT_CHANNEL_VIDEO_LIMIT = 3
# 常规三天任务只读每个已授权频道最近 3 条，避免元数据轮询膨胀；
# 管理员批量验收可通过环境变量扩展至 12 条，以便模型有足够候选找到不同物流题材。
try:
    CHANNEL_VIDEO_LIMIT = max(1, min(12, int(os.environ.get("HOTSPOT_YOUTUBE_CHANNEL_VIDEO_LIMIT", DEFAULT_CHANNEL_VIDEO_LIMIT))))
except ValueError:
    CHANNEL_VIDEO_LIMIT = DEFAULT_CHANNEL_VIDEO_LIMIT
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
    proxy = str(os.environ.get("SA_YOUTUBE_PROXY") or "").strip()
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
    proxy = str(os.environ.get("SA_YOUTUBE_PROXY") or "").strip()
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
    """Attach public video facts before the internal model decides what to download.

    This deliberately runs before `select_for_hook_ingestion`: the model receives
    actual source-provided title/description, never guessed visual content.
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


def fetch_youtube_channel_hotspots(
    channels: list[dict] | None = None,
    *,
    runner: Callable = subprocess.run,
    limit: int | None = None,
) -> dict:
    channels = list(channels or DEFAULT_YOUTUBE_CHANNELS)
    requested_limit = CHANNEL_VIDEO_LIMIT if limit is None else limit
    effective_limit = max(1, min(int(requested_limit), 12))
    result = {
        "channels": len(channels),
        "new": 0,
        "updated": 0,
        "media": 0,
        "errors": [],
        "source_health": [],
    }
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for channel in channels:
        name = str(channel.get("name") or "YouTube").strip()
        channel_url = str(channel.get("url") or "").strip()
        health_name = f"YouTube · {name}"
        health = {"name": health_name, "status": "ok", "items": 0, "error": ""}
        try:
            completed = runner(
                _command(channel_url, effective_limit),
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "YouTube 频道读取失败").strip()[:300])
            payload = json.loads(completed.stdout or "{}")
            entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
            for entry in entries[:effective_limit]:
                video_id = str(entry.get("id") or "").strip()
                title = str(entry.get("title") or "").strip()
                if not video_id or not title:
                    continue
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
                except Exception as signal_exc:
                    result["errors"].append({
                        "feed": health_name,
                        "error": f"热点信号保存失败: {str(signal_exc)[:240]}",
                    })
                authorization_status, rights_note = _configured_source_authorization()
                _, media_created = db.upsert_hotspot_media({
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
                    "download_status": "metadata_ready",
                    "processing_status": "not_started",
                })
                result["new" if created else "updated"] += 1
                result["media"] += int(media_created)
                health["items"] += 1
        except Exception as exc:
            error = str(exc)[:300]
            health.update({"status": "error", "error": error})
            result["errors"].append({"feed": health_name, "error": error})
        result["source_health"].append(health)
    return result
