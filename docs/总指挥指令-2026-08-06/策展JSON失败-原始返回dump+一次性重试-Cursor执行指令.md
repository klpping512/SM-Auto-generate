# 策展 JSON 失败：原始返回 dump + 一次性重试 —— Cursor 执行指令

> 日期：2026-08-06
> 状态：**已执行**（2026-08-06；保留频道 10 条已 requeue 清零 JSON 失败；诊断表待后续偶发写入）
> 关联诊断：`memory: hotspot-server-stale-json-residual`（2026-08-06 复盘）
> 前置动作：**先重启 app**（旧进程 08-04 13:03 起未重启，08-05 修复全未生效），重启并验完三件事后再做本指令

## 一、背景与 Why

库内 86 条母片卡 `processing_failed`，报错 `Hook 策展模型未返回合法 JSON`，app.log 433 次且 08-06 仍在打。
08-05 的 `_extract_json`（剥 `<think>` + 取首个平衡 JSON）已让"漏 `<think>`"类失败消失，但**残余失败仍在**，且**新代码下也会偶发**（909/913/914 在 08-05 21:13/21:39 由 CLI 新码重处理仍报此错；同一批 17:50 跑却良性 0 hook）。

`_extract_json` 抛"未返回合法 JSON"意味着模型返回里**连一个平衡的 `{…}`/`[…]` 都没有**。三种可能，当前无法区分：
- A 截断：输出在中途被 `max_output_tokens=1000` 切断，括号不平衡；
- B 空返回 / 纯错误文本：`content` 为空或网关错误字符串；
- C 偶发 `<think>` 残余但 `_extract_json` 处理不了（可能性低，已加固）。

**本指令要解决两件事：**
1. **原始返回落库**——失败时不把模型原始输出丢掉，写进诊断表，用脚本分类定性（截断 vs 空 vs 纯错误），定出后决定要不要加大 `max_output_tokens` / 换提示词。
2. **一次性重试**——同一条母片不同次调用结果不确定（17:50 绿 / 21:13 崩），JSON 解析失败后**再真调一次模型**，大概率能出合法 JSON。重试**必须绕过模型缓存**，否则第一次的坏返回被缓存，重试原样复现（memory 已验证过"cache_hit:true 假绿"陷阱）。

## 二、铁律（不做的事）

1. **只动策展（curate_hook_clips，planner_text 一路）**。`hotspot_hook_intake.select_for_hook_ingestion`（入库选片）与 `_audit_hooks`（critic 核验）**不在本次范围**，另立项目。
2. **不改 `_extract_json` / `_parse` 的既有逻辑**，只在外面加"失败捕获 + 重试 + 落库"。
3. **不加无限重试**。JSON 失败最多重试 1 次；第 2 次仍失败 → 照旧抛错，app.py 现有 catch 保持原样（`processing_failed`，可被后续全量任务再捡起）。
4. **不为重试降低任何事实/合规门禁**。重试调用的是**同一提示词、同一模型、同一 SOP**，只是再掷一次骰子。
5. **budget 语义不乱改**：`max_calls=1` 的保护升为 `max_calls=2` 是**有意的、单点、带注释**的改动（1 初始 + 1 JSON 重试），且必须保持 `reset=True`（每次重跑=1 次完整尝试）。禁止全局放宽 budget。
6. **诊断写库失败绝不能反噬策展**——插入 helper 内部必须 try/except 吞掉。

## 三、改动清单

### 改动 A：`database.py` — 诊断表 + 两个 helper

**A1.** 在 `init_db()` 的建表区（找 `hotspot_fetch_runs` 建表块约 L426-435 后面，或就近 hotspot 表区）新增：

```sql
CREATE TABLE IF NOT EXISTS hook_curation_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT,
    cache_hit INTEGER DEFAULT 0,
    error TEXT,
    raw_content TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hook_curation_diag_asset
    ON hook_curation_diagnostics (asset_id, created_at);
```

**A2.** 在 database.py 增加两个函数（风格对齐现有 helper）：

```python
def add_hook_curation_diagnostic(asset_id, attempt_number, prompt_version, *,
                                 model=None, cache_hit=False, error=None, raw_content=None):
    """记录一次策展 JSON 失败现场。绝不抛出：写库失败只记日志，不反噬策展。"""
    try:
        raw = (raw_content or "")[:16_000]  # 防超大输出撑爆行
        _execute_write(
            "INSERT INTO hook_curation_diagnostics "
            "(asset_id, attempt_number, prompt_version, model, cache_hit, error, raw_content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            int(asset_id), int(attempt_number), str(prompt_version),
            (model or "")[:64], 1 if cache_hit else 0,
            (error or "")[:200], raw,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception:
        logger.warning("记录 Hook 策展诊断失败 asset=%s", asset_id, exc_info=True)


def list_hook_curation_diagnostics(limit=200, asset_id=None):
    """按时间倒序取诊断行，供定性脚本使用。"""
    if asset_id is not None:
        rows = db.execute(
            "SELECT * FROM hook_curation_diagnostics WHERE asset_id=? "
            "ORDER BY created_at DESC LIMIT ?", (int(asset_id), int(limit))
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM hook_curation_diagnostics ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]
```

