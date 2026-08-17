"""Model-authored narration for an already evidence-locked dual-library preview.

The model may improve narrative and on-screen copy only.  It cannot replace the
reviewed hotspot event, assign a new asset, or turn a Buffalo action into proof
of the external event.
"""
from __future__ import annotations

import asyncio
import json
import re
from uuid import uuid4

import model_router
import douyin_copywriting_sop
import hotspot_video_planner
from video_composition_policy import scene_voiceover_char_limit


PROMPT_VERSION = "dual-library-preview-narration-v7"
CRITIC_PROMPT_VERSION = "dual-library-preview-narration-critic-v4"


def _strip_fence(content: str) -> str:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return raw


def _voiceover_char_limit(scene: dict) -> int:
    """Keep TTS inside the one-pass visual budget for this locked scene.

    The renderer never loops a real video.  A compact per-beat copy budget is
    therefore a production constraint, not a writing preference.  Chinese TTS
    is budgeted at about 5 characters per second (punctuation included), while
    the renderer still measures the resulting audio and rejects any overrun.
    This avoids falsely rejecting compact natural Chinese before TTS has
    measured its real duration.
    """
    return scene_voiceover_char_limit(scene)


def parse_narration(content: str, scene_count: int | list[dict]) -> dict:
    """Fail closed unless the model keeps the exact locked scene count."""
    try:
        payload = json.loads(_strip_fence(content))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("旁白规划未返回合法 JSON") from exc
    rows = payload.get("scenes") if isinstance(payload, dict) else None
    locked_scenes = scene_count if isinstance(scene_count, list) else None
    expected_count = len(locked_scenes) if locked_scenes is not None else int(scene_count)
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(f"旁白规划必须返回 {expected_count} 个锁定分镜")
    scenes = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 个旁白分镜无效")
        voiceover = str(row.get("voiceover") or "").strip()
        text_overlay = str(row.get("text_overlay") or "").strip()
        locked_scene = locked_scenes[index - 1] if locked_scenes else {}
        max_chars = _voiceover_char_limit(locked_scene) if locked_scenes else 150
        # 短图片过渡也可以是一句简短承接，但所有画面仍受同一时长预算。
        min_chars = 4 if locked_scenes else 8
        # 图片只留 1–2 秒，某些模型仍会为它写一整句。若模型同时给出了符合
        # 时长的短字幕，则以那条模型自产生的短字幕作为旁白，不截断句子、也不
        # 由规则系统另写内容；否则仍然拒绝。
        if (
            locked_scenes
            and str(locked_scene.get("evidence_type") or "") == "image"
            and len(voiceover) > max_chars
            and min_chars <= len(text_overlay) <= max_chars
        ):
            voiceover = text_overlay
        if not min_chars <= len(voiceover) <= max_chars:
            raise ValueError(f"第 {index} 个旁白超出 {max_chars} 字的镜头时长预算")
        # 这是格式补齐，不改变模型的内容决策：部分兼容模型会返回正确旁白却
        # 遗漏字幕字段。直接摘取该旁白能保证音画字幕一致，也避免无谓重试。
        if not text_overlay:
            text_overlay = voiceover[:36]
        scenes.append({"voiceover": voiceover, "text_overlay": text_overlay[:36]})
    title = str(payload.get("title") or "").strip()[:100]
    angle = str(payload.get("angle") or "").strip()[:180]
    if not title or not angle:
        raise ValueError("旁白规划缺少标题或角度")
    return {"title": title, "angle": angle, "scenes": scenes}


def parse_critique(content: str) -> tuple[bool, list[str]]:
    """A malformed critic result is not a pass for a factual marketing video."""
    try:
        payload = json.loads(_strip_fence(content))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Critic 未返回合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Critic 返回格式无效")
    issues = [str(item).strip()[:240] for item in (payload.get("issues") or []) if str(item).strip()]
    return bool(payload.get("approved")) and not issues, issues


def _hotspot_facts(events: list[dict]) -> list[dict]:
    facts = []
    for item in events:
        evidence = item.get("evidence") or item.get("evidence_json") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = {}
        facts.append({
            "event_clip_id": item.get("id"),
            "title": item.get("title_zh") or item.get("title_en"),
            "what_happened": str((evidence or {}).get("what_happened") or "")[:360],
            "logistics_question": str((evidence or {}).get("logistics_question") or "")[:220],
        })
    return facts


