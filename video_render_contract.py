"""Renderable scene contract: video, image, text_card, brand_endcard.

Matching may degrade a beat to a text card. That must become a real renderable
object, not an empty asset_id plus a borrowed brand-endcard path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import video_state

RENDER_KINDS = ("video", "image", "text_card", "brand_endcard")
CTA_ROLES = {"brand_cta", "brand_endcard", "brand_close", "cta"}


class RenderContractError(ValueError):
    """A scene is not renderable and could not be repaired."""


def infer_render_kind(scene: dict | None) -> str:
    scene = scene or {}
    explicit = str(scene.get("render_kind") or "").strip()
    if explicit in RENDER_KINDS:
        return explicit
    source = str(scene.get("asset_source") or "")
    role = str(scene.get("scene_role") or "")
    evidence = str(scene.get("evidence_type") or "")
    if source in video_state.TEXT_CARD_SOURCES or evidence == "text_card":
        return "text_card"
    if role in CTA_ROLES or (evidence == "brand_endcard" and source not in video_state.TEXT_CARD_SOURCES):
        if source in video_state.TEXT_CARD_SOURCES:
            return "text_card"
        return "brand_endcard"
    if evidence == "image" or source in {"owned_image_fallback", "type_rotation"}:
        return "image"
    try:
        if int(scene.get("asset_id") or 0) > 0 or int(scene.get("event_clip_id") or 0) > 0:
            return "video" if evidence != "image" else "image"
    except (TypeError, ValueError):
        pass
    return "video"


def plan_hash(scenes: list[dict] | None) -> str:
    payload = []
    for scene in scenes or []:
        payload.append({
            "kind": infer_render_kind(scene),
            "asset_id": scene.get("asset_id"),
            "segment": scene.get("asset_segment_id") or scene.get("event_clip_id"),
            "text": ((scene.get("text_card") or {}) if isinstance(scene.get("text_card"), dict) else {}).get("text")
            or scene.get("voiceover") or "",
        })
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def materialize_text_card(scene: dict, *, reason: str, index: int = 0) -> dict:
    """Turn a hollow beat into an explicit text_card render object."""
    voiceover = str(scene.get("voiceover") or "").strip() or "把当前物流节点核对清楚。"
    overlay = str(scene.get("text_overlay") or "").strip() or voiceover
    try:
        duration_ms = int(scene.get("duration_ms") or round(float(scene.get("duration") or 3) * 1000))
    except (TypeError, ValueError):
        duration_ms = 3000
    duration_ms = max(1500, min(8000, duration_ms))
    source = str(scene.get("asset_source") or "")
    if source not in video_state.TEXT_CARD_SOURCES:
        source = "text_card_fallback"
    scene.update({
        "render_kind": "text_card",
        "evidence_type": "text_card",
        "asset_id": None,
        "asset_segment_id": None,
        "event_clip_id": None,
        "brand_endcard_path": "",
        "brand_endcard_fallback": False,
        "asset_source": source,
        "text_card": {
            "text": overlay[:80],
            "style": "owned_topic_card",
            "background": "buffalo_dark",
            "duration_ms": duration_ms,
        },
        "match_score": scene.get("match_score") or 40,
        "match_reasons": [reason],
        "renderable": True,
    })
    return {
        "scene": index + 1,
        "score": 40,
        "hard_failures": [],
        "issues": [reason],
        "library_origin": "text_card",
        "asset_id": None,
        "usage_count": 0,
        "cooldown": False,
    }


def scene_is_renderable(scene: dict, *, static_dir: Path | None = None) -> bool:
    kind = infer_render_kind(scene)
    if kind == "text_card":
        card = scene.get("text_card") if isinstance(scene.get("text_card"), dict) else {}
        return bool(str(card.get("text") or scene.get("voiceover") or "").strip())
    if kind == "brand_endcard":
        relative = str(scene.get("brand_endcard_path") or video_state.DEFAULT_BRAND_ENDCARD_PATH).strip()
        if not relative:
            return False
        if static_dir is None:
            return True
        candidate = (static_dir / relative).resolve()
        return candidate.is_file() and candidate.is_relative_to(static_dir.resolve())
    try:
        asset_id = int(scene.get("asset_id") or 0)
    except (TypeError, ValueError):
        asset_id = 0
    try:
        event_id = int(scene.get("event_clip_id") or 0)
    except (TypeError, ValueError):
        event_id = 0
    return asset_id > 0 or event_id > 0


def repair_scene_render_sources(scenes: list[dict]) -> int:
    """Guarantee every beat has a render_kind the renderer can execute."""
    repaired = 0
    for index, scene in enumerate(scenes):
        kind = infer_render_kind(scene)
        scene["render_kind"] = kind
        if kind == "text_card":
            if not scene_is_renderable(scene):
                materialize_text_card(scene, reason="文字卡缺少正文，已用旁白补齐", index=index)
                repaired += 1
            else:
                scene["renderable"] = True
            continue
        if kind == "brand_endcard":
            if not str(scene.get("brand_endcard_path") or "").strip():
                scene["brand_endcard_path"] = video_state.DEFAULT_BRAND_ENDCARD_PATH
                repaired += 1
            scene["renderable"] = True
            continue
        if scene_is_renderable(scene):
            scene["renderable"] = True
            continue
        materialize_text_card(scene, reason="分镜缺少可渲染来源，已转为文字卡", index=index)
        repaired += 1
    return repaired


def validate_render_contract(scenes: list[dict], *, static_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not scenes:
        return ["分镜列表为空"]
    for index, scene in enumerate(scenes, 1):
        kind = infer_render_kind(scene)
        if kind not in RENDER_KINDS:
            errors.append(f"第{index}镜 render_kind 非法：{kind}")
            continue
        try:
            duration = float(scene.get("duration_ms") or scene.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0 and not (scene.get("text_card") or {}).get("duration_ms"):
            errors.append(f"第{index}镜时长无效")
        if kind in {"video", "image"}:
            try:
                asset_id = int(scene.get("asset_id") or 0)
            except (TypeError, ValueError):
                asset_id = 0
            try:
                event_id = int(scene.get("event_clip_id") or 0)
            except (TypeError, ValueError):
                event_id = 0
            if asset_id <= 0 and event_id <= 0:
                errors.append(f"第{index}镜 {kind} 缺少有效素材引用")
        elif kind == "text_card":
            card = scene.get("text_card") if isinstance(scene.get("text_card"), dict) else {}
            if not str(card.get("text") or scene.get("voiceover") or "").strip():
                errors.append(f"第{index}镜文字卡没有正文")
        elif kind == "brand_endcard":
            if not scene_is_renderable(scene, static_dir=static_dir):
                errors.append(f"第{index}镜品牌结尾图无效")
        if scene.get("renderable") is False:
            errors.append(f"第{index}镜 renderable=false")
    return errors


def plan_render_capacity(
    *,
    video_count: int,
    image_count: int,
    brand_endcard_count: int = 0,
    min_scenes: int = 8,
    max_scenes: int = 10,
) -> dict:
    """Count available sources first, then decide how many beats of each kind."""
    video_count = max(0, int(video_count or 0))
    image_count = max(0, int(image_count or 0))
    brand_endcard_count = 1 if brand_endcard_count else 0
    available_content = video_count + image_count
    content = min(max_scenes, max(min_scenes, available_content if available_content else min_scenes))
    video = min(video_count, content)
    image = min(image_count, max(0, content - video))
    text_card = max(0, content - video - image)
    return {
        "scene_count": content + brand_endcard_count,
        "video_count": video,
        "image_count": image,
        "text_card_count": text_card,
        "brand_endcard_count": brand_endcard_count,
    }


def contract_summary(scenes: list[dict] | None) -> dict:
    counts = {kind: 0 for kind in RENDER_KINDS}
    for scene in scenes or []:
        counts[infer_render_kind(scene)] += 1
    return {
        "scene_count": len(scenes or []),
        "video_count": counts["video"],
        "image_count": counts["image"],
        "text_card_count": counts["text_card"],
        "brand_endcard_count": counts["brand_endcard"],
        "plan_hash": plan_hash(scenes),
        "renderable_scene_count": sum(
            1 for scene in (scenes or []) if scene_is_renderable(scene)
        ),
    }
