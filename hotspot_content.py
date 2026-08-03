"""Evidence-preserving hotspot-to-Buffalo draft composition."""
from __future__ import annotations

import database as db


def compose(hotspot: dict) -> dict:
    """Compose facts and brand commentary without asking a model to invent facts.

    External ``source_refs`` only cite the hotspot's source fields. Buffalo
    commentary is labeled as brand suggestion and never mapped as news-backed
    evidence.
    """
    raw_title = (hotspot.get("title") or "南非物流动态").strip()
    summary = (hotspot.get("summary") or "").strip()
    publisher = (hotspot.get("publisher") or "来源机构").strip()
    source_url = hotspot.get("source_url") or ""
    title = f"南非物流观察｜{raw_title}"
    fact_sentence = f"据{publisher}报道：{raw_title}。"
    fact_body = f"{fact_sentence}\n{summary}" if summary else fact_sentence
    body = (
        "【事实速览】\n"
        f"{fact_body}\n\n"
        "【Buffalo 观点】\n"
        "以上仅转述公开来源事实，不是 Buffalo 已执行的服务结果。"
        "卖家可据此自行核对订单节点、船期与通关资料；品牌建议不替代官方公告，"
        "也不把新闻摘要写成 Buffalo 能力证明。"
    )
    source_refs = []
    excerpt = summary if summary and summary != raw_title else ""
    if source_url and publisher and raw_title and excerpt:
        source_refs.append({
            "claim": raw_title,
            "url": source_url,
            "source_title": raw_title,
            "publisher": publisher,
            "excerpt": excerpt[:1000],
        })
    attachments = []
    asset_id = hotspot.get("asset_id")
    if asset_id:
        asset = db.get_asset(asset_id)
        if asset and asset.get("status") == "active":
            attachments.append({
                "type": asset["file_type"],
                "path": asset["filepath"],
                "asset_id": asset["id"],
                "license": asset.get("license"),
                "attribution": asset.get("attribution"),
                "source_url": asset.get("source_url"),
            })
    return {
        "title": title,
        "body": body,
        "hashtags": ["南非物流", "Buffalo物流", "物流热点"],
        "source_refs": source_refs,
        "attachments": attachments,
        "hotspot_id": hotspot["id"],
    }
