"""定点处理 za-stock 免版权素材：产出镜头段 + 分类标签（含 primary_category）。

为什么需要这个脚本：
- owned-matching 读的是资产镜头段（asset_segments），段由 process_asset_job 产生；
- `POST /api/assets/process-pending` 按资产 id 升序只取前 200 条，za-stock 是最高 id，
  会被库存里 378 条 youtube 待审资产挤到队尾，永远轮不到；
- 批量 process-pending 也会把 vision 调用浪费在那批 review_required 的 youtube 旧资产上。

用法（项目根目录，App 运行的同一环境）：
    python3 scripts/process_za_stock.py [--asset-id 866 --asset-id 867 ...]

不带参数 = 处理所有 source='za_stock_license' 且需要处理的资产。
可选 --asset-id 只处理指定几条（试跑/复查用）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import asset_processing
import database as db

STATIC_DIR = PROJECT_ROOT / "static"


def _targets(asset_ids: list[int]) -> list[dict]:
    assets = db.list_assets_needing_processing(limit=500)
    rows = [a for a in assets if a.get("source") == "za_stock_license"]
    if asset_ids:
        wanted = set(asset_ids)
        rows = [a for a in rows if int(a["id"]) in wanted]
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int, action="append", dest="asset_ids", default=[])
    args = parser.parse_args()

    targets = _targets(args.asset_ids)
    print(f"待处理 za-stock 素材: {len(targets)} 条")
    ok = fail = 0
    for asset in targets:
        job_id = db.create_asset_processing_job(
            asset["id"], requested_by=None,
            processing_version=asset_processing.PROCESSING_VERSION,
        )
        try:
            result = await asyncio.to_thread(
                asset_processing.process_asset_job, job_id, STATIC_DIR
            )
            ok += 1
            print(f"  ok id={asset['id']} cat={asset['category']} "
                  f"segments={result.get('segments')} primary={result.get('primary_category')}")
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻塞整批
            fail += 1
            print(f"  FAIL id={asset['id']} cat={asset['category']}: {exc}")

    print(f"\n完成: ok={ok} fail={fail}")
    if fail:
        print("有失败条目：单独用 --asset-id 复查对应 id（先看上方失败原因）。")


if __name__ == "__main__":
    asyncio.run(main())