> 注意：`_execute_write` / `logger` 以你仓库既有命名与导入方式为准（改前先看 database.py 现有写 helper 与 logger 定义，别硬套函数名）。

### 改动 B：`model_router.call_text` — 增加 `use_cache` 参数

**现状**：`call_text`（L285-294 签名）第 L303 无条件 `cached = db.get_model_cache(cache_key)`，命中即返回（L304-317）。

**改法**：
1. 签名加参数：`use_cache: bool = True`。
2. 把 L303 改成：
   ```python
   cached = db.get_model_cache(cache_key) if use_cache else None
   ```
3. 其余不动（缓存命中路径仍走 `record_call`，budget 语义不变）。

**这必须是唯一入口改动**，不新增参数去动缓存写入逻辑。调用方默认行为不变（现有所有调用不传该参数 = 仍用缓存）。

### 改动 C：`hotspot_hook_curator.curate_hook_clips` — 一次性重试 + 失败落库

**现状**（L348-399）：
- L363-369：`create_budget(job_id, max_calls=1, max_input_tokens=14_000, max_output_tokens=required_output_budget(...), reset=True)`
- L370-379：`result = asyncio.run(model_router.call_text(job_id, "planner_text", [system, user], prompt_version=PROMPT_VERSION, max_output_tokens=1_000))`
- L380：`hooks = _parse(result["content"], ordered)` ← **抛 `ValueError("Hook 策展模型未返回合法 JSON")` 的地方**

**改法**：

C1. budget 升为 `max_calls=2`，注释写明原因：
```python
    # max_calls=2 = 1 次初始策展 + 1 次 JSON 解析失败重试（同一策展尝试内）。
    # reset=True 保持"每次重跑=1 次完整尝试"语义；不得再往上放。
    model_router.create_budget(
        job_id, max_calls=2, max_input_tokens=14_000,
        max_output_tokens=model_router.required_output_budget("planner_text", 1_000),
        reset=True,
    )
```

C2. 把 L370-380 换成带"一次重试 + 现场落库"的调用：

```python
    messages = [
        {"role": "system", "content": "严格返回 JSON，不要 Markdown，不得补充镜头外事实。"},
        {"role": "user", "content": _prompt(source_title, source_context, ordered)},
    ]
    route_model = (model_router.get_route("planner_text") or {}).get("model") or ""

    def _call(**overrides):
        return asyncio.run(model_router.call_text(
            job_id, "planner_text", messages,
            prompt_version=PROMPT_VERSION,
            max_output_tokens=1_000,
            **overrides,
        ))

    def _try_parse(result: dict) -> list[dict]:
        # 现场落库放在异常抛出路径，原始返回不丢
        try:
            return _parse(result.get("content") or "", ordered)
        except ValueError as exc:
            add_hook_curation_diagnostic(
                int(asset_id), _try_parse.attempt, PROMPT_VERSION,
                model=route_model,
                cache_hit=bool(result.get("cache_hit")),
                error=str(exc),
                raw_content=result.get("content") or "",
            )
            raise

    _try_parse.attempt = 1
    result = _call()
    try:
        hooks = _try_parse(result)
    except ValueError:
        # 一次性重试：必须绕过缓存，避免第一次坏返回原样复现。
        # 命中缓存时 budget 已记 1 次调用，max_calls=2 恰好容纳这次真调。
        _try_parse.attempt = 2
        result = _call(use_cache=False)
        hooks = _try_parse(result)
```

> 说明：
> - `_try_parse.attempt` 用函数属性是为了少改结构；执行时若风格不合适可换闭包/显式参数，语义等价即可。
> - 重试只在 **JSON 解析失败**（`ValueError`）时触发；budget 用尽 / 网络断连等异常不在此路径（网络类已由 call_text 内部 3 次退避重试兜底）。
> - 第 2 次仍失败 → `_try_parse` 抛出 → 上层 app.py `except Exception`（L3185-3190）照旧置 `temporarily_unavailable` → `processing_failed`，行为不回退。
> - 顶部 import：`from database import add_hook_curation_diagnostic`（或以你仓库的 import 惯例为准；注意避免循环 import，database 是底层，curator 引它安全）。

