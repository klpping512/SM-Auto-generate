"""Evidence-preserving hotspot-to-Buffalo draft composition."""
from __future__ import annotations

import database as db
import truth_guard


def compose(hotspot: dict) -> dict:
    """Compose facts and brand commentary without asking a model to invent facts."""
    raw_title = (hotspot.get("title") or "南非物流动态").strip()
    summary = (hotspot.get("summary") or raw_title).strip()
    publisher = (hotspot.get("publisher") or "来源机构").strip()
    source_url = hotspot.get("source_url") or ""
    title = f"南非物流观察｜{raw_title}"
    fact_sentence = f"据{publisher}报道：{raw_title}。"
    body = (
        "【事实速览】\n"
        f"{fact_sentence}\n"
        f"{summary}\n\n"
        "【Buffalo 观点】\n"
        "热点本身不是结论，真正重要的是它可能影响哪一个物流节点。Buffalo 建议相关卖家及时核对船期、清关资料和末端配送安排，"
        "并以承运人、港口或政府部门的最新正式通知作为执行依据。\n\n"
        "Buffalo 持续整理南非物流公开信息，帮助团队更快发现风险、准备预案；品牌建议不替代官方公告。"
    )
    common = {"url": source_url, "source_title": raw_title, "publisher": publisher, "excerpt": summary[:1000]}
    evidence = [{"claim": sentence.rstrip("。！？!?"), **common} for sentence in truth_guard.risky_sentences(title, body)]
    attachments = []
    asset_id = hotspot.get("asset_id")
    if asset_id:
        asset = db.get_asset(asset_id)
        if asset and asset.get("status") == "active":
            attachments.append({"type": asset["file_type"], "path": asset["filepath"], "asset_id": asset["id"], "license": asset.get("license"), "attribution": asset.get("attribution"), "source_url": asset.get("source_url")})
    return {"title": title, "body": body, "hashtags": ["南非物流", "Buffalo物流", "物流热点"], "source_refs": evidence, "attachments": attachments, "hotspot_id": hotspot["id"]}
