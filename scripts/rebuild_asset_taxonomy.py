"""以项目配置的视觉模型重建指定素材的双标签 taxonomy。

示例：
    ./start.sh 的同一环境中执行：
    python3 scripts/rebuild_asset_taxonomy.py --asset-id 145 --asset-id 146

默认只处理 5 条旧素材，避免一次全库重建造成不可控的模型调用；需要扩展时显式
指定更多 ``--asset-id``，或以 ``--limit`` 选择旧素材。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import asset_processing
import database as db
import model_router


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重建素材主场景、物流标签和 Buffalo 品牌标签")
    parser.add_argument("--asset-id", type=int, action="append", dest="asset_ids", default=[])
    parser.add_argument("--limit", type=int, default=5, help="未指定 asset-id 时选择的旧素材数量，最大 100")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    db.init_db()
    if not model_router.key_is_available("vision_tagger"):
        raise SystemExit("未配置 vision_tagger 密钥；请通过项目 .env / 运行环境启用后重试。")
    if args.asset_ids:
        assets = []
        for asset_id in dict.fromkeys(args.asset_ids):
            asset = db.get_asset(asset_id)
            if not asset:
                raise SystemExit(f"素材不存在：{asset_id}")
            assets.append(asset)
    else:
        assets = db.list_assets_needing_taxonomy_rebuild(
            asset_processing.PROCESSING_VERSION, min(max(args.limit, 1), 100)
        )
    results = []
    for asset in assets:
        job_id = db.create_asset_processing_job(asset["id"], processing_version=asset_processing.PROCESSING_VERSION)
        result = asset_processing.process_asset_job(job_id, PROJECT_ROOT / "static")
        updated = db.get_asset(asset["id"]) or {}
        results.append({
            "asset_id": asset["id"], "name": asset["name"], "status": result.get("status"),
            "primary_category": updated.get("primary_category"),
            "processing_status": updated.get("processing_status"),
        })
    print(json.dumps({"processing_version": asset_processing.PROCESSING_VERSION, "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
