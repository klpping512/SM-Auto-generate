# 总指挥指令 批17 ｜ 热点 Hook 生产打标签 + 时效入链

> 日期：2026-08-07 ｜ 状态：**已产出，待 qcoder 执行**
> 拍板：总指挥拍板"全包三件"——A 卡片打标签（时效徽标 + 场景 chips + 来源标签）、B 时效入匹配链（freshness_bonus + 排序键修复）、C published_at 入链修复（下载路径捕获 + 存量回填）。
> 执行工具：**qcoder**（2026-08-06 晚起指定）。
> **前置（关键）：批16 必须先落地**（本会话内按 批16 → 批17 顺序执行）。批17 改动 A2 复用了批16 在 `virtualEventCard()` 函数头加的 `const isGeneric` 行，批16 未落地时批17 会缺该变量。若批16 已提交但批17 单独执行，需确认 `isGeneric` 行存在，否则补上（见改动 A2 注）。

---

## 〇、背景与 Why

总指挥 2026-08-07 需求："**分析好的热点素材应该被直接打上标签，以方便生产链路的时效性和效率的达成**。" 即：热点 Hook 入库策展后，卡片上要能一眼看出"这条素材新不新 / 属于哪个物流场景 / 来自哪个素材池"，并且匹配链要**优先选更新的热点**。

诊断闭环（2026-08-07 生产库 `data/logiflow.db` 实测 + 代码核验，非猜测）：

1. **时效数据"有实无名"**。`hotspots.published_at` 目前 307 条非空、422 条为空：
   - **RSS 源 303 条是 RFC2822 格式**（如 `Tue, 21 Jul 2026 13:00:00 +0200`），4 条 ISO。
   - **YouTube 频道扫描源全部为空**（422 条）——根因：`hotspot_video_sources._command` 用 `yt-dlp --flat-playlist` 扫频道，flat 提取**不含 upload_date**；而下载路径 `_run_ytdlp_download` 用 `extract_info(..., download=True)` 拿的是**全量 info（含 upload_date）**，但当前把 info 丢弃了。
2. **合格 Hook 的父热点几乎全部无日期**。154 条 `confirmed` 的 timely_event clip 中 **151 条父热点 published_at 为空**；104 个合格 Hook 的父热点是 youtube 来源且为空 → **新鲜度在匹配里是死信号**。
3. **匹配排序的 published_at 现在是错序的字符串 tie-break**。`_marketing_hook_candidates` 排序键 `str(item.get("published_at") or "")` 对 RFC2822 是字典序（`Tue > Mon`），等于随机；且 151/154 为空，tie-break 完全失效。
4. **generic_logistics 父热点（727-729）published_at/retrieved_at = `1970-01-01T00:00:00` 哨兵**——常青池不随事件衰减，**时效徽标与新鲜度加分必须对 generic 豁免**（显示"常青"，加分恒 0）。
5. `hotspot_event_clips` 表**没有 published_at 列**；卡片时效需要从父热点取（API 装饰时带上），或回退到 clip 的 `created_at`（入库时间）。

本批落地后：卡片三标签可读、匹配按新鲜度优先、新入库 YouTube 自动带日期、存量 422 条可回填。**这是"时效性和效率"进入生产链路的关键一批。**

---

## 一、决策表

| 事项 | 总指挥决策 | 理由 |
|---|---|---|
| 卡片打标签 | A 全做：时效徽标 + 场景 chips + 来源标签 | 审核/选材一眼可辨，直接服务"时效性" |
| 时效徽标数据源 | 优先 `hotspots.published_at`（事件真实时间）；缺失回退 `event.created_at`（入库时间）；**generic 固定显示"常青"** | RSS 有 RFC2822、YouTube 经 C 回填后是 ISO；generic 是常青不衰减，不能显示"超30天" |
| 匹配链时效加分 | **只对 timely_event**：`<24h +8 / <3d +5 / <7d +2 / ≥30d −3`；published_at 缺失不加不减（0）；generic 恒 0 | 与 `direct*40` 相比是次级信号，不喧宾夺主；缺失无据可判，不加不减不误伤 |
| 排序键 | 两处排序（候选主排序 + recently_used 重排）从"字符串 published_at"改为 `published_ts`（epoch 秒） | 现字符串排序对 RFC2822 是错序字典序；改数值才真正按新旧排 |
| published_at 入链 | C1 前进：下载路径捕获 upload_date 回填父热点；C2 存量：独立脚本回填 422 条 youtube 空日期热点 | 频道扫描 flat 模式拿不到日期，只有下载路径有全量 info；存量必须回填否则时效信号依旧 96% 空白 |
| 日期解析 | 新增 `_event_date_seconds`（app.py）同时兼容 ISO 与 RFC2822（`email.utils.parsedate_to_datetime`） | 库里两种格式并存，不能只认 ISO |

