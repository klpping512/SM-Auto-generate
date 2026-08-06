"""Render one internal preview from a hotspot event plus Buffalo-owned video segments.

This is an acceptance harness: it never publishes, always adds an internal watermark,
and writes only a normal render job plus its output under ``static/uploads``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
import hotspot_intake_sop
import hotspot_logistics_planner
import hotspot_preview_narration
import hotspot_video_planner
import video_renderer


def main() -> int:
    parser = argparse.ArgumentParser(description="生成热点 + Buffalo 双素材库内部预览")
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--nodes", default="末端", help="逗号分隔的物流节点，例如：末端,配送")
    parser.add_argument("--output-name", default="dual-library-acceptance.mp4")
    parser.add_argument("--tts-provider", choices=("qwen", "local_macos"), default="qwen")
    parser.add_argument("--narration-model", choices=("qwen", "rule"), default="qwen",
                        help="默认强制内部 Qwen 按锁定证据改写旁白；rule 仅用于离线调试")
    parser.add_argument("--exclude-owned-asset-ids", default="",
                        help="逗号分隔的 Buffalo 资产 ID；批量验收时排除已用于其他成片的品牌素材")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    db.init_db()
    event = db.get_hotspot_event_clip(args.event_id)
    if not event:
        raise SystemExit(f"热点事件不存在：{args.event_id}")
    owner = db.get_user_by_username("admin")
    if not owner:
        raise SystemExit("缺少 admin 用户，无法记录内部渲染任务")
    source = db.get_hotspot(int(event["hotspot_id"])) or {}
    excluded_owned_assets = set()
    for raw in str(args.exclude_owned_asset_ids or "").split(","):
        try:
            excluded_owned_assets.add(int(raw.strip()))
        except ValueError:
            continue
    owned = [
        item for item in db.list_asset_segments(limit=20_000)
        if not item.get("asset_hotspot_id")
        and int(item.get("asset_id") or 0) not in excluded_owned_assets
    ]
    owned_images = [
        item for item in db.list_assets(file_type="image", status="active")
        if not item.get("hotspot_id")
        and int(item.get("id") or 0) not in excluded_owned_assets
    ]
    related = db.list_hotspot_event_clips(asset_id=event["asset_id"], hotspot_id=event["hotspot_id"])
    brief = hotspot_logistics_planner.build_brief(
        {**source, **event}, owned,
        {"id": "acceptance", "raw_input": args.topic, "subject": args.topic,
         "angle": f"从当前热点出发，解释{args.topic}的实际准备与风险边界。",
         "audience": "南非跨境卖家", "logistics_nodes": [item.strip() for item in args.nodes.split(",") if item.strip()], "platforms": ["douyin"]},
    )
    # The source hook set has already been selected and fact-checked by the
    # internal model.  Keep those 2–3 specific clips together in this
    # acceptance render; do not re-filter the second shot by title keywords.
    brief["approved_hook_event_ids"] = [int(item["id"]) for item in related if item.get("clip_status") == "ready"]
    scenes = hotspot_video_planner.plan_followup_scenes(
        brief, related, owned, owned_images=owned_images,
    )
    hotspot_count = sum(item.get("evidence_type") == "hotspot_video" for item in scenes)
    owned_count = sum(item.get("evidence_type") == "owned_video" for item in scenes)
    required_hotspots = min(2, len(brief["approved_hook_event_ids"]))
    if hotspot_count < required_hotspots or owned_count < 4:
        raise SystemExit(
            f"证据不足：热点={hotspot_count}/{required_hotspots}，Buffalo={owned_count}/4；不生成填充视频"
        )
    narration = {"mode": "rule"}
    if args.narration_model == "qwen":
        rag_evidence = hotspot_intake_sop.retrieve_service_evidence({
            "title": args.topic, "summary": brief.get("hotspot_title") or "",
        })
        generated, narration = hotspot_preview_narration.generate_narration(
            args.topic, brief, scenes, related, rag_evidence,
        )
        for scene, generated_scene in zip(scenes, generated["scenes"]):
            scene.update(generated_scene)
        brief["qwen_title"] = generated["title"]
        brief["qwen_angle"] = generated["angle"]
    scenes = hotspot_video_planner.append_brand_endcard_scenes(scenes)
    job_id = f"acceptance-{uuid4().hex}"
    # 预览与正式成片都固定移动端 9:16；横版新闻源由渲染器满版居中裁切，
    # 不能在同一用户视频中切换为横屏画幅。
    output_size = (540, 960)
    script = {
        "title": f"热点 + Buffalo 验收｜{args.topic}", "scenes": scenes,
        "tier": "internal_preview", "watermark": "内部测试｜双素材库验收",
    }
    db.create_render_job(job_id, script, "冰糖", owner["id"])
    video_renderer.render_job(job_id, PROJECT_ROOT / "static", output_size=output_size,
                              output_name=args.output_name, tts_provider=args.tts_provider)
    job = db.get_render_job(job_id) or {}
    print(json.dumps({
        "job_id": job_id, "status": job.get("status"), "stage": job.get("stage"),
        "output_path": job.get("output_path"), "error": job.get("error"),
        "quality_report": job.get("quality_report"), "brief": brief, "narration": narration, "output_size": output_size,
        "excluded_owned_asset_ids": sorted(excluded_owned_assets),
        "evidence": {"hotspot_video": hotspot_count, "owned_video": owned_count, "scenes": len(scenes)},
    }, ensure_ascii=False, indent=2))
    return 0 if job.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
