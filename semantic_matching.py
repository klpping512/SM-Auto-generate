"""口播/脚本到镜头片段的可解释匹配。"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from asset_processing import TAG_TERMS


WEIGHTS = {
    "semantic": 35,
    "brand": 15,
    "entity": 20,
    "scene_action": 15,
    "mood": 10,
    "business_role": 10,
    "quality": 5,
    "freshness": 5,
}

BUSINESS_TERMS = {
    "风险提示": ("风险", "拥堵", "延误", "注意", "预警"),
    "解决方案": ("解决", "应对", "方案", "建议"),
    "品牌证明": ("我们", "团队", "服务", "能力", "客户"),
    "事实说明": ("数据显示", "目前", "正在", "已经", "消息"),
}

MOOD_TERMS = {
    "紧张": ("拥堵", "延误", "风险", "紧急"),
    "积极": ("提升", "顺利", "增长", "解决"),
    "专业": ("数据", "方案", "清关", "物流", "港口"),
}

HOTSPOT_SCENE_ROLES = {"hotspot_hook", "hotspot_evidence", "fact_context", "impact_explainer"}
OWNED_SCENE_ROLES = {"brand_proof", "owned_proof", "brand_close"}
ROLE_CATEGORY_FIT = {
    # 这是“画面职责→素材分类”的确定性证据，不是把任意素材抬成高分。
    "brand_proof": {"warehouse", "staff", "facility", "delivery", "brand"},
    "owned_proof": {"warehouse", "staff", "facility", "delivery", "brand"},
    "brand_close": {"delivery", "warehouse", "facility", "brand"},
    "impact_explainer": {"warehouse", "facility", "delivery"},
}
SCENE_ROLE_ALIASES = {
    "事实钩子": "hotspot_hook",
    "事实说明": "fact_context",
    "风险提示": "impact_explainer",
    "品牌承接": "brand_proof",
    "行动承接": "brand_close",
}


def _values(text: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = text.casefold()
    return [value for value, terms in mapping.items() if any(term.casefold() in lowered for term in terms)]


def extract_semantics(text: str) -> dict[str, list[str]]:
    text = str(text or "")
    semantics = {dimension: _values(text, values) for dimension, values in TAG_TERMS.items()}
    semantics["mood"] = _values(text, MOOD_TERMS)
    semantics["business_role"] = _values(text, BUSINESS_TERMS)
    return semantics


def _split_text(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", text) if part.strip()]


def build_semantic_atoms(payload: dict) -> list[dict]:
    scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    atoms: list[dict] = []
    if scenes:
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            voiceover = str(scene.get("voiceover") or scene.get("text") or "").strip()
            visual = str(scene.get("visual") or "").strip()
            text = " ".join(value for value in (voiceover, visual) if value)
            if not text:
                continue
            semantics = extract_semantics(text)
            atoms.append({
                "position": index,
                "text": voiceover or visual,
                "semantics": semantics,
                "duration_ms": max(1_000, round(float(scene.get("duration") or 5) * 1_000)),
                "constraints": {
                    "region": semantics.get("region", []),
                    "orientation": scene.get("orientation") or payload.get("orientation"),
                    "scene_role": scene.get("scene_role") or SCENE_ROLE_ALIASES.get(scene.get("business_role")),
                    "hotspot_id": scene.get("hotspot_id") or payload.get("hotspot_id"),
                },
            })
        return atoms

    raw = str(payload.get("script") or payload.get("body") or payload.get("text") or "").strip()
    for index, part in enumerate(_split_text(raw)):
        semantics = extract_semantics(part)
        atoms.append({
            "position": index,
            "text": part,
            "semantics": semantics,
            "duration_ms": max(2_000, min(8_000, round(len(part) / 4.2 * 1_000))),
            "constraints": {"region": semantics.get("region", []), "orientation": payload.get("orientation")},
        })
    return atoms


def _tag_map(segment: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for tag in segment.get("tags") or []:
        if float(tag.get("confidence") or 0) < 0.45:
            continue
        result.setdefault(str(tag.get("dimension") or ""), set()).add(str(tag.get("value") or ""))
    # 新 taxonomy 以 object 表示画面对象；旧匹配器仍以 entity 提取脚本主体，
    # 两者在匹配时等价，保留向后兼容。
    result.setdefault("entity", set()).update(result.get("object", set()))
    category = segment.get("primary_category")
    category_tags = {
        "warehouse": {"entity": {"仓库"}, "scene": {"仓库作业"}},
        "delivery": {"scene": {"道路运输"}},
        "customs": {"business_role": {"风险提示"}},
        "staff": {"entity": {"团队"}},
        "facility": {"scene": {"仓库作业"}},
        "customer": {"business_role": {"品牌证明"}},
    }
    for dimension, values in category_tags.get(category, {}).items():
        result.setdefault(dimension, set()).update(values)
    return result


def _ngrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", str(text or "").casefold())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left))


def _freshness(segment: dict) -> float:
    value = segment.get("event_at") or segment.get("created_at")
    if not value:
        return 0.5
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        days = max(0, (datetime.now(timezone.utc) - parsed).days)
        return max(0.1, math.exp(-days / 180))
    except (ValueError, TypeError):
        return 0.5


def _hard_conflict(atom: dict, tags: dict[str, set[str]], segment: dict) -> str | None:
    constraints = atom.get("constraints") or {}
    scene_role = constraints.get("scene_role")
    asset_hotspot_id = segment.get("asset_hotspot_id")
    if scene_role in HOTSPOT_SCENE_ROLES:
        required_hotspot_id = constraints.get("hotspot_id")
        if asset_hotspot_id is None or (
            required_hotspot_id is not None and int(asset_hotspot_id) != int(required_hotspot_id)
        ):
            return "热点素材库冲突"
        if str(segment.get("asset_rights_status") or "unknown") not in {"licensed", "confirmed", "green"}:
            return "热点素材权利未确认"
    elif scene_role in OWNED_SCENE_ROLES and asset_hotspot_id is not None:
        return "品牌分镜不能使用热点素材"
    # 品牌分镜必须来自确认的 Buffalo 自有库、或有画面/资产文本中的 Buffalo
    # 证据；普通物流素材不能因为分类为 delivery 就冒充品牌素材。真正「可见」
    # 的标识会在评分理由中与自有库归属明确区分。
    if scene_role in {"brand_proof", "brand_close"}:
        searchable = " ".join(str(segment.get(field) or "") for field in (
            "description", "transcript", "ocr_text", "asset_name", "asset_source", "asset_attribution",
        )).casefold()
        trusted_owned = str(segment.get("asset_source") or "").casefold() in {"local_directory", "buffalo_library"}
        visible_brand = "Buffalo" in tags.get("brand", set())
        if not (visible_brand or trusted_owned or "buffalo" in searchable):
            return "品牌分镜缺少 Buffalo 可见或自有库证据"
    required_region = set(atom.get("constraints", {}).get("region") or atom.get("semantics", {}).get("region") or [])
    candidate_region = tags.get("region", set())
    broad = {"南非", "South Africa"}
    if required_region and candidate_region and not (required_region & candidate_region) and not (candidate_region & broad):
        return "地区冲突"
    return None


def _score(atom: dict, segment: dict) -> dict | None:
    tags = _tag_map(segment)
    if _hard_conflict(atom, tags, segment):
        return None
    semantics = atom.get("semantics") or extract_semantics(atom.get("text", ""))
    searchable = " ".join(str(segment.get(field) or "") for field in ("description", "transcript", "ocr_text"))
    searchable += " " + " ".join(value for values in tags.values() for value in values)

    reasons: list[str] = []
    semantic_ratio = _overlap(_ngrams(atom.get("text", "")), _ngrams(searchable))
    if semantic_ratio:
        reasons.append(f"内容语义重合 {round(semantic_ratio * 100)}%")

    brand_ratio = _overlap(set(semantics.get("brand", [])), tags.get("brand", set()))
    if brand_ratio:
        reasons.append("品牌露出匹配：" + "、".join(sorted(set(semantics.get("brand", [])) & tags.get("brand", set()))))

    entity_ratio = _overlap(set(semantics.get("entity", [])), tags.get("entity", set()))
    if entity_ratio:
        reasons.append("主体匹配：" + "、".join(sorted(set(semantics.get("entity", [])) & tags.get("entity", set()))))

    desired_scene_action = set(semantics.get("scene", [])) | set(semantics.get("action", []))
    actual_scene_action = tags.get("scene", set()) | tags.get("action", set())
    scene_action_ratio = _overlap(desired_scene_action, actual_scene_action)
    if scene_action_ratio:
        reasons.append("场景/动作匹配：" + "、".join(sorted(desired_scene_action & actual_scene_action)))

    mood_ratio = _overlap(set(semantics.get("mood", [])), tags.get("mood", set()))
    role_ratio = _overlap(set(semantics.get("business_role", [])), tags.get("business_role", set()))
    scene_role = (atom.get("constraints") or {}).get("scene_role")
    brand_visible = "Buffalo" in tags.get("brand", set())
    brand_text = " ".join(str(segment.get(field) or "") for field in (
        "description", "transcript", "ocr_text", "asset_name", "asset_source", "asset_attribution",
    )).casefold()
    brand_owned = str(segment.get("asset_source") or "").casefold() in {"local_directory", "buffalo_library"}
    brand_identity_evidence = brand_visible or brand_owned or "buffalo" in brand_text
    if scene_role in {"brand_proof", "brand_close"} and not brand_visible:
        reasons.append("品牌归属来自 Buffalo 自有库/资产文本；画面标识未单独验证")
    category = str(segment.get("primary_category") or segment.get("asset_category") or "").casefold()
    role_fit = bool(scene_role and category and category in ROLE_CATEGORY_FIT.get(scene_role, set()))
    if role_fit:
        reasons.append(f"分镜职责匹配：{scene_role} → {category}")
    quality = max(0.0, min(1.0, float(segment.get("quality_score") or 0)))
    fresh = _freshness(segment)
    score = (
        semantic_ratio * WEIGHTS["semantic"] + brand_ratio * WEIGHTS["brand"] + entity_ratio * WEIGHTS["entity"]
        + scene_action_ratio * WEIGHTS["scene_action"] + mood_ratio * WEIGHTS["mood"]
        + role_ratio * WEIGHTS["business_role"] + quality * WEIGHTS["quality"]
        + fresh * WEIGHTS["freshness"]
    )
    # 自动编排的品牌承接镜头往往没有逐条 ASR/OCR，但素材库分类是人工确认的
    # 一等证据。给它独立权重，避免“有几百条素材却全部被判弱匹配”。
    if role_fit:
        score += 70

    required_ms = int(atom.get("duration_ms") or 0)
    available_ms = max(0, int(segment.get("end_ms") or 0) - int(segment.get("start_ms") or 0))
    duration_mismatch = bool(required_ms and available_ms and available_ms < required_ms * 0.75)
    if duration_mismatch:
        score -= min(18, (required_ms - available_ms) / max(required_ms, 1) * 20)
        reasons.append(f"时长不足：需约 {required_ms / 1000:g} 秒，素材 {available_ms / 1000:g} 秒")

    required_orientation = atom.get("constraints", {}).get("orientation")
    actual_orientation = segment.get("orientation")
    orientation_mismatch = bool(
        required_orientation
        and actual_orientation not in {None, "", "unknown", required_orientation}
    )
    composition = tags.get("composition", set())
    unsafe_crop = bool(
        composition & {"edge_risk_both", "edge_risk_left", "edge_risk_right"}
    )
    # 横屏→竖屏：无边缘关键信息风险时可居中裁切，不应一律判为弱匹配。
    # 有 edge_risk_* 时保留惩罚并强制复核。
    orientation_forces_review = False
    if orientation_mismatch:
        if unsafe_crop:
            score -= 12
            orientation_forces_review = True
            risk_labels = sorted(composition & {"edge_risk_both", "edge_risk_left", "edge_risk_right"})
            reasons.append(
                f"画幅不适配裁切：{actual_orientation} → {required_orientation}"
                f"（{'、'.join(risk_labels)}）"
            )
        else:
            score -= 3
            reasons.append(f"画幅可安全居中裁切：{actual_orientation} → {required_orientation}")

    required_region = set(atom.get("constraints", {}).get("region") or [])
    matched_region = required_region & tags.get("region", set())
    if matched_region:
        reasons.insert(0, "地区匹配：" + "、".join(sorted(matched_region)))
    if not reasons:
        reasons.append("仅有基础质量/时效分，缺少直接内容证据")
    score = round(max(0.0, min(100.0, score)), 2)
    strong_evidence = bool(
        matched_region or brand_ratio or entity_ratio or scene_action_ratio or semantic_ratio >= 0.2 or role_fit
    )
    return {
        "segment_id": int(segment["id"]),
        "asset_id": int(segment.get("asset_id") or 0),
        "library_origin": "hotspot" if segment.get("asset_hotspot_id") is not None else "owned",
        "hotspot_id": segment.get("asset_hotspot_id"),
        "media_kind": "video_file" if segment.get("asset_file_type") == "video" else "image",
        "start_ms": int(segment.get("start_ms") or 0),
        "end_ms": int(segment.get("end_ms") or 0),
        "rights_tier": segment.get("asset_rights_status"),
        "source_page_url": segment.get("asset_source_url"),
        "attribution": segment.get("asset_attribution"),
        "match_score": score,
        "reasons": reasons,
        "review_required": bool(
            score < 55 or not strong_evidence or duration_mismatch or orientation_forces_review
        ),
        "orientation_safe_crop": bool(orientation_mismatch and not unsafe_crop),
    }


def rank_segments(
    atom: dict,
    segments: list[dict],
    top_k: int = 3,
    used_segment_ids: set[int] | None = None,
    required_file_type: str | None = None,
    exclude_asset_ids: set[int] | None = None,
    diversify_by_asset: bool = True,
) -> list[dict]:
    used_segment_ids = used_segment_ids or set()
    exclude_asset_ids = exclude_asset_ids or set()
    candidates = []
    for segment in segments:
        if required_file_type and segment.get("asset_file_type") != required_file_type:
            continue
        asset_id = int(segment.get("asset_id") or 0)
        if asset_id and asset_id in exclude_asset_ids:
            continue
        item = _score(atom, segment)
        if not item:
            continue
        if item["segment_id"] in used_segment_ids:
            item["match_score"] = max(0, round(item["match_score"] - 25, 2))
            item["reasons"].append("本次脚本已使用该镜头，已降低重复推荐")
        candidates.append(item)
    candidates.sort(key=lambda item: (-item["match_score"], item["segment_id"]))
    limit = max(1, min(int(top_k), 10))
    result = []
    used_asset_ids: set[int] = set()
    for item in candidates:
        asset_id = int(item.get("asset_id") or 0)
        # 跨母片去重：同一原始视频不得占满 TopK，避免后续分镜无可用母片。
        if diversify_by_asset and asset_id and asset_id in used_asset_ids:
            continue
        result.append(item)
        if asset_id:
            used_asset_ids.add(asset_id)
        if len(result) >= limit:
            break
    for index, item in enumerate(result, 1):
        item["rank"] = index
    return result


def assign_candidates(
    atoms: list[dict],
    segments: list[dict],
    top_k: int = 3,
    required_file_type: str | None = None,
) -> list[dict]:
    """为整条时间线生成跨母片候选，并全局贪心保证优先不撞同一原始视频。"""
    pools: list[list[dict]] = []
    for atom in atoms:
        pools.append(
            rank_segments(
                atom,
                segments,
                top_k=top_k,
                used_segment_ids=None,
                required_file_type=required_file_type,
                diversify_by_asset=True,
            )
        )

    used_segments: set[int] = set()
    used_assets: set[int] = set()
    assignments: list[dict] = []
    for atom, pool in zip(atoms, pools):
        available = []
        for candidate in pool:
            asset_id = int(candidate.get("asset_id") or 0)
            segment_id = int(candidate["segment_id"])
            if segment_id in used_segments:
                continue
            if asset_id and asset_id in used_assets:
                continue
            available.append(candidate)
        if not available:
            available = rank_segments(
                atom,
                segments,
                top_k=top_k,
                used_segment_ids=used_segments,
                required_file_type=required_file_type,
                exclude_asset_ids=used_assets,
                diversify_by_asset=True,
            )
        preferred = available[0] if available else None
        if preferred:
            used_segments.add(int(preferred["segment_id"]))
            preferred_asset = int(preferred.get("asset_id") or 0)
            if preferred_asset:
                used_assets.add(preferred_asset)
        assignments.append({**atom, "candidates": available})
    return assignments
