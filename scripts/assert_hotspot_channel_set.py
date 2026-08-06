#!/usr/bin/env python3
"""铁律 C：断言当前生效 YouTube 频道清单 == 定稿 7 台（2026-08-06 回纳 SA Today/South Africa Now）。回落默认表则非 0 退出。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import hotspot_video_sources  # noqa: E402

EXPECTED = [
    {"name": "eNCA", "url": "https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA"},
    {"name": "Newzroom Afrika", "url": "https://www.youtube.com/@NewzroomAfrikaTV"},
    {"name": "CNBC Africa", "url": "https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ"},
    {"name": "BusinessDayTV", "url": "https://www.youtube.com/@BusinessDayTelevision"},
    {"name": "Transnet NPA", "url": "https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"},
    {"name": "SA Today", "url": "https://www.youtube.com/@SAtoday"},
    {"name": "South Africa Now", "url": "https://www.youtube.com/@SouthAfricaNow1"},
]

FORBIDDEN_NAMES = {"SABC Digital News", "Moneyweb", "SABC"}


def _source_label() -> str:
    raw = os.environ.get("SA_HOTSPOT_VIDEO_CHANNELS_JSON", "")
    if not raw.strip():
        return "default_fallback"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return "default_fallback(invalid_json)"
    valid = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if name and url.startswith(("https://www.youtube.com/", "https://youtube.com/")):
            valid += 1
    return "env_override" if valid else "default_fallback(no_valid_entries)"


def main() -> int:
    effective = hotspot_video_sources.configured_channels()
    source = _source_label()
    eff = {(c["name"], c["url"].rstrip("/")) for c in effective}
    exp = {(c["name"], c["url"].rstrip("/")) for c in EXPECTED}
    forbidden_hit = sorted({c["name"] for c in effective if c["name"] in FORBIDDEN_NAMES})
    ok = eff == exp and source == "env_override" and not forbidden_hit
    report = {
        "ok": ok,
        "source": source,
        "effective": [{"name": n, "url": u} for n, u in sorted(eff)],
        "expected": [{"name": n, "url": u} for n, u in sorted(exp)],
        "missing": [{"name": n, "url": u} for n, u in sorted(exp - eff)],
        "extra": [{"name": n, "url": u} for n, u in sorted(eff - exp)],
        "forbidden_hit": forbidden_hit,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        print("ASSERT FAIL: configured_channels() != 定稿 7 台（或仍 default_fallback / 含禁入台）", file=sys.stderr)
        return 2
    print("ASSERT OK: YouTube env_override == 定稿 7 台")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