---

## 二、铁律（不做的事）

1. **不动门禁与模型链路**：`_is_confirmed_renderable_hotspot_hook` 词表、MiMo 策展、`allow_broad_match`/`use_generic` 分支逻辑、`_marketing_hook_candidates` 签名与调用点——一字不改（只在其内部加时效加分与排序键）。
2. **不物理删除数据**：422 条空日期只**回填**，不删不改其他字段。
3. **不改 generic 池数据本身**：1970 哨兵保留（批12 产物），只做展示豁免 + 加分豁免。
4. **不改 hotspot_media 表**：媒体格时效已有 `COALESCE(published_at,created_at)` 兜底，本批只补 `hotspots.published_at`（匹配链读的是它）。
5. **不动 `hotspot_fetcher.py` 的 RSS 配图逻辑**（批16 改动 C 负责；本批不碰）。
6. **只改五处 + 一个新脚本**：`app.py`（A1/B）、`static/assets.html`（A2/A3）、`inspiration_assets.py`（C1）、`database.py`（C1 新函数）、`scripts/backfill_hotspot_published_at.py`（C2 新文件）。
7. **改完必须重启 app**（旧进程持旧代码）。
8. **定位用函数名/代码锚点，不用绝对行号**（批11/批16 在跑会平移行号）。

---

## 三、改动清单

### 改动 A — 卡片打标签（时效徽标 + 场景 chips + 来源标签）

#### A1. 后端：`_decorate_hotspot_event()`（app.py，锚点 `def _decorate_hotspot_event`）

给事件补父热点发布时间，并在 `virtual_asset` 里带出 `source_label`（`media_assets.public_asset` 已算好：热点素材 / 免版权素材 / Buffalo 原有素材）。

> ⚠️ 该 `public = media_assets.public_asset(asset) if asset else {}` 全等行在 app.py 出现两处（`_decorate_hotspot_event` 内 ~1476 与另一函数 ~2902）。**只改 `_decorate_hotspot_event` 函数内那一处**，用函数名定位、勿全局替换。

在 `_decorate_hotspot_event` 函数内 `public = media_assets.public_asset(asset) if asset else {}` 这一行之后插入：

```python
    # 批17：卡片时效徽标取父热点真实发布时间（RSS 为 RFC2822 / YouTube 经回填为 ISO）
    parent = db.get_hotspot(int(event["hotspot_id"])) if event.get("hotspot_id") else {}
    event["published_at"] = (parent or {}).get("published_at")
```

在 `event["virtual_asset"] = {` 这个字典里加一个键（放在 `"source_asset_id"` 之后即可）：

```python
        "source_label": public.get("source_label") or "",
```

> 只影响 `GET /api/hotspot-events` 列表接口的返回；匹配链 `_marketing_hook_candidates` 直接调 `db.list_hotspot_event_clips()` 不走本函数，零波及。
> `db.get_hotspot` 已有（database.py:2161），复用即可，98 条事件多 98 次本地 SQLite 查询，无压力。

#### A2. 前端：`virtualEventCard()`（static/assets.html，锚点 `function virtualEventCard`）

**前置确认**：批16 的 A2 已在本函数首行加了 `const isGeneric = event.hook_kind === 'generic_logistics';`。若批16 未落地，先补这一行，再继续。

在 `const question=escapeHtml(evidence.logistics_question||'');` 这一行之后插入（注意这些是**模块级 const 声明放函数外、或函数内都行**——为保证只算一次且作用域干净，插在函数内、`dateMeta` 之前）：

