"""Evidence-grounded two-stage video evaluation."""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import ValidationError

import model_router

from .schemas import VideoEvaluationReport


# 质检项目增加终片重复源/转场时间线门禁后变更版本，避免复用旧结论。
PROMPT_VERSION = "qwen-video-quality-v10"

logger = logging.getLogger(__name__)


class EvaluationResponseError(RuntimeError):
    pass


_RECOVERABLE_TEMPORAL_EVIDENCE_ERRORS = frozenset({
    "质检摘要把无技术候选支撑的冻结写为问题",
    "质检摘要把无技术候选支撑的镜头晃动写为问题",
})


def is_recoverable_temporal_evidence_error(error: Exception | str) -> bool:
    """Allow only known false temporal assertions to fall back to hard gates."""
    parts = {
        item.strip()
        for item in re.split(r"[；;]", str(error or ""))
        if item.strip()
    }
    return bool(parts) and parts.issubset(_RECOVERABLE_TEMPORAL_EVIDENCE_ERRORS)


REPORT_SHAPE = {
    "overall_score": "number 0-100",
    "passed": "boolean",
    "summary": "string",
    "technical_issues": [],
    "scores": {
        "prompt_alignment": "number 0-100",
        "visual_quality": "number 0-100",
        "character_consistency": "number 0-100",
        "product_consistency": "number 0-100",
        "temporal_consistency": "number 0-100",
        "motion_quality": "number 0-100",
        "camera_quality": "number 0-100",
        "subtitle_audio_quality": "number 0-100",
        "storytelling": "number 0-100",
        "platform_suitability": "number 0-100",
    },
    "issues": [{
        "start_second": "number",
        "end_second": "number",
        "severity": "low|medium|high",
        "category": "string",
        "description": "string",
        "evidence_frame": "one exact FRAME id supplied in the request",
        "suggested_fix": "string",
    }],
    "regeneration": {
        "required": "boolean",
        "revised_prompt": "string",
        "negative_prompt": "string",
        "storyboard_changes": [],
        "parameter_changes": {},
        "segments_to_regenerate": [],
    },
}


SYSTEM_PROMPT = """你是生成视频成片质检员。只返回一个 JSON 对象，不要 Markdown，不要解释。
必须依据提交的真实关键帧、帧 ID、时间戳、字幕和技术检测结果举证，禁止笼统评价。
每个 issues 项必须填写准确时间段，并从本次请求提供的 FRAME ID 中原样选择 evidence_frame；该帧时间必须落在问题时间窗内。请求中的 `valid_scene_numbers` 是唯一允许引用的分镜编号，不得把 FRAME 序号或时间戳误写成“场景号”，也不得在 regeneration 字段引用不存在的场景。
重点检查：提示词/分镜匹配、真实素材与旁白匹配、重复或静止画面、同一来源/时间范围疑似重复、人物和产品一致性、手脸变形、穿模、闪烁、跳帧、镜头突变、动作、文字/Logo、转场后的字幕音画同步、开头吸引力和平台适配。
如果指标不适用于真实素材，按可见证据正常评分，不得虚构人物或产品问题。
分镜中 `owned_context_image` 是特意安排的 1–2 秒自有图片过渡，`brand_cta` 是特意安排的品牌结尾静态图；它们不是冻结帧、重复画面或技术故障，不能因自身静态性质扣分。请求中的 `intentional_static_windows` 是这两类画面的实际时间窗：技术报告在这些窗内的 freeze 候选必须忽略，不能写进 issues、摘要或扣分。它们也不得被用来证明物流结果。
技术报告中的 silence/freeze 仅是机器候选，必须用实际分镜时间窗和关键帧复核后才能作为问题。`renderer_contract.subtitle_audio_sync.passed=true` 时，逐句字幕由同一段实测 TTS 直接烧录并已校验；不得把规划用的 `text_overlay` 与旁白比较后写成字幕不同步，只有关键帧能直接证明字幕损坏或错位时才可提出视觉问题。不得描述关键帧和分镜中不存在的物体、动作或场景；不确定时不列 issue。
冻结、静止或停帧属于时间序列问题：只有技术报告中存在重叠的 freeze 候选、且该候选不在 `intentional_static_windows` 内时，才可写入 technical_issues 或 issues。单张关键帧不能证明冻结，也不能证明车辆“没有移动”或动作中断；不得凭“画面看起来静止”扣分。“车辆排队/拥堵”描述的是可见的队列状态，并不表示车辆必须正在前进，不能用单张帧把它判为旁白冲突。若同一热点 Hook 连续承载“发生了什么→对用户意味着什么”的旁白，仍属于合理叙事；不得要求地图、数据图表或文字卡来强行过渡。
“镜头晃动、抖动、不稳定”同样属于时间序列问题：只有技术报告中有同一时间窗的稳定性/抖动候选时才能扣分或建议防抖。不能凭一张关键帧说“镜头有轻微晃动”。
不得建议地图、信息图、流程图、文字说明卡、PPT 卡片或任何解释卡；这些形式已被产品硬禁用。只可建议替换为未重复的真实热点 Hook、Buffalo 自有视频、自有图片或更克制的旁白。
总分低于 80 或存在 high 问题时 passed 必须为 false，regeneration.required 必须为 true。
输出必须紧凑：summary 不超过 120 个汉字；technical_issues、issues、storyboard_changes、segments_to_regenerate 各最多 3 项；parameter_changes 最多 5 个键。只保留最高严重度、最影响成片的结论，禁止逐镜头复述或给每一镜都写重做建议。无充分证据或无需重做时，数组必须为空。
严格结构如下：
""" + json.dumps(REPORT_SHAPE, ensure_ascii=False)


