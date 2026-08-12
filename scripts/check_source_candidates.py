#!/usr/bin/env python3
"""信源连通性探针：YouTube 母片层 + RSS/HTML 线索层。纯探测，不写库、不改配置。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
import hotspot_fetcher  # noqa: E402
import hotspot_lexicon  # noqa: E402
import hotspot_video_sources  # noqa: E402

DEAD_RSS_NAMES = (
    "South African Government",
    "South African Reserve Bank",
    "The Citizen",
    "BusinessTech",
)

RSS_CANDIDATES: list[dict] = [
    {
        "name": "Freight News",
        "role": "candidate",
        "urls": [
            "https://www.freightnews.co.za/rss",
            "https://www.freightnews.co.za/feed",
            "https://www.freightnews.co.za/rss.xml",
        ],
    },
    {
        "name": "Logistics News SA",
        "role": "candidate",
        "urls": [
            "https://logisticsnews.co.za/feed",
            "https://logisticsnews.co.za/feed/",
            "https://logisticsnews.co.za/rss",
        ],
    },
    {
        "name": "Supply Chain News Africa",
        "role": "candidate",
        "urls": [
            "https://scnafrica.com/feed",
            "https://scnafrica.com/feed/",
        ],
    },
    {
        "name": "Moneyweb",
        "role": "baseline",
        "urls": ["https://www.moneyweb.co.za/feed/"],
    },
    {
        "name": "BusinessTech",
        "role": "dead_expect",
        "urls": ["https://businesstech.co.za/news/feed/"],
    },
    {
        "name": "The Citizen",
        "role": "dead_expect",
        "urls": ["https://citizen.co.za/feed/"],
    },
]

YOUTUBE_CHANNELS: list[dict] = [
    {"name": "SABC Digital News", "url": "https://www.youtube.com/@sabcdigitalnews", "role": "keep_observe"},
    {"name": "eNCA", "url": "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA", "role": "keep"},
    {"name": "Newzroom Afrika", "url": "https://www.youtube.com/@NewzroomAfrikaTV", "role": "keep"},
    {"name": "South Africa Now", "url": "https://www.youtube.com/@SouthAfricaNow1", "role": "observe"},
    {"name": "SA Today", "url": "https://www.youtube.com/@SAtoday", "role": "cut_confirm"},
    {"name": "CNBC Africa", "url": "https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ", "role": "candidate"},
    {"name": "BusinessDayTV", "url": "https://www.youtube.com/@BusinessDayTelevision", "role": "candidate"},
    {"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw", "role": "candidate"},
]

MONEYWEB_YT_CANDIDATES = [
    "https://www.youtube.com/channel/UCJAfFuVIJki_tlG41Ud0FUw",
    "https://www.youtube.com/@Moneyweb",
    "https://www.youtube.com/@moneyweb",
    "https://www.youtube.com/@MoneywebZA",
    "https://www.youtube.com/@moneywebza",
]

# 草案：总指挥定稿前的预期生效集（不含 SAN/Moneyweb，待 Step 0 后圈定）
DRAFT_EXPECTED_CHANNELS = [
    {"name": "eNCA", "url": "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA"},
    {"name": "Newzroom Afrika", "url": "https://www.youtube.com/@NewzroomAfrikaTV"},
    {"name": "CNBC Africa", "url": "https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ"},
    {"name": "BusinessDayTV", "url": "https://www.youtube.com/@BusinessDayTelevision"},
    {"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"},
]


def _proxy() -> str:
    return str(os.environ.get("SA_HOTSPOT_PROXY") or os.environ.get("SA_YOUTUBE_PROXY") or "").strip()


def _mask_proxy(proxy: str) -> str:
    if not proxy:
        return "(direct / none)"
    return proxy.split("://")[0] + "://***"


def _is_cloudflare(response: httpx.Response) -> bool:
    if response.status_code not in {403, 429, 503}:
        return False
    if "cf-mitigated" in response.headers:
        return True
    return "cloudflare" in (response.headers.get("server") or "").lower()


def _raw_item_count(xml_text: str) -> int:
    try:
        root = ET.fromstring(xml_text.lstrip("\ufeff \t\r\n"))
    except ET.ParseError:
        return 0
    nodes = root.findall(".//item")
    if not nodes:
        nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    return len(nodes)


def _channel_source() -> tuple[list[dict], str]:
    raw = os.environ.get("SA_HOTSPOT_VIDEO_CHANNELS_JSON", "")
    if not raw.strip():
        return hotspot_video_sources.configured_channels(), "default_fallback"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return hotspot_video_sources.configured_channels(), "default_fallback(invalid_json)"
    valid = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip().rstrip("/")
        if not name or not url:
            continue
        if url.startswith("https://www.youtube.com/") or url.startswith("https://youtube.com/"):
            valid += 1
    if valid == 0:
        return hotspot_video_sources.configured_channels(), "default_fallback(no_valid_entries)"
    return hotspot_video_sources.configured_channels(), "env_override"


def _norm_pair(item: dict) -> tuple[str, str]:
    return (str(item.get("name") or "").strip(), str(item.get("url") or "").strip().rstrip("/"))


def assert_effective_channels(expected: list[dict]) -> dict:
    effective, source = _channel_source()
    eff_set = {_norm_pair(x) for x in effective}
    exp_set = {_norm_pair(x) for x in expected}
    ok = eff_set == exp_set
    return {
        "ok": ok,
        "source": source,
        "effective": [{"name": n, "url": u} for n, u in sorted(eff_set)],
        "expected": [{"name": n, "url": u} for n, u in sorted(exp_set)],
        "missing": [{"name": n, "url": u} for n, u in sorted(exp_set - eff_set)],
        "extra": [{"name": n, "url": u} for n, u in sorted(eff_set - exp_set)],
        "has_sa_today": any(n == "SA Today" or "satoday" in u.lower() for n, u in eff_set),
    }


def probe_youtube(name: str, url: str, role: str, limit: int = 5) -> dict:
    result = {
        "name": name,
        "url": url,
        "role": role,
        "alive": False,
        "count": 0,
        "filter_hits": 0,
        "titles": [],
        "error": "",
    }
    cmd = hotspot_video_sources._command(url, limit)
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:300]
        return result
    if completed.returncode != 0:
        result["error"] = (completed.stderr or completed.stdout or "yt-dlp failed").strip()[:300]
        return result
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        result["error"] = f"json decode: {exc}"
        return result
    entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
    titles = []
    hits = 0
    for entry in entries[:limit]:
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        titles.append(title[:120])
        if hotspot_lexicon.FEED_FILTER_PATTERN.search(title):
            hits += 1
    result["alive"] = len(titles) >= 1
    result["count"] = len(titles)
    result["filter_hits"] = hits
    result["titles"] = titles
    return result


def probe_moneyweb_youtube(limit: int = 5) -> dict:
    attempts = []
    for url in MONEYWEB_YT_CANDIDATES:
        row = probe_youtube("Moneyweb", url, "candidate", limit=limit)
        attempts.append({"url": url, "alive": row["alive"], "count": row["count"], "error": row["error"][:120]})
        if row["alive"]:
            row["moneyweb_attempts"] = attempts
            return row
    return {
        "name": "Moneyweb",
        "url": "",
        "role": "candidate",
        "alive": False,
        "count": 0,
        "filter_hits": 0,
        "titles": [],
        "error": "all candidate URLs failed",
        "moneyweb_attempts": attempts,
    }


def probe_rss_site_sync(client: httpx.Client, site: dict) -> dict:
    out = {
        "name": site["name"],
        "role": site["role"],
        "alive": False,
        "hit_url": "",
        "attempts": [],
        "raw_items": 0,
        "filtered_items": 0,
        "titles": [],
        "cloudflare": False,
        "error": "",
    }
    for url in site["urls"]:
        attempt = {"url": url, "status": None, "cloudflare": False, "error": ""}
        try:
            response = client.get(url, follow_redirects=True, timeout=30.0)
            attempt["status"] = response.status_code
            attempt["cloudflare"] = _is_cloudflare(response)
            if attempt["cloudflare"]:
                out["attempts"].append(attempt)
                out["cloudflare"] = True
                out["error"] = "cloudflare challenge"
                continue
            if response.status_code >= 400:
                attempt["error"] = f"http {response.status_code}"
                out["attempts"].append(attempt)
                out["error"] = attempt["error"]
                continue
            text = response.text
            raw_n = _raw_item_count(text)
            try:
                filtered = hotspot_fetcher.parse_feed(text, {"name": site["name"]})
            except ET.ParseError as exc:
                attempt["error"] = f"parse_feed: {exc}"
                out["attempts"].append(attempt)
                out["error"] = attempt["error"]
                continue
            attempt["raw_items"] = raw_n
            attempt["filtered_items"] = len(filtered)
            out["attempts"].append(attempt)
            if len(filtered) >= 1:
                out["alive"] = True
                out["hit_url"] = str(response.url)
                out["raw_items"] = raw_n
                out["filtered_items"] = len(filtered)
                out["titles"] = [item["title"][:120] for item in filtered[:3]]
                out["cloudflare"] = False
                out["error"] = ""
                return out
            out["error"] = "zero items after FEED_FILTER"
            out["raw_items"] = raw_n
            out["filtered_items"] = 0
        except Exception as exc:  # noqa: BLE001
            attempt["error"] = str(exc)[:200]
            out["attempts"].append(attempt)
            out["error"] = attempt["error"]
    return out


def slot_analysis() -> dict:
    db.init_db()
    sources = db.list_hotspot_sources()
    enabled = [s for s in sources if s.get("enabled")]
    dead_still_enabled = [
        {"id": s["id"], "name": s["name"], "feed_url": s["feed_url"]}
        for s in enabled
        if s.get("name") in DEAD_RSS_NAMES
        or any(token in (s.get("feed_url") or "") for token in ("gov.za/news-feed", "resbank.co.za", "citizen.co.za", "businesstech.co.za"))
    ]
    enabled_count = len(enabled)
    cap = hotspot_fetcher.MAX_ENABLED_SOURCES
    room = max(0, cap - enabled_count)
    return {
        "max_enabled": cap,
        "enabled_count": enabled_count,
        "total_configured": len(sources),
        "dead_still_enabled": dead_still_enabled,
        "room_for_new_enabled": room,
        "RESEED_ORDER_REQUIRED": "yes" if dead_still_enabled or room == 0 else "no",
        "advice": (
            f"先停用 {len(dead_still_enabled)} 个死源腾坑，再 INSERT/启用垂直源；"
            f"当前 enabled={enabled_count}/{cap}，若直接 seed 新源："
            + ("会被标 enabled=False（静默零产出）" if room == 0 else f"最多还能启用 {room} 个")
        ),
        "enabled_names": [s["name"] for s in enabled],
    }


def _print_table(title: str, rows: list[dict], cols: list[tuple[str, str]]) -> None:
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)
    header = " | ".join(label for _, label in cols)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(" | ".join(str(row.get(key, ""))[:80] for key, _ in cols))


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 0 信源连通性探针（不写库）")
    parser.add_argument(
        "--expected-channels-json",
        default="",
        help="预期生效频道 JSON 数组；默认用草案 DRAFT_EXPECTED_CHANNELS（仅作断言对照，不改 env）",
    )
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--skip-rss", action="store_true")
    parser.add_argument("--skip-db", action="store_true", help="跳过只读坑位演算")
    args = parser.parse_args()

    proxy = _proxy()
    print(f"proxy={_mask_proxy(proxy)}")
    print("AUTHORIZED_FLAG=retired  # 批22 不再使用全局绿/黄授权闸")

    if args.expected_channels_json.strip():
        expected = json.loads(args.expected_channels_json)
    else:
        expected = DRAFT_EXPECTED_CHANNELS
    assert_result = assert_effective_channels(expected)
    print()
    print("=" * 88)
    print("铁律 C · 生效频道清单断言")
    print("=" * 88)
    print(f"source={assert_result['source']}")
    print(f"ok={assert_result['ok']}  has_sa_today={assert_result['has_sa_today']}")
    print(f"effective({len(assert_result['effective'])}): " + ", ".join(x["name"] for x in assert_result["effective"]))
    print(f"expected ({len(assert_result['expected'])}): " + ", ".join(x["name"] for x in assert_result["expected"]))
    if not assert_result["ok"]:
        print(f"FAIL missing={assert_result['missing']}")
        print(f"FAIL extra={assert_result['extra']}")
        if assert_result["source"].startswith("default_fallback"):
            print("FAIL 已静默回落到默认表（可能含 SA Today）——写入合法 SA_HOTSPOT_VIDEO_CHANNELS_JSON 前勿当生效集")

    yt_rows: list[dict] = []
    if not args.skip_youtube:
        print()
        print("Probing YouTube (母片层 P0)...")
        for ch in YOUTUBE_CHANNELS:
            print(f"  · {ch['name']} ...", flush=True)
            yt_rows.append(probe_youtube(ch["name"], ch["url"], ch["role"]))
        print("  · Moneyweb (try handles) ...", flush=True)
        yt_rows.append(probe_moneyweb_youtube())
        _print_table(
            "YouTube 存活汇总",
            [
                {
                    "name": r["name"],
                    "role": r["role"],
                    "alive": "YES" if r["alive"] else "NO",
                    "count": r["count"],
                    "yhit": f"{r['filter_hits']}/{r['count']}" if r["count"] else "0/0",
                    "titles": " // ".join(r["titles"][:3]) if r["titles"] else r["error"][:60],
                }
                for r in yt_rows
            ],
            [
                ("name", "频道"),
                ("role", "角色"),
                ("alive", "存活"),
                ("count", "条数"),
                ("yhit", "粗Y-hit"),
                ("titles", "标题样例/错误"),
            ],
        )
        for r in yt_rows:
            if r["name"] == "Moneyweb" and r.get("moneyweb_attempts"):
                print("Moneyweb YT attempts:", json.dumps(r["moneyweb_attempts"], ensure_ascii=False))

    rss_rows: list[dict] = []
    if not args.skip_rss:
        print()
        print("Probing RSS (线索层)...")
        client_kwargs = {"timeout": 30.0, "follow_redirects": True}
        if proxy:
            client_kwargs["proxy"] = proxy
        with httpx.Client(**client_kwargs) as client:
            for site in RSS_CANDIDATES:
                print(f"  · {site['name']} ...", flush=True)
                rss_rows.append(probe_rss_site_sync(client, site))
        _print_table(
            "RSS 存活汇总",
            [
                {
                    "name": r["name"],
                    "role": r["role"],
                    "alive": "YES" if r["alive"] else "NO",
                    "cf": "CF" if r["cloudflare"] else "-",
                    "hit": r["hit_url"] or "-",
                    "filt": f"{r['filtered_items']}/{r['raw_items']}" if r["raw_items"] or r["filtered_items"] else "-",
                    "titles": " // ".join(r["titles"][:2]) if r["titles"] else r["error"][:60],
                }
                for r in rss_rows
            ],
            [
                ("name", "信源"),
                ("role", "角色"),
                ("alive", "可用"),
                ("cf", "CF"),
                ("hit", "命中URL"),
                ("filt", "过滤后/原始"),
                ("titles", "样例/错误"),
            ],
        )

    slots = None
    if not args.skip_db:
        print()
        print("=" * 88)
        print("铁律 A · 坑位演算（只读）")
        print("=" * 88)
        slots = slot_analysis()
        print(json.dumps(slots, ensure_ascii=False, indent=2))

    print()
    print("=" * 88)
    print("建议启用清单（供总指挥圈定，本脚本不改任何配置）")
    print("=" * 88)
    yt_alive = [r for r in yt_rows if r["alive"]]
    rss_alive = [r for r in rss_rows if r["alive"] and r["role"] in {"candidate", "baseline"}]
    print("YouTube alive:", ", ".join(f"{r['name']}(Y-hit {r['filter_hits']}/{r['count']})" for r in yt_alive) or "(none)")
    print("RSS alive:", ", ".join(f"{r['name']}→{r['hit_url']}" for r in rss_alive) or "(none)")
    print()
    print("批22 提醒：管理员新增信源后仍需检查来源健康、许可证和真实画面，不再翻全局授权闸。")
    print("铁律 E 提醒：日后 H-hit 须把 budget/JSON/server-disconnect 技术失败从分母剔除并单列。")

    summary = {
        "channel_assert": assert_result,
        "youtube": yt_rows,
        "rss": rss_rows,
        "slots": slots,
    }
    out_path = PROJECT_ROOT / "docs" / "总指挥指令-2026-08-04" / "step0-source-probe-result.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    # Step 0 本身允许「当前生效 ≠ 草案预期」（尚未写 env）；用醒目码但不阻断探针完成。
    # 仅当断言失败时返回 2，方便 CI；交互跑仍已打印全文。
    return 0 if assert_result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
