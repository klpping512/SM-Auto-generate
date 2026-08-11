"""Multi-frame visual critic for hotspot Hook candidates.

Extracts three real frames from the source video, asks ``hook_visual_critic``
what is visible, and never receives mother titles / hotspot summaries as hints.
Failures (missing frames, timeout, empty/illegal JSON) reject the candidate —
they never soft-confirm.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import model_router
from video_quality.frame_extractor import extract_at_timestamps

VISUAL_AUDIT_PROMPT_VERSION = "hotspot-hook-visual-audit-v1"
SCENE_TYPES = {"port", "border", "road", "warehouse", "delivery", "other", "non_event"}
ROLE = "hook_visual_critic"


def compute_frame_offsets_ms(start_ms: int, end_ms: int) -> list[int]:
    """Return three distinct absolute timestamps inside [start_ms, end_ms)."""
    start = max(0, int(start_ms))
    end = max(start, int(end_ms))
    duration = end - start
    if duration <= 0:
        return []
    if duration == 1:
        return [start, start, start]
    margin = min(400, max(0, duration // 5))
    candidates = [
        start + margin,
        start + duration // 2,
        max(start, end - margin - (1 if margin == 0 else 0)),
    ]
    unique: list[int] = []
    for point in candidates:
        clamped = min(max(start, point), end - 1)
        if clamped not in unique:
            unique.append(clamped)
    # Short clips may collapse margins; spread evenly to keep three points.
    if len(unique) < 3 and duration >= 3:
        unique = [start, start + duration // 2, end - 1]
        deduped: list[int] = []
        for point in unique:
            if point not in deduped:
                deduped.append(point)
        unique = deduped
    while len(unique) < 3:
        unique.append(unique[-1] if unique else start)
    return unique[:3]


def resolve_source_video(
    static_root: Path | str | None,
    source_video_path: Path | str | None = None,
    asset_filepath: str | None = None,
) -> Path | None:
    """Resolve a readable mother video path without guessing the CWD."""
    if source_video_path:
        path = Path(source_video_path)
        return path if path.is_file() else None
    if not static_root or not asset_filepath:
        return None
    path = Path(static_root) / str(asset_filepath)
    return path if path.is_file() else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_frames(video_path: Path, offsets_ms: list[int], work_dir: Path) -> list[dict]:
    timestamps = [max(0.0, ms / 1000.0) for ms in offsets_ms]
    extracted = extract_at_timestamps(
        video_path, work_dir, timestamps, resolution=512, max_frames=3, timeout=90,
    )
    by_ts = {
        round(float(item.get("timestamp_seconds") or 0), 3): item
        for item in extracted
        if item.get("path") and Path(item["path"]).is_file()
    }
    frames: list[dict] = []
    for offset_ms, ts in zip(offsets_ms, timestamps):
        item = by_ts.get(round(ts, 3))
        if item is None:
            if not by_ts:
                raise RuntimeError("visual frame extract failed: no decodable frame")
            nearest_key = min(by_ts.keys(), key=lambda value: abs(value - ts))
            item = by_ts.pop(nearest_key)
        else:
            by_ts.pop(round(ts, 3), None)
        path = Path(item["path"])
        if path.stat().st_size < 32:
            raise RuntimeError("visual frame extract failed: corrupt frame")
        frames.append({
            "offset_ms": int(offset_ms),
            "path": path,
            "sha256": _sha256_file(path),
        })
    if len(frames) != 3:
        raise RuntimeError("visual frame extract failed: need three frames")
    return frames


def _visual_prompt(start_ms: int, end_ms: int) -> str:
    return (
        "你是短视频画面核验模型。只根据给出的三帧真实画面判断，不要猜测镜头外事实。"
        "不得参考任何母片标题、新闻摘要或策划文案（本请求也不会提供这些信息）。"
        f"候选时间窗：{start_ms}-{end_ms} 毫秒。"
        "描述你实际看到的对象、动作、场景类型，并判断是否为标题页/Logo/主播/演播室/地图/信息图/空镜。"
        "严格返回单行 JSON："
        '{"accepted":true,"scene_type":"port|border|road|warehouse|delivery|other|non_event",'
        '"visible_objects":["仅列实际可见对象"],"visible_actions":["仅列实际可见动作"],'
        '"is_title_or_logo_card":false,"is_anchor_or_studio":false,"is_map_or_infographic":false,'
        '"supports_visible_event":true,"reason":"不超过80字"}'
    )


def _parse_visual_payload(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        start = next((i for i, ch in enumerate(text) if ch in "{["), -1)
        if start < 0:
            raise ValueError("视觉审核未返回合法 JSON")
        opening = text[start]
        closing = "}" if opening == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        end = -1
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end < 0:
            raise ValueError("视觉审核未返回合法 JSON")
        try:
            payload = json.loads(text[start : end + 1])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("视觉审核未返回合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("视觉审核返回了不支持的 JSON 顶层类型")
    return payload


def _decision_from_payload(payload: dict) -> tuple[bool, dict]:
    scene_type = str(payload.get("scene_type") or "non_event").strip().lower()
    if scene_type not in SCENE_TYPES:
        scene_type = "non_event"
    objects = [str(item).strip()[:40] for item in (payload.get("visible_objects") or []) if str(item).strip()][:12]
    actions = [str(item).strip()[:40] for item in (payload.get("visible_actions") or []) if str(item).strip()][:12]
    flags = {
        "accepted": bool(payload.get("accepted") is True),
        "supports_visible_event": bool(payload.get("supports_visible_event") is True),
        "is_title_or_logo_card": bool(payload.get("is_title_or_logo_card") is True),
        "is_anchor_or_studio": bool(payload.get("is_anchor_or_studio") is True),
        "is_map_or_infographic": bool(payload.get("is_map_or_infographic") is True),
        "scene_type": scene_type,
        "visible_objects": objects,
        "visible_actions": actions,
        "reason": str(payload.get("reason") or "").strip()[:80],
    }
    ok = (
        flags["accepted"]
        and flags["supports_visible_event"]
        and not flags["is_title_or_logo_card"]
        and not flags["is_anchor_or_studio"]
        and not flags["is_map_or_infographic"]
        and flags["scene_type"] != "non_event"
        and bool(objects or actions)
    )
    return ok, flags


def _visual_job_id(asset_id: int, hook: dict, frame_sha: list[str]) -> str:
    payload = {
        "prompt_version": VISUAL_AUDIT_PROMPT_VERSION,
        "asset_id": int(asset_id),
        "event_index": hook.get("event_index"),
        "start_ms": hook.get("start_ms"),
        "end_ms": hook.get("end_ms"),
        "frame_sha256": frame_sha,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"hotspot-hook-visual-{int(asset_id)}-{digest}"


def _reject_evidence(status: str, *, reason: str, offsets: list[int] | None = None) -> dict:
    model_name = None
    try:
        if ROLE in model_router.ROLES:
            model_name = (model_router.get_route(ROLE) or {}).get("model")
    except Exception:
        model_name = None
    return {
        "status": status,
        "prompt_version": VISUAL_AUDIT_PROMPT_VERSION,
        "scene_type": "non_event",
        "frame_offsets_ms": list(offsets or []),
        "frame_sha256": [],
        "visible_objects": [],
        "visible_actions": [],
        "model": model_name,
        "cache_hit": False,
        "reason": reason[:120],
    }


def audit_single_hook(
    asset_id: int,
    hook: dict,
    video_path: Path,
) -> tuple[bool, dict]:
    """Audit one candidate with three real frames. Returns (accepted, evidence)."""
    offsets = compute_frame_offsets_ms(int(hook["start_ms"]), int(hook["end_ms"]))
    if len(offsets) < 3:
        return False, _reject_evidence("rejected", reason="候选时长无法提取三帧", offsets=offsets)
    work_dir = Path(tempfile.mkdtemp(prefix="hook-visual-audit-"))
    try:
        try:
            frames = _extract_frames(video_path, offsets, work_dir)
        except Exception as exc:
            return False, _reject_evidence(
                "rejected", reason=f"帧提取失败：{str(exc)[:80]}", offsets=offsets,
            )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": _visual_prompt(int(hook["start_ms"]), int(hook["end_ms"]))},
        ]
        for index, frame in enumerate(frames, start=1):
            encoded = base64.b64encode(frame["path"].read_bytes()).decode("ascii")
            content.append({"type": "text", "text": f"帧{index} @ {frame['offset_ms']}ms"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            })
        job_id = model_router.route_scoped_job_id(
            _visual_job_id(asset_id, hook, [frame["sha256"] for frame in frames]),
            ROLE,
        )
        model_router.create_budget(
            job_id,
            max_calls=1,
            max_input_tokens=40_000,
            max_output_tokens=model_router.required_output_budget(ROLE, 900),
            reset=True,
        )
        try:
            result = asyncio.run(model_router.call_multimodal_json(
                job_id,
                ROLE,
                [
                    {"role": "system", "content": "严格返回 JSON。只描述可见画面，不得臆造地点或事件身份。"},
                    {"role": "user", "content": content},
                ],
                prompt_version=VISUAL_AUDIT_PROMPT_VERSION,
                max_attempts=2,
            ))
            raw = str(result.get("content") or "").strip()
            if not raw:
                return False, _reject_evidence(
                    "rejected", reason="视觉审核返回空内容", offsets=offsets,
                )
            payload = _parse_visual_payload(raw)
            accepted, flags = _decision_from_payload(payload)
        except Exception as exc:
            return False, _reject_evidence(
                "rejected", reason=f"视觉审核失败：{str(exc)[:80]}", offsets=offsets,
            )
        evidence = {
            "status": "accepted" if accepted else "rejected",
            "prompt_version": VISUAL_AUDIT_PROMPT_VERSION,
            "scene_type": flags["scene_type"],
            "frame_offsets_ms": [frame["offset_ms"] for frame in frames],
            "frame_sha256": [frame["sha256"] for frame in frames],
            "visible_objects": flags["visible_objects"],
            "visible_actions": flags["visible_actions"],
            "model": model_router.get_route(ROLE).get("model"),
            "cache_hit": bool(result.get("cache_hit")),
            "reason": flags["reason"],
            "is_title_or_logo_card": flags["is_title_or_logo_card"],
            "is_anchor_or_studio": flags["is_anchor_or_studio"],
            "is_map_or_infographic": flags["is_map_or_infographic"],
            "supports_visible_event": flags["supports_visible_event"],
        }
        return accepted, evidence
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def audit_hooks(
    asset_id: int,
    hooks: list[dict],
    *,
    static_root: Path | str | None = None,
    source_video_path: Path | str | None = None,
    asset_filepath: str | None = None,
) -> tuple[list[dict], dict]:
    """Filter candidates through multi-frame visual critic."""
    if not hooks:
        return [], {"status": "nothing_to_audit", "accepted_count": 0}
    video = resolve_source_video(static_root, source_video_path, asset_filepath)
    if video is None:
        for hook in hooks:
            evidence = dict(hook.get("evidence") or {})
            evidence["visual_audit"] = _reject_evidence(
                "rejected", reason="缺少可读母片路径，无法做视觉审核",
                offsets=compute_frame_offsets_ms(int(hook["start_ms"]), int(hook["end_ms"])),
            )
            hook["evidence"] = evidence
            hook["review_status"] = "review_required"
        return [], {
            "status": "rejected_all",
            "accepted_count": 0,
            "reason": "missing_source_video",
        }
    if not model_router.key_is_available(ROLE):
        return [], {
            "status": "model_unavailable",
            "accepted_count": 0,
            "reason": "hook_visual_critic 未配置",
        }
    accepted_hooks: list[dict] = []
    for hook in hooks:
        ok, visual = audit_single_hook(int(asset_id), hook, video)
        evidence = dict(hook.get("evidence") or {})
        evidence["visual_audit"] = visual
        hook["evidence"] = evidence
        if ok:
            accepted_hooks.append(hook)
        else:
            hook["review_status"] = "review_required"
    return accepted_hooks, {
        "status": "verified" if accepted_hooks else "rejected_all",
        "accepted_count": len(accepted_hooks),
        "model": model_router.get_route(ROLE).get("model"),
    }
