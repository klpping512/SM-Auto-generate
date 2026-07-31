"""Register a license-verified Mixkit clip as generic logistics b-roll."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
import media_assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    db.init_db()
    admin = db.get_user_by_username("admin")
    if not admin:
        raise SystemExit("缺少 admin 用户")
    asset = media_assets.ingest_file(
        args.source, PROJECT_ROOT / "static", category="delivery", origin="mixkit_license",
        created_by=admin["id"], name="Mixkit 港口集装箱船｜通用物流说明画面",
    )
    db.update_asset_provenance(
        asset["id"],
        "https://mixkit.co/free-stock-video/cargo-ship-full-of-containers-4011/",
        "Mixkit Stock Video Free License",
        "Mixkit clip 4011；仅作为通用港口/运输背景，不代表南非现场或 Buffalo 能力。",
    )
    print(asset["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
