"""用项目内置模型把已分析热点母片收敛为少量可复用 Hook 片段。

本模块不抓取、不下载，也不替内容模型决定“哪条热点适合某个选题”。它只在一条
已经入库并完成 ASR/OCR/视觉分析的母片内，选择 2–3 个有明确画面事实的短片段，
并把选择理由与物流切入点写回素材库，供后续内容规划模型调用。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Iterable

from pathlib import Path

import model_router
import hotspot_hook_selection_sop
import hotspot_hook_visual_audit
import hotspot_lexicon
from database import add_hook_curation_diagnostic

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _balanced_json_substring(text: str) -> str | None:
    """Return the first balanced `{...}` or `[...]` substring, or None."""
    start = -1
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            break
    if start < 0:
        return None
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escape = False
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
                return text[start : index + 1]
    return None


def _extract_json(raw: str) -> Any:
    """Strip fences/think blocks and load the first JSON object or array."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    text = _THINK_BLOCK_RE.sub("", text).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    extracted = _balanced_json_substring(text)
    if extracted is None:
        raise ValueError("未返回合法 JSON")
    try:
        return json.loads(extracted)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("未返回合法 JSON") from exc


MIN_HOOK_MS = 4_000
MAX_HOOK_MS = 14_000
MAX_HOOKS_PER_SOURCE = 3
MIN_HOOK_CONFIDENCE = 0.35
# 全量镜头分析可以覆盖长母片；策展提示词则必须把每段证据压缩到模型预算内，
# 否则“已分析”会在生成标题与事件说明前被输入门禁拦下。
PROMPT_VERSION = "hotspot-hook-curation-v8-empty-repair"
AUDIT_PROMPT_VERSION = "hotspot-hook-grounding-audit-v5"


def _derive_hook_keywords(fact_text: str) -> list[str]:
    # Grounded only in what_happened/title_zh — never the model's invented
    # logistics_question bridge sentence. Taxonomy lives in hotspot_lexicon.
    keywords = sorted(hotspot_lexicon.category_profile(fact_text, mode="event"))
    return keywords or ["hotspot"]


def _compact_segment(segment: dict) -> dict:
    raw_tags = segment.get("tags") or []
    # composition 维度单独取出，不占用下面 tags[:3] 的展示名额——渲染层会把
    # 每个镜头居中裁切成 9:16，只保留正中约三分之一宽度，这条信号直接影响
    # 策展模型该不该选这段镜头，不能被其它标签挤掉。
    edge_risk = next(
        (str(tag.get("value") or "").replace("edge_risk_", "")
         for tag in raw_tags if str(tag.get("dimension") or "") == "composition"),
        "none",
    )
    tags = [
        f"{str(tag.get('dimension') or '')[:16]}:{str(tag.get('value') or '')[:28]}"
        for tag in raw_tags
        if str(tag.get("value") or "").strip() and str(tag.get("dimension") or "") != "composition"
    ]
    description = str(segment.get("description") or "").strip()
    # asset_processing 会把母片标题拼到每段 description 开头；标题已经单独
    # 放进提示词，不能让它挤占每个镜头的可见动作证据。
    asset_name = str(segment.get("asset_name") or "").strip()
    if asset_name and description.startswith(asset_name):
        description = description[len(asset_name):].lstrip(" ：:-，,。")
    return {
        "segment_index": int(segment.get("segment_index") or 0),
        "start_ms": int(segment.get("start_ms") or 0),
        "end_ms": int(segment.get("end_ms") or 0),
        # 长合集最多有近百个镜头；这里保留可核验的画面、语音、OCR和标签摘要，
        # 不把同一事实的冗长转写重复塞给策展模型。
        "description": description[:80],
        "transcript": str(segment.get("transcript") or "")[:60],
        "ocr": str(segment.get("ocr_text") or "")[:30],
        "tags": tags[:3],
        "edge_risk": edge_risk,
    }


