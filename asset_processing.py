"""镜头切分与素材分类的确定性核心规则。

ASR、OCR 和场景检测器均为可替换输入；本模块只负责将证据归一化，避免把
某个模型的低置信度输出直接当成事实。
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import database as db
import model_router


# v4：扩大语义覆盖与画幅风险标签，驱动存量素材重新进入 taxonomy 重建队列。
PROCESSING_VERSION = "semantic-v4-coverage"
MIN_SEGMENT_MS = 2_000
MAX_SEGMENT_MS = 8_000
MAX_REMOTE_VISUAL_SEGMENTS = max(12, min(120, int(os.environ.get("ASSET_MAX_REMOTE_VISUAL_SEGMENTS", "96"))))
BUFFALO_BRAND_MARKERS = ("buffalo", "buffalo logistics", "we deliver hope")


def taxonomy_reuse_enabled() -> bool:
    """存量 taxonomy 重建默认复用已有预览/字幕，只重跑视觉打标。"""
    return os.environ.get("ASSET_TAXONOMY_REUSE_MEDIA", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _media_file_exists(static_dir: Path, relative: str | None) -> bool:
    if not relative:
        return False
    path = (Path(static_dir) / relative).resolve()
    static_root = Path(static_dir).resolve()
    return static_root in path.parents and path.is_file()


def load_reusable_segment_plan(asset: dict, static_dir: Path) -> list[dict] | None:
    """若旧版本分段的关键帧都还在盘上，直接复用时间轴与本地证据。

    全量重建最慢的部分是逐镜 ffmpeg 切预览 + ASR/OCR；匹配治理需要的是新的
    分类/标签/edge_risk。有可复用媒体时跳过本地重编码，只刷新视觉标注。
    """
    if not taxonomy_reuse_enabled():
        return None
    rows = db.list_asset_segments(asset_id=int(asset["id"]), status="", limit=2_000)
    if not rows:
        return None
    by_version: dict[str, list[dict]] = {}
    for row in rows:
        by_version.setdefault(str(row.get("processing_version") or "v1"), []).append(row)
    best: list[dict] | None = None
    best_score = -1
    for segments in by_version.values():
        ordered = sorted(segments, key=lambda item: int(item.get("segment_index") or 0))
        reusable: list[dict] = []
        for item in ordered:
            thumbnail = item.get("thumbnail_path") or ""
            if not _media_file_exists(static_dir, thumbnail):
                if asset.get("file_type") == "image":
                    thumbnail = asset.get("thumbnail") or asset.get("filepath") or ""
                    if not _media_file_exists(static_dir, thumbnail):
                        break
                else:
                    break
            preview = item.get("preview_path") or None
            if preview and not _media_file_exists(static_dir, preview):
                preview = None
            reusable.append({
                "segment_index": int(item.get("segment_index") or 0),
                "start_ms": int(item.get("start_ms") or 0),
                "end_ms": int(item.get("end_ms") or 0),
                "orientation": item.get("orientation") or "unknown",
                "preview_path": preview,
                "thumbnail_path": thumbnail,
                "transcript": item.get("transcript") or "",
                "ocr_text": item.get("ocr_text") or "",
            })
        else:
            if len(reusable) > best_score:
                best = reusable
                best_score = len(reusable)
    return best if best_score > 0 else None

PRIMARY_CATEGORIES = {
    "warehouse", "delivery", "customs", "brand", "staff", "facility", "customer", "other",
}

CATEGORY_TERMS = {
    "warehouse": ("海外仓", "仓库", "仓储", "分拣", "入库", "出库", "货架", "堆场", "装卸", "container terminal", "terminal"),
    "delivery": ("物流", "运输", "配送", "卡车", "船舶", "港口", "shipping", "truck", "port"),
    "customs": ("清关", "海关", "报关", "关税", "customs", "clearance"),
    "brand": ("品牌", "标识", "logo", "brand"),
    "staff": ("员工", "团队", "工作人员", "会议", "访谈", "staff", "team"),
    "facility": ("叉车", "设备", "传送带", "机器", "facility", "forklift"),
    "customer": ("客户", "签收", "案例", "反馈", "customer", "delivery receipt"),
}

TAG_TERMS = {
    "brand": {
        # 仅把可见的 Buffalo 标识作为品牌标签；不能由文字口播推断品牌归属。
        "Buffalo": ("buffalo", "buffalo logistics", "we deliver hope"),
    },
    "region": {
        "德班": ("德班", "durban"),
        "开普敦": ("开普敦", "cape town"),
        "约翰内斯堡": ("约翰内斯堡", "johannesburg", "joburg"),
        "南非": ("南非", "south africa"),
    },
    "entity": {
        "卡车": ("卡车", "货车", "拖车", "truck"),
        "集装箱": ("集装箱", "container"),
        "船舶": ("船舶", "货轮", "vessel", "ship"),
        "仓库": ("海外仓", "仓库", "warehouse"),
        "港口": ("港口", "port", "terminal"),
        "团队": ("团队", "员工", "staff", "team"),
    },
    "action": {
        "排队": ("排队", "拥堵", "queue", "congestion"),
        "分拣": ("分拣", "sorting", "sort"),
        "装卸": ("装卸", "loading", "unloading"),
        "运输": ("运输", "行驶", "shipping", "transport"),
        "签收": ("签收", "receipt", "received"),
        "入库": ("入库", "inbound"),
        "出库": ("出库", "outbound"),
    },
    "scene": {
        "港口作业": ("港口", "port", "terminal"),
        "仓库作业": ("海外仓", "仓库", "分拣", "入库", "出库", "warehouse", "sorting"),
        "道路运输": ("卡车", "运输", "truck", "transport"),
    },
}

EVIDENCE_WEIGHT = {"manual": 1.0, "source": 0.92, "ocr": 0.84, "asr": 0.78, "model": 0.68, "filename": 0.55}

MODEL_TAG_ALIASES = {
    "scene": {
        "delivery": "道路运输", "road transport": "道路运输", "transport": "道路运输",
        "warehouse": "仓库作业", "warehouse operation": "仓库作业",
    },
    "object": {
        "truck": "卡车", "lorry": "卡车", "trailer": "拖车", "van": "货车",
    },
}


def normalize_scene_boundaries(
    boundaries: list[int], duration_ms: int,
    min_ms: int = MIN_SEGMENT_MS, max_ms: int = MAX_SEGMENT_MS,
) -> list[tuple[int, int]]:
    """把模型切点转成连续、可用于短视频的 2–8 秒镜头。"""
    duration_ms = max(0, int(duration_ms))
    if duration_ms == 0:
        return [(0, 0)]
    points = sorted({0, duration_ms, *(max(0, min(duration_ms, int(v))) for v in boundaries)})
    points = [point for point in points if point in {0, duration_ms} or 0 < point < duration_ms]

    merged: list[list[int]] = []
    for start, end in zip(points, points[1:]):
        if end <= start:
            continue
        if merged and end - start < min_ms:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_ms:
        merged[1][0] = merged[0][0]
        merged.pop(0)

    result: list[tuple[int, int]] = []
    for start, end in merged:
        length = end - start
        pieces = max(1, math.ceil(length / max_ms))
        while pieces > 1 and length / pieces < min_ms:
            pieces -= 1
        for index in range(pieces):
            left = start + round(length * index / pieces)
            right = start + round(length * (index + 1) / pieces)
            result.append((left, right))
    return result or [(0, duration_ms)]


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _category_scores(evidence: list[tuple[str, str]]) -> tuple[str, float, list[dict]]:
    scores = {category: 0.0 for category in CATEGORY_TERMS}
    explanations: list[dict] = []
    for source, text in evidence:
        if not text:
            continue
        weight = EVIDENCE_WEIGHT[source]
        for category, terms in CATEGORY_TERMS.items():
            hits = [term for term in terms if term.casefold() in text.casefold()]
            if hits:
                gain = min(weight + 0.08 * (len(hits) - 1), 1.0)
                scores[category] += gain
                explanations.append({"source": source, "category": category, "hits": hits[:4], "score": round(gain, 3)})
    winner, score = max(scores.items(), key=lambda item: item[1])
    if score == 0:
        return "other", 0.25, []
    competing = sorted(scores.values(), reverse=True)
    margin = score - (competing[1] if len(competing) > 1 else 0)
    confidence = min(0.96, 0.52 + score * 0.25 + max(0, margin) * 0.12)
    return winner, round(confidence, 3), sorted(explanations, key=lambda item: -item["score"])


def _extract_tags(evidence: list[tuple[str, str]], manual_tags: dict | None = None) -> list[dict]:
    tags: dict[tuple[str, str], dict] = {}
    blocked_dimensions = set()
    for dimension, values in (manual_tags or {}).items():
        if not isinstance(values, list):
            values = [values]
        valid = [str(value).strip() for value in values if str(value).strip()]
        if valid:
            blocked_dimensions.add(str(dimension))
        for value in valid:
            tags[(str(dimension), value.casefold())] = {
                "dimension": str(dimension), "value": value, "confidence": 1.0,
                "source": "manual", "confirmed": True,
            }
    for source, text in evidence:
        if not text:
            continue
        for dimension, values in TAG_TERMS.items():
            if dimension in blocked_dimensions:
                continue
            for value, terms in values.items():
                if _contains(text, terms):
                    key = (dimension, value.casefold())
                    candidate = {
                        "dimension": dimension, "value": value,
                        "confidence": EVIDENCE_WEIGHT[source], "source": source, "confirmed": False,
                    }
                    if key not in tags or candidate["confidence"] > tags[key]["confidence"]:
                        tags[key] = candidate
    return sorted(tags.values(), key=lambda item: (item["dimension"], -item["confidence"], item["value"]))


def classify_evidence(
    filename: str, transcript: str = "", ocr_text: str = "",
    source_metadata: str = "", model_description: str = "", manual: dict | None = None,
    model_category: str = "", model_confidence: float | int | None = None,
    model_tags: dict[str, list[str]] | None = None,
) -> dict:
    """按人工 > 来源 > OCR > ASR > 模型 > 文件名合并分类证据。"""
    manual = manual or {}
    manual_category = str(manual.get("primary_category") or "")
    evidence = [
        ("source", source_metadata), ("ocr", ocr_text), ("asr", transcript),
        ("model", model_description), ("filename", Path(filename).stem),
    ]
    if manual_category in PRIMARY_CATEGORIES:
        category, confidence, decision = manual_category, 1.0, "confirmed"
        explanations = [{"source": "manual", "category": category, "hits": [category], "score": 1.0}]
    elif model_category in PRIMARY_CATEGORIES and float(model_confidence or 0) >= 0.78:
        # 视觉模型明确识别到画面主体时优先采用；仍把描述和 OCR/ASR 留作
        # 可审计证据，低置信度不会覆盖既有规则。
        rule_category, rule_confidence, rule_explanations = _category_scores(evidence)
        # 空白画面、封面或抽帧异常常被视觉模型高置信判为 other。若文件名、来源、
        # OCR/ASR 已给出明确物流场景，不让这个“看不见内容”的 other 覆盖可审计证据。
        if model_category == "other" and rule_category != "other" and rule_confidence >= 0.7:
            category, confidence, explanations = rule_category, rule_confidence, rule_explanations
            decision = "auto"
        else:
            category = model_category
            confidence = round(min(0.96, float(model_confidence)), 3)
            decision = "auto"
            explanations = [{"source": "model", "category": category, "hits": [category], "score": confidence}]
    else:
        category, confidence, explanations = _category_scores(evidence)
        decision = "auto" if confidence >= 0.7 else "review_required"
    return {
        "primary_category": category,
        "confidence": confidence,
        "decision": decision,
        "tags": _merge_model_tags(_extract_tags(evidence, manual.get("tags")), model_tags, model_confidence),
        "evidence": explanations,
    }


def _orientation(width: int | None, height: int | None) -> str:
    width, height = int(width or 0), int(height or 0)
    if not width or not height:
        return "unknown"
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def build_processing_plan(asset: dict, scene_boundaries: list[int]) -> list[dict]:
    orientation = _orientation(asset.get("width"), asset.get("height"))
    if asset.get("file_type") == "image":
        return [{"segment_index": 0, "start_ms": 0, "end_ms": 0, "orientation": orientation}]
    duration_ms = round(float(asset.get("duration") or 0) * 1000)
    return [
        {"segment_index": index, "start_ms": start, "end_ms": end, "orientation": orientation}
        for index, (start, end) in enumerate(normalize_scene_boundaries(scene_boundaries, duration_ms))
    ]


def visual_segment_indexes(segment_count: int, limit: int = MAX_REMOTE_VISUAL_SEGMENTS) -> set[int]:
    """Choose a bounded, evenly distributed subset for remote vision tagging.

    Long compilations can have hundreds of deterministic 2–8 second slices.
    Calling the model after its budget has already been consumed creates noisy
    ``BudgetExceeded`` warnings and makes the final portion look analysed when
    it was not.  Sampling retains coverage across the mother video; all slices
    still keep local OCR/ASR evidence and remain available to Hook curation.
    """
    segment_count = max(0, int(segment_count))
    limit = max(1, int(limit))
    if segment_count <= limit:
        return set(range(segment_count))
    if limit == 1:
        return {0}
    return {
        round(position * (segment_count - 1) / (limit - 1))
        for position in range(limit)
    }


def processing_capabilities() -> dict:
    asr_model_value = os.environ.get("ASSET_ASR_MODEL_PATH", "").strip()
    asr_model = Path(asr_model_value) if asr_model_value else None
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "scene_detect": bool(shutil.which("ffmpeg")),
        "asr": _module_available("faster_whisper") and bool(asr_model and _valid_asr_model(asr_model)),
        "ocr": _module_available("rapidocr") or _module_available("rapidocr_onnxruntime"),
    }


def _module_available(name: str) -> bool:
    try:
        from importlib.util import find_spec
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _valid_asr_model(path: Path) -> bool:
    return path.is_dir() and (path / "model.bin").is_file() and (path / "config.json").is_file()


def detect_scene_boundaries(path: Path, duration_ms: int) -> list[int]:
    """在 320px/5fps 代理流上检测切点，避免全尺寸 4K HEVC 分析堵塞队列。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(path),
                 "-vf", "fps=5,scale=320:-2,select=gt(scene\\,0.32),showinfo",
                 "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120, check=False,
            )
            starts = [0]
            for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr or ""):
                point = round(float(value) * 1_000)
                if 0 < point < duration_ms:
                    starts.append(point)
            if len(starts) > 1:
                return sorted(set(starts))
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
    return list(range(0, max(0, duration_ms), 6_000))


