"""把外部热点事实与 Buffalo 内部能力证据隔离后组成可复用证据包。"""
from __future__ import annotations

import re

import database as db


def _norm(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _independent_excerpt(claim: str, hotspot: dict) -> str | None:
    """Return a source excerpt that does not merely repeat the claim text."""
    claim_n = _norm(claim)
    if not claim_n:
        return None
    title = str(hotspot.get("title") or "").strip()
    summary = str(hotspot.get("summary") or "").strip()
    explicit = str(
        hotspot.get("source_excerpt")
        or hotspot.get("excerpt")
        or hotspot.get("body_excerpt")
        or ""
    ).strip()
    for candidate in (explicit, summary, title):
        excerpt = str(candidate or "").strip()
        if not excerpt:
            continue
        if _norm(excerpt) == claim_n:
            continue
        return excerpt[:1_000]
    return None


def _fact_claims(hotspot: dict) -> list[dict]:
    title = str(hotspot.get("title") or "").strip()
    summary = str(hotspot.get("summary") or "").strip()
    sentences = [title]
    sentences.extend(
        part.strip()
        for part in re.split(r"(?<=[。！？.!?])\s+|\n+", summary)
        if part.strip() and part.strip() != title
    )
    claims = []
    for sentence in sentences[:6]:
        if not sentence:
            continue
        excerpt = _independent_excerpt(sentence, hotspot)
        if not excerpt:
            continue
        claims.append({
            "claim": sentence[:1_000],
            "source_url": str(hotspot.get("source_url") or ""),
            "source_title": title,
            "publisher": str(hotspot.get("publisher") or ""),
            "excerpt": excerpt,
            "published_at": hotspot.get("published_at"),
            "retrieved_at": hotspot.get("retrieved_at"),
        })
    return claims


def _confirmed_brand_claims(ids: list[int] | None) -> list[dict]:
    if not ids:
        return []
    items = db.list_brand_evidence(ids=ids)
    return [
        {
            "claim": item["claim"],
            "excerpt": item["evidence_note"],
            "publisher": "Buffalo internal evidence",
            "source_title": "Buffalo 已确认能力说明",
            "brand_evidence_id": item["id"],
        }
        for item in items
        if item["status"] == "confirmed" and item["disclosure_level"] == "public"
    ]


def build_package(
    hotspot_id: int,
    *,
    created_by: int | None = None,
    brand_evidence_ids: list[int] | None = None,
) -> dict:
    hotspot = db.get_hotspot(hotspot_id)
    if not hotspot:
        raise ValueError("热点不存在")
    fact_claims = _fact_claims(hotspot)
    brand_claims = _confirmed_brand_claims(brand_evidence_ids)
    facts_complete = bool(
        fact_claims
        and hotspot.get("source_url")
        and hotspot.get("publisher")
        and hotspot.get("retrieved_at")
    )
    if not facts_complete:
        status = "needs_fact_review"
    elif not brand_claims:
        status = "needs_brand_evidence"
    else:
        status = "ready"
    return db.create_evidence_package(
        hotspot_id,
        fact_claims,
        brand_claims,
        status,
        created_by,
    )
