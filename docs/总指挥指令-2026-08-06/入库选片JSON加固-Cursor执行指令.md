# 入库选片 JSON 加固：原始返回 dump + 健壮抽取 + 一次性重试 —— Cursor 执行指令

> 日期：2026-08-06
> 状态：**待执行**（另立项目，从收束盘点挂账项转正）
> 性质：复刻策展 JSON 加固（`策展JSON失败-原始返回dump+一次性重试-Cursor执行指令.md`，已闭环 `94fe241`）的同一套模式，打在**入库选片**这条链上。
> 范围：`hotspot_hook_intake.py` / `database.py` / 新脚本 / 测试。**不碰**策展、核验、`model_router.py`（`use_cache` 参数策展修复已带上了）、受控开闸、展示修复。
> 与 za-stock 两份指令（#2 受控开闸、#3 展示修复）**无文件冲突**，可并行。

## 一、背景与 Why

`select_for_hook_ingestion`（`hotspot_hook_intake.py:165`）负责"哪些热点母片值得下载/复处理"的模型决策，有**两段**模型调用，故障模式与已闭环的策展 bug **完全同款**：

1. **选片段**（selection，`planner_text`，L206-215）：`max_calls=1` 单发，`_parse_selections`（L86）解析 `{"selections":[...]}`。JSON 非法 → 抛 `ValueError("热点入库模型未返回合法 JSON")`。**无诊断、无重试**。
2. **审计段**（audit，`critic`，L232-241）：`max_calls=1` 单发，`_parse_audit`（L145）解析 `{"approved":[...]}`。JSON 非法 → 抛 `ValueError("热点入库事实审计模型未返回合法 JSON")`。**无诊断、无重试**。

后果：一次坏返回直接让整批决策失败（reprocess 脚本 `_reprocess_media` 会 `raise ValueError`），原始输出被丢弃，**无法区分**是截断（`max_output_tokens` 切断）、空返回、还是纯错误文本。策展那条链已经闭环（dump+重试+分类），入库选片这条链还是裸的。

另外：两个 parse 函数只剥 ``` 围栏就 `json.loads`，**没有**策展 `_extract_json` 的 `<think>` 剥离 + 平衡 JSON 抽取能力——同一模型偶发 `<think>`/截断时，这里必挂。

**本指令三件事：**
1. **健壮抽取**：`_parse_selections` / `_parse_audit` 的 JSON 提取改用策展已验证的 `_extract_json`（剥 `<think>` + 取首个平衡 JSON），行级校验逻辑一字不动。这直接消灭"漏 `<think>` / 半截但平衡"类失败，不花模型调用。
2. **原始返回落库**：解析失败时把模型原始输出写 `hook_intake_diagnostics` 表（按 run `job_id` + `stage` 关联），配 `scripts/dump_hook_intake_diagnostics.py` 分类定性。
3. **一次性重试**：JSON 失败后**绕缓存**（`use_cache=False`）真调一次；再失败照旧抛错，上游行为不变。

## 二、铁律（不做的事）

1. **不碰策展/核验/受控开闸/展示修复**——本指令只动入库选片这条链与它的诊断基建。
2. **不改 `_parse_selections` / `_parse_audit` 的行级校验**（allowed 白名单、confidence、admission_mode、evidence 校验、字数截断、buffalo 文本防护等全保留）——只换"怎么把原始返回变成 JSON 对象"这一层。
3. **不加无限重试**：JSON 失败最多重试 1 次；第 2 次仍失败 → 照旧抛 `ValueError`，脚本现有 catch 保持原样。
4. **不为重试降低任何事实/合规门禁**——重试是同一提示词、同一模型、同一 SOP，只是再掷一次骰子。
5. **budget 单点升 `max_calls=1→2`**（选片 + 审计各一处），带注释；禁止全局放宽。`reset` 语义维持现状（本次不加）。
6. **诊断写库失败绝不能反噬决策**——helper 内部 try/except 吞掉（与 `add_hook_curation_diagnostic` 同款）。
7. **`_extract_json` 复用不引入循环依赖**——`hotspot_hook_curator` 不 import intake（已核实），顶层 `from hotspot_hook_curator import _extract_json` 安全；若未来 curator 反向依赖 intake，改局部 import。

## 三、改动清单

### 改动 A：`database.py` — `hook_intake_diagnostics` 表 + 两个 helper

**A1.** 在 `init_db()` 建表区（`hook_curation_diagnostics` 表块附近，约 L436-448）新增：

```sql
CREATE TABLE IF NOT EXISTS hook_intake_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    job_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT,
    cache_hit INTEGER DEFAULT 0,
    error TEXT,
    raw_content TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hook_intake_diag_run
    ON hook_intake_diagnostics (stage, job_id, created_at);