def _prompt(source_title: str, source_context: str, segments: list[dict]) -> str:
    evidence = json.dumps([_compact_segment(item) for item in segments], ensure_ascii=False)
    sop = hotspot_hook_selection_sop.policy_contract()
    return (
        "你是热点视频 Hook 素材策展模型。只从给定的已分析镜头中选择 1–3 段可复用现场画面，"
        "优先物流向（道路、港口、仓储、清关、配送）；若母片是体育、综合日更或政务现场，"
        "只要画面事实可核验也可保留。不要决定最终选题、不要替 Buffalo 编造服务能力，"
        "不要选择主播空镜、标题页、泛泛地图或无法说明发生何事的镜头。\n"
        "每个 Hook 必须：1) 由连续镜头组成；2) 总时长 4–14 秒；3) 用证据描述画面里发生的事；"
        "4) 说明它为何能吸引停留；5) 给出谨慎的 logistics_question（物流向画面给直接切入，"
        "非物流现场可用弱关联条件式问题），不能声称 Buffalo 已解决或已介入。\n"
        "母片标题和本轮获准的事件范围优先级高于镜头猜测。新闻合集（多事件合集）允许最多 3 条"
        "不同 event_identity，每条仍须对应标题范围内可核验的独立现场，镜头不重叠。"
        "若镜头描述与事件范围冲突，或不能确定画面正在呈现该事件，跳过该候选。"
        "不得把垃圾、路人或普通物品说成包裹、货物或订单。"
        "若没有满足条件的 Hook，返回空数组。严格返回单行 JSON："
        "{\"hooks\":[{\"event_identity\":\"不超过48字、该候选事件的稳定标识\",\"start_segment_index\":0,\"end_segment_index\":1,"
        "\"title_zh\":\"不超过32字\",\"what_happened\":\"不超过120字的可核验画面事实\","
        "\"hook_reason\":\"不超过100字\",\"logistics_question\":\"不超过100字\","
        "\"confidence\":0到1}]}。\n"
        f"母片标题：{source_title[:240] or '未提供'}\n"
        f"本轮获准事件范围：{source_context[:1200] or '仅可使用与母片标题直接一致的镜头'}\n"
        f"后端 Hook 选择 SOP：{json.dumps(sop, ensure_ascii=False)}\n"
        f"已分析镜头：{evidence}"
    )


