# Cursor 执行指令 · P1 话题扫描（决定匹配算法方向）

> 总指挥背景（先读再动手）：P0 诊断端点已上线（`GET /api/diagnostics/owned-matching`）。
> 单跑 `topic=清关` 发现它死于"内容缺口 + 最严闸门"，语义向量救不了。
> 定 P1 之前，必须先扫一批代表性话题，分清三种死法各占多少，才能决定买哪副药：
> ① `category_mismatch` 且库里有货但闸太严 → 改 NODE_CATEGORY_RULES 放闸（便宜）
> ② 闸内有货但关键词漏配 → 上语义向量（贵、治本）
> ③ `empty_pool` 真没货 → 补内容
>
> 本指令**只做观测汇总**，复用现成 HTTP 端点，**不新增任何匹配逻辑、不改任何选片/闸门/阈值代码**。

---

## 目标

新建一个脚本 `scripts/sweep_matching_diagnostics.py`，对一批话题**循环调用现成端点** `GET /api/diagnostics/owned-matching?topic=X`，把每个话题的 `verdict / starving_side / funnel / hotspot_pool / eligible_categories / category_inventory` 汇成一张 Markdown 表打印到终端，并把完整 JSON 落盘到 `docs/总指挥指令-2026-08-04/sweep-result.json` 供总指挥核。

**硬边界（不得越界）：**
- 不 import `hotspot_video_planner` / `app` 里的匹配函数，不重建 brief、不重建闸门——**只发 HTTP 请求给现成端点**。
- 不改 `app.py`、不改任何 planner/lexicon 文件。
- 脚本是只读工具，零副作用。

---

## 认证与地址（做成可配置，别写死）

端点是 `Depends(require_role(UserRole.ADMIN))`。脚本从环境变量取认证，避免把凭证写进代码：

- `DIAG_BASE_URL`：默认 `http://127.0.0.1:8000`
- `DIAG_COOKIE`：整段 Cookie 头（如浏览器里已登录 admin，直接复制 `Cookie:` 值）
- `DIAG_TOKEN`：如果是 Bearer 方案，则设此项，脚本发 `Authorization: Bearer <token>`

两者给其一即可；都没给就裸请求（本地若关了鉴权也能跑）。

---

## 话题清单（覆盖 8 类死法探针）

```python
TOPICS = [
    "清关",          # customs —— 已知严闸
    "末端配送",       # last_mile —— 闸最松，对照组
    "仓储",          # warehouse —— 库存主力
    "干线运输",       # delivery/transport
    "关税",          # customs/border
    "物流成本",       # cost_risk
    "港口",          # port
    "供应链中断",     # disruption
]
```

---

## 脚本内容（直接创建此文件）

`scripts/sweep_matching_diagnostics.py`：

```python
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

    OUT_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整 JSON 已写入：{OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## 运行方式

```bash
# 先确保服务在跑；DIAG_COOKIE 用你已登录 admin 的浏览器 Cookie 值
export DIAG_COOKIE='<粘贴你的 admin Cookie>'
python scripts/sweep_matching_diagnostics.py
```

---

## 验收清单（Cursor 自检后回报）

- [ ] 脚本只发 HTTP 请求给 `/api/diagnostics/owned-matching`，未 import 任何匹配/brief 函数
- [ ] 未修改 `app.py` 及任何 planner/lexicon/taxonomy 文件
- [ ] 8 个话题跑通，输出 Markdown 表 + `sweep-result.json` 落盘
- [ ] 单个话题请求失败不中断整批（逐条容错）
- [ ] `eligible` 为空时显示 `*`（表示无节点约束），不误判为死法

## 回报格式（给总指挥定 P1）

把终端那张 Markdown 表原样贴回，外加一句你的观察：**哪种 verdict 占多数**（category_mismatch / empty_pool / thin_but_matched / healthy），以及 `starving_side` 里 owned vs hotspot 的分布。这张表决定 P1 买哪副药。
