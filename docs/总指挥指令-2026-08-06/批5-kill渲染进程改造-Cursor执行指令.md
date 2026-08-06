# 总指挥指令 批5 ｜kill 渲染进程改造（批2 #10 遗留 · 立项）

> **前置**：批 1-4 已验收闭环。批 2 文档 #10 明确"不做 kill 进程"并留清债务批；本批正式立项。
> **性质**：功能改造（渲染超时清理从"只标状态"升级为"真杀进程"）。改动集中在 `video_renderer.py` + 新增测试，**约 30 分钟**。
> **红线**：只动 `cleanup_stale_jobs` 与新增测试；不碰 `render_job` 主流程、不碰 `run_cancelable_process`/`cancel_render`（已有机制，直接用）。

---

## 根因（总指挥独立调研，2026-08-06）

**已有机制**（`video_renderer.py`，无需新建）：
- `run_cancelable_process(job_id, cmd, ...)`（:214）：每个 subprocess 用 `start_new_session=True` 独立进程组，并注册到 `_ACTIVE_PROCESSES[job_id]`。
- `cancel_render(job_id)`（:190）：遍历该 job 的活跃进程组，`SIGTERM → wait(2s) → SIGKILL` 兜底。

**现状 bug 链**（`cleanup_stale_jobs`，:1159-1183）：
1. running 超时（>RENDER_TIMEOUT=300s）只标 `failed`，**不杀进程**；
2. 而 `render_job` 的 `is_canceled`（:1226-1230）只认 `{"cancel_requested","canceled"}`，**不认 `failed`** → 渲染线程照跑；
3. 渲染线程最后正常完成 → :1621 标 `succeeded`，**覆盖 cleanup 标过的 `failed`** → 超时清理对 running 分支**实际完全无效**（pending 分支无进程才有效）。

**修复方向**：cleanup 的 running 超时分支 → **先 `cancel_render(job_id)` 杀进程组，再标 `canceled`**。标 `canceled` 而非 `failed` 是关键：`is_canceled` 认 `canceled` → 渲染线程在下一个 0.25s communicate 轮询/场景边界抛 `RenderCanceled` → :1626 标 `canceled`（与 cleanup 一致，不会覆盖成 succeeded）→ 线程干净退出。

---

## 指令 #18：cleanup_stale_jobs running 超时分支真杀进程

**改动** — `video_renderer.py` `cleanup_stale_jobs`（:1174-1181）：

```python
        if job["status"] == "running" and age > RENDER_TIMEOUT:
            cancel_render(job["id"])  # 真杀进程组，防僵尸 ffmpeg 继续烧资源
            db.update_render_job(
                job["id"], status="canceled", stage="超时清理",
                error=f"渲染超过 {RENDER_TIMEOUT} 秒自动终止",
            )
            stale_count += 1
        elif job["status"] == "pending" and age > RENDER_TIMEOUT * 2:
            db.update_render_job(
                job["id"], status="failed", stage="超时清理",
                error="排队超过 10 分钟自动取消",
            )
            stale_count += 1
```

**要点**：
- `cancel_render` 是本模块已有函数（:190），直接调用；无进程时返回 False，无害。
- running 超时状态从 `failed` 改为 `canceled`——语义是"渲染被终止"，与手动取消路径同态，且保证 `render_job` 线程必停。
- **pending 分支保持 `failed` 不动**（排队中无进程可杀）。
- 若执行 agent 发现任何测试断言 running 超时 → `failed`，同步改为 `canceled`（总指挥已 grep tests 无既有断言，理论零影响）。

**验收**：
1. `grep -rn "status=\"failed\"" video_renderer.py | grep 超时清理` → 无输出（running 分支不再标 failed）。
2. 新增测试（见 #19）通过。

---

## 指令 #19：新增 kill 渲染进程回归测试

**目标**：固化"超时清理会杀进程 + 标 canceled"的行为，防止退回"只标状态"。

**改动** — `tests/test_video_generation_rendering.py`（该文件已有 run_cancelable_process/cancel_render 测试，复用模式）：

新增用例 `test_cleanup_stale_jobs_kills_running_render`：

1. 用 `run_cancelable_process("job-stale", ["sleep", "60"], cancel_check=lambda: False)` 在后台线程启动一个长进程（注册进 `_ACTIVE_PROCESSES`）。
2. monkeypatch `db.get_unfinished_render_jobs` → 返回 `[{"id": "job-stale", "status": "running", "created_at": <7 分钟前 ISO>}]`。
3. monkeypatch `db.update_render_job` → 记录调用参数。
4. 调 `video_renderer.cleanup_stale_jobs()`。
5. 断言：
   - 该进程已被终止：`proc.poll()` 非 None（或进程组已死，用 `os.kill(proc.pid, 0)` 抛 ProcessLookupError）。
   - `update_render_job` 收到 `status="canceled"`、`stage="超时清理"`、error 含"自动终止"。
   - `_ACTIVE_PROCESSES` 中该 job 的进程集合已被清理（run_cancelable_process finally discard）。

> 注意：`cancel_render` 的 `process.wait(timeout=2)` 会阻塞，测试里 sleep 进程被 SIGTERM 后应快速退出，用例总时长控制在数秒内。

**验收**：`pytest tests/test_video_generation_rendering.py -q` 全绿（含既有 3 条 + 新用例）。

---

## 明确不做（防漂移）

- ❌ 不改 `render_job` 主流程、不改 `run_cancelable_process`、不改 `cancel_render`。
- ❌ 不引入 pid 列（`_ACTIVE_PROCESSES` 内存态已足够：app 运行期间的渲染进程都在注册表）。
- ❌ 不处理 app 重启前的孤儿进程（`start_new_session=True` 使它们独立于父进程，重启后不在 `_ACTIVE_PROCESSES`，`cancel_render` 找不到）。孤儿进程属于系统级清理（`ps aux | grep ffmpeg` 手动杀），**不在本批**，验收时如遇说明即可。
- ❌ 不动 DB schema、不动其他模块。

## 执行顺序与验收总口径

1. 只两步：#18 → #19，完成后跑 `pytest tests/test_video_generation_rendering.py -q`。
2. **全量回归** `pytest -q`：与基线对比（当前 871 passed / 8 failed），8 个存量失败必须逐条一致，不得新增。
3. **运行时验证**（可选但建议）：重启 app 后，往 `video_render_jobs` 插一条 `status='running'` 且 `created_at` 为 6 分钟前的记录（如 `_stale_running`），等下一个 60s 周期后：`SELECT id,status,stage,error FROM video_render_jobs WHERE id='_stale_running'` → 期望 `canceled / 超时清理 / 渲染超过 300 秒自动终止`；同时 `ps aux | grep ffmpeg` 确认无该 job 的残留进程。验证后删测试记录。
4. 同步 Obsidian 改进日志（三处）+ 更新指令索引。
5. 提交信息：`批5 kill渲染进程：#18 cleanup 超时真杀进程+标canceled #19 回归测试`。

## 回滚

git 恢复 `cleanup_stale_jobs` 改动块；测试同步回退。回滚后回到"只标状态不杀进程"的现状（已知无效，但无新风险）。