def _make_video_preview(source: Path, static_dir: Path, asset_id: int, segment: dict) -> tuple[str | None, str | None]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None, None
    folder_rel = Path("assets") / "segments" / str(asset_id) / PROCESSING_VERSION
    folder = static_dir / folder_rel
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"segment-{segment['segment_index']:04d}"
    preview_rel = folder_rel / f"{stem}.mp4"
    thumb_rel = folder_rel / f"{stem}.jpg"
    start = segment["start_ms"] / 1_000
    duration = max(0.1, (segment["end_ms"] - segment["start_ms"]) / 1_000)
    subprocess.run(
        [ffmpeg, "-y", "-ss", str(start), "-i", str(source), "-t", str(duration),
         "-map", "0:v:0", "-an", "-vf", "scale=720:-2", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "30", "-movflags", "+faststart",
         str(static_dir / preview_rel)],
        capture_output=True, timeout=120, check=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-ss", str(start + min(0.5, duration / 2)), "-i", str(source),
         "-frames:v", "1", "-vf", "scale=640:-2", str(static_dir / thumb_rel)],
        capture_output=True, timeout=60, check=True,
    )
    return preview_rel.as_posix(), thumb_rel.as_posix()


def _transcribe(path: Path) -> str:
    model_path_value = os.environ.get("ASSET_ASR_MODEL_PATH", "").strip()
    model_path = Path(model_path_value) if model_path_value else None
    if not (_module_available("faster_whisper") and model_path and _valid_asr_model(model_path)):
        return ""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(str(model_path), device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(path), language=None, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())[:12_000]
    except Exception:
        return ""


