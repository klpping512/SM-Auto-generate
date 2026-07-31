"""Versioned Buffalo RAG SOP for deciding whether a hotspot video merits download.

This module owns business boundaries and retrieval, while ``hotspot_hook_intake``
only routes the resulting bounded context to the configured internal model.  That
separation makes model changes a route/configuration concern rather than a rewrite
of the hotspot admission policy.
"""
from __future__ import annotations

import re
from typing import Iterable

import database as db


SOP_ID = "buffalo-hotspot-hook-intake"
SOP_VERSION = "v2"
MAX_EVIDENCE_PER_CANDIDATE = 3
MAX_EVIDENCE_CHARS = 360

_PREFERRED_CATEGORIES = {"产品资料", "公司介绍", "成功案例", "品牌规范"}
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "south", "africa",
    "关于", "导致", "当地", "新闻", "热点", "物流", "公司", "服务", "事件",
}
_RETRIEVAL_ALIASES = (
    ("warehouse", "仓库", "仓储", "海外仓", "入库", "出库"),
    ("parcel", "package", "包裹", "分拣", "检查"),
    ("loading", "装卸", "装车", "拖车", "货车"),
    ("delivery", "last mile", "配送", "末端", "派送"),
    ("cross border", "border", "跨境", "口岸", "入境"),
    ("customs", "clearance", "清关", "报关", "税务"),
    ("port", "harbour", "港口", "码头", "船期"),
)


def policy_contract() -> dict:
    """The stable contract shared by every model provider used for intake."""
    return {
        "sop_id": SOP_ID,
        "sop_version": SOP_VERSION,
        "goal": "只下载能以已验证 Buffalo 服务资料自然承接、且可能含现场 Hook 的热点母片。",
        "admission_rules": [
            "每个接受候选必须引用至少一条提供的 RAG 证据 ID，并声明 direct 或 contextual 承接模式。",
            "direct：候选本身明确呈现与 RAG 服务边界直接对应的仓储、包裹、装卸、配送等现场动作。",
            "contextual：候选明确讲述物流运行、运输、货运、跨境、港口、道路、仓配或电商履约中的外部变化；RAG 仅承接 Buffalo 已证实的一个服务动作，热点只能提出问题，绝不证明 Buffalo 解决了该事件。",
            "热点只可用于说明外部事实或提出物流问题；不得由热点推断 Buffalo 的服务结果。",
            "没有直接服务关联或具体物流运行关联、只有泛泛联想、只有主播/评论画面时必须拒绝。",
            "市政环卫、垃圾/污水、治安、政治、娱乐或其他公共事务，除非 RAG 明确覆盖该服务领域，否则两种模式均必须拒绝。",
        ],
        "required_output": [
            "media_id", "admission_mode", "rag_evidence_ids", "service_fit", "expected_hook", "why",
            "logistics_question", "confidence",
        ],
    }


def _terms(text: str) -> set[str]:
    normalized = str(text or "").casefold()
    words = set(re.findall(r"[a-z0-9]{3,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    for width in (2, 3, 4):
        words.update(chinese[index:index + width] for index in range(max(0, len(chinese) - width + 1)))
    for aliases in _RETRIEVAL_ALIASES:
        if any(alias in normalized for alias in aliases):
            words.update(aliases)
    return {word for word in words if word not in _STOPWORDS}


def _knowledge_evidence() -> list[dict]:
    """Read current confirmed knowledge rather than copying service facts into a prompt."""
    rows: list[dict] = []
    for category in db.get_kb_categories():
        category_name = str(category.get("name") or "")
        for document_stub in db.get_kb_documents(int(category["id"])):
            document = db.get_kb_document(int(document_stub["id"])) or {}
            content = str(document.get("content") or "").strip()
            if not content:
                continue
            rows.append({
                "id": f"kb:{int(document['id'])}",
                "kind": "knowledge_base",
                "category": category_name,
                "title": str(document.get("title") or "")[:120],
                "text": content,
                "category_priority": 2 if category_name in _PREFERRED_CATEGORIES else 0,
            })
    for evidence in db.list_brand_evidence(status="confirmed"):
        text = "\n".join(
            value for value in (str(evidence.get("claim") or "").strip(), str(evidence.get("evidence_note") or "").strip())
            if value
        )
        if not text:
            continue
        rows.append({
            "id": f"brand:{int(evidence['id'])}",
            "kind": "brand_evidence",
            "category": "已确认品牌证据",
            "title": str(evidence.get("claim") or "")[:120],
            "text": text,
            "category_priority": 3,
        })
    return rows


def retrieve_service_evidence(hotspot: dict, corpus: Iterable[dict] | None = None) -> list[dict]:
    """Return compact, cited RAG excerpts for one hotspot without fabricating scope."""
    query = " ".join(str(hotspot.get(key) or "") for key in (
        "title", "title_zh", "hotspot_title", "summary", "summary_zh", "hotspot_summary",
    ))
    query_terms = _terms(query)
    ranked: list[tuple[int, dict]] = []
    for source in (list(corpus) if corpus is not None else _knowledge_evidence()):
        source_terms = _terms(f"{source.get('title', '')}\n{source.get('text', '')}")
        overlap = len(query_terms.intersection(source_terms))
        # Product/company/case material is allowed as a low-ranked scope fallback,
        # but the model must still prove direct applicability and cite it explicitly.
        score = overlap * 20 + int(source.get("category_priority") or 0)
        ranked.append((score, source))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    result = []
    for score, source in ranked[:MAX_EVIDENCE_PER_CANDIDATE]:
        result.append({
            "id": str(source["id"]),
            "kind": str(source["kind"]),
            "category": str(source["category"]),
            "title": str(source["title"])[:120],
            "excerpt": str(source["text"])[:MAX_EVIDENCE_CHARS],
            "retrieval_score": score,
        })
    return result


def enrich_candidates(candidates: Iterable[dict]) -> tuple[list[dict], dict]:
    """Attach dynamic RAG evidence and fail closed when the knowledge base is empty."""
    corpus = _knowledge_evidence()
    enriched: list[dict] = []
    without_evidence: list[int] = []
    for raw in candidates:
        item = dict(raw)
        evidence = retrieve_service_evidence(item, corpus)
        item["rag_evidence"] = evidence
        item["sop_id"] = SOP_ID
        item["sop_version"] = SOP_VERSION
        if evidence:
            enriched.append(item)
        else:
            without_evidence.append(int(item["media_id"]))
    return enriched, {
        "sop_id": SOP_ID,
        "sop_version": SOP_VERSION,
        "knowledge_sources": len(corpus),
        "eligible_candidates": len(enriched),
        "rejected_without_rag_evidence": without_evidence,
    }
