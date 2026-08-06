"""真实运行方案 B：公开信源 → 证据包 → 三种内部样本。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
import hotspot_fetcher
import sample_harness
import evidence_harness
import video_renderer


STATIC_DIR = PROJECT_ROOT / "static"


def _admin_id() -> int | None:
    user = db.get_user_by_username("admin")
    return int(user["id"]) if user else None


def _save_iol_reference(created_by: int | None) -> None:
    db.upsert_inspiration_item({
        "source_type": "secondary_discovery",
        "source_role": "hotspot_discovery",
        "source_url": "https://iol.co.za/news/south-africa/",
        "canonical_url": "https://iol.co.za/news/south-africa/",
        "title": "IOL South Africa News",
        "summary": "二级热点发现入口；事实需回查官方或第二独立来源，媒体不可自动素材化。",
        "author": "Independent Online",
        "media_kind": "article_link",
        "rights_status": "restricted",
        "materialization_status": "reference_only",
        "created_by": created_by,
    })


def _render_internal_preview(bundle: dict, created_by: int | None) -> dict:
    if created_by is None:
        raise RuntimeError("没有可记录渲染任务的管理员账号")
    asset_ids = {asset["id"] for asset in db.list_assets(status="active")}
    script = video_renderer.normalize_script(
        {
            **bundle["video"],
            "voice": "冰糖",
            "watermark": "内部测试｜素材待确认",
            "tier": "internal_preview",
        },
        asset_ids,
    )
    render_job_id = f"sample-{bundle['id']}"
    if not db.get_render_job(render_job_id):
        db.create_render_job(render_job_id, script, "冰糖", created_by)
    else:
        db.update_render_job(
            render_job_id, status="pending", stage="等待渲染", progress=0,
            output_path=None, error=None,
        )
    video_renderer.render_job(
        render_job_id,
        STATIC_DIR,
        output_size=(540, 960),
        output_name=f"sample-{bundle['id']}.mp4",
        tts_provider="local_macos",
    )
    job = db.get_render_job(render_job_id)
    if not job or job["status"] != "succeeded":
        raise RuntimeError((job or {}).get("error") or "内部视频预览生成失败")
    db.update_sample_bundle_preview(
        bundle["id"], job["output_path"], job.get("quality_report") or {}
    )
    return job


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行南非热点证据 Harness")
    parser.add_argument("--fetch", action="store_true", help="从 5 个公开官方 Feed 抓取最新热点")
    parser.add_argument("--hotspot-id", type=int, help="指定已有热点 ID")
    parser.add_argument("--brand-evidence-id", type=int, action="append", default=[])
    parser.add_argument("--render-video", action="store_true", help="使用 macOS 本地旁白生成带水印的 540×960 内部视频预览")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "samples")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    db.init_db()
    created_by = _admin_id()
    seeded = hotspot_fetcher.seed_default_sources(created_by)
    _save_iol_reference(created_by)
    fetch_result = None
    if args.fetch:
        fetch_result = asyncio.run(
            hotspot_fetcher.fetch_hotspots(STATIC_DIR, created_by=created_by)
        )
    hotspot_id = args.hotspot_id
    if hotspot_id is None:
        hotspots = db.list_hotspots(limit=100)
        if not hotspots:
            print(json.dumps({
                "status": "no_hotspot",
                "seeded_sources": seeded,
                "fetch": fetch_result,
            }, ensure_ascii=False, indent=2))
            return 2
        hotspot_id = int(hotspots[0]["id"])
    package = evidence_harness.build_package(
        hotspot_id,
        created_by=created_by,
        brand_evidence_ids=args.brand_evidence_id,
    )
    bundle = sample_harness.generate_bundle(
        package["id"], created_by=created_by, output_root=args.output
    )
    preview = None
    if args.render_video:
        preview = _render_internal_preview(bundle, created_by)
    print(json.dumps({
        "status": "ok",
        "seeded_sources": seeded,
        "fetch": fetch_result,
        "hotspot_id": hotspot_id,
        "evidence_package_id": package["id"],
        "package_status": package["status"],
        "bundle_id": bundle["id"],
        "output_dir": bundle["output_dir"],
        "preview_path": preview.get("output_path") if preview else None,
        "publish_allowed": False,
        "quality_issues": bundle["quality_issues"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
