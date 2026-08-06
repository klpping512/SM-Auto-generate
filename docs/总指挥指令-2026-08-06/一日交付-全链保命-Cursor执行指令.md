# 一日交付：全链保命 Cursor 执行指令 —— 2026-08-06

> 总指挥：ylanlll 拍板范围 = **P0 止血 + P1 闭环 + 数据降权**（一天内）。清债务（死路由/脚本归档/文案统一）挂下批。
> 执行方式：Cursor 逐条粘贴执行，**每批做完跑完测试再进下一批**，不要三批一起改。
> 前置修正：**发布链 huimei 误判已推翻**——发布已重构为 adapter 架构（抖音/小红书走 RPA、twitter/facebook/reddit 走 API），huimei CLI 只影响 8 个次要平台。所以没有"装 huimei"这回事，只有"缺失时显式报错"。
> 测试基线：全量 817 passed / 8 存量 UI 失败（与本批无关）。

---

## 批 1｜P0 止血（约 1.5 小时）

### 指令 #5：修复 `manual_fallback` NameError（潜伏崩溃）

**目标**：`video_generation.py:1213` 引用从未赋值的 `manual_fallback`，当脚本 tier 为 `internal_preview` 且语义质检通过时必抛 `NameError`。改为恒用 `internal_preview`（该分支只在质检通过时进入，manual_preview 是"质检不可用"的降级态，走不到这里，故直写是正确语义）。

**改动 1** — `video_generation.py`，定位这行：
```python
"review_mode": "manual_preview" if manual_fallback else "internal_preview",
```
改为：
```python
"review_mode": "internal_preview",
```

**改动 2** — 删除死函数。`video_generation.py` 定位（约 405-407 行）整个函数：
```python
def allow_manual_preview_fallback(report: dict, decision: QualityDecision) -> bool:
    """Compatibility hook: an unavailable semantic review cannot complete a job."""
    return False
```
整段删除（含上面空行保留一个）。

**改动 3** — `tests/test_video_generation_state.py` 删除 4 处对该函数的引用：
- 第 168 行：`allow_manual_preview_fallback,`（import 语句里删这一项，注意逗号）
- 第 182 行：`assert allow_manual_preview_fallback(report, decision) is False`
- 第 199 行：`from video_generation import allow_manual_preview_fallback, route_video_evaluation_quality` → 只留 `route_video_evaluation_quality`
- 第 208 行：`assert allow_manual_preview_fallback(report, route_video_evaluation_quality(report)) is False`

**验收**：
```bash
pytest tests/test_video_generation_state.py -q
pytest tests/test_video_generation_ui.py -q
```
全绿。再 `grep -rn "manual_fallback\|allow_manual_preview_fallback" --include="*.py" .` 应只剩 0 命中。

**回滚**：恢复 4 处改动即可。

---

### 指令 #6：入库选片 create_budget 补 `reset=True`（消除重跑 BudgetExceeded）

**目标**：`hotspot_hook_intake.py` selection/audit 两处 `create_budget` 未传 `reset=True`，job_id 是确定性 fingerprint，同候选集重跑时 `calls_used` 跨次累加，第二次重跑 `reserve_call` 即抛 `BudgetExceeded`。curator 同场景（`hotspot_hook_curator.py:226/366`）已传 `reset=True`，此处是没抄全。

**改动** — `hotspot_hook_intake.py` 两处 `model_router.create_budget(` 调用各加一个 `reset=True,` 参数：

第一处（约 243 行，selection 的 job_id 后）：
```python
model_router.create_budget(
    job_id, max_calls=2, max_input_tokens=16_000,
    max_output_tokens=model_router.required_output_budget("planner_text", 1_000),
    reset=True,   # 同候选集重跑视为新一次决策尝试，重置 budget，避免确定性 job_id 粘死
    # max_calls=2 = 1 次初始选片 + 1 次 JSON 解析失败重试（同一决策尝试内）。
)
```

第二处（约 269 行，audit 的 job_id 后）：
```python
model_router.create_budget(
    audit_job_id, max_calls=2, max_input_tokens=10_000,
    max_output_tokens=model_router.required_output_budget("critic", 500),
    reset=True,   # 同上：重跑审计视为新尝试
    # max_calls=2 = 1 次初始审计 + 1 次 JSON 解析失败重试。
)
```

**验收**：
```bash
pytest tests/test_hotspot_hook_intake.py -q 2>/dev/null || pytest -q tests/test_dry_run_hotspot_intake_sop.py 2>/dev/null
python3 scripts/dry_run_hotspot_intake_sop.py --sop custom --limit 3
python3 scripts/dry_run_hotspot_intake_sop.py --sop custom --limit 3   # 同一命令重跑第二次
```
第二次重跑不应出现 "模型调用次数预算已用完 / BudgetExceeded"。确认 `create_budget` 签名已有 `reset` 参数（database.py/model_router.py，默认 False）——若有签名差异，把 `reset` 参数加进签名默认 `False`。

**回滚**：删掉两处 `reset=True,` 行。

