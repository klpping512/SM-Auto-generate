"""Canonical video project/job/file state, independent of UI copy."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

COPY_SOURCES = {"model", "model_repair", "policy_repair", "fallback", "corpus"}
_ACTIVE_VIDEO_JOB_STATUSES = ("pending", "running", "cancel_requested")
_GENERATING_VIDEO_JOB_STATUSES = ("pending", "running", "cancel_requested")
_ENDCARD_ROLES = {"brand_endcard", "brand_close", "cta"}
_ENDCARD_TYPES = {"brand_endcard"}
DEFAULT_BRAND_ENDCARD_PATH = "uploads/brand-endcards/buffalo-cape-town-van.png"
TEXT_CARD_SOURCES = {"text_card_fallback", "diversity_text_card"}

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

SCENE_ROLE_FALLBACKS = {
    "opening": (
        "先把这条物流变化说清楚，再看现场怎么承接。",
        "这不是抽象趋势，而是马上会碰到的履约节点。",
        "先看冲突从哪来，再看仓库和干线怎么接住。",
    ),
    "contrast": (
        "同样一批货，节点一变，等待成本和交付窗口就会分开。",
        "表面看是延误，真正差的是分拣、装车和清关有没有对齐。",
        "一边是可见拥堵，一边是可执行的分流和改约。",
    ),
    "risk": (
        "如果现场继续空等，货值、时效和客诉会一起上来。",
        "风险不在标题，而在下一班车和下一票清关有没有预案。",
        "一旦卡在同一入口，后段仓储和派送都会被拖住。",
    ),
    "action": (
        "Buffalo 这边按区域核对、分拣并改走可执行路由。",
        "仓内人员按票核对包裹，同步记录处理结果。",
        "现场把货物从拥堵口挪到可作业的分拣和发运节点。",
    ),
    "cta": (
        "需要南非仓配承接时，把货值和时效交给 Buffalo 处理。",
        "把下一票分流、清关和入仓交给看得见的作业现场。",
        "要落地而不是空等，就按现有仓配动作接着做。",
    ),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_sql() -> str:
    return utc_now().strftime("%Y-%m-%d %H:%M:%S")


def resolve_media_path(path: str | None) -> Path | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    if raw.startswith("/static/"):
        candidate = STATIC_DIR / raw[len("/static/"):]
        if candidate.is_file():
            return candidate
    nested = STATIC_DIR / raw.lstrip("/")
    if nested.is_file():
        return nested
    rooted = ROOT / raw.lstrip("/")
    if rooted.is_file():
        return rooted
    return candidate if candidate.exists() else None


def probe_video_artifact(path: str | None) -> dict:
    """Return existence, readability and duration for a candidate MP4."""
    result = {
        "path": str(path or "").strip(),
        "exists": False,
        "readable": False,
        "size": 0,
        "duration_ms": 0,
        "ok": False,
        "probe": "missing",
    }
    resolved = resolve_media_path(path)
    if resolved is None:
        return result
    result["path"] = str(resolved)
    if not resolved.is_file():
        return result
    result["exists"] = True
    try:
        size = int(resolved.stat().st_size)
    except OSError:
        return result
    result["size"] = size
    result["readable"] = os.access(resolved, os.R_OK) and size > 0
    if not result["readable"]:
        result["probe"] = "unreadable"
        return result
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Unit tests and hosts without FFmpeg still need a file-backed gate.
        result["duration_ms"] = 1000 if size > 0 else 0
        result["ok"] = result["duration_ms"] > 0
        result["probe"] = "stat"
        return result
    try:
        completed = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(resolved),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
        )
        duration = float((completed.stdout or b"").decode("utf-8", "ignore").strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        duration = 0.0
    result["duration_ms"] = int(duration * 1000) if duration > 0 else 0
    result["ok"] = result["readable"] and result["duration_ms"] > 0
    result["probe"] = "ffprobe"
    return result


def derive_artifact_status(job: dict | None) -> str:
    if not isinstance(job, dict):
        return "absent"
    output = probe_video_artifact(job.get("output_path"))
    if output["ok"]:
        return "final"
    preview = probe_video_artifact(job.get("preview_path"))
    if preview["ok"]:
        return "preview"
    return "absent"


def derive_quality_status(job: dict | None) -> str:
    if not isinstance(job, dict):
        return "unknown"
    report = job.get("quality_report") if isinstance(job.get("quality_report"), dict) else {}
    publication = report.get("publication") if isinstance(report.get("publication"), dict) else {}
    tier = str(publication.get("tier") or "").strip()
    if publication.get("publish_allowed") is False or tier in {"quality_hold", "internal_preview"}:
        return "hold"
    if str(job.get("status") or "") == "succeeded" and publication.get("publish_allowed") is True:
        return "passed"
    if str(job.get("status") or "") == "succeeded" and derive_artifact_status(job) == "final":
        if publication.get("publish_allowed") is False:
            return "hold"
        if tier == "quality_hold":
            return "hold"
        return "passed" if publication.get("publish_allowed") is not False else "hold"
    return "unknown"


def map_queue_status(status: str | None) -> str:
    value = str(status or "").strip()
    if value == "published":
        return "published"
    if value in {"failed", "publish_failed"}:
        return "publish_failed"
    if value == "pending_review":
        return "pending_review"
    if value in {"queued", "approved"}:
        return "queued"
    return "not_queued"


def project_status_for_job(job: dict | None, *, artifact_status: str | None = None) -> str:
    if not job:
        return "draft"
    status = str(job.get("status") or "").strip()
    artifact = artifact_status or derive_artifact_status(job)
    if status in _GENERATING_VIDEO_JOB_STATUSES:
        return "generating"
    if status == "needs_review":
        return "needs_review"
    if status == "succeeded":
        return "ready" if artifact == "final" else "draft"
    if status == "failed":
        return "failed"
    if status == "canceled":
        return "canceled"
    return "draft"


def result_label(job: dict | None) -> str:
    """User-visible pipeline result. quality_hold is never shown as succeeded."""
    if not job:
        return "draft"
    status = str(job.get("status") or "").strip()
    if status in {"failed", "canceled", "pending", "running", "needs_review", "cancel_requested"}:
        return status
    if status == "succeeded":
        if derive_artifact_status(job) != "final":
            return "failed"
        if derive_quality_status(job) == "hold":
            return "quality_hold"
        return "succeeded"
    return status or "draft"


def scene_asset_token(scene: dict | None) -> str:
    scene = scene or {}
    role = str(scene.get("scene_role") or "")
    evidence = str(scene.get("evidence_type") or "")
    source = str(scene.get("asset_source") or "")
    if source in TEXT_CARD_SOURCES:
        return f"textcard:{source}:{scene.get('scene') or ''}:{scene.get('duration_ms') or 0}"
    if role in _ENDCARD_ROLES or evidence in _ENDCARD_TYPES:
        return ""
    asset_id = scene.get("asset_id")
    segment_id = scene.get("asset_segment_id") or scene.get("event_clip_id") or ""
    start_ms = scene.get("asset_start_ms")
    end_ms = scene.get("asset_end_ms")
    return f"{evidence}:{asset_id}:{segment_id}:{start_ms}:{end_ms}"


def scene_asset_signature(scenes: list[dict] | None) -> str:
    tokens = [scene_asset_token(scene) for scene in (scenes or [])]
    tokens = [token for token in tokens if token]
    raw = "|".join(tokens)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def normalize_copy_source(value: object) -> str:
    source = str(value or "").strip() or "fallback"
    if source == "repair":
        return "model_repair"
    if source not in COPY_SOURCES:
        return "fallback"
    return source


def fallback_voiceover_for_role(scene: dict, seed: str, index: int) -> str:
    role = str(scene.get("scene_role") or scene.get("evidence_type") or "")
    family = "cta" if role in _ENDCARD_ROLES or "cta" in role or "brand" in role else (
        "opening" if index <= 1 or "hook" in role or "open" in role else (
            "risk" if any(token in role for token in ("risk", "warn", "delay")) else (
                "action" if any(token in role for token in ("action", "owned", "proof", "ops")) else "contrast"
            )
        )
    )
    options = SCENE_ROLE_FALLBACKS[family]
    digest = hashlib.sha256(f"{seed}|{family}|{index}".encode("utf-8")).hexdigest()
    line = options[int(digest[:8], 16) % len(options)]
    return line if line.endswith("。") else f"{line}。"


def repair_incomplete_scenes(script: dict, *, seed: str = "") -> tuple[dict, list[str]]:
    """Fix incomplete scene sentences locally; never discard the whole script."""
    import video_topic_contract

    repaired = {**script, "scenes": [dict(item) for item in script.get("scenes") or []]}
    notes: list[str] = []
    issues = video_topic_contract.incomplete_sentence_issues(repaired)
    if not issues:
        return repaired, notes
    broken_indexes = set()
    for issue in issues:
        text = str(issue)
        if text.startswith("第") and "镜" in text:
            try:
                broken_indexes.add(int(text[1:text.index("镜")]))
            except ValueError:
                continue
    for index in sorted(broken_indexes):
        if index < 1 or index > len(repaired["scenes"]):
            continue
        scene = repaired["scenes"][index - 1]
        original = str(scene.get("voiceover") or "").strip()
        replacement = fallback_voiceover_for_role(scene, seed or str(script.get("title") or ""), index)
        scene["voiceover"] = replacement
        scene["onscreen_text"] = scene.get("onscreen_text") or replacement.rstrip("。")[:24]
        scene["copy_source"] = "fallback"
        scene["repair_reason"] = f"第{index}镜句子校验失败，已替换为确定性镜头模板"
        notes.append(scene["repair_reason"])
        if original:
            scene["replaced_voiceover"] = original
    remaining = video_topic_contract.incomplete_sentence_issues(repaired)
    for issue in remaining:
        notes.append(str(issue))
    return repaired, notes


def enrich_job(job: dict | None, *, publication_status: str = "not_queued") -> dict | None:
    if not job:
        return None
    item = dict(job)
    item["artifact_status"] = derive_artifact_status(item)
    item["quality_status"] = derive_quality_status(item)
    item["publication_status"] = publication_status
    item["result"] = result_label(item)
    output = probe_video_artifact(item.get("output_path"))
    preview = probe_video_artifact(item.get("preview_path"))
    item["artifact_probe"] = {"output": output, "preview": preview}
    item["publish_allowed"] = (
        item["result"] == "succeeded"
        and item["quality_status"] == "passed"
        and item["artifact_status"] == "final"
    )
    return item


def enrich_project(project: dict | None, job: dict | None = None, *, publication_status: str = "not_queued") -> dict | None:
    if not project:
        return None
    item = dict(project)
    enriched_job = enrich_job(job, publication_status=publication_status) if job else None
    artifact = enriched_job["artifact_status"] if enriched_job else "absent"
    quality = enriched_job["quality_status"] if enriched_job else "unknown"
    item["artifact_status"] = artifact
    item["quality_status"] = quality
    item["publication_status"] = publication_status
    if enriched_job:
        item["result"] = enriched_job["result"]
        item["publish_allowed"] = enriched_job["publish_allowed"]
        if item.get("status") == "ready" and artifact != "final":
            item["status"] = "draft"
    else:
        if item.get("status") == "ready" and artifact != "final":
            item["status"] = "draft"
        item["result"] = item.get("status") or "draft"
        item["publish_allowed"] = False
    return item


def build_diagnostics(project: dict | None, job: dict | None = None) -> dict:
    job = enrich_job(job) if job else None
    project = enrich_project(project, job) if project else None
    report = (job or {}).get("quality_report") if isinstance((job or {}).get("quality_report"), dict) else {}
    script = report.get("script") if isinstance(report.get("script"), dict) else {}
    scenes = script.get("scenes") if isinstance(script.get("scenes"), list) else []
    gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
    publication = report.get("publication") if isinstance(report.get("publication"), dict) else {}
    stuck = str((job or {}).get("stage") or "queued")
    if job and job.get("status") in {"pending", "running", "cancel_requested"}:
        why = f"当前停在 {stuck}"
    elif not job:
        why = "还没有生成任务"
    elif (job or {}).get("result") == "failed":
        why = str(job.get("error_message") or gate.get("issues") or "生成失败")
    elif (job or {}).get("artifact_status") != "final":
        why = "任务结束但没有可读取的 MP4"
    else:
        why = "成片已生成"
    return {
        "project_id": (project or {}).get("id"),
        "job_id": (job or {}).get("id"),
        "revision_id": (job or {}).get("revision_id") or (project or {}).get("current_revision_id"),
        "stuck_stage": stuck,
        "job_status": (job or {}).get("status"),
        "result": (job or {}).get("result") or (project or {}).get("result"),
        "why_no_output": why,
        "artifact_status": (job or {}).get("artifact_status") or "absent",
        "quality_status": (job or {}).get("quality_status") or "unknown",
        "publication_status": (job or {}).get("publication_status") or "not_queued",
        "file_exists": bool(((job or {}).get("artifact_probe") or {}).get("output", {}).get("exists")),
        "file_ok": bool(((job or {}).get("artifact_probe") or {}).get("output", {}).get("ok")),
        "copy_provenance": report.get("copy_provenance") or [],
        "asset_report": report.get("asset_diversity") or report.get("match_scenes") or [],
        "quality_blockers": list(gate.get("issues") or publication.get("semantic_issues") or []),
        "error_code": (job or {}).get("error_code"),
        "error_message": (job or {}).get("error_message"),
    }


def dump_json(value: dict | list | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False)
