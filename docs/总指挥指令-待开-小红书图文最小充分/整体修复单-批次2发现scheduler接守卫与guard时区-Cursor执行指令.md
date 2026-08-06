# 整体修复单：批次 2 两发现（scheduler 接 diff 守卫 + guard 统一 UTC 口径）—— Cursor 执行指令

> 日期：2026-08-06
> 状态：**已执行**（R2 `d864c6b` → R1 `c7296cd`）。
> 设计真源：批次 2 验收备注（`README.md` 第 2 批小节）两处非阻塞发现；`xhs_diff_guard.py` 现状代码。
> 拍板：总指挥确认——批次 0-3 全部验收完毕后，对批次 2 验收记录的两处覆盖/口径问题做最小修复。只修这两点，不扩范围。
> 目标：① 定时发布路径不绕过差异化守卫；② guard 的「今日」口径与 `publish_log.published_at`（UTC）一致，消除本地 00:00–08:00 窗口的单号日发超限漏洞。

## 零、前置依赖

| 子项 | 依赖 | 状态 |
|---|---|---|
| R1 scheduler 接守卫 | `xhs_diff_guard.check`（批次 2 已就绪）、`check_scheduled_publish` 现码 | ✅ 可执行 |
| R2 guard 统一 UTC | `publish_log.published_at` 落 UTC `datetime('now')`（存量事实） | ✅ 可执行 |

> **建议顺序**：R2 先行（口径统一），R1 随后（守卫语义不变，只是补调度入口）。两处独立提交。

## 一、背景与 Why

批次 2 验收记录的两处发现，均为**非阻塞但会漏拦/错记**：

1. **定时发布绕过守卫（覆盖缺口）**：`publish_item`/`publish_batch` 已接入 `_enforce_xhs_diff_guard`，但 `check_scheduled_publish`（scheduler.py 定时发布）发布小红书时**不调用** diff guard——单号≤2/日、同指纹、素材≤3 号三条规则在定时路径全部失效。当前无自动排程在跑（无定时任务创建），故此前判定「暂不构成实时风险」；但定时能力一旦启用，就是差异化规则的**后门**。
2. **guard 时区口径不一致（边界窗口）**：`xhs_diff_guard._today_local()` 用本地 `datetime.now()`（UTC+8）算「今日」，而 `publish_log.published_at` 落的是 UTC `datetime('now')`。本地 00:00–08:00 发布的帖子会被 guard 记为「昨天」，而同一帖在 `count_published_today`（UTC `date('now')`）里算「今天」——两套「今日」并存，极端下单号一天实际可发 4 篇。

本单真实缺口：① 定时发布路径无守卫；② guard 的「今日」定义与库内 UTC 日期漂移。

## 二、铁律（不做的事）

1. **不改守卫语义**：`check()` 三规则、只拦不排程、返回 409/人话原因、不消耗重试——全部保持。本单只补**调用入口**和**日期口径**。
2. **不新增表/列/索引**：本单零 schema 改动。
3. **不顺手改其他**：不碰 `ratelimit` 口径、不碰 `count_published_today`、不碰 `asset_taxonomy`、不碰批次 3 台账。
4. **R1 的拦截处理与 app.py `_enforce_xhs_diff_guard` 对齐**：被拦时 `update_queue_status(item_id, item.get("status") or "queued", reason)` + 跳过本次发布，**不**消耗重试、**不**改 scheduled_at 顺延。
5. **R2 的「今日」一律 `date('now')`**（UTC，与 `datetime('now')` 存储同源），**禁止**再用 Python 侧 `datetime.now()` 或本地时区计算日期边界。

## 三、改动清单（两处独立提交，R2 → R1）

### 改动 R2：`xhs_diff_guard.py` 时区统一 UTC（先做，独立提交）

现状：`_today_local()`（L28-29）用 `datetime.now()`；`_list_today_published_xhs`（L60）与 `account_daily_count`（L81）把该日期作为参数传给 `date(pl.published_at) = date(?)`。

改法（最小、无签名变更）：

1. **删除** `_today_local()` 函数与 `from datetime import datetime`（L8，若已无其他使用）。
2. `_list_today_published_xhs`：删除 `today = _today_local()`；SQL 改 `AND date(pl.published_at) = date(?)` → `AND date(pl.published_at) = date('now')`；删除 `(today,)` 参数。
3. `account_daily_count`：同 2，删除 today 变量与参数，SQL 用 `date('now')`。

要点：`date('now')` 与 `publish_log.published_at` 的 `datetime('now')` 由**同一个 SQLite 时钟**产生，日期边界天然对齐；三处查询（account_daily / asset_matrix / fingerprint）统一到 UTC 自然日。

**明确不做**：不做「本地时区换算」（`datetime(published_at).astimezone(...)`）；不把「今日」做成可注入参数。