def _ocr(path: Path) -> str:
    if not processing_capabilities()["ocr"]:
        return ""
    try:
        if _module_available("rapidocr"):
            from rapidocr import RapidOCR
        else:
            from rapidocr_onnxruntime import RapidOCR
        result = RapidOCR()(str(path))
        if hasattr(result, "txts"):
            return " ".join(result.txts or [])[:4_000]
        rows = result[0] if isinstance(result, tuple) else result
        return " ".join(str(row[1]) for row in (rows or []) if len(row) > 1)[:4_000]
    except Exception:
        return ""


def _quality(asset: dict, segment: dict) -> float:
    width, height = int(asset.get("width") or 0), int(asset.get("height") or 0)
    resolution = min(1.0, min(width, height) / 1080) if width and height else 0.35
    duration_ms = segment["end_ms"] - segment["start_ms"]
    duration_score = 1.0 if asset.get("file_type") == "image" or 2_000 <= duration_ms <= 8_000 else 0.6
    return round(0.75 * resolution + 0.25 * duration_score, 3)


def _merge_model_tags(tags: list[dict], model_tags: dict[str, list[str]] | None,
                      confidence: float | int | None) -> list[dict]:
    merged = {(item["dimension"], item["value"].casefold()): item for item in tags}
    for dimension, values in (model_tags or {}).items():
        if dimension not in {"brand", "scene", "object", "action"}:
            continue
        for value in values:
            clean = str(value).strip()[:80]
            if clean:
                merged.setdefault((dimension, clean.casefold()), {
                    "dimension": dimension, "value": clean,
                    "confidence": round(float(confidence or 0.68), 3), "source": "model", "confirmed": False,
                })
    return sorted(merged.values(), key=lambda item: (item["dimension"], item["value"]))