def _data_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _frame_id(index: int, timestamp: float) -> str:
    return f"FRAME_{index:04d}@{timestamp:.3f}s"


_SCENE_REFERENCE_RE = re.compile(r"(?:场景|镜头)\s*(\d+)(?:\s*(?:[-–—]|至|到)\s*(\d+))?", re.IGNORECASE)
_BANNED_REMEDIATION_TERMS = ("地图", "信息图", "流程图", "PPT", "文字说明卡", "文字卡")


def _scene_count(storyboard) -> int:
    return len(storyboard.get("scenes") or []) if isinstance(storyboard, dict) else 0


_MAX_EVALUATION_ITEMS = 3
_MAX_PARAMETER_CHANGES = 5
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def _bounded_model_items(value, limit: int, *, prioritize_severity: bool = False):
    """Keep model output within the public schema without changing its meaning.

    The model is instructed to return bounded arrays, but it can still emit a
    longer list. Selecting the most important entries at this boundary avoids
    converting a usable quality report into ``evaluation_status=unavailable``.
    Direct schema validation remains strict for all other callers.
    """
    if not isinstance(value, list) or len(value) <= limit:
        return value
    indexed = list(enumerate(value))
    if prioritize_severity:
        indexed.sort(key=lambda pair: (
            -_SEVERITY_RANK.get(
                str(pair[1].get("severity", "")).casefold()
                if isinstance(pair[1], dict)
                else "",
                0,
            ),
            -int(bool(pair[1].get("evidence_frame")))
            if isinstance(pair[1], dict)
            else 0,
            pair[0],
        ))
        selected_indexes = {index for index, _ in indexed[:limit]}
        return [item for index, item in enumerate(value) if index in selected_indexes]
    return value[:limit]


def normalize_evaluation_payload(
    payload: dict,
    *,
    job_id: str = "",
    review_stage: str = "",
) -> dict:
    """Normalize bounded arrays emitted by the model before strict validation.

    This is intentionally narrower than a schema relaxation: it only handles
    overlong arrays at the model boundary. Invalid item shapes, evidence,
    time windows, banned remediation, and the actual score/pass decision still
    go through the existing validators.
    """
    normalized = dict(payload)
    overflow: dict[str, int] = {}

    for field, prioritize in (
        ("technical_issues", True),
        ("issues", True),
    ):
        value = normalized.get(field)
        if isinstance(value, list) and len(value) > _MAX_EVALUATION_ITEMS:
            overflow[field] = len(value)
            normalized[field] = _bounded_model_items(
                value, _MAX_EVALUATION_ITEMS, prioritize_severity=prioritize,
            )

    regeneration = normalized.get("regeneration")
    if isinstance(regeneration, dict):
        regeneration = dict(regeneration)
        normalized["regeneration"] = regeneration
        for field in ("storyboard_changes", "segments_to_regenerate"):
            value = regeneration.get(field)
            if isinstance(value, list) and len(value) > _MAX_EVALUATION_ITEMS:
                overflow[f"regeneration.{field}"] = len(value)
                regeneration[field] = value[:_MAX_EVALUATION_ITEMS]
        parameter_changes = regeneration.get("parameter_changes")
        if isinstance(parameter_changes, dict) and len(parameter_changes) > _MAX_PARAMETER_CHANGES:
            overflow["regeneration.parameter_changes"] = len(parameter_changes)
            regeneration["parameter_changes"] = dict(
                list(parameter_changes.items())[:_MAX_PARAMETER_CHANGES]
            )

    if overflow:
        logger.warning(
            "质检模型输出超过有界契约，已在严格校验前归一化: job_id=%s stage=%s overflow=%s",
            job_id or "unknown",
            review_stage or "unknown",
            overflow,
        )
    return normalized


