"""公众号图文长文生成模块（阶段 0）。

复用 model_router 的 "planner_text" 路由（不新增路由配置），生成结构化正文 JSON。
事实锚定硬约束：正文里的具体数字/费用/期限/日期必须能在投喂资料包中找到依据，
找不到就写模糊表述或标 [待核实]，不允许模型自己编造。
"""
from __future__ import annotations

import asyncio
import json

import database as db
import model_router
from hotspot_hook_curator import _extract_json

PROMPT_VERSION = "wechat-article-gen-v1"

SYSTEM_PROMPT = (
    "严格返回 JSON。正文中出现的任何具体数字、费用、期限、日期、机构名称，"
    "必须能在下面提供的资料片段中找到依据；找不到依据时改写成不含具体数值的模糊表述，"
    "并在 evidence_footnotes 里跳过该句，不允许编造或从常识补充数字。"
    "文章结构：导语（背景+读者痛点+结构预告）→ 分节正文（每节一个小标题，"
    "可选一个结构化对比字段，字段名和值都用文本描述，不要输出 HTML）→ 结尾总结。"
)


def _build_user_prompt(article: dict) -> str:
    topic_brief = str(article.get("topic_brief") or "").strip()
    reference_style = str(article.get("reference_style") or "").strip()
    materials = json.loads(article.get("materials_json") or "[]")

    lines = []
    if topic_brief:
        lines.append(f"选题说明：{topic_brief}")
    if reference_style:
        lines.append(f"参考范文/风格说明（仅对标结构，不作为事实来源）：{reference_style}")
    lines.append("")
    lines.append("资料片段（带编号，evidence_footnotes 的 material_index 回指这里的编号）：")
    for i, material in enumerate(materials, start=1):
        lines.append(f"[{i}] 来源：{material.get('source_note') or ''}")
        if material.get("source_url"):
            lines.append(f"    链接：{material['source_url']}")
        lines.append(f"    原文：{(material.get('excerpt') or '').strip()}")
    lines.append("")
    lines.append(
        "请返回 JSON："
        '{"intro": "导语", "sections": [{"heading": "小节标题", "body": "正文段落", '
        '"comparison_card": {"字段名": "值", ...}}], "conclusion": "结尾总结", '
        '"evidence_footnotes": [{"claim_text": "包含具体数字的原句", "material_index": 编号}]}'
    )
    return "\n".join(lines)


def generate_article(article_id: int) -> dict:
    """生成一篇图文长文的结构化正文；失败不写库。"""
    article = db.get_article(article_id)
    if article is None:
        return {"status": "article_not_found", "article_id": article_id}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(article)},
    ]

    payload = None
    last_result = None
    last_error = None
    for attempt in (1, 2):
        job_id = f"article-gen:{article_id}:{attempt}"
        model_router.create_budget(
            job_id, max_calls=2, max_input_tokens=14_000,
            max_output_tokens=model_router.required_output_budget("planner_text", 1_500),
            reset=True,
        )
        try:
            result = asyncio.run(model_router.call_text(
                job_id, "planner_text", messages,
                prompt_version=PROMPT_VERSION,
                max_output_tokens=1_500,
                use_cache=(attempt == 1),
            ))
        except Exception as exc:  # noqa: BLE001 模型调用层错误统一按失败重试
            last_error = str(exc)
            continue
        last_result = result
        try:
            payload = _extract_json(str(result.get("content") or ""))
            if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
                raise ValueError("JSON 顶层必须是含 sections 数组的对象")
            break
        except ValueError as exc:
            last_error = str(exc)
            payload = None

    if payload is None:
        return {
            "status": "generation_failed",
            "article_id": article_id,
            "error": last_error or "模型未返回合法 JSON",
            "raw_content": (last_result or {}).get("content") or "",
        }

    intro = str(payload.get("intro") or "").strip()
    sections = []
    for section in payload.get("sections") or []:
        heading = str(section.get("heading") or "").strip()
        body = str(section.get("body") or "").strip()
        if not heading or not body:
            continue
        item = {"heading": heading, "body": body}
        comparison = section.get("comparison_card")
        if isinstance(comparison, dict) and comparison:
            item["comparison_card"] = {str(k): str(v) for k, v in comparison.items()}
        sections.append(item)
    conclusion = str(payload.get("conclusion") or "").strip()

    footnotes = []
    for note in payload.get("evidence_footnotes") or []:
        if not isinstance(note, dict):
            continue
        claim = str(note.get("claim_text") or "").strip()
        try:
            index = int(note.get("material_index"))
        except (TypeError, ValueError):
            continue
        if claim and 1 <= index <= len(json.loads(article.get("materials_json") or "[]")):
            footnotes.append({"claim_text": claim, "material_index": index})

    structured = {
        "intro": intro,
        "sections": sections,
        "conclusion": conclusion,
    }
    db.update_article(
        article_id,
        generated_content_json=json.dumps(structured, ensure_ascii=False),
        evidence_footnotes_json=json.dumps(footnotes, ensure_ascii=False),
    )
    return {
        "status": "ok",
        "article_id": article_id,
        "section_count": len(sections),
        "footnote_count": len(footnotes),
        "word_count": len(intro + conclusion + "".join(s["body"] for s in sections)),
        "model": (model_router.get_route("planner_text") or {}).get("model") or "",
    }