---

### 指令 #7：huimei 缺失显式报错（不再静默失败）

**目标**：`publish_via_huimei` 在 huimei CLI 不存在时兜底字符串 `"huimei"`，执行必然 FileNotFoundError 被吞成含糊失败。加显式探测返回明确错误，让 8 个依赖 huimei 的平台（微信系/bilibili/微博/快手/头条/知乎/百家号/tiktok）状态可感知。**抖音/小红书/facebook/twitter/reddit 走 RPA/API 适配器，不受影响，无需处理。**

**改动** — `publisher.py`，定位 `publish_via_huimei`（约 64 行），在 `huimei_platform = get_huimei_platform(platform)` 和 `if not huimei_platform:` 检查之后、构建 `cmd` 之前，插入：
```python
    if not shutil.which(HUIMEI_BIN):
        return {
            "success": False,
            "platform": platform,
            "error": "huimei CLI 未安装（仅影响微信系/bilibili/微博/快手/头条/知乎/百家号/tiktok；"
                     "抖音/小红书/facebook/twitter/reddit 走 RPA/API 适配器不受影响）",
        }
```
`shutil` 已在文件顶部 import（第 5 行），无需新增。

**验收**：
```bash
python3 -c "import asyncio, publisher; print(asyncio.run(publisher.publish_via_huimei('douyin', 't', 'c')))"
```
应返回含 "huimei CLI 未安装" 的明确 error，而不是 FileNotFoundError/空失败。跑相关测试确认无回归（`pytest tests/test_media_api.py -q` 若存在）。

**回滚**：删除插入块。

---

## 批 2｜P1 闭环（约半天）

### 指令 #8：重生成血缘闭环（/retry 补血缘 + /resume 硬上限）

**目标**：`/retry` 不设 `prior_job_id`/`regen_attempt`，失败重试（最需要质检历史）血缘断裂；`/resume` 的 `max_attempts=2` 只在 UI 强制，绕 UI 调 API 可无限重跑。

**改动** — `routes/video_generation_routes.py`：

**① /resume**：定位 `if job["status"] != "needs_review": raise HTTPException(...)` 之后、`payload = body.payload...` 之前，加硬上限检查：
```python
        if int(job.get("regen_attempt") or 0) >= 2:
            raise HTTPException(409, "已达重生成上限（2 次），请人工评审脚本或另建项目")
```

**② /retry**：定位 `retried, _ = db.create_or_get_video_generation_job(...)` 之后、`db.add_video_generation_event(retried["id"], "job_retried", ...)` 之前，补血缘（与 /resume 对齐）：
```python
        retried = db.update_video_generation_job(
            retried["id"],
            prior_job_id=job_id,
            regen_attempt=int(job.get("regen_attempt") or 0) + 1,
        ) or retried
```

**验收**：
```bash
pytest tests/test_video_generation_ui.py -q
pytest tests/test_video_generation_state.py -q
```
手测：对一个 `regen_attempt>=2` 的 job 调 `/resume` 应返回 409；对 failed job 调 `/retry` 后查 `GET /api/video-generation/jobs/{new_id}` 应带 `prior_job_id` 且 `regen_attempt=1`。

**回滚**：删除插入块。

---

### 指令 #9：`optimized_generation` 死数据——**今日不动，决策如下**

审计发现 `optimize_prompt()` 每次质检烧一次模型调用产出永不回灌的 `optimized-generation.json`，前端仅作"参考提示词"展示（video-project.html 已带"改提示词不会改变画面"警告）。**今天不改**，理由：① 前端展示依赖（video-project.html:464/493）；② 删除要动 service.py + video_generation.py + 前端 + 测试 4 个面，一天内放大风险；③ 它是成本浪费非链路健康问题。**移入清债务批**，届时连同前端展示块一并处理。

---

### 指令 #10：渲染超时清理周期化

**目标**：`cleanup_stale_jobs()` 只在 app 启动时跑一次，运行中卡死的渲染任务永远不被清理（要等重启）。改为 app 生命周期内每 60s 后台跑一次。**不做 kill 进程**（渲染是分镜多次 subprocess，无单一 PID 可 kill，需加 pid 列+渲染生命周期改造，改动面大，留清债务批）。

**改动** — `app.py` lifespan（122-188 行）：

**①** 定位 `video_worker_task = asyncio.create_task(...)` 之后（约 172 行附近），加：
```python
    async def _periodic_stale_cleanup():
        while True:
            try:
                video_renderer.cleanup_stale_jobs()
            except Exception:
                logger.exception("周期清理渲染任务失败")
            await asyncio.sleep(60)

    stale_cleanup_task = asyncio.create_task(_periodic_stale_cleanup())
```

**②** finally 块里 `video_worker_stop.set()` 与 `await video_worker_task` 之后、`sched.stop_scheduler()` 之前，加：
```python
        stale_cleanup_task.cancel()
        await asyncio.gather(stale_cleanup_task, return_exceptions=True)
```