def _renderer_subtitle_sync_verified(storyboard) -> bool:
    if not isinstance(storyboard, dict):
        return False
    contract = storyboard.get("renderer_contract") or {}
    return bool(((contract.get("subtitle_audio_sync") or {}).get("passed")))


def _is_renderer_sync_only_subtitle_issue(issue) -> bool:
    """Return whether an issue contradicts only the renderer's measured sync."""
    category = str(
        issue.get("category") if isinstance(issue, dict) else getattr(issue, "category", "")
    ).casefold()
    if "subtitle" not in category:
        return False
    return any(term in category for term in (
        "audio", "sync", "mismatch", "alignment", "timing", "同步", "音画", "时序", "对齐",
    ))


def _static_overlap_seconds(start: float, end: float, windows: list[dict]) -> float:
    intervals = []
    for window in windows:
        left = max(start, float(window["start_second"]))
        right = min(end, float(window["end_second"]))
        if right > left:
            intervals.append((left, right))
    intervals.sort()
    merged: list[list[float]] = []
    for left, right in intervals:
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return sum(right - left for left, right in merged)


def _technical_static_candidates(technical_report: dict | None) -> list[dict]:
    """Return only machine-detected temporal candidates supplied to the evaluation model."""
    if not isinstance(technical_report, dict):
        return []
    candidates = []
    for item in technical_report.get("issues") or []:
        if not isinstance(item, dict) or not _mentions_static_candidate(item):
            continue
        try:
            start, end = float(item.get("start_second")), float(item.get("end_second"))
        except (TypeError, ValueError):
            continue
        if end > start:
            candidates.append({"start_second": start, "end_second": end})
    return candidates


def _is_supported_static_finding(item: dict, candidates: list[dict]) -> bool:
    """A freeze claim needs a matching temporal detector candidate, not one frame."""
    try:
        start, end = float(item.get("start_second")), float(item.get("end_second"))
    except (TypeError, ValueError):
        return False
    duration = max(0.0, end - start)
    if duration <= 0:
        return False
    for candidate in candidates:
        overlap = max(
            0.0,
            min(end, float(candidate["end_second"])) - max(start, float(candidate["start_second"])),
        )
        if overlap / duration >= 0.5:
            return True
    return False


def _mentions_static_candidate(item: dict) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("category", "description", "suggested_fix"))
    return any(term in text.casefold() for term in ("freeze", "冻结", "静止", "停帧", "intentional_static_windows"))


def _mentions_camera_motion(item: dict) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("category", "description", "suggested_fix"))
    normalized = text.casefold()
    return any(term in normalized for term in ("晃动", "抖动", "不稳定", "防抖", "shake", "jitter", "stabiliz"))


def _technical_camera_motion_candidates(technical_report: dict | None) -> list[dict]:
    if not isinstance(technical_report, dict):
        return []
    candidates = []
    for item in technical_report.get("issues") or []:
        if not isinstance(item, dict) or not _mentions_camera_motion(item):
            continue
        try:
            start, end = float(item.get("start_second")), float(item.get("end_second"))
        except (TypeError, ValueError):
            continue
        if end > start:
            candidates.append({"start_second": start, "end_second": end})
    return candidates