def _scene_narration_boundary(scene: dict) -> str:
    """Give the writer and Critic an explicit boundary per locked visual."""
    evidence_type = str(scene.get("evidence_type") or "")
    if evidence_type == "hotspot_video":
        return "只能复述 verified_hotspot_facts；不可把局部事件写成所有订单已延误。"
    if evidence_type == "image":
        return (
            "这是 Buffalo 自有图片，只作 1–2 秒情绪/场景过渡。旁白可提出条件性问题或建议，"
            "如‘先核对订单状态’，但不能说图片证明了核对、扫描、同步、出库或配送结果。"
        )
    return (
        "这是 Buffalo 自有实拍。若 visual 未明确写出某个动作，只能说‘镜头展示仓内/配送现场’，"
        "不得推断扫描、订单归类、系统同步、转运、出库或针对本次热点的应对已经发生。"
    )


def build_messages(topic: str, brief: dict, scenes: list[dict], related_events: list[dict], rag_evidence: list[dict]) -> list[dict]:
    locked_scenes = [
        {
            "scene": index + 1,
            "role": scene.get("scene_role"),
            "evidence_type": scene.get("evidence_type"),
            "primary_category": scene.get("primary_category"),
            "visual": scene.get("visual"),
            "event_clip_id": scene.get("event_clip_id"),
            "asset_id": scene.get("asset_id"),
            "duration_ms": scene.get("duration_ms"),
            "voiceover_max_chars": _voiceover_char_limit(scene),
            "narration_boundary": _scene_narration_boundary(scene),
        }
        for index, scene in enumerate(scenes)
    ]
    return [
        {
            "role": "system",
            "content": (
                "你是部署在 Buffalo 物流内容系统内的中文短视频总编。你只能改写已锁定分镜的旁白和字幕，"
                "不能增删分镜、不能替换热点事件或素材。输出一条 50–90 秒内部验收视频的叙事，"
                "开头先讲清热点发生了什么，再解释一个具体物流影响，随后用 Buffalo 自有画面中可见的动作回应，"
                "最后把该动作转成可核验的品牌优势（风险前置、动作可核对、异常可留痕或交接更稳）。"
                "禁止空承接，例如‘异常后，Buffalo 核对仓内分拣。’‘接下来看看我们的解决方案。’‘这就是物流安全的重要性。’"
                "承接必须说明：什么风险、哪个物流节点、Buffalo 做了什么、为什么体现稳定性或安全性。"
                "热点不证明 Buffalo 服务，Buffalo 也不能宣称已解决该热点；但热点必须成为品牌营销引子：在事实和物流影响之后，用一个已确认或画面可见的动作，说明 Buffalo 的具体优势（风险前置、动作可核对、异常可留痕或交接更稳）。不写绝对时效、成本、安全或覆盖率。"
                "若热点只写国际油价或新闻下三分之一，不能推断南非当地油价、运费或客户成本已经变化；"
                "若热点只写道路事故或限行，不能推断所有货物已延误。只可用‘可能需要核对’这类谨慎表达。"
                "避免‘承接每一步’、‘提前准备’等空泛重复。每一镜必须给新信息，且只说该镜头事实或提供的 RAG 边界。"
                "每镜旁白必须不超过 locked_scenes 的 voiceover_max_chars；不能把一段长旁白塞进短素材。"
                "热点画面只说发生了什么，Buffalo 视频只说可见动作并完成品牌优势承接。evidence_type=image 是 Buffalo 自有图片，"
                "只能做 1–2 秒场景过渡或承接条件性订单问题；图片旁白不能说‘图中正在核对/扫描/同步’。"
                "每镜的 narration_boundary 是硬边界，逐镜遵守。"
                "信息图、流程图和 PPT 卡片已被系统禁用；不可要求、描述或以其填充旁白。"
                + douyin_copywriting_sop.prompt_for_video_planner()
                + "仅返回 JSON：{\"title\":\"\",\"angle\":\"\",\"scenes\":[{\"voiceover\":\"\",\"text_overlay\":\"\"}]}。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "topic": topic,
                "brief": {
                    "hotspot_title": brief.get("hotspot_title"),
                    "angle": brief.get("angle"),
                    "logistics_topic": brief.get("logistics_topic"),
                    "audience": brief.get("audience"),
                    "brand_claims": brief.get("brand_claims"),
                    "negative_claims": brief.get("negative_claims"),
                },
                "verified_hotspot_facts": _hotspot_facts(related_events),
                "buffalo_rag_evidence": rag_evidence,
                "locked_scenes": locked_scenes,
            }, ensure_ascii=False),
        },
    ]