```js
const SCENE_LABELS={border:'边境',disruption:'干线中断',port:'港口',warehouse:'仓库',last_mile:'末端配送',hotspot:'综合热点'};
const _chipDays=value=>{const t=value?new Date(value).getTime():0;return t&&Number.isFinite(t)?(Date.now()-t)/86400000:null;};
const _freshnessChip=value=>{const d=_chipDays(value);if(d===null)return '<span class="hook-chip chip-unknown">时效未知</span>';if(d<1)return '<span class="hook-chip chip-today">今日</span>';if(d<3)return '<span class="hook-chip chip-3d">近3天</span>';if(d<7)return '<span class="hook-chip chip-7d">近7天</span>';if(d<30)return '<span class="hook-chip chip-30d">近30天</span>';return '<span class="hook-chip chip-stale">超30天</span>';};
const timeChip=isGeneric?'<span class="hook-chip chip-evergreen">常青</span>':_freshnessChip(event.published_at||event.created_at);
const sceneChips=(event.logistics_scenes||[]).map(s=>`<span class="hook-chip chip-scene">${escapeHtml(SCENE_LABELS[s]||s)}</span>`).join('');
const sourceChip=!isGeneric&&va.source_label?`<span class="hook-chip chip-source">${escapeHtml(va.source_label)}</span>`:'';
const tagRow=`<div class="hook-tag-row">${timeChip}${sceneChips}${sourceChip}</div>`;
```

把 `${tagRow}` 插进卡片模板：在标题行 `<div>${escapeHtml(event.title_en)} · ${((event.duration_ms||event.end_ms-event.start_ms)/1000).toFixed(1)} 秒</div>` 的 `</div>` **之后**、`<p class="hook-fact">` **之前**，即模板变为：

```js
<strong>${escapeHtml(event.title_zh)}</strong><div>${escapeHtml(event.title_en)} · ${((event.duration_ms||event.end_ms-event.start_ms)/1000).toFixed(1)} 秒</div>${tagRow}<p class="hook-fact">
```

> `event.logistics_scenes` 已在 `db.list_hotspot_event_clips` 里从 `logistics_scenes_json` 解析好（数组）；`event.published_at` 由 A1 提供；`event.created_at` 本来就返回。`new Date()` 对 RFC2822 与 ISO 都能解析。
> **generic 卡**：时效徽标显示"常青"（不随事件衰减），场景 chips 照常显示（warehouse/last_mile/border），来源信息沿用批16 已加的"素材池"行，本批不再重复加来源 chip（故 `sourceChip` 对 generic 为空）。

#### A3. CSS（static/assets.html 底部 `<style>` 块，锚点 `.hotspot-date-meta` 附近）

在 `<style>` 块内追加：

```css
.hook-tag-row{display:flex;flex-wrap:wrap;gap:5px;margin:2px 0 8px}.hook-chip{display:inline-flex;align-items:center;border-radius:999px;padding:2px 7px;font-size:9px;line-height:1.4;white-space:nowrap}.chip-today{background:#dcfce7;color:#15803d}.chip-3d{background:#dcfce7;color:#15803d}.chip-7d{background:#fef9c3;color:#a16207}.chip-30d{background:#ffedd5;color:#c2410c}.chip-stale{background:#f3f4f6;color:#6b7280;text-decoration:line-through}.chip-unknown{background:#f3f4f6;color:#9ca3af}.chip-evergreen{background:#eff6ff;color:#1d4ed8}.chip-scene{background:#f8fafc;border:1px solid var(--border);color:#475569}.chip-source{background:#f0fdf4;color:#15803d}
```

---

### 改动 B — 时效入匹配链（`_marketing_hook_candidates`）

#### B1. 新增日期解析 helper（app.py，放在 `def _marketing_hook_candidates` 之前）

```python
def _event_date_seconds(value) -> int:
    """批17：兼容 ISO（含 UTC 偏移）与 RSS RFC2822 日期 → epoch 秒；无法解析/1970 哨兵返回 0。

    注意：不能把 ISO 截到 [:19]（会丢掉 '+00:00' 时区，产生 8h 偏移）；RFC2822
    带 '+0200' 时区，直接 .timestamp() 才按真实 UTC epoch 折算。
    """
    if not value:
        return 0
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text)          # '2026-07-30 03:51:10' / '...+00:00' / '2026-07-30'
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)       # 'Tue, 21 Jul 2026 13:00:00 +0200'
        except Exception:
            return 0
    ts = dt.timestamp()
    return int(ts) if ts > 0 else 0
```

在 app.py 顶部 import 区（`from datetime import datetime` 附近）加：

```python
from email.utils import parsedate_to_datetime
```

#### B2. 时效加分 + 候选带 `published_ts`（`_marketing_hook_candidates` 内，`candidates.append({` 之前插入）