```

**A2.** 在 database.py 增加两个函数（仿 `add_hook_curation_diagnostic` / `list_hook_curation_diagnostics`，L2091/2124 附近；以你仓库的写 helper 与 logger 命名为准）：

```python
def add_hook_intake_diagnostic(stage, job_id, attempt_number, prompt_version, *,
                               model=None, cache_hit=False, error=None, raw_content=None):
    """记录一次入库选片 JSON 失败现场。绝不抛出：写库失败只记日志，不反噬决策。"""
    try:
        raw = (raw_content or "")[:16_000]
        _execute_write(
            "INSERT INTO hook_intake_diagnostics "
            "(stage, job_id, attempt_number, prompt_version, model, cache_hit, error, raw_content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            str(stage), str(job_id), int(attempt_number), str(prompt_version),
            (model or "")[:64], 1 if cache_hit else 0,
            (error or "")[:200], raw,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception:
        logger.warning("记录 Hook 入库诊断失败 stage=%s job=%s", stage, job_id, exc_info=True)


def list_hook_intake_diagnostics(limit=200, stage=None, job_id=None):
    """按时间倒序取诊断行，供定性脚本使用。"""
    sql = "SELECT * FROM hook_intake_diagnostics"
    params: list = []
    if stage:
        sql += " WHERE stage=?"
        params.append(stage)
    if job_id:
        sql += (" AND " if stage else " WHERE ") + " job_id=?"
        params.append(job_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    rows = db.execute(sql, params).fetchall()
    return [dict(row) for row in rows]
```

> `_execute_write` / `db` / `logger` 以 database.py 现有命名与导入方式为准（写前看 `add_hook_curation_diagnostic` 的落实现抄风格）。

### 改动 B：`hotspot_hook_intake.py` — 健壮抽取 + 重试包装

**B1. 顶部 import 补齐：**

```python
from database import add_hook_intake_diagnostic
from hotspot_hook_curator import _extract_json  # 无循环（curator 不 import intake）；若未来反向依赖改局部 import
```

**B2. `_parse_selections`（L86）的 JSON 提取段**——把"剥围栏 + json.loads"换成 `_extract_json`（自动处理围栏/`<think>`/平衡 JSON）：

原 L86-93：

```python
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        rows = json.loads(raw).get("selections") or []
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("热点入库模型未返回合法 JSON") from exc
```

改为：

```python
    try:
        parsed = _extract_json(content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("热点入库模型未返回合法 JSON") from exc
    rows = parsed.get("selections") if isinstance(parsed, dict) else []
```

L94 起的行级校验（media_id/confidence/allowed/admission_mode/evidence/service_fit 等）**一字不动**。

**B3. `_parse_audit`（L145）同款替换**：

原 L146-152：

```python
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        rows = json.loads(raw).get("approved") or []
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("热点入库事实审计模型未返回合法 JSON") from exc
```

改为：

```python
    try:
        parsed = _extract_json(content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("热点入库事实审计模型未返回合法 JSON") from exc
    rows = parsed.get("approved") if isinstance(parsed, dict) else []
```

**B4. 新增模块级重试包装函数**（放在 `_parse_audit` 之后、`select_for_hook_ingestion` 之前）：

```python
def _call_with_json_retry(stage, job_id, role, prompt_version, messages, max_tokens, parse_fn):
    """调用 + 解析；JSON 解析失败时绕缓存（use_cache=False）真调一次。

    - 失败现场逐次写 hook_intake_diagnostics（attempt=1 初始 / attempt=2 重试）；
    - 两次都失败 → 照旧抛 ValueError（上游行为不变）；
    - 返回 (parsed, retried)。stage: 'selection' | 'audit'。
    """
    model = (model_router.get_route(role) or {}).get("model") or ""
    retried = False

    def _call(use_cache):
        return asyncio.run(model_router.call_text(
            job_id, role, messages,
            prompt_version=prompt_version,
            max_output_tokens=max_tokens,
            use_cache=use_cache,
        ))

    def _parse(result, attempt):
        content = result.get("content") or ""
        try:
            return parse_fn(content)
        except ValueError as exc:
            # 原始返回不丢；写库失败只记日志，绝不反噬决策。
            add_hook_intake_diagnostic(
                stage, job_id, attempt, prompt_version,
                model=model, cache_hit=bool(result.get("cache_hit")),
                error=str(exc), raw_content=content,
            )
            raise

    result = _call(use_cache=True)
    try:
        parsed = _parse(result, 1)
    except ValueError:
        # 一次性重试：必须绕过缓存，避免第一次坏返回原样复现。
        retried = True
        result = _call(use_cache=False)
        parsed = _parse(result, 2)
    return parsed, retried
```

**B5. `select_for_hook_ingestion` 两处调用改用包装 + budget 升 `max_calls=2`。**

选片段（原 L202-216）——budget 与调用改为：

```python
    model_router.create_budget(
        job_id, max_calls=2, max_input_tokens=16_000,
        max_output_tokens=model_router.required_output_budget("planner_text", 1_000),
        # max_calls=2 = 1 次初始选片 + 1 次 JSON 解析失败重试（同一决策尝试内）。
    )
    selections, sel_retried = _call_with_json_retry(
        "selection", job_id, "planner_text", PROMPT_VERSION,
        [
            {"role": "system", "content": "严格返回 JSON，不要 Markdown；不能根据镜头外信息或未提供的 RAG 编造事实。"},
            {"role": "user", "content": _prompt(candidates, maximum, sop, target_rows)},
        ],
        1_000,
        lambda content: _parse_selections(content, allowed, target_ids),
    )
    result = None  # 旧 `result` 变量在本函数仅用于 meta 的 cache_hit；改从包装返回值取
```

> 注意：原 L206-215 的 `result` 变量在 L222 和 L274 被 `result.get("cache_hit")` 使用。B5 后由 `_call_with_json_retry` 内部管理调用，`result` 不再存在——**L222/L274 的 `cache_hit` 改从返回的 retried/状态推导**，例如 meta 里新增 `"retries": {"selection": 1 if sel_retried else 0, "audit": 1 if audit_retried else 0}`，并把 `cache_hit` 字段保留为初始语义（或直接去掉，改由诊断表提供每 attempt 的 cache_hit）。**验收时保证 meta 结构不破坏现有测试断言的字段**（现有测试断言 `meta["status"]`、`meta["audit"]["approved_count"]` 等，不依赖 cache_hit）。

审计段（原 L228-242）——同样替换：

```python
    model_router.create_budget(
        audit_job_id, max_calls=2, max_input_tokens=10_000,
        max_output_tokens=model_router.required_output_budget("critic", 500),
        # max_calls=2 = 1 次初始审计 + 1 次 JSON 解析失败重试。
    )
    approved, audit_retried = _call_with_json_retry(
        "audit", audit_job_id, "critic", AUDIT_PROMPT_VERSION,
        [
            {"role": "system", "content": "严格返回 JSON；RAG 证据不足或关联牵强时必须拒绝。"},
            {"role": "user", "content": _audit_prompt(candidates, selections, sop)},
        ],
        500,
        lambda content: _parse_audit(content, {item["media_id"] for item in selections}),
    )
```

L242 的 `approved = _parse_audit(...)` 行删除（包装已返回）。meta 在 L270-282 补：

```python
        "retries": {"selection": 1 if sel_retried else 0, "audit": 1 if audit_retried else 0},
```

### 改动 C：新脚本 `scripts/dump_hook_intake_diagnostics.py` — 失败定性

功能：列出最近诊断行，按 stage 分组 + 启发式分类 + 明细。分类逻辑（纯字符串，可抄策展 dump 脚本）：

```python
def classify(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "空返回"
    if "{" not in text and "[" not in text:
        return "纯错误或拒绝文本(无括号)"
    bal = 0
    for ch in text:
        if ch == "{":
            bal += 1
        elif ch == "}":
            bal -= 1
    return "截断(括号不平衡)" if bal > 0 else "其它(有括号但解析失败)"
```

行为：
1. 参数：`--limit 200`、`--stage selection|audit`（可选）。
2. 输出：stage 计数总览 + 分类计数 + 每行明细（id、stage、job_id、attempt、model、cache_hit、error、raw_content 前 300 字符 + 总长度）。
3. 末尾结论提示：`截断为主 → 需评估调大该段 max_output_tokens/压缩证据；空/纯错误为主 → 重试即可，重试后再统计失败率。`

### 改动 D：测试

在 `tests/test_hotspot_hook_curation.py`（已含 intake 测试，用 `tmp_db` + async `fake_call` monkeypatch `model_router.call_text` 的既有模式）追加：

- `test_intake_retries_once_on_json_failure_then_succeeds`：fake call_text 对 selection 段第 1 次返回坏 JSON（`"not json"`），第 2 次返回合法 selections；audit 段正常。断言：call_text 被调 **2 次**（selection）、第 2 次 `use_cache=False`、`selected` 非空、`meta["retries"]["selection"] == 1`。
- `test_intake_records_diagnostic_on_both_failures`：selection 段两次都坏 → 抛 `ValueError`；断言 `list_hook_intake_diagnostics(stage="selection")` 有 **2 行**（attempt=1、attempt=2），`raw_content` 与 `cache_hit` 正确。
- `test_intake_audit_retries_on_json_failure`：selection 正常；audit 第 1 次坏、第 2 次好 → 成功，`meta["retries"]["audit"] == 1`。
- `test_parse_selections_recovers_think_and_fence`：直接调 `_parse_selections`，content 为 `"<think>先想一下</think>\n```json\n{\"selections\":[...]}\n```"` → 正常解析出合法 selection（证明健壮抽取生效）。
- `test_intake_budget_allows_two_calls`：monkeypatch `create_budget` 捕获入参，断言 selection 与 audit 两个 job 的 `max_calls == 2`。

跑：`python3 -m pytest tests/test_hotspot_hook_curation.py tests/test_hotspot_prewarm_workflow.py tests/test_reprocess_hotspot_hook_source.py tests/test_model_router.py -q`，再全量 `python3 -m pytest -q`，记录总数与基线（当前 807 passed / 8 存量 UI 失败）。

## 四、验收清单（做完逐条勾）

1. pytest：5 条新测试全过；既有 intake 7 条（`test_hook_ingestion_*` / `test_rag_sop_*` 等）不回归；全量无新增破坏。
2. **对抗式复核（别只看测试绿）**：
   - 用 `python3 scripts/dry_run_hotspot_intake_sop.py` 真跑一轮候选，确认正常路径无回归（`meta["retries"]` 应全 0）；
   - 手动构造一次坏返回（或临时 monkeypatch）→ 确认 `hook_intake_diagnostics` 表落库原始返回，`use_cache=False` 重试生效；
   - 跑 `python3 scripts/dump_hook_intake_diagnostics.py --limit 30` 看分类计数。
3. 把 dump 分类计数报给总指挥拍板：截断为主 → 下一条指令调大该段 `max_output_tokens` / 压缩证据；空/纯错误为主 → 维持重试。
4. **重启 app + 任何长驻调度/长驻进程**（stale-server 老规矩）：入库选片由 `scripts/reprocess_hotspot_hook_source.py` / `dry_run_hotspot_intake_sop.py` 驱动（app.py 无直接调用点），但 diagnostic 基建与 DB 共用，长驻进程必须换新码。

## 五、回滚

单点回滚：`git revert` 本次改动（涉及 database.py / hotspot_hook_intake.py / 新脚本 / 测试）。回滚后恢复旧行为：JSON 失败 → 直接抛错（不健壮抽取、不重试、不落库）。诊断表留下不影响运行。与受控开闸/展示修复指令互不牵连，可独立回滚。

## 六、备注

- **与策展加固的关系**：策展那条链（`94fe241`）用的是"不改 parse、外部加失败捕获+重试+落库"；本指令在此基础上**多一步**——把 parse 的 JSON 提取升级为 `_extract_json`。原因：入库选片的两段解析**从来就没有** `<think>`/平衡 JSON 兜底（策展有 `_extract_json`），不升级就只能靠模型重试硬扛这类可避免的失败。行级校验一字未动，不违背合规。
- **为什么诊断按 `job_id`+`stage` 而不是 asset_id**：入库选片是**批量决策**（一条调用覆盖多条候选），不是策展那种"单资产一条调用"。`job_id` 由 fingerprint 派生（`hotspot-hook-intake-{sha256}[:16]`），同一次决策的两段（selection/audit）job 前缀不同，stage 区分之。
- 若后续发现 audit 段 `max_output_tokens=500` 偏小导致截断为主，按 dump 结论单独出指令调（不要顺手调）。

## 七、总指挥核实结论（2026-08-06）

> 前置核实：qcoder 对本指令调用链的六条事实核查全部属实，但定性需要修正。本小节为拍板定调，执行前先读。

**事实核实（已逐一对照源码）：**

1. `select_for_hook_ingestion`（`hotspot_hook_intake.py:165`）仅被两个脚本与测试调用：`dry_run_hotspot_intake_sop.py:59`、`reprocess_hotspot_hook_source.py:91`。`scheduler.py` 与 `app.py` 均无直接调用点。
2. 生产主力路径（三天全量 `prewarm_authorized_hotspot_media` + 6h 增量 `fetch_hotspots_then_incremental_hook_intake`）在 `scheduler.py:244-249` **硬编码 `intake_decision` bypass** 了本函数——已授权视频全部进入分析，不做模型选片。
3. **消费端已有兜底**：`app.py:217` `_normalized_hotspot_intake_decision` 防御性解析 `intake_decision_json`（"without letting malformed JSON block curation"），materialization 各读取点（L3011/3094/3164）吃坏 JSON 只会降级为 `{}`，不会炸。
4. Curator 不 import intake（`hotspot_hook_curator.py` 仅 import model_router / hotspot_hook_selection_sop / hotspot_lexicon / database），本指令 B1 顶层 `from hotspot_hook_curator import _extract_json` **无循环依赖**，安全。
5. `model_router.call_text` 默认 `use_cache=True`（`model_router.py:293`），B4 的绕缓存重试语义成立。
6. 策展加固已闭环（commit `94fe241`，`curator.py:364-406`：max_calls=2 / reset=True / use_cache=False 重试 / 诊断落库），本指令 B1-B5 为同一模式的复刻。

**定性修正（拍板）：**

本指令**不是**"策展同款高危修复"。策展在用户可见链路（每次 materialization 都调），入库选片既低频、消费端又有兜底。本指令真实买到的价值是：

1. **免费文本恢复**——`_extract_json` 剥 `<think>` + 取平衡 JSON，不花模型调用消灭一类本可避免的失败；
2. **一次性重试**——吸收偶发坏返回，省管理员手动重跑；
3. **诊断采样**（最有价值）——`hook_intake_diagnostics` 表给存量"截断 vs 空返回"待决问题（策展拍板先观察）额外开了一个采样源，服务后续分类定性。

**优先级**：排当前挂账项（za-stock 展示修复 / 受控开闸）**之后**执行——它不是用户可见风险。执行时 B1-B5 照抄；不改行级校验；验收后把 dump 分类计数报总指挥拍板。

**一个结构性提示**：调度器 bypass 选片模型是"成本换确定性"的设计（全量分析 vs 模型筛选）。若未来恢复模型驱动选片省 API 成本，本加固将从"保险"升级为"生产关键"——这也是现在便宜做掉它的理由。
