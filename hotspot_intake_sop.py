"""Buffalo RAG 证据检索：热点 → 已确认 Buffalo 服务事实的引文检索。

批 6（体检报告 §8 拍板）：模型 RAG 下载决策链（hotspot_hook_intake 的
select_for_hook_ingestion）已删除；本模块仅保留证据检索能力，供
``run_dual_library_preview.py`` / ``audit_existing_dual_preview.py`` 等
双素材库预览/审计工具使用。
"""
from __future__ import annotations

from typing import Iterable

import database as db
import hotspot_lexicon


MAX_EVIDENCE_PER_CANDIDATE = 3
MAX_EVIDENCE_CHARS = 360

_PREFERRED_CATEGORIES = {"产品资料", "公司介绍", "成功案例", "品牌规范"}
# Back-compat aliases for any external imports of the previous private constants.
_STOPWORDS = set(hotspot_lexicon.STOPWORDS)
_RETRIEVAL_ALIASES = hotspot_lexicon.RETRIEVAL_ALIAS_GROUPS


def _terms(text: str) -> set[str]:
    return hotspot_lexicon.extract_terms(text)


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