def _visual_tag_dimensions(payload: dict) -> dict[str, list[str]]:
    result = {"brand": [], "scene": [], "object": [], "action": [], "composition": []}
    mapping = {
        "brand_tags": "brand", "scene_tags": "scene", "object_tags": "object", "action_tags": "action",
    }
    for field, dimension in mapping.items():
        values = payload.get(field) or []
        cleaned = []
        for value in values:
            text = str(value).strip()[:80]
            if not text:
                continue
            cleaned.append(MODEL_TAG_ALIASES.get(dimension, {}).get(text.casefold(), text))
        result[dimension] = list(dict.fromkeys(cleaned))[:6]
    # 兼容接入前的 tags 数组；不丢弃已缓存模型结果。
    if not any(result[k] for k in ("brand", "scene", "object", "action")):
        result["object"] = [str(value).strip()[:80] for value in (payload.get("tags") or []) if str(value).strip()][:6]
    # 只在有裁切风险时落一条 tag；none 不产生标签，保持数据干净，也让下游
    # 策展证据里“没有标签”天然等同于“安全”，不用额外区分未标注和已确认安全。
    edge_risk = str(payload.get("edge_risk") or "").strip().lower()
    if edge_risk in {"left", "right", "both"}:
        result["composition"] = [f"edge_risk_{edge_risk}"]
    return result


