"""Deterministic provenance gate for factual/current-event content."""
from __future__ import annotations

import re
from urllib.parse import urlparse

SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])\s*|\n+")
RISK_PATTERNS = [
    re.compile(r"\b(?:19|20)\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?\b"),
    re.compile(r"(?:\b\d+(?:\.\d+)?\s*%|R\s?\d[\d,.]*|\$\s?\d[\d,.]*|\b\d+(?:\.\d+)?\s*(?:天|小时|周|days?|hours?|weeks?))", re.I),
    re.compile(r"(?:今天|昨日|本周|近期|最近|最新|目前|当前|刚刚|突发|宣布|发布|发生|表示|证实|据.{0,30}报道|according to|reported|announced|latest|today|yesterday|currently)", re.I),
    re.compile(r"(?:官方数据|数据显示|行业报告|政府|海关|港口|Transnet|SARS|South African|南非|德班|开普敦|约翰内斯堡).{0,30}(?:显示|表明|称|宣布|发生|增长|下降|拥堵|罢工|延误|reported|said|announced)", re.I),
]


def risky_sentences(title: str, body: str) -> list[str]:
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(f"{title}。{body}") if s.strip()]
    return [s for s in sentences if any(pattern.search(s) for pattern in RISK_PATTERNS)]


def _valid_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def evaluate(title: str, body: str, evidence: list[dict] | None) -> dict:
    """Every risky sentence needs explicit evidence mapped to text in the draft."""
    risks = risky_sentences(title, body)
    if not risks:
        return {"status": "not_required", "risky_sentences": [], "uncovered": [], "invalid_evidence": []}
    valid, invalid, content = [], [], f"{title}\n{body}"
    for index, item in enumerate(evidence or []):
        claim = str(item.get("claim") or "").strip()
        url = str(item.get("url") or "").strip()
        source_title = str(item.get("source_title") or "").strip()
        publisher = str(item.get("publisher") or "").strip()
        excerpt = str(item.get("excerpt") or "").strip()
        if not claim or claim not in content or not _valid_url(url) or not source_title or not publisher or not excerpt:
            invalid.append(index)
        else:
            valid.append({**item, "claim": claim, "url": url, "source_title": source_title, "publisher": publisher, "excerpt": excerpt})
    uncovered = [sentence for sentence in risks if not any(ev["claim"] in sentence or sentence in ev["claim"] for ev in valid)]
    status = "verified" if valid and not uncovered and not invalid else "needs_evidence"
    return {"status": status, "risky_sentences": risks, "uncovered": uncovered, "invalid_evidence": invalid}


def publish_error(item: dict) -> str | None:
    result = evaluate(item.get("title", ""), item.get("body", ""), item.get("source_refs") or [])
    if result["status"] == "needs_evidence":
        return f"真实性门禁未通过：{len(result['uncovered'])} 条事实性表述缺少逐条来源证据"
    return None