def _is_keyframe_only_motion_inference(issue) -> bool:
    """A still image cannot disprove a queue/congestion narration or motion."""
    text = " ".join(str(getattr(issue, key, "") or "") for key in ("category", "description", "suggested_fix"))
    normalized = text.casefold()
    queue_state = any(term in normalized for term in ("排队", "拥堵", "等待", "滞留"))
    motion_claim = any(term in normalized for term in ("未移动", "没有移动", "停止移动", "静止", "停住", "动作中断"))
    return queue_state and motion_claim


def _is_unsupported_camera_motion_issue(item: dict, candidates: list[dict]) -> bool:
    """Camera shake is temporal and needs a matching detector window too."""
    if not _mentions_camera_motion(item):
        return False
    return not _is_supported_static_finding(item, candidates)


def _summary_invents_freeze_without_candidate(summary: str, candidates: list[dict]) -> bool:
    """Do not let a fabricated freeze claim survive only in the summary."""
    if candidates:
        return False
    text = str(summary or "").casefold()
    return bool(re.search(r"(?:存在|出现|发现|有).{0,8}(?:技术)?(?:冻结|停帧)|(?:冻结|停帧).{0,8}(?:问题|现象)", text))


def _summary_invents_camera_motion_without_candidate(summary: str, candidates: list[dict]) -> bool:
    if candidates:
        return False
    text = str(summary or "").casefold()
    return bool(re.search(r"(?:镜头|画面).{0,8}(?:晃动|抖动|不稳定)|(?:晃动|抖动|不稳定).{0,8}(?:问题|影响)", text))