### 改动 R1：`scheduler.py` `check_scheduled_publish` 接 diff 守卫（独立提交）

现状（L546-558）：truth_guard → ratelimit → 解析 attachments/account → `publisher.dispatch`。小红书条目在定时路径无守卫。

改法：

1. **顶部 import**：`import xhs_diff_guard`（与 `truth_guard` 等并列）。
2. 在 `account` 解析之后、`result = await publisher.dispatch(...)` **之前**插入（仅小红书）：

```python
        # 批次 2 覆盖缺口修复：定时发布不绕过差异化守卫（只拦不排程）
        if platform == "xiaohongshu":
            guard_account_id = account["id"] if account else item.get("target_account_id")
            guard_ok, guard_reason = xhs_diff_guard.check(item, db, guard_account_id)
            if not guard_ok:
                db.update_queue_status(item_id, item.get("status") or "queued", guard_reason)
                logger.warning("差异化守卫拦截定时发布: id=%d, %s", item_id, guard_reason)
                continue
```

语义对齐 app.py `_enforce_xhs_diff_guard`：account 优先取已解析账号的 `id`（含 owner 归属校验后的值），无账号回退 `target_account_id`；被拦不改 status（保持 queued）、只写 `error_msg`、不消耗重试、不进入 dispatch。

**明确不做**：不在 scheduler 做 `_repair_xhs_queue_media`（守卫只读 attachments 的 asset_id，不需要文件存在）；不调整拦截后的下次重试/顺延逻辑。

### 改动 R3：测试

`tests/test_xhs_diff_guard.py` 增补：

1. **UTC 边界**：造一条 `published_at` 为「昨天 UTC」的记录（直接 `INSERT INTO publish_log ... datetime('now','-1 day')`），`account_daily_count` 不计入；今日记录正常计入。
2. **scheduler 接守卫（拦截）**：仿批次 3 `test_scheduler_success_calls_ensure` 模式——同一账号今日已发 2 篇，再让第 3 篇定时到点触发 `check_scheduled_publish`；断言 `publisher.dispatch` **未被调用**、queue status 仍为 queued、`error_msg` 含「单号今日已达上限」。
3. **scheduler 非小红书不受影响**：抖音定时条目走原路径（dispatch 被调用），守卫不介入。
4. 回归：现有 diff guard 测试全绿（seed 走 `add_publish_log` 落 UTC，`date('now')` 口径下天然一致）、批次 2/3 相关测试无回归。

跑：定向 `tests/test_xhs_diff_guard.py` + `tests/test_xhs_ledger.py`，再全量 `python3 -m pytest -q`。

## 四、验收清单

1. pytest：新增全绿，现有 diff guard / 台账测试无回归，全量无新增破坏。
2. **重启 app**。
3. **R2 边界**：本地时间在 00:00–08:00 窗口时（或临时把一条 publish_log 的 `published_at` 改到本地昨天/UTC 今天），guard 的「今日计数」与 `publish_log` 里 `date(published_at)=date('now')` 的行数一致。
4. **R1 拦截**：同一账号今日已发 2 篇小红书 → 给第 3 篇设 `scheduled_at` 为过去时间并触发 `check_scheduled_publish` → 该条**不发布**、status 保持 queued、`error_msg` 为「单号今日已达上限…」；日志出现「差异化守卫拦截定时发布」。
5. **R1 放行**：未触任何规则的定时小红书条目正常发布，且发布成功后台账（批次 3）照常预建。
6. **非小红书**：抖音定时条目不受守卫影响，正常走发布。
7. **语义不变**：`check()` 三规则与「只拦不排程」行为与 app.py 手动发布路径一致（diff guard 无逻辑改动）。

## 五、回滚

按改动独立回滚：`git revert` 对应提交。

- R2 回滚：`_today_local()` 恢复本地口径，重新回到批次 2 发现的时区状态（可接受，非实时风险）。
- R1 回滚：scheduler 恢复无守卫，定时发布再次成为后门——**如需回滚，应在有自动排程前执行**。

两处互不牵连；无 schema 变更，回滚零风险面。

## 六、备注

- R2 用 `date('now')` 而非 Python UTC 计算，是因为**同一 SQLite 时钟**保证与存储端 `datetime('now')` 绝对一致；Python `datetime.now(timezone.utc)` 在午夜边界可能与 SQLite 差一瞬，不做此选择。
- R1 拦截后 status 保持 queued、仅 `error_msg` 带原因——与 app.py `_enforce_xhs_diff_guard` 完全同语义，运营在队列里能看到「为什么没发」，手动改完可再发。
- 本单是批次 0-3 之外的最小收尾，不含任何新功能；验收通过后小红书链路「生成→门禁→渲染→差异化→可观测发布→台账复盘」的守卫覆盖与日期口径即闭环。
