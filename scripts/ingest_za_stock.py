#!/usr/bin/env python3
"""把 LOCAL_ASSET_ROOT/za-stock 下已下载素材批量入库（含 provenance sidecar）。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
import local_asset_import  # noqa: E402
import media_assets  # noqa: E402

MEDIA_EXTS = {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"}


def stock_root() -> Path:
    return local_asset_import.configured_root() / "za-stock"


def iter_media(root: Path, category: str | None = None):
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in MEDIA_EXTS:
            continue
        if path.name.startswith("."):
            continue
        rel = path.relative_to(root)
        if category and (not rel.parts or rel.parts[0] != category):
            continue
        yield path


def load_sidecar(media_path: Path) -> dict | None:
    sidecar = Path(str(media_path) + ".json")
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    required = ("source_url", "license", "category", "note")
    if any(not str(data.get(key) or "").strip() for key in required):
        return None
    return data


def ingest_tree(
    root: Path | None = None,
    *,
    category: str | None = None,
    dry_run: bool = False,
    ingest_file=media_assets.ingest_file,
    update_provenance=db.update_asset_provenance,
) -> dict:
    root = root or stock_root()
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"added": 0, "dedup": 0, "skip_sidecar": 0})
    admin = db.get_user_by_username("admin")
    if not admin:
        raise SystemExit("缺少 admin 用户")
    for path in iter_media(root, category=category):
        rel = path.relative_to(root)
        cat = rel.parts[0] if rel.parts else "other"
        sidecar = load_sidecar(path)
        if not sidecar:
            print(f"[warn] missing sidecar, skip: {path}")
            counts[cat]["skip_sidecar"] += 1
            continue
        if dry_run:
            guessed = media_assets.guess_category(path, root.resolve())
            print(f"[dry-run] {path} → category={guessed} provenance_ok")
            counts[cat]["added"] += 1
            continue
        asset = ingest_file(
            path,
            PROJECT_ROOT / "static",
            category="auto",
            origin="za_stock_license",
            created_by=admin["id"],
            import_root=root,
            storage_mode="hardlink",
        )
        if asset.get("_dedup"):
            counts[cat]["dedup"] += 1
            continue
        update_provenance(
            asset["id"],
            sidecar["source_url"],
            sidecar["license"],
            sidecar.get("note") or sidecar.get("attribution") or "",
        )
        counts[cat]["added"] += 1
        print(f"ingested id={asset['id']} cat={cat} file={path.name}")
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--category", default=None)
    args = parser.parse_args()
    db.init_db()
    root = stock_root()
    print(f"root={root}")
    summary = ingest_tree(root, category=args.category, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