def _curation_job_id(asset_id: int, source_title: str, source_context: str, segments: list[dict]) -> str:
    """Use the analysed evidence as the retry/cache identity, not just asset id.

    A source video may be re-analysed after a visual-model or prompt upgrade.  Reusing
    one exhausted database budget for all versions silently blocks a legitimate retry.
    """
    fingerprint = json.dumps({
        "prompt_version": PROMPT_VERSION,
        "hook_selection_sop": hotspot_hook_selection_sop.policy_contract(),
        "source_title": str(source_title or ""),
        "source_context": str(source_context or ""),
        "segments": [_compact_segment(item) for item in segments],
    }, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"hotspot-hook-curation-{int(asset_id)}-{digest}"


def _audit_job_id(asset_id: int, source_title: str, source_context: str, hooks: list[dict]) -> str:
    """Give each factual-audit pass an evidence-sensitive, retry-safe budget."""
    payload = {
        "prompt_version": AUDIT_PROMPT_VERSION,
        "hook_selection_sop": hotspot_hook_selection_sop.policy_contract(),
        "source_title": str(source_title or ""),
        "source_context": str(source_context or ""),
        "hooks": [
            {
                "event_index": hook["event_index"],
                "what_happened": hook["evidence"]["what_happened"],
                "visual_audit": (hook.get("evidence") or {}).get("visual_audit") or {},
                "segments": [_compact_segment(segment) for segment in hook["segments"]],
            }
            for hook in hooks
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"hotspot-hook-audit-{int(asset_id)}-{digest}"


def _audit_prompt(source_title: str, source_context: str, hooks: list[dict]) -> str:
    candidates = []
    for hook in hooks:
        visual = dict((hook.get("evidence") or {}).get("visual_audit") or {})
        candidates.append({
            "candidate_index": hook["event_index"],
            "title_zh": hook.get("title_zh"),
            "what_happened": hook["evidence"]["what_happened"],
            "hook_reason": hook["evidence"]["hook_reason"],
            "visual_scene_type": visual.get("scene_type"),
            "visual_objects": visual.get("visible_objects") or [],
            "visual_actions": visual.get("visible_actions") or [],
            "segments": [_compact_segment(segment) for segment in hook["segments"]],
        })
    return (
        "你是热点素材库的事实核验模型，不负责补救或润色。"
        "母片标题和来源摘要只是待核对的来源线索，不能证明候选帧中出现了对应地点、主体或动作。"
        f"来源线索标题：{source_title[:240] or '未提供'}。"
        "逐条核验候选 Hook：仅在以下全部成立时才接受："
        "（1）画面可见事实（visual_objects/visual_actions/scene_type）与 what_happened 不矛盾；"
        "（2）来源事件身份与画面可见事实不矛盾，但不能仅靠标题补足画面缺失的地点或动作；"
        "（3）没有把垃圾、普通袋子、路人或未知物品臆断为包裹、货物、订单或物流作业；"
        "（4）不是主播/字幕/标题页/Logo 卡。若视觉审核已标明标题卡或主播，必须拒绝。"
        "社会、体育、政务等非物流现场只要画面可核验也可接受，不要仅因缺少物流视觉就拒绝。"
        "新闻合集允许不同独立事件各保留一条。不确定、矛盾、依赖猜测的候选必须拒绝。"
        "严格返回单行 JSON：{\"accepted\":[{\"candidate_index\":1,\"reason\":\"不超过80字\"}]}。"
        f"本轮获准事件范围：{source_context[:1200] or '仅可使用与来源线索直接一致且画面可核验的镜头'}\n"
        f"后端 Hook 选择 SOP：{json.dumps(hotspot_hook_selection_sop.policy_contract(), ensure_ascii=False)}\n"
        f"候选：{json.dumps(candidates, ensure_ascii=False)}"
    )


def _audit_hooks(asset_id: int, source_title: str, source_context: str, hooks: list[dict]) -> tuple[list[dict], dict]:
    if not hooks:
        return [], {"status": "nothing_to_audit", "accepted_count": 0}
    job_id = model_router.route_scoped_job_id(_audit_job_id(asset_id, source_title, source_context, hooks), "critic")
    # reset=True: same mother re-curation reuses a deterministic job_id; sticky
    # INSERT OR IGNORE would otherwise keep exhausted calls_used and block retry.
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=10_000,
        max_output_tokens=model_router.required_output_budget("critic", 400),
        reset=True,
    )
    result = asyncio.run(model_router.call_text(
        job_id,
        "critic",
        [
            {
                "role": "system",
                "content": "严格返回 JSON。拒绝臆断与无画面支撑的候选，但接受与标题一致的非物流现场。",
            },
            {"role": "user", "content": _audit_prompt(source_title, source_context, hooks)},
        ],
        prompt_version=AUDIT_PROMPT_VERSION,
        max_output_tokens=400,
    ))
    try:
        payload = _extract_json(str(result.get("content") or ""))
    except ValueError as exc:
        raise ValueError("Hook 事实核验模型未返回合法 JSON") from exc
    if isinstance(payload, dict):
        accepted_rows = payload.get("accepted") or []
    elif isinstance(payload, list):
        # Some model responses omit the documented wrapper. The individual
        # acceptance records are still checked against candidate indexes below.
        accepted_rows = payload
    else:
        raise ValueError("Hook 事实核验模型返回了不支持的 JSON 顶层类型")
    allowed = {int(item["event_index"]): item for item in hooks}
    accepted_indexes: set[int] = set()
    for row in accepted_rows:
        try:
            candidate_index = int((row or {}).get("candidate_index"))
        except (AttributeError, TypeError, ValueError):
            continue
        if candidate_index in allowed:
            accepted_indexes.add(candidate_index)
    accepted = []
    for item in hooks:
        evidence = dict(item.get("evidence") or {})
        if int(item["event_index"]) in accepted_indexes:
            evidence["text_audit"] = {
                "status": "accepted",
                "prompt_version": AUDIT_PROMPT_VERSION,
            }
            item["evidence"] = evidence
            item["review_status"] = "confirmed"
            accepted.append(item)
        else:
            evidence["text_audit"] = {
                "status": "rejected",
                "prompt_version": AUDIT_PROMPT_VERSION,
            }
            item["evidence"] = evidence
            item["review_status"] = "review_required"
    return accepted, {
        "status": "verified" if accepted else "rejected_all",
        "accepted_count": len(accepted),
        "model": model_router.get_route("critic").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
        "prompt_version": AUDIT_PROMPT_VERSION,
    }


def _parse(content: str, segments: list[dict]) -> list[dict]:
    try:
        payload = _extract_json(content)
    except ValueError as exc:
        raise ValueError("Hook 策展模型未返回合法 JSON") from exc
    if isinstance(payload, dict):
        rows = payload.get("hooks") or []
    elif isinstance(payload, list):
        # Accept a bare candidate list, then apply the same duration, visual
        # evidence, overlap and factual-audit gates as the wrapped response.
        rows = payload
    else:
        raise ValueError("Hook 策展模型返回了不支持的 JSON 顶层类型")
    by_index = {int(item.get("segment_index") or 0): item for item in segments}
    result: list[dict] = []
    occupied: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            start_index = int(row.get("start_segment_index"))
            end_index = int(row.get("end_segment_index"))
            confidence = float(row.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        indexes = list(range(start_index, end_index + 1))
        selected = [by_index.get(index) for index in indexes]
        if not indexes or any(item is None for item in selected) or occupied.intersection(indexes):
            continue
        if hotspot_hook_selection_sop.obvious_rejection_reason(selected):
            continue
        start_ms = int(selected[0].get("start_ms") or 0)
        end_ms = int(selected[-1].get("end_ms") or 0)
        duration_ms = end_ms - start_ms
        title = str(row.get("title_zh") or "").strip()[:32]
        candidate_identity = str(row.get("event_identity") or "").strip()[:48]
        happened = str(row.get("what_happened") or "").strip()[:120]
        reason = str(row.get("hook_reason") or "").strip()[:100]
        question = str(row.get("logistics_question") or "").strip()[:100]
        if not (MIN_HOOK_MS <= duration_ms <= MAX_HOOK_MS and title and candidate_identity and happened and reason and question):
            continue
        if not MIN_HOOK_CONFIDENCE <= confidence <= 1:
            continue
        # 合集允许不同 event_identity；仅禁止镜头重叠，总量仍受 MAX_HOOKS_PER_SOURCE 约束。
        occupied.update(indexes)
        result.append({
            "event_index": len(result) + 1,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "title_zh": title,
            "title_en": title,
            "location": None,
            "entities": [],
            "keywords": _derive_hook_keywords(f"{happened} {title}"),
            "logistics_scenes": _derive_hook_keywords(f"{happened} {title}"),
            "hook_kind": "timely_event",
            "confidence": round(confidence, 3),
            # confirmed only after visual + text audits both accept.
            "review_status": "review_required",
            "segments": selected,
            "evidence": {
                "what_happened": happened,
                "hook_reason": reason,
                "logistics_question": question,
                "curator": "planner_text",
                "hook_sop_id": hotspot_hook_selection_sop.SOP_ID,
                "hook_sop_version": hotspot_hook_selection_sop.SOP_VERSION,
                "event_identity": candidate_identity,
                "selected_segment_indexes": indexes,
            },
        })
        if len(result) >= MAX_HOOKS_PER_SOURCE:
            break
    return result


def _has_safe_hook_window(segments: list[dict]) -> bool:
    """Return whether deterministic gates expose any 4–14s contiguous window.

    This never creates a Hook.  It only decides whether one empty model response
    deserves the workflow's single existing retry; the retry result still goes
    through ``_parse`` and the independent critic audit.
    """
    ordered = sorted(segments, key=lambda item: int(item.get("segment_index") or 0))
    for start_pos, first in enumerate(ordered):
        selected: list[dict] = []
        previous_index: int | None = None
        for item in ordered[start_pos:]:
            index = int(item.get("segment_index") or 0)
            if previous_index is not None and index != previous_index + 1:
                break
            selected.append(item)
            previous_index = index
            duration_ms = int(item.get("end_ms") or 0) - int(first.get("start_ms") or 0)
            if duration_ms > MAX_HOOK_MS:
                break
            if duration_ms >= MIN_HOOK_MS and not hotspot_hook_selection_sop.obvious_rejection_reason(selected):
                return True
    return False


def _empty_result_repair_instruction() -> str:
    return (
        "上一轮返回了空 hooks，但后端确定性门禁确认至少存在一个连续 4–14 秒、且不是纯主播/标题页/"
        "地图/Logo 墙的可用画面窗口。请仅重做一次选择：优先从 description、tags、transcript、OCR "
        "直接支持的可见动作或现场状态中选 1–3 条。若画面不能证明母片标题中的具体结论，"
        "event_identity、title_zh 和 what_happened 必须改写为中性的可见场景事实，不得照抄或扩写"
        "未被画面支持的实体、因果、数量或 Buffalo 服务能力。只有所有候选都确实不满足画面门禁时"
        "才再次返回空数组。仍按原 JSON schema 返回，不要解释。"
    )


def curate_hook_clips(
    asset_id: int,
    source_title: str,
    segments: Iterable[dict],
    source_context: str = "",
    *,
    static_root: Path | str | None = None,
    source_video_path: Path | str | None = None,
    asset_filepath: str | None = None,
) -> tuple[list[dict], dict]:
    """由内置策展模型从母片已分析镜头中作 Hook 决策；无模型或无有效片段时不入库。"""
    ordered = sorted((dict(item) for item in segments), key=lambda item: int(item.get("segment_index") or 0))
    if not ordered:
        return [], {"status": "no_segments", "reason": "母片没有可供策展的已分析镜头"}
    required_roles = ("planner_text", "critic", "hook_visual_critic")
    if not all(model_router.key_is_available(role) for role in required_roles):
        return [], {"status": "model_unavailable", "reason": "内置 Hook 策展或视觉审核模型未配置"}
    job_id = model_router.route_scoped_job_id(
        _curation_job_id(int(asset_id), source_title, source_context, ordered), "planner_text"
    )
    # max_calls=2 = 1 次初始策展 + 1 次坏 JSON 或过度保守空结果修复（同一策展尝试内）。
    # reset=True 保持"每次重跑=1 次完整尝试"语义；不得再往上放。
    per_call_output_budget = model_router.required_output_budget("planner_text", 1_000)
    model_router.create_budget(
        job_id, max_calls=2,
        # The router enforces cumulative job budgets.  Both the original call
        # and the single repair may consume the full per-call envelope.
        max_input_tokens=28_000,
        max_output_tokens=per_call_output_budget * 2,
        reset=True,
    )
    messages = [
        {"role": "system", "content": "严格返回 JSON，不要 Markdown，不得补充镜头外事实。"},
        {"role": "user", "content": _prompt(source_title, source_context, ordered)},
    ]
    route_model = (model_router.get_route("planner_text") or {}).get("model") or ""

    def _call(**overrides):
        return asyncio.run(model_router.call_text(
            job_id, "planner_text", messages,
            prompt_version=PROMPT_VERSION,
            max_output_tokens=1_000,
            **overrides,
        ))

    def _try_parse(result: dict, attempt: int) -> list[dict]:
        # 现场落库放在异常抛出路径，原始返回不丢
        try:
            return _parse(result.get("content") or "", ordered)
        except ValueError as exc:
            add_hook_curation_diagnostic(
                int(asset_id), attempt, PROMPT_VERSION,
                model=route_model,
                cache_hit=bool(result.get("cache_hit")),
                error=str(exc),
                raw_content=result.get("content") or "",
            )
            raise

    result = _call()
    retried_empty = False
    try:
        hooks = _try_parse(result, 1)
    except ValueError:
        # 一次性重试：必须绕过缓存，避免第一次坏返回原样复现。
        # 命中缓存时 budget 已记 1 次调用，max_calls=2 恰好容纳这次真调。
        result = _call(use_cache=False)
        hooks = _try_parse(result, 2)
    else:
        # A valid empty array is normally a legitimate rejection.  MiMo may,
        # however, over-apply the event-grounding clause and reject every frame
        # even when deterministic gates expose visible, non-anchor footage.
        # Spend the workflow's one existing retry on a neutral-scene repair;
        # never retry when all windows are deterministically unusable.
        if not hooks and _has_safe_hook_window(ordered):
            messages.append({"role": "user", "content": _empty_result_repair_instruction()})
            result = _call(use_cache=False)
            hooks = _try_parse(result, 2)
            retried_empty = True
    if not hooks:
        return [], {
            "status": "no_qualified_hooks",
            "model": model_router.get_route("planner_text").get("model"),
            "cache_hit": bool(result.get("cache_hit")),
            "hook_count": 0,
            "empty_result_retry": retried_empty,
        }
    hooks, visual_audit = hotspot_hook_visual_audit.audit_hooks(
        int(asset_id),
        hooks,
        static_root=static_root,
        source_video_path=source_video_path,
        asset_filepath=asset_filepath,
    )
    if visual_audit.get("status") == "model_unavailable":
        return [], {
            "status": "temporarily_unavailable",
            "reason": visual_audit.get("reason") or "视觉审核模型暂时不可用",
            "model": model_router.get_route("planner_text").get("model"),
            "cache_hit": bool(result.get("cache_hit")),
            "hook_count": 0,
            "empty_result_retry": retried_empty,
            "visual_audit": visual_audit,
        }
    if not hooks:
        return [], {
            "status": "no_qualified_hooks",
            "model": model_router.get_route("planner_text").get("model"),
            "cache_hit": bool(result.get("cache_hit")),
            "hook_count": 0,
            "empty_result_retry": retried_empty,
            "visual_audit": visual_audit,
        }
    hooks, audit = _audit_hooks(int(asset_id), source_title, source_context, hooks)
    return hooks, {
        "status": "curated" if hooks else "no_qualified_hooks",
        "model": model_router.get_route("planner_text").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
        "hook_count": len(hooks),
        "empty_result_retry": retried_empty,
        "visual_audit": visual_audit,
        "grounding_audit": audit,
        "hook_selection_sop": {
            "sop_id": hotspot_hook_selection_sop.SOP_ID,
            "sop_version": hotspot_hook_selection_sop.SOP_VERSION,
        },
    }