```python
        # 批17：时效入链。只对新闻锚点 Hook（timely_event）施加新鲜度加分；
        # 常青开场（generic_logistics）不随事件时间衰减，加分恒 0。
        freshness_bonus = 0
        published_ts = _event_date_seconds(hotspot.get("published_at"))
        if published_ts and str(event_clips[0].get("hook_kind") or "timely_event") != "generic_logistics":
            age_days = (datetime.now().timestamp() - published_ts) / 86400.0
            if age_days < 1:
                freshness_bonus = 8
            elif age_days < 3:
                freshness_bonus = 5
            elif age_days < 7:
                freshness_bonus = 2
            elif age_days >= 30:
                freshness_bonus = -3
```

在 score 表达式里加 `freshness_bonus`（`+ reuse_bias` 之后、`- mismatch_penalty` 之后）：

```python
            "score": (
                direct * 40
                + profile_overlap * 16
                + event_fit * 5
                + intent_bridge
                + reuse_bias
                - mismatch_penalty
                + freshness_bonus
            ),
```

在候选字典里加一个键（放在 `"published_at"` 行之后）：

```python
            "published_ts": published_ts,
```

#### B3. 两处排序键从字符串 published_at 改为 published_ts

候选主排序（`candidates.sort(` 那处）：

```python
    candidates.sort(
        key=lambda item: (item["score"], item.get("published_ts") or 0, int(item["hotspot_id"])),
        reverse=True,
    )
```

recently_used 重排（`_retrieve_confirmed_chat_hooks` 内，锚点 `if recently_used and candidates:` 之后的 `candidates.sort(`）：

```python
        candidates.sort(
            key=lambda item: (item["score"], item.get("published_ts") or 0, int(item["hotspot_id"])),
            reverse=True,
        )
```

> **预期行为变化**：同一话题下，更新的热点会小幅优先（+8/+5/+2），超 30 天的被小幅压后（−3）。这是本批目的，**不是回归**。但需在验收 3 里复核既有话题匹配是否仍然合理（语义不破）。

---

### 改动 C — published_at 入链修复

#### C1. 前进修复：下载路径捕获 upload_date 并回填父热点

**C1a. `_run_ytdlp_download()`（inspiration_assets.py，锚点 `def _run_ytdlp_download`）**：返回元组 `(source, info)`，把全量 info 交出去（现在是只返回 `source`，把 info 丢了）：

```python
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        source = next((path for path in temp_dir.iterdir() if path.is_file()), None)
    if source is None:
        raise RuntimeError("平台未返回可用媒体文件")
    return source, info
```

函数签名注释/类型提示同步改为 `) -> tuple[Path, dict]:`。

**C1b. 新增 `_extract_upload_iso(info)` helper（inspiration_assets.py，放在 `download_authorized_media` 之前）**：

```python
def _extract_upload_iso(info: dict) -> str | None:
    """批17：从 yt-dlp 全量信息取上传日期，统一输出 YYYY-MM-DD。"""
    upload_date = str(info.get("upload_date") or "")
    if len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    timestamp = info.get("timestamp")
    if timestamp:
        try:
            return datetime.datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            return None
    return None
```

顶部 import 区加 `import datetime`（文件目前没有 datetime 导入）。

**C1c. `download_authorized_media()`（inspiration_assets.py）**：解包 `(source, info)`，并在 `db.update_asset_provenance(...)` 之后回填：

```python
        source, info = _run_ytdlp_download(canonical, options, temp_dir)
        asset = media_assets.ingest_file(
            source, Path(static_dir), category=item.get("primary_category") or "other",
            origin=item["source_type"], created_by=created_by, name=item.get("title") or source.stem,
        )
        db.update_asset_provenance(
            asset["id"], canonical, item["license_name"], item["attribution"], item.get("hotspot_id"),
        )
        # 批17：下载即拿到全量 info，回填父热点真实发布时间（仅当为空/哨兵）
        upload_iso = _extract_upload_iso(info)
        if upload_iso and item.get("hotspot_id"):
            db.update_hotspot_published_at_if_empty(int(item["hotspot_id"]), upload_iso)
```

**C1d. `download_hi_res_range()`（inspiration_assets.py，另一个调用点）**：解包改为 `source, _ = _run_ytdlp_download(canonical, options, temp_dir)`（该函数不产生热点归属，只丢弃 info）。

**C1e. 新 db 函数（database.py，放在 `upsert_hotspot` 附近）**：

