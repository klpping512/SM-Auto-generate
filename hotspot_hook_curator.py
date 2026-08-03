"""用项目内置模型把已分析热点母片收敛为少量可复用 Hook 片段。

本模块不抓取、不下载，也不替内容模型决定“哪条热点适合某个选题”。它只在一条
已经入库并完成 ASR/OCR/视觉分析的母片内，选择 2–3 个有明确画面事实的短片段，
并把选择理由与物流切入点写回素材库，供后续内容规划模型调用。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Iterable

import model_router
import hotspot_hook_selection_sop
import hotspot_lexicon


MIN_HOOK_MS = 4_000
MAX_HOOK_MS = 14_000
MAX_HOOKS_PER_SOURCE = 3
# 全量镜头分析可以覆盖长母片；策展提示词则必须把每段证据压缩到模型预算内，
# 否则“已分析”会在生成标题与事件说明前被输入门禁拦下。
PROMPT_VERSION = "hotspot-hook-curation-v6"
AUDIT_PROMPT_VERSION = "hotspot-hook-grounding-audit-v3"


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
        "你是热点视频 Hook 素材策展模型。只从给定的已分析镜头中选择 1–3 段，"
        "为之后的物流短视频提供开场注意力钩子。不要决定最终选题、不要替 Buffalo 编造服务能力，"
        "不要选择主播空镜、标题页、泛泛地图或无法说明发生何事的镜头。\n"
        "所有返回的 Hook 必须属于同一个具体事件、同一地点/事故/处置过程，绝不能从新闻合集拼接两个不同事件。"
        "每个 Hook 必须：1) 由连续镜头组成；2) 总时长 4–14 秒；3) 用证据描述画面里发生的事；"
        "4) 说明它为何能吸引停留；5) 给出一个谨慎的“物流切入问题”，只能提问或解释影响，不能声称 Buffalo 已解决。\n"
        "母片标题和本轮获准的事件范围优先级高于镜头猜测；若母片是多事件合集，只能选择能直接对应"
        "本轮事件范围的镜头，不能因为另一段也有道路、车辆或冲击力就混入。若镜头描述与事件范围冲突，"
        "或不能确定画面正在呈现该事件，必须返回空数组。不得把垃圾、路人或普通物品说成包裹、货物或订单。"
        "若没有满足条件的 Hook，返回空数组。严格返回单行 JSON："
        "{\"hooks\":[{\"event_identity\":\"不超过48字、同一事件的稳定标识\",\"start_segment_index\":0,\"end_segment_index\":1,"
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
        "hook_selection_sop": hotspot_hook_selection_sop.policy_contract(),
        "source_title": str(source_title or ""),
        "source_context": str(source_context or ""),
        "hooks": [
            {
                "event_index": hook["event_index"],
                "what_happened": hook["evidence"]["what_happened"],
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
    candidates = [
        {
            "candidate_index": hook["event_index"],
            "what_happened": hook["evidence"]["what_happened"],
            "hook_reason": hook["evidence"]["hook_reason"],
            "segments": [_compact_segment(segment) for segment in hook["segments"]],
        }
        for hook in hooks
    ]
    return (
        "你是物流热点素材库的事实核验模型，不负责补救或润色。母片标题是已验证事件事实："
        f"{source_title[:240] or '未提供'}。逐条核验候选 Hook：仅在以下全部成立时才接受："
        "（1）候选的画面说明与母片事件不矛盾；（2）画面说明可由给出的镜头证据直接支持；"
        "（3）没有把垃圾、普通袋子、路人或未知物品臆断为包裹、货物、订单或物流作业；"
        "（4）对于合集母片，候选必须属于本轮获准事件范围，不能混用同一视频的其他独立新闻事件。"
        "任何不确定、矛盾、依赖猜测或只是主播/字幕画面的候选都必须拒绝。"
        "严格返回单行 JSON：{\"accepted\":[{\"candidate_index\":1,\"reason\":\"不超过80字\"}]}。"
        f"本轮获准事件范围：{source_context[:1200] or '仅可使用与母片标题直接一致的镜头'}\n"
        f"后端 Hook 选择 SOP：{json.dumps(hotspot_hook_selection_sop.policy_contract(), ensure_ascii=False)}\n"
        f"候选：{json.dumps(candidates, ensure_ascii=False)}"
    )


def _audit_hooks(asset_id: int, source_title: str, source_context: str, hooks: list[dict]) -> tuple[list[dict], dict]:
    if not hooks:
        return [], {"status": "nothing_to_audit", "accepted_count": 0}
    job_id = model_router.route_scoped_job_id(_audit_job_id(asset_id, source_title, source_context, hooks), "critic")
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=10_000,
        max_output_tokens=model_router.required_output_budget("critic", 400),
    )
    result = asyncio.run(model_router.call_text(
        job_id,
        "critic",
        [
            {"role": "system", "content": "严格返回 JSON。宁可拒绝，也不能依据镜头外推断接受候选。"},
            {"role": "user", "content": _audit_prompt(source_title, source_context, hooks)},
        ],
        prompt_version=AUDIT_PROMPT_VERSION,
        max_output_tokens=400,
    ))
    raw = str(result.get("content") or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
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
    accepted = [item for item in hooks if int(item["event_index"]) in accepted_indexes]
    return accepted, {
        "status": "verified" if accepted else "rejected_all",
        "accepted_count": len(accepted),
        "model": model_router.get_route("critic").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
    }


def _parse(content: str, segments: list[dict]) -> list[dict]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
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
    event_identity = ""
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
        if not 0.45 <= confidence <= 1:
            continue
        if event_identity and candidate_identity.casefold() != event_identity.casefold():
            continue
        event_identity = candidate_identity
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
            "confidence": round(confidence, 3),
            "review_status": "confirmed",
            "segments": selected,
            "evidence": {
                "what_happened": happened,
                "hook_reason": reason,
                "logistics_question": question,
                "curator": "planner_text",
                "hook_sop_id": hotspot_hook_selection_sop.SOP_ID,
                "hook_sop_version": hotspot_hook_selection_sop.SOP_VERSION,
                "event_identity": event_identity,
                "selected_segment_indexes": indexes,
            },
        })
        if len(result) >= MAX_HOOKS_PER_SOURCE:
            break
    return result


def curate_hook_clips(
    asset_id: int,
    source_title: str,
    segments: Iterable[dict],
    source_context: str = "",
) -> tuple[list[dict], dict]:
    """由内置 Qwen 从母片已分析镜头中作 Hook 决策；无模型或无有效片段时不入库。"""
    ordered = sorted((dict(item) for item in segments), key=lambda item: int(item.get("segment_index") or 0))
    if not ordered:
        return [], {"status": "no_segments", "reason": "母片没有可供策展的已分析镜头"}
    if not model_router.key_is_available("planner_text") or not model_router.key_is_available("critic"):
        return [], {"status": "model_unavailable", "reason": "内置 Hook 策展模型未配置"}
    job_id = model_router.route_scoped_job_id(
        _curation_job_id(int(asset_id), source_title, source_context, ordered), "planner_text"
    )
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=14_000,
        max_output_tokens=model_router.required_output_budget("planner_text", 1_000),
    )
    result = asyncio.run(model_router.call_text(
        job_id,
        "planner_text",
        [
            {"role": "system", "content": "严格返回 JSON，不要 Markdown，不得补充镜头外事实。"},
            {"role": "user", "content": _prompt(source_title, source_context, ordered)},
        ],
        prompt_version=PROMPT_VERSION,
        max_output_tokens=1_000,
    ))
    hooks = _parse(result["content"], ordered)
    if not hooks:
        return [], {
            "status": "no_qualified_hooks",
            "model": model_router.get_route("planner_text").get("model"),
            "cache_hit": bool(result.get("cache_hit")),
            "hook_count": 0,
        }
    hooks, audit = _audit_hooks(int(asset_id), source_title, source_context, hooks)
    return hooks, {
        "status": "curated" if hooks else "no_qualified_hooks",
        "model": model_router.get_route("planner_text").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
        "hook_count": len(hooks),
        "grounding_audit": audit,
        "hook_selection_sop": {
            "sop_id": hotspot_hook_selection_sop.SOP_ID,
            "sop_version": hotspot_hook_selection_sop.SOP_VERSION,
        },
    }
