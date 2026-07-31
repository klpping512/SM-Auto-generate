"""从单一证据包生成视频、图文和公众号三种内部样本。"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import database as db
import model_router
import semantic_matching


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "data" / "samples"


def _video(package: dict, claim_ids: list[str]) -> dict:
    fact = package["fact_claims"][0]
    title = fact["source_title"] or fact["claim"]
    brand = package["brand_claims"]
    brand_line = (
        brand[0]["claim"]
        if brand
        else "企业需要把仓储、资料核对和配送节点逐项落实，并保留人工确认。"
    )
    scenes = [
        {"scene": 1, "duration": 3, "voiceover": f"南非物流出现一条值得关注的官方更新：{title}", "visual": "当前热点现场或双语标题卡", "business_role": "事实钩子", "scene_role": "hotspot_hook"},
        {"scene": 2, "duration": 4, "voiceover": fact["excerpt"][:120], "visual": "当前热点事实画面与来源信息", "business_role": "事实说明", "scene_role": "fact_context"},
        {"scene": 3, "duration": 4, "voiceover": "对跨境卖家来说，先核对运输安排、清关资料和末端交接，不用未经确认的消息替代正式通知。", "visual": "热点影响解释或信息卡", "business_role": "风险提示", "scene_role": "impact_explainer"},
        {"scene": 4, "duration": 14, "voiceover": brand_line, "visual": "Buffalo 自有仓库、扫描、分拣或装车画面", "business_role": "品牌承接", "scene_role": "brand_proof"},
        {"scene": 5, "duration": 10, "voiceover": "把热点转成可执行清单，再根据官方进展更新方案。需要关注南非物流变化，可以保存这份检查框架。", "visual": "Buffalo 自有装车、出库与品牌收尾画面", "business_role": "行动承接", "scene_role": "brand_close"},
    ]
    assignments = semantic_matching.assign_candidates(
        semantic_matching.build_semantic_atoms({
            "scenes": scenes, "orientation": "portrait", "hotspot_id": package["hotspot_id"],
        }),
        db.list_asset_segments(limit=500),
        top_k=3,
    )
    for scene, assignment in zip(scenes, assignments):
        candidates = []
        for item in assignment["candidates"]:
            candidate = dict(item)
            segment = db.get_asset_segment(candidate["segment_id"])
            if segment:
                candidate.update({
                    "asset_id": segment["asset_id"],
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "description": segment.get("description") or "",
                })
            candidates.append(candidate)
        scene["candidates"] = candidates
        if candidates:
            selected = candidates[0]
            segment = db.get_asset_segment(selected["segment_id"])
            scene["asset_segment_id"] = selected["segment_id"]
            scene["match_score"] = selected["match_score"]
            scene["match_review_required"] = bool(selected.get("review_required"))
            scene["match_reasons"] = selected.get("reasons") or []
            if segment:
                scene.update({
                    "asset_id": segment["asset_id"],
                    "asset_start_ms": segment["start_ms"],
                    "asset_end_ms": segment["end_ms"],
                })
            scene["library_origin"] = selected.get("library_origin")
    hotspot_missing = [
        scene for scene in scenes
        if scene["scene_role"] in semantic_matching.HOTSPOT_SCENE_ROLES
        and not scene.get("candidates")
    ]
    owned_missing = [
        scene for scene in scenes
        if scene["scene_role"] in semantic_matching.OWNED_SCENE_ROLES
        and not scene.get("candidates")
    ]
    material_gaps = []
    if hotspot_missing:
        material_gaps.append(
            f"热点素材未就绪：{len(hotspot_missing)} 个事实分镜没有当前热点的已授权图片或视频片段"
        )
    if owned_missing:
        material_gaps.append(
            f"原本素材未就绪：{len(owned_missing)} 个品牌分镜没有可解释的 Buffalo 自有镜头"
        )
    return {
        "title": f"南非物流观察｜{title}",
        "duration_target": 35,
        "orientation": "portrait",
        "tier": "internal_preview",
        "watermark": "内部测试｜素材待确认",
        "claim_ids": claim_ids,
        "scenes": scenes,
        "material_status": "blocked" if material_gaps else "ready",
        "material_gaps": material_gaps,
    }


def _carousel(package: dict, claim_ids: list[str]) -> dict:
    fact = package["fact_claims"][0]
    title = fact["source_title"] or fact["claim"]
    brand = package["brand_claims"]
    brand_text = brand[0]["claim"] if brand else "品牌能力数据尚待内部确认，本样本只提供流程建议。"
    return {
        "title": f"一条南非物流更新，应该怎么看？",
        "claim_ids": claim_ids,
        "pages": [
            {"page": 1, "role": "cover", "title": "南非物流更新", "body": title},
            {"page": 2, "role": "fact", "title": "官方信息", "body": fact["excerpt"][:180]},
            {"page": 3, "role": "impact", "title": "先看三个节点", "body": "运输安排、清关资料、末端交接"},
            {"page": 4, "role": "checklist", "title": "运营检查清单", "body": "核对正式通知；检查资料版本；确认仓库与配送衔接。"},
            {"page": 5, "role": "brand", "title": "Buffalo 承接", "body": brand_text},
            {"page": 6, "role": "source", "title": "来源与边界", "body": f"来源：{fact['publisher']}。具体执行以原文最新版本为准。"},
        ],
    }


def _wechat_body(package: dict) -> str:
    fact = package["fact_claims"][0]
    title = fact["source_title"] or fact["claim"]
    summary = fact["excerpt"]
    publisher = fact["publisher"]
    brand = package["brand_claims"]
    brand_paragraph = (
        f"在企业自身的履约链路中，目前能够对外引用的内部证据是：{brand[0]['claim']}这项能力的价值，不在于借热点作夸张表达，而在于把公开信息转换成仓库、资料和配送环节的具体动作。"
        if brand
        else "在没有已确认品牌能力证据之前，内容不写具体时效、覆盖比例或成功案例，只讨论团队可以公开验证的流程动作。这样做会少一些戏剧性，却能避免把外部事件误写成自身业绩。"
    )
    paragraphs = [
        f"最近，一条来自{publisher}的南非物流信息值得跨境团队关注：{title}。原文摘要指出，{summary}这条信息首先是一项外部事实，不等于所有线路都会受到同样影响，更不能直接推导出某一家物流企业的服务结果。",
        "面对热点，最容易出现的错误是只追求一个醒目的结论。真正影响履约的，往往是运输安排、清关资料、仓库操作和末端交接之间的连续性。任何一个节点的信息没有及时同步，后续团队就可能用旧版本资料做新任务，增加沟通和返工成本。",
        "第一步应当回到原始来源。确认发布机构、发布时间和适用范围，再把信息拆成可以执行的问题：涉及哪个地区，影响哪个时间段，针对哪些业务参与者，是否存在后续更新。社交媒体上的转述可以帮助发现线索，但不能替代政府部门、港口、税务机构或承运人的正式通知。",
        "第二步是检查自己的订单和资料。运营人员需要把受关注订单单独列出，核对发票、装箱单、收件信息和申报资料是否一致，并记录由谁确认、何时确认。这里不需要额外的复杂系统，一张责任清楚的检查表，通常比一段笼统的风险提醒更有用。",
        "第三步是检查仓库与配送衔接。仓库是否知道任务优先级，包裹状态是否能被前端团队看到，异常件由谁处理，末端配送是否收到同一版本的信息。这些问题看起来基础，却决定团队能否在外部条件变化时保持清晰，而不是临时依靠个人记忆救火。",
        brand_paragraph,
        "内容团队也需要保留证据边界。新闻正文负责说明发生了什么，自有素材负责展示日常流程，两者不能被剪辑成虚假的事件现场。版权不明确的图片和视频只作为灵感链接；进入成片的画面必须来自自有素材或许可证明确的来源，并保留署名和原始链接。",
        "对于视频和图文，同一个事实可以使用不同表达结构。短视频适合先提出外部变化，再给出三项检查动作；图文适合把事实、影响、清单和来源分成多页；长文则需要解释为什么这些动作有关联。形式可以变化，但事实 claim、来源 URL 和品牌证据不能变化。",
        "如果后续官方信息发生更新，旧内容应被标记为过期并重新检查，而不是继续用作“今日热点”。如果当前自有素材与口播不匹配，系统应列出缺少的仓库、扫描、装车或系统操作画面，交给人工补充，而不是用弱相关镜头填满时间。",
        "这套方法的重点不是让模型写得更多，而是让每一句事实都能回到来源，让每一句品牌表述都能回到内部依据。热点只是入口，最终输出仍然要服务于清晰、可信和可执行的物流沟通。具体业务安排，请以相关机构最新正式通知和实际订单情况为准。",
    ]
    body = "\n\n".join(paragraphs)
    if len(body) < 800:
        body += "\n\n发布前还应由业务、内容和素材负责人分别确认事实、品牌与画面，三项均通过后再进入发布队列。"
    return body[:1200]


def _wechat(package: dict, claim_ids: list[str]) -> dict:
    fact = package["fact_claims"][0]
    return {
        "title": f"从一条南非物流更新，看跨境履约如何做好节点准备",
        "claim_ids": claim_ids,
        "body": _wechat_body(package),
        "source_refs": [
            {
                "claim_id": claim["id"],
                "claim": claim["claim"],
                "url": claim["source_url"],
                "publisher": claim["publisher"],
                "source_title": claim["source_title"],
                "excerpt": claim["excerpt"],
            }
            for claim in package["fact_claims"]
        ],
    }


def _write_outputs(output_dir: Path, bundle: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, key in (
        ("video-script.json", "video"),
        ("carousel.json", "carousel"),
        ("manifest.json", "manifest"),
    ):
        (output_dir / filename).write_text(
            json.dumps(bundle[key], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    wechat = bundle["wechat"]
    (output_dir / "wechat.md").write_text(
        f"# {wechat['title']}\n\n{wechat['body']}\n", encoding="utf-8"
    )


def generate_bundle(
    package_id: str,
    *,
    created_by: int | None = None,
    output_root: Path | None = None,
) -> dict:
    package = db.get_evidence_package(package_id)
    if not package or not package["fact_claims"]:
        raise ValueError("证据包不存在或没有事实证据")
    bundle_id = uuid4().hex
    model_router.create_budget(bundle_id)
    claim_ids = [
        claim["id"] for claim in package["fact_claims"] + package["brand_claims"]
    ]
    video = _video(package, claim_ids)
    carousel = _carousel(package, claim_ids)
    wechat = _wechat(package, claim_ids)
    issues = list(video.get("material_gaps") or [])
    if package["status"] != "ready":
        issues.append("品牌证据尚未确认，样本不得包含具体能力或业绩承诺")
    if any(not scene.get("candidates") for scene in video["scenes"]):
        issues.append("部分口播没有可解释的本地镜头候选")
    weak_match_count = sum(
        1 for scene in video["scenes"] if scene.get("match_review_required")
    )
    if weak_match_count:
        issues.append(
            f"{weak_match_count} 个镜头匹配低于质量门槛，必须人工换镜头后再成片"
        )
    budget = db.get_model_budget(bundle_id)
    manifest = {
        "bundle_id": bundle_id,
        "evidence_package_id": package_id,
        "claim_ids": claim_ids,
        "fact_sources": sorted({claim["source_url"] for claim in package["fact_claims"]}),
        "model_usage": budget,
        "quality_issues": issues,
        "publish_allowed": False,
    }
    output_dir = Path(output_root or DEFAULT_OUTPUT_ROOT) / bundle_id
    payload = {
        "id": bundle_id,
        "evidence_package_id": package_id,
        "status": "internal_preview",
        "publish_allowed": False,
        "quality_issues": issues,
        "video": video,
        "carousel": carousel,
        "wechat": wechat,
        "manifest": manifest,
        "output_dir": str(output_dir),
    }
    _write_outputs(output_dir, payload)
    return db.create_sample_bundle(payload, created_by)
