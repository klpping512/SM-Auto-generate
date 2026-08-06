"""热点 Hook 库的入库前模型筛选。

定时器只负责找出已授权、尚未下载且足够长的候选；是否值得占用下载和分析资源，
由项目内的 Qwen 根据热点事实与媒体元数据作出决定。模型缺席时宁可不入库，不能用
关键词规则代替内容决策。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Iterable

import model_router
import hotspot_intake_sop
from database import add_hook_intake_diagnostic
# curator 不 import intake，无循环依赖；若未来反向依赖改为局部 import。
from hotspot_hook_curator import _extract_json


PROMPT_VERSION = "hotspot-hook-intake-v5"
# v3: download-stage audit now treats the collector-verified title/summary as
# the only available pre-download visual facts.  Bumping this version also
# creates a fresh cache and one-call budget for the changed decision contract.
AUDIT_PROMPT_VERSION = "hotspot-hook-intake-audit-v5"
MIN_CONFIDENCE = 0.55


def _candidate(media: dict, hotspot: dict) -> dict:
    # YouTube channel discovery only has a playlist title initially.  The
    # pre-download metadata hydrator records the single-video title/description
    # separately so a model decision is based on source facts, not a generic
    # collector placeholder or a guessed visual.
    source_title = str(media.get("intake_title") or "").strip()
    source_summary = str(media.get("intake_summary") or "").strip()
    return {
        "media_id": int(media["id"]),
        "duration_seconds": round(float(media.get("duration_seconds") or 0), 1),
        "platform": str(media.get("platform") or ""),
        "published_at": media.get("published_at") or hotspot.get("published_at"),
        "hotspot_title": (source_title or str(hotspot.get("title_zh") or hotspot.get("title") or ""))[:220],
        "hotspot_summary": (source_summary or str(hotspot.get("summary_zh") or hotspot.get("summary") or ""))[:600],
        "publisher": str(hotspot.get("publisher") or "")[:120],
    }


def _prompt(candidates: list[dict], maximum: int, sop: dict, targeted_requests: list[dict]) -> str:
    return (
        "你是部署在物流内容系统内的热点 Hook 入库决策模型。候选都是已获授权、可以下载的长视频。"
        "必须严格执行后端 SOP 与候选自带的 Buffalo RAG 证据；你只能从给出的证据 ID 中引用。"
        "可以选择 direct（视频本身是与 RAG 对应的物流现场）或 contextual（视频明确讲物流运行变化，"
        "再用 RAG 已证实动作提出应对问题）；contextual 不是泛泛联想，不能用于市政垃圾、治安、政治、"
        "娱乐等公共事务。RAG 仅证明 Buffalo 服务边界，绝不是候选视频的镜头证据；"
        "expected_hook 只能复述候选标题/摘要明确描述的画面，不能因 RAG 补出 Buffalo 标识、车辆、人员、"
        "条码或任何未在候选中出现的细节。"
        f"最多选 {maximum} 条；没有合适候选时返回空数组。严格返回单行 JSON："
        "{\"selections\":[{\"media_id\":1,\"admission_mode\":\"direct或contextual\",\"rag_evidence_ids\":[\"kb:1\"],"
        "\"service_fit\":\"不超过120字，只说明证据明确支持的 Buffalo 动作；contextual 不得宣称解决热点\","
        "\"expected_hook\":\"不超过100字的预期现场画面\","
        "\"why\":\"不超过120字\",\"logistics_question\":\"不超过100字的谨慎问题\",\"confidence\":0到1,\"target_request_ids\":[1]}]}。\n"
        f"后端SOP：{json.dumps(sop, ensure_ascii=False)}\n"
        "定向采集请求只用于排序：只有候选本身及 RAG 确实支持时才能选择；没有匹配则保留空数组。"
        "若一条候选确实服务某个请求，在对应 selection 写 target_request_ids（只能引用给定 id）。\n"
        f"定向采集请求：{json.dumps(targeted_requests, ensure_ascii=False)}\n"
        f"候选：{json.dumps(candidates, ensure_ascii=False)}"
    )


def _audit_prompt(candidates: list[dict], selections: list[dict], sop: dict) -> str:
    return (
        "你是热点下载前的 Buffalo RAG 事实审计模型。只审计，不补写服务能力。"
        "候选中的标题与摘要均已由采集链路核验，下载前必须将它们作为候选视频唯一可信的镜头事实；"
        "不得因视频尚未下载而要求额外画面证明，也不得把 RAG 当作镜头事实。"
        "逐条作四项客观检查：(1)引用 RAG 证据直接支持 service_fit 中的 Buffalo 动作；"
        "(2)direct 必须与视频事实直接对应；contextual 必须由视频事实明确呈现物流运行、运输、货运、"
        "跨境、港口、道路、仓配或电商履约变化，且热点只提出问题、不声称 Buffalo 的结果；"
        "(3)expected_hook 逐字语义上不超出标题/摘要明确事实；"
        "(4)不是市政垃圾/污水、治安、政治、娱乐等与 RAG 无明确覆盖的牵强公共事务。四项都通过时必须批准，"
        "不得仅因是下载前元数据、没有额外画面或没有第二份 RAG 而拒绝。RAG 仍不能单独证明"
        "品牌标识、车辆、人员、条码等镜头细节；这些细节必须已写在候选标题或摘要中。"
        "严格返回单行 JSON：{\"approved\":[{\"media_id\":1,\"reason\":\"不超过80字\"}],"
        "\"rejected\":[{\"media_id\":2,\"reason\":\"不超过80字，说明哪一项客观检查不通过\"}]}。\n"
        f"后端SOP：{json.dumps(sop, ensure_ascii=False)}\n"
        f"候选：{json.dumps(candidates, ensure_ascii=False)}\n"
        f"待审计决策：{json.dumps(selections, ensure_ascii=False)}"
    )


def _parse_selections(content: str, allowed: dict[int, dict], target_request_ids: set[int]) -> list[dict]:
    try:
        parsed = _extract_json(content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("热点入库模型未返回合法 JSON") from exc
    rows = parsed.get("selections") if isinstance(parsed, dict) else []
    selections: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            media_id = int(row.get("media_id"))
            confidence = float(row.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        candidate = allowed.get(media_id)
        if not candidate or confidence < MIN_CONFIDENCE:
            continue
        admission_mode = str(row.get("admission_mode") or "direct").strip().casefold()
        if admission_mode not in {"direct", "contextual"}:
            continue
        valid_evidence_ids = {str(item["id"]) for item in candidate.get("rag_evidence") or []}
        evidence_ids = [str(item).strip() for item in (row.get("rag_evidence_ids") or [])]
        evidence_ids = [item for item in evidence_ids if item in valid_evidence_ids]
        service_fit = str(row.get("service_fit") or "").strip()[:120]
        expected_hook = str(row.get("expected_hook") or "").strip()[:100]
        why = str(row.get("why") or "").strip()[:120]
        question = str(row.get("logistics_question") or "").strip()[:100]
        if not evidence_ids or not service_fit or not expected_hook or not why or not question:
            continue
        known_visual_text = " ".join(str(candidate.get(key) or "") for key in ("hotspot_title", "hotspot_summary")).casefold()
        expected_visual_text = expected_hook.casefold()
        # The RAG may say Buffalo owns a warehouse or has branded footage, but that
        # cannot prove an unrelated candidate video shows Buffalo branding.
        if "buffalo" in expected_visual_text and "buffalo" not in known_visual_text:
            continue
        if any(item["media_id"] == media_id for item in selections):
            continue
        selections.append({
            "media_id": media_id,
            "admission_mode": admission_mode,
            "rag_evidence_ids": evidence_ids,
            "service_fit": service_fit,
            "expected_hook": expected_hook,
            "why": why,
            "logistics_question": question,
            "confidence": round(min(confidence, 1), 3),
            "target_request_ids": [
                request_id for request_id in {
                    int(value) for value in (row.get("target_request_ids") or [])
                    if str(value).strip().isdigit()
                } if request_id in target_request_ids
            ],
        })
    return selections


def _parse_audit(content: str, allowed_ids: set[int]) -> dict[int, str]:
    try:
        parsed = _extract_json(content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("热点入库事实审计模型未返回合法 JSON") from exc
    rows = parsed.get("approved") if isinstance(parsed, dict) else []
    approved: dict[int, str] = {}
    for row in rows:
        try:
            media_id = int((row or {}).get("media_id"))
        except (AttributeError, TypeError, ValueError):
            continue
        reason = str((row or {}).get("reason") or "").strip()[:80]
        if media_id in allowed_ids and reason:
            approved[media_id] = reason
    return approved


def _call_with_json_retry(stage, job_id, role, prompt_version, messages, max_tokens, parse_fn):
    """调用 + 解析；JSON 解析失败时绕缓存（use_cache=False）真调一次。

    - 失败现场逐次写 hook_intake_diagnostics（attempt=1 初始 / attempt=2 重试）；
    - 两次都失败 → 照旧抛 ValueError（上游行为不变）；
    - 返回 (parsed, retried, result)。stage: 'selection' | 'audit'。
    """
    model = (model_router.get_route(role) or {}).get("model") or ""
    retried = False

    def _call(use_cache):
        return asyncio.run(model_router.call_text(
            job_id, role, messages,
            prompt_version=prompt_version,
            max_output_tokens=max_tokens,
            use_cache=use_cache,
        ))

    def _parse(result, attempt):
        content = result.get("content") or ""
        try:
            return parse_fn(content)
        except ValueError as exc:
            # 原始返回不丢；写库失败只记日志，绝不反噬决策。
            add_hook_intake_diagnostic(
                stage, job_id, attempt, prompt_version,
                model=model, cache_hit=bool(result.get("cache_hit")),
                error=str(exc), raw_content=content,
            )
            raise

    result = _call(use_cache=True)
    try:
        parsed = _parse(result, 1)
    except ValueError:
        # 一次性重试：必须绕过缓存，避免第一次坏返回原样复现。
        retried = True
        result = _call(use_cache=False)
        parsed = _parse(result, 2)
    return parsed, retried, result


def select_for_hook_ingestion(
    media_rows: Iterable[dict],
    hotspots_by_id: dict[int, dict],
    *,
    maximum: int = 8,
    targeted_requests: Iterable[dict] = (),
) -> tuple[list[dict], dict]:
    """仅由内置模型选出可下载的热点母片；没有模型则返回空集。"""
    rows = [dict(item) for item in media_rows]
    if not rows:
        return [], {"status": "no_candidates", "reason": "没有待入库的授权长视频"}
    if not model_router.key_is_available("planner_text") or not model_router.key_is_available("critic"):
        return [], {"status": "model_unavailable", "reason": "内置热点入库模型未配置，本轮不下载"}
    maximum = max(1, min(int(maximum), 24))
    base_candidates = [
        _candidate(row, hotspots_by_id.get(int(row.get("hotspot_id") or 0), {}))
        for row in rows[:48]
    ]
    candidates, sop_meta = hotspot_intake_sop.enrich_candidates(base_candidates)
    if not candidates:
        return [], {
            "status": "no_rag_evidence",
            "reason": "Buffalo RAG 没有可供下载前决策引用的已验证服务资料，本轮不下载",
            "rag_sop": sop_meta,
        }
    allowed = {int(item["media_id"]): item for item in candidates}
    sop = hotspot_intake_sop.policy_contract()
    target_rows = [
        {"id": int(item["id"]), "topic": str(item.get("topic") or "")[:300]}
        for item in targeted_requests if str(item.get("id") or "").isdigit()
    ][:20]
    target_ids = {int(item["id"]) for item in target_rows}
    fingerprint = json.dumps({"sop": sop, "prompt_version": PROMPT_VERSION, "candidates": candidates, "targets": target_rows}, ensure_ascii=False, sort_keys=True)
    job_id = model_router.route_scoped_job_id(
        "hotspot-hook-intake-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16],
        "planner_text",
    )
    model_router.create_budget(
        job_id, max_calls=2, max_input_tokens=16_000,
        max_output_tokens=model_router.required_output_budget("planner_text", 1_000),
        # max_calls=2 = 1 次初始选片 + 1 次 JSON 解析失败重试（同一决策尝试内）。
    )
    selections, sel_retried, result = _call_with_json_retry(
        "selection", job_id, "planner_text", PROMPT_VERSION,
        [
            {"role": "system", "content": "严格返回 JSON，不要 Markdown；不能根据镜头外信息或未提供的 RAG 编造事实。"},
            {"role": "user", "content": _prompt(candidates, maximum, sop, target_rows)},
        ],
        1_000,
        lambda content: _parse_selections(content, allowed, target_ids),
    )
    if not selections:
        return [], {
            "status": "no_qualified_media",
            "selected_count": 0,
            "model": model_router.get_route("planner_text").get("model"),
            "cache_hit": bool(result.get("cache_hit")),
            "retries": {"selection": 1 if sel_retried else 0, "audit": 0},
            "rag_sop": sop_meta,
        }
    audit_job_id = model_router.route_scoped_job_id("hotspot-hook-intake-audit-" + hashlib.sha256(
        json.dumps({"sop": sop, "prompt_version": AUDIT_PROMPT_VERSION, "candidates": candidates, "selections": selections}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16], "critic")
    model_router.create_budget(
        audit_job_id, max_calls=2, max_input_tokens=10_000,
        max_output_tokens=model_router.required_output_budget("critic", 500),
        # max_calls=2 = 1 次初始审计 + 1 次 JSON 解析失败重试。
    )
    approved, audit_retried, audit_result = _call_with_json_retry(
        "audit", audit_job_id, "critic", AUDIT_PROMPT_VERSION,
        [
            {"role": "system", "content": "严格返回 JSON；RAG 证据不足或关联牵强时必须拒绝。"},
            {"role": "user", "content": _audit_prompt(candidates, selections, sop)},
        ],
        500,
        lambda content: _parse_audit(content, {item["media_id"] for item in selections}),
    )
    by_id = {int(row["id"]): row for row in rows}
    selected: list[dict] = []
    for selection in selections:
        media_id = int(selection["media_id"])
        if media_id not in approved or media_id not in by_id:
            continue
        if any(int(item["id"]) == media_id for item in selected):
            continue
        selected.append({
            **by_id[media_id],
            "intake_decision": {
                "admission_mode": selection["admission_mode"],
                "why": selection["why"],
                "logistics_question": selection["logistics_question"],
                "confidence": selection["confidence"],
                "service_fit": selection["service_fit"],
                "expected_hook": selection["expected_hook"],
                "rag_evidence_ids": selection["rag_evidence_ids"],
                "sop_id": hotspot_intake_sop.SOP_ID,
                "sop_version": hotspot_intake_sop.SOP_VERSION,
                "audit_reason": approved[media_id],
                "curator": "planner_text",
                "target_request_ids": selection["target_request_ids"],
            },
        })
        if len(selected) >= maximum:
            break
    return selected, {
        "status": "selected" if selected else "no_qualified_media",
        "selected_count": len(selected),
        "model": model_router.get_route("planner_text").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
        "retries": {"selection": 1 if sel_retried else 0, "audit": 1 if audit_retried else 0},
        "rag_sop": sop_meta,
        "audit": {
            "model": model_router.get_route("critic").get("model"),
            "cache_hit": bool(audit_result.get("cache_hit")),
            "approved_count": len(selected),
            "planner_selected_count": len(selections),
        },
    }