def _call_planner(messages: list[dict], *, phase: str) -> tuple[dict, dict]:
    job_id = model_router.route_scoped_job_id(
        f"dual-library-preview-narration-{phase}-{uuid4().hex[:16]}", "planner_text"
    )
    # One JSON row is required for every locked scene.  A 50–90s plan commonly
    # has 10–14 beats, so 1,200 visible tokens can stop MiniMax halfway through
    # a valid JSON object.  Reserve the configured planner maximum instead.
    visible_output = int(model_router.get_route("planner_text").get("max_tokens") or 1_800)
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=7_000,
        max_output_tokens=model_router.required_output_budget("planner_text", visible_output),
    )
    result = asyncio.run(model_router.call_text(
        job_id, "planner_text", messages,
        prompt_version=f"{PROMPT_VERSION}-{phase}", max_output_tokens=visible_output,
        json_mode=True,
    ))
    return result, {
        "model": model_router.get_route("planner_text").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
        "usage": result.get("usage") or {},
    }


def _review_messages(messages: list[dict], proposal: dict) -> list[dict]:
    context = messages[-1]["content"] if messages else "{}"
    return [
        {
            "role": "system",
            "content": (
                "你是 Buffalo 物流短视频的事实 Critic。只检查，不改写。逐镜核验："
                "(1)热点镜是否只复述给定热点事实；(2)品牌镜是否只描述可见动作、给定 RAG 边界，并把动作转成 Buffalo 的具体优势；"
                "(3)不得把国际油价推断为南非当地油价、运费或客户成本已变化；"
                "(4)不得把道路事故或限行推断为全部订单已延误；(5)不能把热点当作 Buffalo 服务结果；"
                "(6)禁止空承接句，如‘异常后，Buffalo 核对仓内分拣’‘接下来看看我们的解决方案’‘这就是物流安全的重要性’；"
                "Buffalo 承接必须写清风险、物流节点、可见动作和优势。"
                "对 evidence_type=image：允许条件性建议（如‘先核对订单状态’），不要求图片证明该建议；"
                "但不得写成图片中已经完成核对、扫描、同步、出库或配送。"
                "对 Buffalo 视频：只在 visual 明确出现动作时才可描述该动作，否则仅可说镜头展示仓内/配送现场。"
                "只返回 JSON：{\"approved\":true或false,\"issues\":[\"具体问题\"]}。"
            ),
        },
        {"role": "user", "content": json.dumps({"evidence_context": json.loads(context), "proposal": proposal}, ensure_ascii=False)},
    ]


def _call_critic(messages: list[dict], proposal: dict, *, phase: str) -> tuple[bool, list[str], dict]:
    if not model_router.key_is_available("critic"):
        raise RuntimeError("内部 Critic 未配置，拒绝跳过事实审计")
    review_messages = _review_messages(messages, proposal)
    job_id = model_router.route_scoped_job_id(
        f"dual-library-preview-narration-critic-{phase}-{uuid4().hex[:16]}", "critic"
    )
    # MiniMax's visible JSON and its separated reasoning still share the
    # completion budget.  600 tokens can be consumed by the fact review before
    # the final JSON is emitted on a multi-scene video, so use the configured
    # Critic ceiling and keep the parser fail-closed.
    visible_output = int(model_router.get_route("critic").get("max_tokens") or 1_400)
    model_router.create_budget(
        job_id, max_calls=1, max_input_tokens=8_000,
        max_output_tokens=model_router.required_output_budget("critic", visible_output),
    )
    result = asyncio.run(model_router.call_text(
        job_id, "critic", review_messages,
        prompt_version=f"{CRITIC_PROMPT_VERSION}-{phase}", max_output_tokens=visible_output,
        json_mode=True,
    ))
    approved, issues = parse_critique(result.get("content") or "")
    return approved, issues, {
        "model": model_router.get_route("critic").get("model"),
        "cache_hit": bool(result.get("cache_hit")),
        "usage": result.get("usage") or {},
    }