C3. 后续 L381-388 代码不动（`if not hooks: ...`、`_audit_hooks` 原样）。

### 改动 D：新脚本 `scripts/dump_hook_curation_diagnostics.py` — 失败定性

功能：列出最近诊断行，按启发式分类并打印统计与明细。分类逻辑（纯字符串判断）：

```python
def classify(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "空返回"
    if "{" not in text and "[" not in text:
        return "纯错误或拒绝文本(无括号)"
    # 括号不平衡 = 大概率截断
    bal = 0
    for ch in text:
        if ch == "{":
            bal += 1
        elif ch == "}":
            bal -= 1
    return "截断(括号不平衡)" if bal > 0 else "其它(有括号但解析失败)"
```

脚本行为：
1. 参数：`--limit 200`、`--asset-id N`（可选）。
2. 输出：分类计数总览 + 每行明细（id、asset_id、attempt、model、cache_hit、error、raw_content 前 300 字符 + 总长度）。
3. 末尾打印一行结论提示：`截断为主 → 需评估调大 max_output_tokens/压缩证据；空返回/纯错误为主 → 重试即可，重试后再统计失败率。`

明细里 asset_id 关联母片用：`SELECT id, publisher, intake_title FROM hotspot_media WHERE asset_id=?`。

### 改动 E：测试

**E1.** `tests/test_hook_curator_json.py` 追加（用 monkeypatch 假 `call_text`）：

- `test_curate_retries_once_on_json_parse_failure_then_succeeds`：假 call_text 第 1 次返回 `<think>…</think>`+坏内容（或直接非 JSON），第 2 次返回合法 hooks payload → `curate_hook_clips` 返回 1 条 hook；断言 call_text 被调 **2 次**、第 2 次 `use_cache=False`。
- `test_curate_records_diagnostic_on_both_failures`：两次都坏 → 抛 `ValueError`；断言诊断表写了 **2 行**（attempt=1、attempt=2），raw_content 正确、cache_hit 正确。
- `test_curate_budget_allows_two_calls`：断言 curate 内部 `create_budget` 收到的 `max_calls == 2`（monkeypatch create_budget 捕获入参）。

**E2.** 跑全量：`python3 -m pytest tests/test_hook_curator_json.py tests/test_hotspot_hook_curation.py tests/test_hotspot_prewarm_workflow.py tests/test_model_router.py -q`，以及仓库基线（`python3 -m pytest -q`），记录总数与既有基线（当前 780+）。

## 四、验收清单（做完逐条勾）

1. `pytest tests/test_hook_curator_json.py` 3 条新测试全过，既有 5 条不回归。
2. 全量 `pytest -q` 通过，无新增存量断言破坏（重点 `test_model_router.py` 的 budget 语义测试——它测的是通用 budget，不因 curate 单点 max_calls=2 变）。
3. **对抗式复核（别只看测试绿）**：
   - 挑 909/913/914（当前 JSON-failed）之一，用 `reprocess_hotspot_hook_source.py --media-id N --skip-analysis` 重跑；
   - 期望：要么出合法 JSON（直接 ready 或出 hook），要么仍失败但 `hook_curation_diagnostics` 表出现该 asset 的原始返回；
   - 跑 `python3 scripts/dump_hook_curation_diagnostics.py --asset-id N` 看分类是截断/空/纯错误；
   - **关键：确认重试确实绕过缓存**——若第一次命 cache 且 raw_content 与缓存前一致，第二次 `cache_hit=0` 才算通过。
4. 用 `dump` 脚本汇总最近 30 条诊断，把分类计数报给我（总指挥拍板：截断为主 → 下一条指令调大策展 max_output_tokens / 压缩证据输入；空/纯错误为主 → 维持重试即可，考虑对空返回也加"1 次 use_cache=False 重试"）。

## 五、回滚

单点回滚：`git revert` 本次改动（涉及 database.py / model_router.py / hotspot_hook_curator.py / 新脚本 / 测试）。回滚后旧行为恢复：JSON 失败 → 直接 processing_failed（不重试、不落库）。诊断表留下不影响运行。

## 六、备注

- 本次不动 `hotspot_hook_intake.py` 的 `_parse_selections` / `_parse_audit`（它们也抛 JSON 错，但那是"入库选片"阶段，属另一条链，已记入 memory 后续项）。
- 上线前**先重启 app**，否则旧进程仍跑旧码，本指令改动不生效（与 memory 里 stale-server 教训一致）。
