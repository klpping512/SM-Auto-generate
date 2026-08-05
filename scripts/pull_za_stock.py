#!/usr/bin/env python3
"""南非免版权 b-roll 批量下载器（Pexels + Pixabay）→ LOCAL_ASSET_ROOT/za-stock/<category>/。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import local_asset_import  # noqa: E402

QUERY_MAP: dict[str, list[str]] = {
    "customs": [
        "cargo customs inspection", "container port inspection", "shipping documents desk",
        "airport cargo terminal", "container yard crane", "freight paperwork",
    ],
    "delivery": [
        "cargo ship containers", "container truck highway", "last mile delivery courier",
        "delivery van city", "port container loading", "logistics truck fleet",
    ],
    "facility": [
        "forklift warehouse", "conveyor belt parcels", "package sorting machine",
        "barcode scanner warehouse", "automated sorting line",
    ],
    "warehouse": [
        "warehouse shelves aerial", "workers picking warehouse", "pallet stacking warehouse",
    ],
    "staff": [
        "warehouse workers team", "logistics office meeting", "dispatch team working",
    ],
    "brand": [
        "cape town aerial drone", "johannesburg skyline", "south africa highway aerial",
    ],
    "customer": [
        "online shopping unboxing", "ecommerce customer parcel", "person receiving package",
    ],
    "other": [],
}

BASE_NOTE = (
    "免版权通用背景，非南非现场、非 Buffalo 自有能力；"
    "口播不得宣称南非现场或自有仓/车队。"
)
CUSTOMS_NOTE = (
    "通用港口/清关背景，非南非现场，口播仅可表述为『备货待清关』；"
    + BASE_NOTE
)


def _proxy() -> str | None:
    value = (
        os.environ.get("SA_HOTSPOT_PROXY")
        or os.environ.get("SA_YOUTUBE_PROXY")
        or ""
    ).strip()
    return value or None


def stock_root() -> Path:
    return local_asset_import.configured_root() / "za-stock"


def provenance_note(category: str) -> str:
    return CUSTOMS_NOTE if category == "customs" else BASE_NOTE


def pick_pexels_file(video: dict, min_height: int) -> dict | None:
    files = [f for f in (video.get("video_files") or []) if isinstance(f, dict)]
    candidates = []
    for item in files:
        height = int(item.get("height") or 0)
        link = str(item.get("link") or "")
        file_type = str(item.get("file_type") or "").casefold()
        looks_mp4 = link.casefold().endswith(".mp4") or "mp4" in link.casefold() or "video/mp4" in file_type
        if height >= min_height and link and looks_mp4:
            candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda f: abs(int(f.get("height") or 0) - 1080))
    return candidates[0]


def write_sidecar(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def download_file(client: httpx.Client, url: str, dest: Path) -> None:
    with client.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
        tmp.replace(dest)


def pull_pexels(
    client: httpx.Client,
    *,
    api_key: str,
    category: str,
    query: str,
    per_query: int,
    max_seconds: int,
    min_height: int,
    root: Path,
    dry_run: bool,
) -> tuple[int, int, int]:
    added = skipped = failed = 0
    headers = {"Authorization": api_key}
    try:
        resp = client.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={
                "query": query,
                "per_page": min(15, max(1, per_query)),
                "orientation": "portrait",
                "size": "medium",
            },
            timeout=30.0,
        )
        if resp.status_code == 429:
            time.sleep(2.0)
            resp = client.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={
                    "query": query,
                    "per_page": min(15, max(1, per_query)),
                    "orientation": "portrait",
                    "size": "medium",
                },
                timeout=30.0,
            )
        resp.raise_for_status()
        videos = resp.json().get("videos") or []
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] pexels query={query!r}: {exc}")
        return 0, 0, 1

    time.sleep(0.4)
    for video in videos:
        try:
            duration = int(video.get("duration") or 0)
            if duration > max_seconds:
                skipped += 1
                continue
            chosen = pick_pexels_file(video, min_height)
            if not chosen:
                skipped += 1
                continue
            vid = video.get("id")
            dest = root / category / f"za_{category}_pexels_{vid}.mp4"
            sidecar = Path(str(dest) + ".json")
            if dest.exists():
                skipped += 1
                continue
            user = video.get("user") or {}
            author = f"{user.get('name') or 'unknown'} {user.get('url') or ''}".strip()
            meta = {
                "source_url": str(video.get("url") or ""),
                "license": "Pexels License",
                "author": author,
                "category": category,
                "note": provenance_note(category),
            }
            if dry_run:
                print(f"[dry-run] would download {dest.name}")
                added += 1
                continue
            download_file(client, str(chosen.get("link")), dest)
            write_sidecar(sidecar, meta)
            added += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] pexels item failed: {exc}")
            failed += 1
    return added, skipped, failed


def pull_pixabay(
    client: httpx.Client,
    *,
    api_key: str,
    category: str,
    query: str,
    per_query: int,
    max_seconds: int,
    min_height: int,
    root: Path,
    dry_run: bool,
) -> tuple[int, int, int]:
    added = skipped = failed = 0
    try:
        resp = client.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": api_key,
                "q": query,
                "per_page": max(3, min(20, per_query)),
                "safesearch": "true",
                "video_type": "all",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] pixabay query={query!r}: {exc}")
        return 0, 0, 1

    time.sleep(0.7)
    for hit in hits:
        try:
            duration = int(hit.get("duration") or 0)
            if duration > max_seconds:
                skipped += 1
                continue
            videos = hit.get("videos") or {}
            large = videos.get("large") or {}
            medium = videos.get("medium") or {}
            file_url = str(large.get("url") or medium.get("url") or "")
            height = int(large.get("height") or medium.get("height") or 0)
            if not file_url or height < min_height:
                skipped += 1
                continue
            vid = hit.get("id")
            dest = root / category / f"za_{category}_pixabay_{vid}.mp4"
            sidecar = Path(str(dest) + ".json")
            if dest.exists():
                skipped += 1
                continue
            meta = {
                "source_url": str(hit.get("pageURL") or ""),
                "license": "Pixabay Content License",
                "author": str(hit.get("user") or "unknown"),
                "category": category,
                "note": provenance_note(category),
            }
            if dry_run:
                print(f"[dry-run] would download {dest.name}")
                added += 1
                continue
            download_file(client, file_url, dest)
            write_sidecar(sidecar, meta)
            added += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] pixabay item failed: {exc}")
            failed += 1
    return added, skipped, failed


def run_pull(
    categories: list[str],
    *,
    per_query: int,
    max_seconds: int,
    min_height: int,
    dry_run: bool,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    root = stock_root()
    root.mkdir(parents=True, exist_ok=True)
    pexels_key = os.environ.get("PEXELS_API_KEY", "").strip()
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    summary: dict[str, Any] = {}
    owns_client = client is None
    client = client or httpx.Client(proxy=_proxy(), timeout=30.0)
    try:
        for category in categories:
            queries = QUERY_MAP.get(category) or []
            added = skipped = failed = 0
            for query in queries:
                if pexels_key:
                    a, s, f = pull_pexels(
                        client, api_key=pexels_key, category=category, query=query,
                        per_query=per_query, max_seconds=max_seconds, min_height=min_height,
                        root=root, dry_run=dry_run,
                    )
                    added += a; skipped += s; failed += f
                if pixabay_key:
                    a, s, f = pull_pixabay(
                        client, api_key=pixabay_key, category=category, query=query,
                        per_query=per_query, max_seconds=max_seconds, min_height=min_height,
                        root=root, dry_run=dry_run,
                    )
                    added += a; skipped += s; failed += f
            if not pexels_key and not pixabay_key:
                print(f"[warn] no API keys; skip category={category}")
            print(f"category={category} added={added} skipped={skipped} failed={failed}")
            summary[category] = {"added": added, "skipped": skipped, "failed": failed}
    finally:
        if owns_client:
            client.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull royalty-free ZA stock b-roll")
    parser.add_argument(
        "--category", nargs="*", default=["customs", "facility", "delivery"],
        help="categories to pull (default: customs facility delivery)",
    )
    parser.add_argument("--per-query", type=int, default=3)
    parser.add_argument("--max-seconds", type=int, default=20)
    parser.add_argument("--min-height", type=int, default=720)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cats = [c.strip() for c in args.category if c.strip() in QUERY_MAP]
    if not cats:
        print("no valid categories", file=sys.stderr)
        return 2
    print(f"root={stock_root()}")
    run_pull(
        cats,
        per_query=args.per_query,
        max_seconds=args.max_seconds,
        min_height=args.min_height,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