def deterministic_evidence_issues(proposal: dict, related_events: list[dict]) -> list[str]:
    """Reject causal leaps that a Critic could otherwise overlook.

    This is only a factual constraint: the internal planner still creates
    all wording and the Critic still reviews it. It blocks turning an oil-price
    or road-incident hook into an unsupported local commercial outcome.
    """
    facts = " ".join(
        " ".join((
            str(item.get("title_zh") or ""), str(item.get("title_en") or ""),
            str((item.get("evidence") or {}).get("what_happened") or ""),
        ))
        for item in related_events
    ).casefold()
    copy = " ".join(
        f"{scene.get('voiceover') or ''} {scene.get('text_overlay') or ''}"
        for scene in proposal.get("scenes") or []
    )
    issues: list[str] = []
    if any(token in facts for token in ("油价", "oil price", "red sea", "红海")):
        prohibited_cost_claims = (
            r"(?:南非|当地).{0,20}(?:油价|运费|配送成本|物流成本).{0,20}(?:波动|上涨|增加|变化)",
            r"(?:燃油附加费|运费|配送成本|物流成本).{0,20}(?:波动|上涨|增加|变化)",
            r"(?:油价|能源扰动).{0,20}(?:导致|造成|让).{0,20}(?:运费|附加费|成本).{0,20}(?:波动|上涨|增加|变化)",
        )
        if any(re.search(pattern, copy, flags=re.IGNORECASE) for pattern in prohibited_cost_claims):
            issues.append("热点仅证明国际油价/红海新闻，不能写成南非当地油价、运费、燃油附加费或配送成本发生或将发生波动；只能提示核对具体报价或计划。")
    if any(token in facts for token in ("道路", "road", "卡车", "truck", "翻车", "事故", "限行")):
        if re.search(r"(?:所有|全部|每一笔).{0,18}(?:订单|货物).{0,18}(?:延误|延迟|受阻)", copy):
            issues.append("道路事件只能说明该路段/现场受阻，不能写成所有订单或货物都已延误。")
    import hotspot_intake_policy
    if hotspot_intake_policy.contains_generic_bridge_filler(copy):
        issues.append("Buffalo 承接不能使用空泛套话，必须写清风险、物流节点、可见动作和优势。")
    return issues


# 清关 preparation 模式的确定性过度宣称黑名单。完成词均含「已…」或「…完成」，
# 准备词用「待/等待/前/准备/备齐」，子串集合天然不重叠（由单测证明）。
# 注意：不带「已」的「海关放行/货物放行」与指令要求放行的准备式
# 「等待海关放行」存在子串重叠、无法用子串黑名单区分，故不入表；
# 「已放行」已足以覆盖完成宣称。
CUSTOMS_DONE_CLAIMS = (
    "已清关", "清关完成", "完成清关", "已通关", "通关完成",
    "已放行", "已报关完成",
)
DELIVERY_DONE_CLAIMS = (
    "已送达", "已交付", "已签收", "派送完成", "已妥投", "妥投完成", "送达客户",
)
_CUSTOMS_NODE_TERMS = {"清关", "customs", "关税"}
_NON_CUSTOMS_CATEGORIES = {"warehouse", "delivery", "staff", "facility"}

# 白名单正向强制的作用域常量：与黑名单触发范围完全同源（同一对集合），
# 差别只在动作——黑名单再看文本，白名单命中场景即强制，不看文本。
BORROWED_CUSTOMS_CONTEXT = frozenset(_NON_CUSTOMS_CATEGORIES)
CUSTOMS_NODES = frozenset(_CUSTOMS_NODE_TERMS)