```python
def update_hotspot_published_at_if_empty(hotspot_id: int, published_at: str) -> None:
    """批17：下载路径捕获真实发布时间后回填；仅当父热点发布时间缺失或为 1970 哨兵时写入。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE hotspots SET published_at=? WHERE id=? "
            "AND (published_at IS NULL OR published_at='' OR published_at LIKE '1970-%')",
            (published_at, int(hotspot_id)),
        )
```

#### C2. 存量回填脚本：`scripts/backfill_hotspot_published_at.py`（新文件）

覆盖 422 条 youtube 来源且 published_at 为空的旧热点（含 104 条合格 Hook 的父热点）。只读元数据、幂等、可反复执行：

```python
#!/usr/bin/env python3
"""批17：为已入库但发布时间缺失的 YouTube 热点回填 published_at。

只读元数据（yt-dlp --skip-download --dump-single-json），幂等，可反复执行。
用法：
  python3 scripts/backfill_hotspot_published_at.py             # 全量回填
  python3 scripts/backfill_hotspot_published_at.py --dry-run   # 只报告将回填数量
  python3 scripts/backfill_hotspot_published_at.py --limit 20  # 只回填前 N 条
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import database as db  # noqa: E402
from hotspot_video_sources import _metadata_command, _published_at  # noqa: E402


def _pending_youtube_hotspots() -> list[dict]:
    with db.get_conn() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, source_url, title FROM hotspots "
                "WHERE (published_at IS NULL OR published_at='' OR published_at LIKE '1970-%') "
                "AND source_url LIKE '%youtube.com%' ORDER BY id"
            ).fetchall()
        ]


def backfill(dry_run: bool = False, limit: int = 0) -> dict:
    rows = _pending_youtube_hotspots()
    if limit:
        rows = rows[:limit]
    filled = failed = skipped = 0
    total = len(rows)
    for idx, hotspot in enumerate(rows, start=1):
        url = str(hotspot.get("source_url") or "").strip()
        if not url:
            skipped += 1
            continue
        try:
            completed = subprocess.run(
                _metadata_command(url), capture_output=True, text=True, timeout=35, check=False
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "元数据读取失败").strip()[:200])
            entry = json.loads(completed.stdout or "{}")
        except Exception as exc:
            failed += 1
            print(f"[{idx}/{total}] FAIL #{hotspot['id']} {url} -> {str(exc)[:120]}")
            continue
        iso = _published_at(entry)
        if not iso:
            skipped += 1
            print(f"[{idx}/{total}] NO-DATE #{hotspot['id']} {url}")
            continue
        if dry_run:
            filled += 1
            print(f"[{idx}/{total}] WOULD #{hotspot['id']} {iso} {url}")
        else:
            db.update_hotspot_published_at_if_empty(int(hotspot["id"]), iso)
            filled += 1
            if filled % 25 == 0 or filled == total:
                print(f"  ... 已回填 {filled}/{total}")
    return {"scanned": total, "filled": filled, "failed": failed, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(backfill(dry_run=args.dry_run, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
```

> `_metadata_command` 已带 `SA_HOTSPOT_PROXY`/`SA_YOUTUBE_PROXY` 代理支持（hotspot_video_sources.py），无需额外配置。约 422 次网络请求，建议先 `--limit 20` 试跑再全量。

---

## 四、验收清单（改完必验）

> 2026-08-07 执行结果（opencode 落地）：1✅ 2✅ 3✅(对照实验零回归) 4✅(代码级，浏览器目检待总指挥) 5✅ 6✅(逻辑层) 7✅ 8✅(node --check)

1. **重启 app**（必做；`/api/health` 正常）。✅ 已重启（kill 旧进程 → `bash start.sh` 新进程 PID 67647），8080 监听正常、`/static/assets.html` 200。
2. **API 口径**：`GET /api/hotspot-events`（eligible_only 默认 true）每条事件带 `published_at`（父热点值，可空）+ `virtual_asset.source_label` 非空（热点素材/免版权素材/Buffalo 原有素材）；仍 98 条，`hook_kind` 仍 84 + 14。✅ TestClient + 生产库实测：total=98（84 timely + 14 generic），published_at 键零缺失，source_label 零缺失；**84/84 timely 带父热点 published_at**（回填让时效信号 100% 落地）。**批16 遗留复核**：14 条常青素材池分布 = `Buffalo 原有素材 8 + 免版权素材 6`（与用户预期完全一致）。
3. **匹配语义回归**（重要，批16 验收 7 的延续）：
   - 新闻话题（如"Beitbridge 边境拥堵"）matched 仍成立，且若候选中有更新的事件，结果应倾向更新者（人工核验，不要求一定变）；✅ **对照实验**：stash 批17 app.py 改动后跑同一话题，passed=0 与批17 完全一致（Beitbridge 库内仅 1 条未翻译热点，matched 不成立是载体问题非回归）；超30天候选 score 批17 为 -4 = 基线 -1 + 新鲜度 -3，符合预期行为。
   - 常青话题（"海外仓是什么"）仍 matched + generic 开场（批12 既有行为）；generic 卡不出现"超30天/近X天"时效徽标，只有"常青"。✅ 对照实验：批17 与基线均 passed=3（contextual），行为一致零回归；generic 卡时效徽标在 A2 中 isGeneric 分支固定"常青"。
