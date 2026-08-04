"""P1 话题扫描：循环调用现成诊断端点，汇总三种死法分布。只读，零副作用。"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("DIAG_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
COOKIE = os.environ.get("DIAG_COOKIE", "")
TOKEN = os.environ.get("DIAG_TOKEN", "")

TOPICS = [
    "清关", "末端配送", "仓储", "干线运输",
    "关税", "物流成本", "港口", "供应链中断",
]

OUT_JSON = Path(__file__).resolve().parents[1] / "docs" / "总指挥指令-2026-08-04" / "sweep-result.json"


def fetch(topic: str) -> dict:
    qs = urllib.parse.urlencode({"topic": topic})
    req = urllib.request.Request(f"{BASE_URL}/api/diagnostics/owned-matching?{qs}")
    if COOKIE:
        req.add_header("Cookie", COOKIE)
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def top_inventory(inv: dict, n: int = 3) -> str:
    if not inv:
        return "-"
    items = sorted(inv.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:n]
    return " ".join(f"{k}:{v}" for k, v in items)


def main() -> int:
    rows = []
    raw = {}
    for topic in TOPICS:
        try:
            data = fetch(topic)
        except Exception as exc:  # noqa: BLE001 —— 观测脚本，逐条容错继续
            rows.append((topic, f"ERROR: {str(exc)[:60]}", "-", "-", "-", "-", "-", "-", "-", "-"))
            raw[topic] = {"error": str(exc)}
            continue
        diag = data.get("diagnostics") or {}
        funnel = diag.get("funnel") or {}
        eligible = diag.get("eligible_categories")
        rows.append((
            topic,
            str(diag.get("verdict") or "-"),
            str(data.get("starving_side") or "-"),
            str(funnel.get("is_video", "-")),
            str(funnel.get("not_licensed_stock", "-")),
            str(funnel.get("category_match", "-")),
            str(funnel.get("after_dedup", "-")),
            str(data.get("hotspot_pool", "-")),
            ",".join(eligible) if eligible else "*",  # * = 无节点约束（全类可用）
            top_inventory(diag.get("category_inventory") or {}),
        ))
        raw[topic] = data

    header = ["topic", "verdict", "starving", "is_video", "not_stock",
              "cat_match", "dedup", "hotspot_pool", "eligible", "top_inventory"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(c) for c in row) + " |")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整 JSON 已写入：{OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