**验收**：重启 app，观察日志 60s 无异常；手动向 `video_render_jobs` 插一条 `status='pending'` 且 `created_at` 为 20 分钟前的记录（`python3 -c` 或用 sqlite3），等下一个周期应被标 failed（"排队超过 10 分钟自动取消"）。验证后删掉测试记录。

**回滚**：删除两个新增块。

---

## 批 3｜数据降权（约 1 小时）

### 指令 #11：旧频道垃圾资产降权（不删文件）

**目标**：assets 表 `category='other'` 且来自**已砍频道**的 337 条（SABC Digital News 254 + SA Today 56 + South Africa Now 27）是信源整合前的历史垃圾，匹配闸不认 `other` 纯占池子（吃 20000 扫描 cap、进 dedup、干扰诊断）。**仅标注 `deprecated` 降权，不删 DB、不删文件，可回滚。** 现役频道（eNCA/Newzroom/BDTV/CNBC/Transnet）的 other 素材不动。

**改动 1** — `database.py`：定位既有 `_ensure_column` 块（约 943-944 行，video_render_jobs 的 clips/quality_report 那两行附近），加：
```python
        _ensure_column(conn, "assets", "deprecated", "INTEGER DEFAULT 0")
```

**改动 2** — `database.py` `list_asset_segments`（约 3968 行）SELECT 列表里 `a.license AS asset_license,` 之后加：
```python
                        a.deprecated AS asset_deprecated,
```

**改动 3** — 新建 `scripts/mark_legacy_channel_assets.py`（只写 DB 状态，不动文件）：
```python
#!/usr/bin/env python3
"""将已砍频道的旧 youtube 素材标记 deprecated=1（降权，不删文件）。可回滚。"""
import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "logiflow.db"
# 已砍频道前缀：信源整合前的历史垃圾（匹配闸不认 other，纯占池子）
LEGACY_PREFIXES = ("SABC", "SA Today", "South Africa Now")

def _conn():
    return sqlite3.connect(DB)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不修改")
    args = ap.parse_args()
    conn = _conn(); conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    like = " OR ".join(["name LIKE ? || '%'" for _ in LEGACY_PREFIXES])
    rows = cur.execute(
        f"SELECT id, name, source, category, deprecated FROM assets "
        f"WHERE source='youtube' AND category='other' AND ({like})",
        list(LEGACY_PREFIXES),
    ).fetchall()
    already = [r for r in rows if r["deprecated"]]
    todo = [r for r in rows if not r["deprecated"]]
    print(f"命中 {len(rows)} 条（已降权 {len(already)}，待降权 {len(todo)}）")
    for r in todo[:20]:
        print(f"  id={r['id']} {r['name']!r}")
    if not args.dry_run and todo:
        cur.executemany(
            "UPDATE assets SET deprecated=1 WHERE id=?", [(r["id"],) for r in todo]
        )
        conn.commit()
        print(f"已降权 {len(todo)} 条")
    conn.close()

if __name__ == "__main__":
    main()
```

**改动 4** — `hotspot_video_planner.py` `_owned_candidates`（约 270 行）循环里 `if not _is_owned_video_segment(item): continue` 之后、`if not _is_buffalo_usable_source(item): continue` 之前，加：
```python
        if item.get("asset_deprecated"):
            continue
```

**改动 5** — 测试：`tests/test_matching_diagnostics.py` 或新建 `tests/test_deprecated_asset_exclusion.py`，构造一条带 `asset_deprecated=1` 的段传入 `_owned_candidates`，断言被排除；`deprecated=0` 不被排除。

**验收**：
```bash
python3 scripts/mark_legacy_channel_assets.py --dry-run     # 期望命中 337（SABC 254+SA Today 56+SAN 27）
python3 scripts/mark_legacy_channel_assets.py                # 执行降权
python3 scripts/mark_legacy_channel_assets.py --dry-run     # 再跑：待降权 0
pytest tests/test_matching_diagnostics.py tests/test_deprecated_asset_exclusion.py -q
```
重启 app 后 `GET /api/diagnostics/owned-matching?topic=清关` 读数应正常（za-stock customs 仍可用，不受影响）。

**回滚**：`sqlite3 data/logiflow.db "UPDATE assets SET deprecated=0 WHERE source='youtube' AND category='other'"` 即可还原。

---

## 执行节奏与收尾

1. **顺序**：批1 → 批2 → 批3，每批跑完验收再进下一批，禁止三批一起改。
2. **重启**：批2/批3 涉及 app.py 与数据库列，改完必须重启 app（`python3 app.py`）新码才生效。
3. **收尾（AGENTS.md 强制）**：全部验收通过后，同步 Obsidian 改进日志 + `git add` + `git commit`（不 push），并在改进日志记录 git 状态。
4. **今日明确不做**：死路由/死文件删除、19 个脚本归档、Qwen 文案统一、requirements 补 python-dotenv、optimized_generation 删除、kill 渲染进程、多窗真修、audit 快模型 A/B——全部挂"清债务"下批。

---

*验收口径：总指挥复核代码与读数，不盲信执行回执。*