def requires_safe_customs_copy(primary_category: str, logistics_nodes: list[str]) -> bool:
    """当一条 scene 用非-customs 素材出现在 customs 节点下(借用清关上下文)时返回 True。
    此时该 scene 的口播必须强制走安全准备模板——不检测文本，直接剥夺其自由宣称的机会(真气密)。
    真 customs 素材(primary_category=='customs')返回 False——它有权正常改写。"""
    category = str(primary_category or "").casefold()
    if category not in BORROWED_CUSTOMS_CONTEXT:
        return False
    nodes = {str(node).casefold() for node in (logistics_nodes or [])}
    return bool(nodes & CUSTOMS_NODES)


# 受控开闸：za-stock 免版权通用背景，即使 primary_category=='customs' 也必须走
# 安全准备模板——画面是通用空镜，口播不得宣称南非现场或 Buffalo 自有能力。
_ZASTOCK_SOURCES = frozenset({"za_stock_license"})


def is_zastock_context(source: str) -> bool:
    return str(source or "").casefold() in _ZASTOCK_SOURCES


def overclaim_completion_issues(voiceover: str, primary_category: str, logistics_nodes: list[str]) -> list[str]:
    """当一条 scene 用非-customs 素材在 customs 节点下宣称已完成受监管结果时，
    返回问题列表（非空即违规）。纯确定性子串匹配，无模型调用，可单测。"""
    category = str(primary_category or "").casefold()
    if category not in _NON_CUSTOMS_CATEGORIES:
        return []
    nodes = {str(node).casefold() for node in (logistics_nodes or [])}
    if not (nodes & _CUSTOMS_NODE_TERMS):
        return []
    text = str(voiceover or "")
    issues: list[str] = []
    for term in CUSTOMS_DONE_CLAIMS:
        if term in text:
            issues.append(f"非清关素材不得宣称清关已完成：命中完成词「{term}」；只能说清关前的准备动作。")
    for term in DELIVERY_DONE_CLAIMS:
        if term in text:
            issues.append(f"非清关素材不得宣称交付已完成：命中完成词「{term}」；只能说发运前的准备动作。")
    return issues


def apply_overclaim_guard(
    generated_scenes: list[dict],
    scenes: list[dict],
    logistics_nodes: list[str],
) -> list[dict]:
    """对模型产出的逐镜文案做后置确定性拦截，两层防线：

    1. 白名单正向强制（whitelist_forced）：借用清关上下文的非-customs scene
       命中危险场景即无条件替换为安全准备模板，不看模型文本——真气密。
    2. 黑名单兜底（blacklist_fallback）：其余 scene 保留完成词检测回退
       （防御纵深，不删）。

    返回 overclaim_guard 命中记录（含 mode、原句、分类、替换后文案），供生产链
    写入渲染报告。回退文案同步到 text_overlay，确保字幕与旁白一致。
    """
    records: list[dict] = []
    for index, (item, scene) in enumerate(zip(generated_scenes, scenes)):
        voiceover = str(item.get("voiceover") or "")
        overlay = str(item.get("text_overlay") or "")
        category = str(scene.get("primary_category") or "")
        source = str(scene.get("asset_source") or "")
        try:
            max_chars = scene_voiceover_char_limit(scene)
        except (TypeError, ValueError):
            max_chars = None
        # 受控开闸：za-stock 素材无论分类一律强制安全模板（画面是通用背景）。
        # 真 customs 自有素材仍可正常改写；黑名单兜底保留。
        if is_zastock_context(source) or requires_safe_customs_copy(category, logistics_nodes):
            # 第一道（白名单/正向强制）：借来上下文或 za-stock 通用背景，无条件用安全模板，
            # 模型那句连看都不看——不给它自由说话的机会（真气密）。
            safe_copy = hotspot_video_planner.safe_customs_preparation_copy(
                category, max_chars=max_chars, min_chars=5,
            )
            records.append({
                "scene": index + 1,
                "primary_category": category,
                "asset_source": source,
                "mode": "whitelist_forced",
                "issues": [],
                "original_voiceover": voiceover,
                "replaced_voiceover": safe_copy,
            })
            item["voiceover"] = safe_copy
            item["text_overlay"] = safe_copy.rstrip("。")[:24]
            continue
        # 第二道（黑名单兜底）：其余 scene 保留原有过度宣称检测（防御纵深，不删）。
        issues = overclaim_completion_issues(f"{voiceover} {overlay}", category, logistics_nodes)
        if not issues:
            continue
        safe_copy = hotspot_video_planner.safe_customs_preparation_copy(
            category, max_chars=max_chars, min_chars=5,
        )
        records.append({
            "scene": index + 1,
            "primary_category": category,
            "mode": "blacklist_fallback",
            "issues": issues,
            "original_voiceover": voiceover,
            "replaced_voiceover": safe_copy,
        })
        item["voiceover"] = safe_copy
        item["text_overlay"] = safe_copy.rstrip("。")[:24]
    return records