def _visual_analysis(job_id: str, image_path: Path, segment: dict) -> dict:
    """用 Qwen-VL 标注一个片段；异常降级但必须留下可审计原因。"""
    if not model_router.key_is_available("vision_tagger"):
        return {"_error": "vision_tagger 未配置或未启用"}
    if not image_path.is_file():
        return {"_error": "视觉关键帧不存在"}
    try:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = (
            "你是物流短视频素材标注员。只根据这张关键帧识别，不要猜测不可见信息。"
            "返回单行 JSON：{\"primary_category\":\"warehouse|delivery|customs|brand|staff|facility|customer|other\","
            "\"confidence\":0到1,\"description\":\"不超过40字中文画面描述\","
            "\"brand_tags\":[\"最多2个可见品牌；看不到则空数组\"],"
            "\"scene_tags\":[\"最多3个主场景标签\"],\"object_tags\":[\"最多6个可见对象\"],"
            "\"action_tags\":[\"最多4个可见动作\"],"
            "\"edge_risk\":\"none|left|right|both\"}。渲染时画面会被居中放大裁切成 9:16 竖屏，"
            "只保留画面正中约三分之一宽度，两侧会被切掉。edge_risk 表示关键可辨认信息"
            "（文字、车牌、Logo、人脸等）是否落在会被切掉的左侧/右侧/两侧区域：都在正中安全区内"
            "填 none；关键信息偏向左边缘填 left；偏右填 right；左右都有关键信息填 both。"
            "主分类只表示主要场景："
            "仓库作业归 warehouse；道路/末端运输归 delivery；人物在仓库工作仍归 warehouse，人物放 object_tags；"
            "叉车/货架/传送带在仓库中仍归 warehouse，设备放 object_tags。品牌露出绝不改变主分类："
            "可见 Buffalo/Buffalo Logistics 标志时，brand_tags 填 Buffalo；看不清或未出现时必须留空，不能猜测。"
            "标签用中文标准词：道路运输、仓库作业、卡车、货车、拖车等，不要输出英文同义词。"
            f"片段时间：{segment['start_ms'] / 1000:.1f}-{segment['end_ms'] / 1000:.1f} 秒。"
        )
        result = asyncio.run(model_router.call_multimodal_json(
            job_id, "vision_tagger",
            [
                {"role": "system", "content": "严格返回 JSON，不要 Markdown。"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                ]},
            ],
            prompt_version="asset-vision-v2-brand",
            max_attempts=2,
        ))
        parsed = _parse_visual_json(result["content"])
        category = str(parsed.get("primary_category") or "").strip().lower()
        confidence = float(parsed.get("confidence") or 0)
        if category not in PRIMARY_CATEGORIES or not 0 <= confidence <= 1:
            return {"_error": "视觉模型未返回有效主分类"}
        return {
            "primary_category": category,
            "confidence": confidence,
            "description": str(parsed.get("description") or "").strip()[:500],
            "tags": _visual_tag_dimensions(parsed),
        }
    except Exception as exc:
        # 不能把远程模型故障伪装成「模型把它判为 other」。错误会随处理任务
        # 保留，管理员可据此判断是重试、改提示词还是修复路由。
        detail = str(exc).replace("\n", " ").strip()[:240]
        return {"_error": f"视觉标注降级：{type(exc).__name__}{(': ' + detail) if detail else ''}"}


