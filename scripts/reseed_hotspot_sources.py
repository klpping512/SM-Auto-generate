#!/usr/bin/env python3
"""批22：补齐官方、港口/边境、道路和货运信源，并断言启用集。

只负责补齐数据库中的可信源，不依赖旧的全局授权开关。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
import hotspot_fetcher  # noqa: E402

DISABLE_MATCHERS = (
    ("name", "South African Government"),
    ("name", "South African Reserve Bank"),
    ("name", "The Citizen"),
    ("name", "BusinessTech"),
    ("url_substr", "gov.za/news-feed"),
    ("url_substr", "resbank.co.za"),
    ("url_substr", "citizen.co.za"),
    ("url_substr", "businesstech.co.za"),
)

EXPECTED_ENABLED_NAMES = {source["name"] for source in hotspot_fetcher.DEFAULT_OFFICIAL_SOURCES}

FREIGHT = {
    "name": "Freight News",
    "url": "https://www.freightnews.co.za/rss",
    "allowed_domains": ["freightnews.co.za"],
}


def _is_dead(source: dict) -> bool:
    name = str(source.get("name") or "")
    url = str(source.get("feed_url") or "")
    for kind, value in DISABLE_MATCHERS:
        if kind == "name" and name == value:
            return True
        if kind == "url_substr" and value in url:
            return True
    return False


def _snapshot() -> list[dict]:
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "enabled": bool(s["enabled"]),
            "feed_url": s["feed_url"],
        }
        for s in db.list_hotspot_sources()
    ]


def reseed(*, dry_run: bool = False) -> dict:
    db.init_db()
    before = _snapshot()
    actions: list[str] = []

    # 1) 先停用死源腾坑
    for source in db.list_hotspot_sources():
        if not source.get("enabled") or not _is_dead(source):
            continue
        actions.append(f"disable#{source['id']} {source['name']}")
        if not dry_run:
            db.update_hotspot_source(
                source["id"],
                source["name"],
                source["feed_url"],
                source["allowed_domains"],
                False,
            )

    enabled_count = sum(1 for s in db.list_hotspot_sources() if s.get("enabled"))
    cap = hotspot_fetcher.MAX_ENABLED_SOURCES

    # 2) 再加 / 启用 Freight News
    existing = next(
        (s for s in db.list_hotspot_sources() if FREIGHT["url"] in s["feed_url"] or s["name"] == FREIGHT["name"]),
        None,
    )
    if existing is None:
        if enabled_count >= cap:
            raise SystemExit(
                f"坑位仍满 enabled={enabled_count}/{cap}，拒绝 INSERT Freight（铁律 A）。"
                f"已计划动作: {actions}"
            )
        actions.append(f"insert {FREIGHT['name']} enabled=1")
        if not dry_run:
            db.create_hotspot_source(
                FREIGHT["name"],
                FREIGHT["url"],
                FREIGHT["allowed_domains"],
                None,
                True,
            )
    elif not existing.get("enabled"):
        if enabled_count >= cap:
            raise SystemExit(f"坑位仍满，无法启用已有 Freight News id={existing['id']}")
        actions.append(f"enable#{existing['id']} Freight News")
        if not dry_run:
            db.update_hotspot_source(
                existing["id"],
                FREIGHT["name"],
                FREIGHT["url"],
                FREIGHT["allowed_domains"],
                True,
            )
    else:
        actions.append("freight already enabled")

    # 3) 补齐 DEFAULT 中缺失的保留源（仅当坑位允许）
    if not dry_run:
        hotspot_fetcher.seed_default_sources()

    after = _snapshot()
    enabled_names = {s["name"] for s in after if s["enabled"]}
    ok = enabled_names == EXPECTED_ENABLED_NAMES
    return {
        "ok": ok,
        "actions": actions,
        "before": before,
        "after": after,
        "enabled_names": sorted(enabled_names),
        "expected_enabled_names": sorted(EXPECTED_ENABLED_NAMES),
        "missing": sorted(EXPECTED_ENABLED_NAMES - enabled_names),
        "extra": sorted(enabled_names - EXPECTED_ENABLED_NAMES),
        "enabled_count": len(enabled_names),
        "max_enabled": cap,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reseed hotspot RSS sources (disable dead → add Freight)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assert-only", action="store_true", help="只断言当前启用集，不改库")
    args = parser.parse_args()
    if args.assert_only:
        db.init_db()
        enabled_names = {s["name"] for s in db.list_hotspot_sources() if s.get("enabled")}
        ok = enabled_names == EXPECTED_ENABLED_NAMES
        report = {
            "ok": ok,
            "enabled_names": sorted(enabled_names),
            "expected_enabled_names": sorted(EXPECTED_ENABLED_NAMES),
            "missing": sorted(EXPECTED_ENABLED_NAMES - enabled_names),
            "extra": sorted(enabled_names - EXPECTED_ENABLED_NAMES),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if ok else 2
    report = reseed(dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        print("ASSERT FAIL: RSS enabled set != expected", file=sys.stderr)
        return 2
    print("ASSERT OK: enabled set matches batch22 default sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