def _non_intentional_static_candidates(candidates: list[dict], static_windows: list[dict]) -> list[dict]:
    """Exclude detector hits that are wholly explained by planned endcards."""
    retained = []
    for candidate in candidates:
        try:
            start, end = float(candidate["start_second"]), float(candidate["end_second"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = max(0.0, end - start)
        if duration and _static_overlap_seconds(start, end, static_windows) / duration >= 0.8:
            continue
        retained.append(candidate)
    return retained


def _regeneration_invents_static_fix(regeneration: dict, candidates: list[dict], static_windows: list[dict]) -> bool:
    """A CTA's intentional still frame must not be proposed as a defect to fix."""
    if _non_intentional_static_candidates(candidates, static_windows):
        return False
    changes = regeneration.get("storyboard_changes") if isinstance(regeneration, dict) else []
    text = json.dumps(changes or [], ensure_ascii=False).casefold()
    return bool(re.search(r"(?:冻结|停帧)|(?:避免|修复|减少).{0,12}静止", text))


def _frame_timestamp(frame_id: str) -> float | None:
    match = re.search(r"@([0-9.]+)s$", str(frame_id or ""))
    return float(match.group(1)) if match else None


def _validate_scene_reference_text(text: str, scene_count: int) -> None:
    if scene_count <= 0:
        return
    for match in _SCENE_REFERENCE_RE.finditer(str(text or "")):
        for raw in match.groups():
            if raw is not None and not 1 <= int(raw) <= scene_count:
                raise EvaluationResponseError(
                    f"质检结果引用不存在的场景 {raw}；本次仅有 1–{scene_count} 场景"
                )


def _validate_structured_scene_references(value, scene_count: int) -> None:
    if scene_count <= 0:
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in {"scene", "scene_id", "scene_number"}:
                try:
                    number = int(nested)
                except (TypeError, ValueError):
                    _validate_scene_reference_text(str(nested or ""), scene_count)
                else:
                    if not 1 <= number <= scene_count:
                        raise EvaluationResponseError(
                            f"质检再生成计划引用不存在的场景 {number}；本次仅有 1–{scene_count} 场景"
                        )
            else:
                _validate_structured_scene_references(nested, scene_count)
    elif isinstance(value, list):
        for nested in value:
            _validate_structured_scene_references(nested, scene_count)
    elif isinstance(value, str):
        _validate_scene_reference_text(value, scene_count)


def intentional_static_windows(storyboard) -> list[dict]:
    """Return actual render windows for planned static context and CTA imagery."""
    if not isinstance(storyboard, dict):
        return []
    scenes = storyboard.get("scenes") or []
    timeline = storyboard.get("render_timeline") or []
    by_scene = {
        int(item.get("scene")): item
        for item in timeline if isinstance(item, dict) and str(item.get("scene") or "").isdigit()
    }
    windows = []
    cursor = 0.0
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        duration = max(0.0, float(scene.get("duration") or scene.get("duration_ms", 0) / 1000 or 0))
        window = by_scene.get(index) or {"start": cursor, "end": cursor + duration}
        start, end = float(window.get("start") or 0), float(window.get("end") or 0)
        cursor = end
        evidence_type = str(scene.get("evidence_type") or "")
        role = str(scene.get("scene_role") or "")
        if evidence_type in {"image", "brand_endcard"} or role in {"owned_context_image", "brand_cta"}:
            windows.append({
                "scene": index,
                "start_second": round(start, 3),
                "end_second": round(end, 3),
                "kind": "brand_cta" if evidence_type == "brand_endcard" or role == "brand_cta" else "owned_context_image",
            })
    return windows


def build_evaluation_messages(
    *,
    original_prompt: str,
    storyboard,
    target_platform: str,
    technical_report: dict,
    transcript_segments: list[dict],
    frames: list[dict],
    reference_images: list[str],
    review_stage: str,
) -> tuple[list[dict], list[dict]]:
    frame_index = []
    content: list[dict] = [{
        "type": "text",
        "text": json.dumps(
            {
                "review_stage": review_stage,
                "original_prompt": original_prompt,
                "storyboard": storyboard,
                "target_platform": target_platform,
                "technical_report": technical_report,
                "intentional_static_windows": intentional_static_windows(storyboard),
                "valid_scene_numbers": list(range(1, _scene_count(storyboard) + 1)),
                "renderer_contract": (storyboard or {}).get("renderer_contract") if isinstance(storyboard, dict) else {},
                "transcript": transcript_segments,
                "instruction": "逐帧核对并按系统指定结构返回 JSON。",
            },
            ensure_ascii=False,
        ),
    }]
    for reference_index, source in enumerate(reference_images[:10], 1):
        path = Path(source).expanduser().resolve()
        if not path.exists() or not path.is_file():
            continue
        content += [
            {"type": "text", "text": f"REFERENCE_{reference_index:03d}｜参考图，不可作为问题时间证据"},
            {"type": "image_url", "image_url": {"url": _data_url(path)}},
        ]
    for index, frame in enumerate(frames, 1):
        path = Path(frame["path"]).resolve()
        timestamp = float(frame["timestamp_seconds"])
        identifier = str(frame.get("frame_id") or _frame_id(index, timestamp))
        frame_index.append({
            "frame_id": identifier,
            "timestamp_seconds": round(timestamp, 3),
            "path": str(path),
            "reason": str(frame.get("reason") or "unknown"),
        })
        content += [
            {"type": "text", "text": f"{identifier}｜{frame.get('reason') or 'unknown'}"},
            {"type": "image_url", "image_url": {"url": _data_url(path)}},
        ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ], frame_index


def parse_json_content(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < start:
        raise EvaluationResponseError("评估模型没有返回 JSON 对象")
    candidate = stripped[start:end + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # 评估模型偶尔会在最后一个内部对象正常闭合后漏掉最外层 `}`。
        # 只补齐可由括号栈严格推导的容器闭合符；绝不猜测字段、字符串或值，
        # 后续的 Pydantic 和证据校验仍会拒绝任何语义不完整的结论。
        repaired = _close_unclosed_json_containers(candidate)
        if repaired is None:
            raise EvaluationResponseError(f"评估模型 JSON 无法解析：{exc.msg}") from exc
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError as repaired_exc:
            raise EvaluationResponseError(
                f"评估模型 JSON 无法解析：{repaired_exc.msg}"
            ) from repaired_exc
    if not isinstance(value, dict):
        raise EvaluationResponseError("评估模型返回值不是 JSON 对象")
    return value


def _close_unclosed_json_containers(candidate: str) -> str | None:
    """Safely append only deterministically missing `]` / `}` characters."""
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append(pairs[char])
        elif char in "}]":
            if not stack or char != stack.pop():
                return None
    if in_string or not stack:
        return None
    return candidate + "".join(reversed(stack))


def _validate_evidence(
    report: VideoEvaluationReport,
    duration: float | None,
    storyboard=None,
    technical_report: dict | None = None,
) -> None:
    allowed = {item["frame_id"] for item in report.frame_index}
    by_frame_number = {
        item["frame_id"].split("@", 1)[0]: item["frame_id"]
        for item in report.frame_index
        if "@" in str(item.get("frame_id") or "")
    }
    errors: list[str] = []
    for issue in report.issues:
        # The evaluation model can preserve the supplied FRAME_0007 index while re-estimating
        # its timestamp from the visual.  The index still identifies the exact
        # submitted image, so canonicalize only that stable prefix; never accept
        # an unknown frame number or a loose timestamp-only match.
        provided = str(issue.evidence_frame or "")
        prefix = provided.split("@", 1)[0]
        if provided not in allowed and prefix in by_frame_number:
            issue.evidence_frame = by_frame_number[prefix]
        if allowed and issue.evidence_frame not in allowed:
            errors.append(f"问题证据帧不在提交索引中：{issue.evidence_frame or '空'}")
        frame_timestamp = _frame_timestamp(issue.evidence_frame)
        if frame_timestamp is not None and not issue.start_second - 0.6 <= frame_timestamp <= issue.end_second + 0.6:
            # The evaluation model may preserve a real FRAME number but reuse an estimated
            # scene timestamp. The frame is the authoritative visual evidence,
            # so retain the issue while anchoring its narrow review window there.
            padding = 0.5
            issue.start_second = round(max(0.0, frame_timestamp - padding), 3)
            issue.end_second = round(
                min(float(duration) if duration is not None else frame_timestamp + padding, frame_timestamp + padding),
                3,
            )
        if duration is not None and issue.end_second > duration + 0.5:
            errors.append("问题时间段超出视频时长")
        try:
            _validate_scene_reference_text(issue.description, _scene_count(storyboard))
            _validate_scene_reference_text(issue.suggested_fix, _scene_count(storyboard))
        except EvaluationResponseError as exc:
            errors.append(str(exc))
        if any(term.casefold() in issue.suggested_fix.casefold() for term in _BANNED_REMEDIATION_TERMS):
            errors.append("质检建议包含产品禁用的信息图或文字卡形式")
        if "intentional_static_windows" in " ".join((issue.description, issue.suggested_fix)):
            errors.append("质检结果把预期静态图片窗口写为问题")
        if _renderer_subtitle_sync_verified(storyboard) and _is_renderer_sync_only_subtitle_issue(issue):
            errors.append("质检结果与已验证的渲染字幕音画契约冲突")
        if _is_keyframe_only_motion_inference(issue):
            errors.append("质检结果用单张关键帧推断排队或拥堵旁白的运动状态")

    scene_count = _scene_count(storyboard)
    regeneration = report.regeneration.model_dump()
    try:
        _validate_structured_scene_references(regeneration, scene_count)
    except EvaluationResponseError as exc:
        errors.append(str(exc))
    for value in (regeneration.get("revised_prompt"), regeneration.get("negative_prompt")):
        try:
            _validate_scene_reference_text(str(value or ""), scene_count)
        except EvaluationResponseError as exc:
            errors.append(str(exc))

    static_windows = intentional_static_windows(storyboard)
    static_candidates = _technical_static_candidates(technical_report)
    non_intentional_static_candidates = _non_intentional_static_candidates(static_candidates, static_windows)
    camera_motion_candidates = _technical_camera_motion_candidates(technical_report)
    if _summary_invents_freeze_without_candidate(report.summary, non_intentional_static_candidates):
        errors.append("质检摘要把无技术候选支撑的冻结写为问题")
    if _summary_invents_camera_motion_without_candidate(report.summary, camera_motion_candidates):
        errors.append("质检摘要把无技术候选支撑的镜头晃动写为问题")
    if _regeneration_invents_static_fix(regeneration, static_candidates, static_windows):
        errors.append("质检再生成计划把预期静态图片窗口写为需要修复")
    for issue in report.issues:
        if _is_unsupported_camera_motion_issue(issue.model_dump(), camera_motion_candidates):
            errors.append("质检结果把无技术候选支撑的镜头晃动写为问题")
    for item in [*report.technical_issues, *(entry.model_dump() for entry in report.issues)]:
        if not isinstance(item, dict) or not _mentions_static_candidate(item):
            continue
        try:
            start, end = float(item.get("start_second")), float(item.get("end_second"))
        except (TypeError, ValueError):
            continue
        candidate_duration = max(0.0, end - start)
        if candidate_duration and _static_overlap_seconds(start, end, static_windows) / candidate_duration >= 0.8:
            errors.append("质检结果把预期静态图片窗口写为 freeze 问题")
        elif not _is_supported_static_finding(item, static_candidates):
            errors.append("质检结果把无技术候选支撑的静态画面写为 freeze 问题")
        if _is_unsupported_camera_motion_issue(item, camera_motion_candidates):
            errors.append("质检结果把无技术候选支撑的镜头晃动写为问题")
    if errors:
        raise EvaluationResponseError("；".join(dict.fromkeys(errors)))


def _normalize_final_recoverable_output(
    report: VideoEvaluationReport,
    storyboard=None,
    technical_report: dict | None = None,
) -> bool:
    """Repair only deterministic policy conflicts left after the evaluation model's retry.

    The model occasionally repeats a freeze finding inside an explicitly static
    endcard window, or suggests a product-banned text card.  These are policy
    conflicts, not visual evidence.  Preserve all other findings and let the
    normal validator reject them.
    """
    changed = False
    removed_unsupported_temporal_claim = False
    static_windows = intentional_static_windows(storyboard)
    static_candidates = _technical_static_candidates(technical_report)
    camera_motion_candidates = _technical_camera_motion_candidates(technical_report)

    def is_expected_static(item: dict) -> bool:
        if not _mentions_static_candidate(item):
            return False
        try:
            start, end = float(item.get("start_second")), float(item.get("end_second"))
        except (TypeError, ValueError):
            return False
        duration = max(0.0, end - start)
        return bool(duration) and (
            _static_overlap_seconds(start, end, static_windows) / duration >= 0.8
            or not _is_supported_static_finding(item, static_candidates)
        )

    retained_technical = []
    for item in report.technical_issues:
        if isinstance(item, dict) and is_expected_static(item):
            changed = True
            removed_unsupported_temporal_claim = True
            continue
        if isinstance(item, dict) and _is_unsupported_camera_motion_issue(item, camera_motion_candidates):
            changed = True
            removed_unsupported_temporal_claim = True
            continue
        retained_technical.append(item)
    report.technical_issues = retained_technical

    retained_issues = []
    for issue in report.issues:
        item = issue.model_dump()
        if is_expected_static(item):
            changed = True
            removed_unsupported_temporal_claim = True
            continue
        if _is_unsupported_camera_motion_issue(item, camera_motion_candidates):
            changed = True
            removed_unsupported_temporal_claim = True
            continue
        if _renderer_subtitle_sync_verified(storyboard) and _is_renderer_sync_only_subtitle_issue(issue):
            changed = True
            continue
        if any(term.casefold() in issue.suggested_fix.casefold() for term in _BANNED_REMEDIATION_TERMS):
            issue.suggested_fix = "替换为未重复的真实热点 Hook 或 Buffalo 自有素材，并保持克制旁白。"
            changed = True
        retained_issues.append(issue)
    report.issues = retained_issues

    regeneration = report.regeneration
    retained_changes = []
    for item in regeneration.storyboard_changes:
        text = json.dumps(item, ensure_ascii=False).casefold()
        if any(term in text for term in ("冻结", "停帧", "静止", "晃动", "抖动", "防抖", "稳定")):
            changed = True
            removed_unsupported_temporal_claim = True
            continue
        retained_changes.append(item)
    regeneration.storyboard_changes = retained_changes

    # The model sometimes keeps its own unsupported freeze claim only in the
    # prose summary.  Once every such structured claim has been removed, leave
    # a bounded factual summary instead of making a technically sound render
    # fall back to manual review for an assertion it was not allowed to make.
    non_intentional_candidates = _non_intentional_static_candidates(
        static_candidates, static_windows,
    )
    if _summary_invents_freeze_without_candidate(report.summary, non_intentional_candidates):
        report.summary = "已忽略无时间序列证据支撑的冻结判断；其余问题以保留的分镜证据为准。"
        changed = True
        removed_unsupported_temporal_claim = True
    if _summary_invents_camera_motion_without_candidate(report.summary, camera_motion_candidates):
        report.summary = "已忽略无时间序列证据支撑的镜头晃动判断；其余问题以保留的分镜证据为准。"
        changed = True
        removed_unsupported_temporal_claim = True

    # A failed score is trustworthy only while it still has a supported issue
    # behind it.  If the false static claim was the sole reason for failure,
    # retain the renderer's technical gates and complete the semantic review;
    # never use this path when any non-static model finding remains.
    if removed_unsupported_temporal_claim and not report.technical_issues and not report.issues:
        report.overall_score = max(80.0, float(report.overall_score))
        report.passed = True
        report.summary = "未发现有充分证据支持的重大质量问题。"
        regeneration.required = False
        regeneration.revised_prompt = ""
        regeneration.negative_prompt = ""
        regeneration.storyboard_changes = []
        regeneration.parameter_changes = {}
        regeneration.segments_to_regenerate = []
        changed = True
    return changed


async def evaluate_video(
    *,
    job_id: str,
    original_prompt: str,
    storyboard,
    target_platform: str,
    technical_report: dict,
    transcript_status: str,
    transcript_segments: list[dict],
    frames: list[dict],
    reference_images: list[str],
    review_stage: str = "global",
    caller: Callable[..., Awaitable[dict]] | None = None,
) -> VideoEvaluationReport:
    messages, frame_index = build_evaluation_messages(
        original_prompt=original_prompt,
        storyboard=storyboard,
        target_platform=target_platform,
        technical_report=technical_report,
        transcript_segments=transcript_segments,
        frames=frames,
        reference_images=reference_images,
        review_stage=review_stage,
    )
    call = caller or model_router.call_multimodal_json
    last_error: Exception | None = None
    last_invalid_core: dict | None = None
    last_report: VideoEvaluationReport | None = None
    last_duration: float | None = None
    for attempt in range(2):
        attempt_messages = messages
        if attempt:
            correction = {
                "validation_error": str(last_error or "未知校验错误"),
                "invalid_core": last_invalid_core or {},
                "instruction": (
                    "只修正无效的场景号、时间窗、证据帧和禁用建议；"
                    "问题没有充分证据时直接删除。返回完整 JSON，不得引用 FRAME 序号为场景号。"
                    "所有问题和重做列表最多 3 项，禁止逐镜头展开。"
                ),
            }
            attempt_messages = [messages[0], {
                "role": "user",
                "content": [
                    *messages[1]["content"],
                    {"type": "text", "text": "上一次输出未通过校验，请按以下受限改写要求只返回完整 JSON："
                     + json.dumps(correction, ensure_ascii=False)},
                ],
            }]
        response = await call(
            job_id,
            "video_evaluator",
            attempt_messages,
            prompt_version=f"{PROMPT_VERSION}-{review_stage}-attempt-{attempt + 1}",
        )
        try:
            payload = parse_json_content(str(response.get("content") or ""))
            payload.update({
                "evaluation_status": "completed",
                "review_stage": review_stage,
                "frame_index": frame_index,
                "transcript_status": transcript_status,
            })
            payload = normalize_evaluation_payload(
                payload, job_id=job_id, review_stage=review_stage,
            )
            report = VideoEvaluationReport.model_validate(payload)
            duration_value = (technical_report.get("metadata") or {}).get("duration_seconds")
            last_report = report
            last_duration = float(duration_value) if duration_value is not None else None
            _validate_evidence(
                report,
                last_duration,
                storyboard,
                technical_report,
            )
            return report
        except (EvaluationResponseError, ValidationError, ValueError) as exc:
            if attempt == 1 and last_report is not None and _normalize_final_recoverable_output(
                last_report, storyboard, technical_report,
            ):
                try:
                    _validate_evidence(
                        last_report, last_duration,
                        storyboard, technical_report,
                    )
                    return last_report
                except (EvaluationResponseError, ValidationError, ValueError) as normalized_exc:
                    exc = normalized_exc
            last_error = exc
            if "payload" in locals() and isinstance(payload, dict):
                last_invalid_core = {
                    key: payload.get(key)
                    for key in ("overall_score", "passed", "summary", "technical_issues", "issues", "regeneration")
                    if key in payload
                }
    raise EvaluationResponseError(str(last_error or "评估模型质检结果校验失败"))