def _parse_visual_json(content: str) -> dict:
    """兼容 Qwen-VL 偶发包裹在 Markdown 代码块中的 JSON。"""
    cleaned = str(content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    left, right = cleaned.find("{"), cleaned.rfind("}")
    if left < 0 or right < left:
        raise ValueError("视觉模型未返回 JSON 对象")
    parsed = json.loads(cleaned[left:right + 1])
    if not isinstance(parsed, dict):
        raise ValueError("视觉模型返回格式错误")
    return parsed


def process_asset_job(job_id: str, static_dir: Path) -> dict:
    """执行单个素材的镜头化、分类和标签入库；所有可选模型失败时安全降级。"""
    job = db.get_asset_processing_job(job_id)
    if not job:
        raise ValueError("素材处理任务不存在")
    asset = db.get_asset(job["asset_id"])
    if not asset:
        raise ValueError("素材不存在")
    source = (Path(static_dir) / asset["filepath"]).resolve()
    static_root = Path(static_dir).resolve()
    if static_root not in source.parents or not source.is_file():
        raise ValueError("素材文件不存在或路径非法")
    try:
        db.update_asset_processing_job(job_id, status="running", stage="scene_detection", progress=10,
                                       attempts=int(job.get("attempts") or 0) + 1,
                                       started_at=datetime_now())
        reused_plan = load_reusable_segment_plan(asset, static_root)
        if reused_plan:
            plan = reused_plan
            db.update_asset_processing_job(job_id, stage="taxonomy_refresh", progress=15)
        else:
            duration_ms = round(float(asset.get("duration") or 0) * 1_000)
            boundaries = detect_scene_boundaries(source, duration_ms) if asset["file_type"] == "video" else []
            plan = build_processing_plan(asset, boundaries)
        primary_results = []
        visual_errors: list[str] = []
        # 对常见的 3–10 分钟母片，默认 96 个视觉调用覆盖全部镜头。更长合集
        # 采用均匀抽样，绝不在预算耗尽后继续发远程调用并把异常伪装成分析结果。
        visual_indexes = visual_segment_indexes(len(plan))
        model_router.create_budget(
            f"asset-vision-{asset['id']}-{job_id}", max_calls=max(4, len(visual_indexes)),
            max_input_tokens=max(20_000, len(visual_indexes) * 1_200),
            max_output_tokens=max(6_000, len(visual_indexes) * 400),
        )
        for index, segment in enumerate(plan):
            stage_name = "taxonomy_refresh" if reused_plan else "asr_ocr"
            db.update_asset_processing_job(
                job_id, stage=stage_name, progress=20 + round(60 * index / max(1, len(plan))),
            )
            preview_path = thumbnail_path = None
            transcript = ocr_text = ""
            analysis_path = source
            if reused_plan:
                preview_path = segment.get("preview_path")
                thumbnail_path = segment.get("thumbnail_path")
                transcript = segment.get("transcript") or ""
                ocr_text = segment.get("ocr_text") or ""
            else:
                if asset["file_type"] == "video":
                    try:
                        preview_path, thumbnail_path = _make_video_preview(
                            source, static_root, asset["id"], segment,
                        )
                        if preview_path:
                            analysis_path = static_root / preview_path
                    except (subprocess.SubprocessError, OSError):
                        preview_path = thumbnail_path = None
                transcript = _transcribe(analysis_path) if asset["file_type"] == "video" else ""
                ocr_target = static_root / thumbnail_path if thumbnail_path else source
                ocr_text = _ocr(ocr_target)
            # 视频缩略图生成失败时不把整个 MP4 Base64 上传给视觉模型；这既会
            # 触发无意义的大额调用，也可能超出接口请求限制。
            visual_target = (
                static_root / thumbnail_path if thumbnail_path
                else source if asset["file_type"] == "image" else None
            )
            visual = _visual_analysis(
                f"asset-vision-{asset['id']}-{job_id}", visual_target, segment,
            ) if visual_target and index in visual_indexes else {}
            if visual.get("_error"):
                visual_errors.append(str(visual["_error"]))
            visual_usable = bool(visual.get("primary_category"))
            # 旧 category 没有人工确认语义，不能再锁死模型修正；只有通过编辑接口
            # 明确确认的分类才作为 manual 证据。
            manual = {"primary_category": asset["primary_category"]} if asset.get("primary_category_source") == "manual" else None
            classified = classify_evidence(
                asset["name"], transcript=transcript, ocr_text=ocr_text,
                model_description=" ".join([visual.get("description", ""), *sum(visual.get("tags", {}).values(), [])]),
                model_category=visual.get("primary_category", ""),
                model_confidence=visual.get("confidence"), model_tags=visual.get("tags"), manual=manual,
            )
            segment_id = db.create_asset_segment({
                "asset_id": asset["id"],
                "segment_index": segment["segment_index"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "orientation": segment.get("orientation") or "unknown",
                "preview_path": preview_path,
                "thumbnail_path": thumbnail_path or asset.get("thumbnail"),
                "transcript": transcript, "ocr_text": ocr_text,
                "description": " ".join(value for value in (asset["name"], visual.get("description", ""), transcript, ocr_text) if value)[:4_000],
                "primary_category": classified["primary_category"], "quality_score": _quality(asset, segment),
                "primary_category_source": "manual" if manual else ("model" if visual_usable else "rule"),
                "status": "active", "processing_version": PROCESSING_VERSION,
            })
            db.replace_segment_tags(segment_id, classified["tags"])
            primary_results.append(classified)
        best = max(primary_results, key=lambda item: item["confidence"])
        state = "ready" if all(item["decision"] != "review_required" for item in primary_results) else "review_required"
        db.deactivate_asset_segments_except_version(asset["id"], PROCESSING_VERSION)
        warning = "; ".join(dict.fromkeys(visual_errors))[:1_000] or None
        db.update_asset_processing_job(job_id, status="succeeded", stage=state, progress=100, error=warning)
        db.update_asset_semantic_state(asset["id"], best["primary_category"], state,
                                       source="manual" if asset.get("primary_category_source") == "manual" else "model")
        # 热点视频的 Hook 选择由热点入库层的内置策展模型完成。此处只做原子镜头
        # 分析，不能再按地点/关键词规则把整条母片机械切成若干“待确认事件”。
        return {"job_id": job_id, "asset_id": asset["id"], "status": "succeeded", "stage": state,
                "segments": len(plan), "primary_category": best["primary_category"]}
    except Exception as exc:
        db.update_asset_processing_job(job_id, status="failed", stage="failed", error=str(exc)[:500])
        raise


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