def generate_narration(
    topic: str,
    brief: dict,
    scenes: list[dict],
    related_events: list[dict],
    rag_evidence: list[dict],
) -> tuple[dict, dict]:
    """Ask the configured internal planner to author only the locked storyboard copy."""
    if not model_router.key_is_available("planner_text"):
        raise RuntimeError("内部内容规划模型未配置，拒绝使用规则模板旁白")
    messages = build_messages(topic, brief, scenes, related_events, rag_evidence)
    first_result, first_meta = _call_planner(messages, phase="initial")
    planner_meta: dict = {"initial": first_meta}
    latest_result = first_result
    proposal = None
    # 兼容模型偶尔会省略字幕字段、截断 JSON，或给某一镜只写两三个字。连续三次
    # 仍不合格才停止；绝不以规则文案静默顶替模型的内容决策。
    for attempt in range(3):
        try:
            proposal = parse_narration(latest_result.get("content") or "", scenes)
            break
        except ValueError as exc:
            if attempt == 2:
                raise
            repair_messages = [
                *messages,
                {"role": "assistant", "content": str(latest_result.get("content") or "")[:8_000]},
                {"role": "user", "content": (
                    f"上一版未通过硬校验：{exc}。只重发完整 JSON，不要解释、不要 Markdown。"
                    f"必须刚好 {len(scenes)} 个 scenes；每镜 voiceover 至少 4 字且不超过该镜 "
                    "voiceover_max_chars；text_overlay 不能为空。禁止写‘本地仓’、‘入库’、‘交付’"
                    "这类只有 2–3 个字的占位词。"
                )},
            ]
            phase = f"format-repair-{attempt + 1}"
            latest_result, retry_meta = _call_planner(repair_messages, phase=phase)
            planner_meta[phase] = retry_meta
    if proposal is None:
        raise ValueError("旁白未返回可用分镜")
    approved, issues, critic_meta = _call_critic(messages, proposal, phase="initial")
    issues = [*issues, *deterministic_evidence_issues(proposal, related_events)]
    approved = approved and not issues
    if approved:
        guard_records = apply_overclaim_guard(
            proposal["scenes"], scenes, brief.get("logistics_nodes") or [],
        )
        return proposal, {
            "planner": planner_meta, "critic": critic_meta,
            "prompt_version": PROMPT_VERSION, "revision_count": 0,
            "overclaim_guard": guard_records,
        }

    revision_messages = [
        *messages,
        {"role": "assistant", "content": json.dumps(proposal, ensure_ascii=False)},
        {"role": "user", "content": "Critic 指出了以下事实问题。仅重写 JSON 中的旁白与字幕，保留场景数：" + json.dumps(issues, ensure_ascii=False)},
    ]
    revised_result, revised_planner_meta = _call_planner(revision_messages, phase="revision")
    revised = parse_narration(revised_result.get("content") or "", scenes)
    approved, revised_issues, revised_critic_meta = _call_critic(messages, revised, phase="revision")
    revised_issues = [*revised_issues, *deterministic_evidence_issues(revised, related_events)]
    approved = approved and not revised_issues
    if not approved:
        raise ValueError("旁白未通过事实审计：" + "；".join(revised_issues[:3]))
    guard_records = apply_overclaim_guard(
        revised["scenes"], scenes, brief.get("logistics_nodes") or [],
    )
    return revised, {
        "planner": revised_planner_meta, "critic": revised_critic_meta,
        "initial_critic": {"approved": False, "issues": issues, **critic_meta},
        "prompt_version": PROMPT_VERSION, "revision_count": 1,
        "overclaim_guard": guard_records,
    }