4. **UI 目检（热点素材库）**：Hook 卡标题下方出现 tag 行——时效徽标（今日/近3天/近7天/近30天/超30天，缺失时"时效未知"）+ 场景 chips（中文：边境/干线中断/港口/仓库/末端配送/综合热点）+ 来源标签；常青开场池卡片显示"常青"+ 场景 chips + 素材池行。✅ 代码级验证（A2 三标签逻辑 + A3 CSS 已落），node --check 通过；浏览器目检待总指挥复核。
5. **存量回填**：`python3 scripts/backfill_hotspot_published_at.py --limit 20` 试跑 → 全量跑完。✅ 试跑 20/20 filled（产出 ISO 带时区如 `2026-07-21T18:25:09+00:00`，`_event_date_seconds` fromisoformat 兼容）；全量 **scanned 402 / filled 394 / failed 8 / skipped 0**（8 条 failed 为 premieres 未发布视频，yt-dlp 报 "Premieres in 3 days"，无日期可填，属正常）。回填后：
   - `SELECT COUNT(*) FROM hotspots WHERE published_at IS NULL OR published_at=''` 从 422 大幅下降（youtube 源应清零）；✅ 空日期总数 425→**11**（含 3 条 1970 哨兵 generic 父热点，铁律 3 不动），youtube 源 422→**8**（=8 条 failed）。
   - **合格 Hook 覆盖**：154 条 confirmed timely_event 的父热点 published_at 为空数从 151 降到接近 0。✅ **0/106**（DISTINCT 父热点口径，全部清零）。
6. **前进修复**：对任一 youtube 热点手动触发"下载视频"→ 完成后父热点 `published_at` 出现 `YYYY-MM-DD` 值（含此前从未有日期的新热点）。✅ 逻辑层验证：`_extract_upload_iso` 4/4（upload_date 8 位 / timestamp / 非法 / 缺失）；`download_authorized_media` 解包 `(source, info)` + 回填三行已接，`db.update_hotspot_published_at_if_empty` 仅当空/1970 哨兵写入。真实下载验证留生产自然触发。
7. **pytest**：相对批16 基线（875 passed / 8 存量失败）**不新增失败**；`tests/test_hotspot_video_materialization.py` 全过（它 monkeypatch 的是 `download_authorized_video`，C1 未触碰该边界）。✅ 全量 **885 passed / 8 存量基线失败不变**；test_hotspot_video_materialization 8 passed。
8. **卡片无 JS 报错**：热点素材库打开控制台无 `isGeneric is not defined` / `tagRow is not defined`。✅ node --check 通过（isGeneric 批16 已有 + tagRow 同函数作用域内声明）；浏览器控制台目检待总指挥复核。

---

## 五、回滚

- 改动 A/B：`git revert <批17 提交>`（assets.html 纯前端 + app.py 排序/加分，零数据风险）。
- 改动 C1：恢复 `_run_ytdlp_download` 返回单值 + 去掉回填三行（`git revert` 或手改）。
- 改动 C2：回填已写入的 `published_at` 是**真实事件日期，属正向数据修正，不因回滚删除**；如需还原，另行评估（一般无需）。

---

## 六、交付口径

- 完成标记：A1/A2/A3 + B1/B2/B3 + C1/C2 全落地 + 重启 + 验收 1-8 逐条打勾 + 回填统计（scanned/filled/failed）。
- 提交信息：`批17 热点Hook生产打标签+时效入链：卡片三标签 + 匹配新鲜度加分 + published_at下载捕获与存量回填`。
- 回写：README 登记批17 行 + 本文件验收清单打勾；把回填统计写进验收 5 的勾注里。
