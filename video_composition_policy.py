"""Deterministic source-usage gates for every generated video timeline.

These rules deliberately live outside a model prompt.  A stronger writing model
can improve a script, but it must never be able to re-introduce a repeated
source or turn a short real clip into a loop.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable


INFOGRAPHIC_EVIDENCE_TYPES = {
    "explanation_card", "map_card", "process_card", "data_card",
    "infographic", "info_card", "chart", "title_card", "slideshow", "presentation",
}
NON_REAL_EVIDENCE_TYPES = INFOGRAPHIC_EVIDENCE_TYPES | {"brand_endcard", "image", "text_card"}


def is_explanation_scene(scene: dict) -> bool:
    """Identify legacy information-card scenes so every stage can reject them.

    The name is retained for compatibility with historical scripts, but these
    scenes are now forbidden rather than rendered as a fallback.
    Real ``text_card`` beats are an explicit render kind, not infographics.
    """
    kind = str(scene.get("render_kind") or "")
    evidence = str(scene.get("evidence_type") or "")
    if kind == "text_card" or evidence == "text_card":
        return False
    return (
        str(scene.get("scene_role") or "") in {
            "logistics_explainer", "explanation", "infographic", "info_card", "presentation",
        }
        or evidence in INFOGRAPHIC_EVIDENCE_TYPES
    )


def is_real_video_scene(scene: dict) -> bool:
    evidence_type = str(scene.get("evidence_type") or "")
    return bool(scene.get("asset_id")) and evidence_type not in NON_REAL_EVIDENCE_TYPES


def scene_voiceover_char_limit(scene: dict) -> int:
    """Approximate a safe narration budget for any locked visual beat."""
    try:
        duration_seconds = float(scene.get("duration_ms") or 0) / 1000
        if duration_seconds <= 0:
            duration_seconds = float(scene.get("duration") or 0)
    except (TypeError, ValueError):
        duration_seconds = 0
    if duration_seconds <= 0:
        return 150
    return max(8, min(52, int(max(0.0, duration_seconds - 0.25) * 5.0)))


def formal_voiceover_char_bounds(scene: dict) -> tuple[int | None, int | None]:
    """Match the formal scripting min/max window used by app.py before TTS."""
    if str(scene.get("render_kind") or "") == "brand_endcard":
        return None, None
    if (
        str(scene.get("evidence_type") or "") == "brand_endcard"
        and str(scene.get("render_kind") or "") != "text_card"
        and str(scene.get("asset_source") or "") not in {"text_card_fallback", "diversity_text_card"}
    ):
        return None, None
    try:
        duration_seconds = max(0.0, float(scene.get("duration_ms") or 0) / 1000)
    except (TypeError, ValueError):
        return None, None
    if not duration_seconds:
        return None, None
    rate = 2.6 if duration_seconds <= 4.5 else 2.8
    minimum = max(8, int(math.ceil(duration_seconds * rate)))
    maximum = max(8, int(duration_seconds * 3.6))
    return minimum, maximum


def _range(scene: dict) -> tuple[int, int] | None:
    try:
        start = int(scene.get("asset_start_ms") or (scene.get("clip_ref") or {}).get("start_ms") or 0)
        end = int(scene.get("asset_end_ms") or (scene.get("clip_ref") or {}).get("end_ms") or 0)
    except (TypeError, ValueError):
        return None
    return (start, end) if end > start else None


def source_usage_report(scenes: Iterable[dict]) -> dict:
    """Return a user-readable hard-gate report for source and time-range reuse.

    Buffalo source video is proof of a visible action, so the same raw video is
    allowed once only.  A hotspot parent can supply two distinct hooks, but their
    ranges must not overlap; a repeated time range is a loop in disguise.
    """
    indexed = [(index + 1, scene) for index, scene in enumerate(scenes) if isinstance(scene, dict)]
    issues: list[str] = []
    segment_scenes: dict[int, list[int]] = defaultdict(list)
    owned_assets: dict[int, list[int]] = defaultdict(list)
    za_stock_assets: dict[int, list[int]] = defaultdict(list)
    image_assets: dict[int, list[int]] = defaultdict(list)
    hotspot_assets: dict[int, list[tuple[int, tuple[int, int] | None]]] = defaultdict(list)
    exact_ranges: dict[tuple[int, int, int], list[int]] = defaultdict(list)

    for scene_number, scene in indexed:
        # 静态图片也是素材，不得在同一条片里反复出现来伪装为动态变化。
        if str(scene.get("evidence_type") or "") == "image":
            try:
                image_asset_id = int(scene.get("asset_id") or 0)
            except (TypeError, ValueError):
                image_asset_id = 0
            if not image_asset_id:
                issues.append(f"第{scene_number}镜静态图片没有素材来源")
            else:
                image_assets[image_asset_id].append(scene_number)
            continue
        if not is_real_video_scene(scene):
            continue
        try:
            asset_id = int(scene.get("asset_id") or 0)
        except (TypeError, ValueError):
            asset_id = 0
        if not asset_id:
            issues.append(f"第{scene_number}镜是真实视频但没有素材来源")
            continue
        try:
            segment_id = int(scene.get("asset_segment_id") or 0)
        except (TypeError, ValueError):
            segment_id = 0
        if segment_id:
            segment_scenes[segment_id].append(scene_number)
        clip_range = _range(scene)
        if clip_range:
            exact_ranges[(asset_id, *clip_range)].append(scene_number)
        if str(scene.get("evidence_type") or "") == "hotspot_video" or scene.get("event_clip_id"):
            hotspot_assets[asset_id].append((scene_number, clip_range))
        elif str(scene.get("asset_source") or "") == "za_stock_license":
            za_stock_assets[asset_id].append(scene_number)
        else:
            owned_assets[asset_id].append(scene_number)

    for segment_id, scene_numbers in segment_scenes.items():
        if len(scene_numbers) > 1:
            issues.append(f"asset_segment_id {segment_id} 被第{'、'.join(map(str, scene_numbers))}镜重复使用")
    for asset_id, scene_numbers in owned_assets.items():
        if len(scene_numbers) > 1:
            issues.append(f"Buffalo 原始视频 {asset_id} 被第{'、'.join(map(str, scene_numbers))}镜重复使用")
    for asset_id, scene_numbers in za_stock_assets.items():
        if len(scene_numbers) > 1:
            issues.append(f"za_stock 素材 {asset_id} 被第{'、'.join(map(str, scene_numbers))}镜重复使用")
    for asset_id, scene_numbers in image_assets.items():
        if len(scene_numbers) > 1:
            issues.append(f"Buffalo 静态图片 {asset_id} 被第{'、'.join(map(str, scene_numbers))}镜重复使用")
    for key, scene_numbers in exact_ranges.items():
        if len(scene_numbers) > 1:
            asset_id, start, end = key
            issues.append(f"素材 {asset_id} 的 {start}–{end}ms 时间范围被第{'、'.join(map(str, scene_numbers))}镜重复使用")
    for asset_id, records in hotspot_assets.items():
        if len(records) > 2:
            issues.append(f"热点母片 {asset_id} 使用了 {len(records)} 个 Hook，最多允许 2 个")
        ranges = [(scene_number, value) for scene_number, value in records if value]
        for index, (first_scene, first) in enumerate(ranges):
            for second_scene, second in ranges[index + 1:]:
                if max(first[0], second[0]) < min(first[1], second[1]):
                    issues.append(
                        f"热点母片 {asset_id} 的第{first_scene}、{second_scene}镜时间范围重叠，不能作为不同 Hook"
                    )

    return {
        "passed": not issues,
        "issues": list(dict.fromkeys(issues)),
        "owned_asset_count": len(owned_assets),
        "za_stock_asset_count": len(za_stock_assets),
        "hotspot_parent_count": len(hotspot_assets),
        "asset_segment_count": len(segment_scenes),
        "image_asset_count": len(image_assets),
    }


def subtitle_timeline_report(scene_subtitles: Iterable[dict], *, final_duration: float, transition_duration: float) -> dict:
    """Verify that per-scene speech/subtitles still form a valid final timeline.

    Cues are generated against measured TTS audio per scene.  This additionally
    checks the final concatenated timeline after crossfades, where past versions
    could report every scene as valid but leave a final timeline gap.
    """
    items = [item for item in scene_subtitles if isinstance(item, dict)]
    cursor = 0.0
    issues: list[str] = []
    timeline: list[dict] = []
    for index, item in enumerate(items, 1):
        duration = max(0.0, float(item.get("render_duration") or 0))
        sync = item.get("sync") or {}
        audio_duration = max(0.0, float(sync.get("audio_duration") or 0))
        subtitle_end = max(0.0, float(sync.get("subtitle_end") or 0))
        if not sync.get("passed"):
            issues.append(f"第{index}镜字幕未覆盖旁白音频")
        if subtitle_end > audio_duration + 0.12:
            issues.append(f"第{index}镜字幕超出旁白音频")
        scene_end = cursor + duration
        timeline.append({"scene": index, "start": round(cursor, 3), "end": round(scene_end, 3)})
        cursor = max(0.0, scene_end - (transition_duration if index < len(items) else 0.0))
    # Do not infer visual content from pixels here—source-usage gates prevent
    # duplication upstream and this report verifies the final timed timeline.
    expected = max(0.0, cursor)
    if abs(expected - float(final_duration or 0)) > 0.4:
        issues.append("转场后的最终字幕时间线与成片时长不一致")
    return {"passed": not issues, "issues": list(dict.fromkeys(issues)), "timeline": timeline,
            "expected_final_duration": round(expected, 3), "final_duration": round(float(final_duration or 0), 3)}
