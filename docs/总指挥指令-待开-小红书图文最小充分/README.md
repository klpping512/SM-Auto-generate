# 总指挥指令（待开）· 小红书图文最小充分

> **状态：待开 · 本回合仅立索引，不执行代码。**  
> **设计真源：** [`docs/小红书图文种草链路-对抗优化版.md`](../小红书图文种草链路-对抗优化版.md)  
> **触发条件：** 总指挥确认对抗优化版设计后，按批另写「Cursor 执行指令」再开工。  
> **原则：** 最小充分；禁止视频 P3 / `hotspot_lexicon` SEO / huimei 主路径修复 / 自动扒数。

---

## 批次总览

| 批 | 优先级 | 主题 | 预估 | 状态 |
|---|---|---|---|---|
| 0 | P0 | 发布可观测 + 失败 dump | 0.5–1 天 | ✅ 已验收（2026-08-06 总指挥对抗审阅通过，两处备注见下） |
| 1 | P1 | 渲染前/后门禁 + 标题三层规则 | 1–2 天 | ✅ 已验收（2026-08-06 总指挥对抗审阅通过，备注见下） |
| 2 | P2 | SEO 词库 / 分类配图 / 差异化守卫 | ~1 周 | ✅ 已验收（2026-08-06 总指挥对抗审阅通过，备注见下） |
| 3 | P3 | 人工台账 + 周复盘导出 | 运营为主 | ✅ 已验收（2026-08-06 总指挥对抗审阅通过，备注见下） |

---

## 第 0 批 · 止血（真 P0）

**状态：已验收（2026-08-06）。** 执行指令见 [第0批-小红书发布可观测性-Cursor执行指令.md](第0批-小红书发布可观测性-Cursor执行指令.md)。

**验收备注（2026-08-06）：** 六项改动全部核对通过（A/B/C/D/E/F，逐条翻码比对 + 26/26 harness 断言通过）。

1. **范围串入**：`database.py` 的 `assets.deprecated` 列 + `list_asset_segments` 的 `asset_deprecated` 不属于第 0 批（本批只该动 `publish_log`），随 abfc948 一并提交。加法改动、无破坏，疑似并行素材渠道任务残留——**待并行任务推进时确认归属**，若属并行任务应从本批剔除记录在案。
2. **scheduler.py 超清单但合理**：改动清单只列 app.py 路由，但 `check_scheduled_publish` 同样回写 `publish_log`/queue 状态；executor 补了对称改动，**保留**（否则定时发布路径不落新列）。

**剩余验收项（需真环境/真账号，VM 无法覆盖）：** 全量 pytest 回归（本地跑 `python3 -m pytest -q`）；重启 app 后制造「磁盘缺失附件」与「未登录」两条链路目检（执行指令验收清单 3-6）。

**范围草稿（历史，被正式指令取代）：**

1. `adapters/xiaohongshu.py` + `publisher.dispatch` 路径：失败返回显式 `reason`（未登录 / 无图 / 超时 / 选择器失败等）。
2. 健康检查：账号就绪态（credentials / cookie），**不**以 `which huimei` 作为小红书可用性信号。
3. 轻量失败 dump：单表 `xhs_publish_diagnostics` **或** 扩现有 `publish_log` 字段——只记失败现场；**禁止**复刻 `hook_curation_diagnostics` + `hook_intake_diagnostics` 双表模式。

**明确不做：** 修复/安装 huimei CLI；把 `publish_via_huimei` 接回主路径。

---

## 第 1 批 · 门禁（真杠杆）

**状态：✅ 已验收（2026-08-06）。** 执行指令见 [第1批-渲染前后门禁与标题三层-Cursor执行指令.md](第1批-渲染前后门禁与标题三层-Cursor执行指令.md)。

**验收备注（2026-08-06）：** 改动 A-D 逐条翻码核对 + harness 复现新测试 11/11 + 有界重试 2 场景（修复成功 / 耗尽兜底）通过；语义红线无违规。

1. **误删事故（已恢复）**：474cb7a 曾误删 `/api/douyin/render` 路由 + `video_render_semaphore`，7c718f9 完整回填，净效果为零。纪律提示：后续 commit 前 `git diff --stat` 自查非目标区域。
2. **ADLAW「唯一」词**：口语文案（"唯一需要注意的是…"）可能误命中，命中即打回重试、后果可控；留待运营台账期按实际误伤率微调词表。
3. **VM 验证边界**：全量 pytest（841 passed / 8 UI 基线）以 executor 本地为准；VM 无网络装不了 pytest，新增测试与重试逻辑用 harness 复现。运行时目检（验收清单 3-6）需真环境完成。

**目标：** 坏结构 / 绝对化用语 / 无证据风险句进不了渲染与队列。

**范围草稿：**

1. 新模块建议 `xhs_quality_gate.py`：纯函数门禁 + 单测。
2. 渲染前：结构（5–7 页、cover、headline≤18、points 字数）+ 广告法黑名单 + `truth_guard.evaluate`（title + 拼接 image_pages 文本）；失败有界重试 `max_calls=2`。
3. 渲染后：文件存在、1242×1660、PNG 可解码、attachments 与页数一致。
4. `ai_engine.py`：封面钩子 3–10 字 / 卡面 headline≤18 / 笔记标题分层写入 prompt 与常量。

**明确不做：** 全页白名单强制；OCR/对比度/版式 AI 评估；移植 `weighted_actionable_score`。

**语义红线：** 禁止文档或代码注释写「过 truth_guard = 物流事实已校验」。truth_guard = 风险句须有证据。

---

## 第 2 批 · 运营接入

**状态：✅ 已验收（2026-08-06）。** 执行指令见 [第2批-SEO词库与分类配图与差异化守卫-Cursor执行指令.md](第2批-SEO词库与分类配图与差异化守卫-Cursor执行指令.md)。

**验收备注（2026-08-06）：** 改动 A-D 逐条翻码核对 + harness 30/30 断言通过；铁律检查全绿（不碰 `hotspot_lexicon`/`asset_taxonomy` 本体、唯一新表 `xhs_seo_lexicon`、guard 只拦不排程、无自动排程）。两处非阻塞发现，建议并入批次 3 前的小修单：

1. **scheduler 定时发布路径未接 diff guard（覆盖缺口）**：`publish_item`/`publish_batch` 已拦截，但 `check_scheduled_publish`（scheduler.py 定时发布）发布小红书时不会进单号≤2/日、同指纹、素材≤3 号拦截。当前无自动排程在跑（无定时任务创建），暂不构成实时风险；待首次自动排程启用前把 guard 接进 scheduler 发布路径即可。
2. **时区口径不一致（边界窗口）**：guard `_today_local()` 用本地 `datetime.now()`（UTC+8），而 `publish_log.published_at` 落 UTC `datetime('now')`；本地 00:00–08:00 发布会被记为「昨天」，极端下单号一天实际可发 4 篇。建议统一为存量 `count_published_today` 的 UTC 口径（`date('now')`）。

**已落地：**
1. **2B** `xhs_photo_match` + `render_carousel(photo_pool=...)`，attachments 打 `asset_id`
2. **2C** `xhs_diff_guard` 零新表；发布路径 409 只拦不排程
3. **2A** `xhs_seo_lexicon` 种子 + `seo_meta` 注入/落库（运营四层矩阵待校准）

**明确不做：** `from hotspot_lexicon import ...` 当 SEO；改 `asset_taxonomy` 本体；封面 A/B；自动排程。

---

## 第 3 批 · 台账与复盘

**状态：✅ 已验收（2026-08-06）。** 执行指令见 [第3批-发布台账与周复盘导出-Cursor执行指令.md](第3批-发布台账与周复盘导出-Cursor执行指令.md)。

**验收备注（2026-08-06）：** 改动 A-E 逐条翻码核对 + harness 25/25 断言通过；铁律/红线全绿（台账不读入门禁、无新增诊断表、scheduler 只加预建、批次 2 两发现仍排除）。一处范围串入备注见下。

1. **范围串入（非阻塞）**：`/api/xhs/ledger*` 五个端点 + `_build_xhs_ledger_csv` + `_xhs_ledger_owner_filter` 落在 `b4c9851`（公众号工作台提交）里，不在批次 3 提交 `a9c3dbc` 内。功能与行为正确，仅提交归属不符；根因是共享工作区并行会话（公众号线）把 xhs 台账 API 一并带进其提交。同批次 0 的 assets.deprecated 一类——记录在案，推送/回滚按「功能」而非「提交」追溯。
2. **手动发布路径确认非第四触点**：`_run_manual_publish` 只把会话置 `ready`/`closed`，不落 `publish_log` 的 `published`，台账无需预建。

**已落地：**
1. 单表 `xhs_ledger` + 录入/列表/候选/导出 API
2. 发布成功三处触点自动预建（含 `scheduler.py`，仅加预建不碰 diff guard）
3. `static/ledger.html` + 导航「发布台账」；周复盘 CSV 五区块 utf-8-sig

**明确不做：** 自动 RPA 扒数；台账读入门禁；顺手修批次 2 scheduler/时区。

---

## 整体修复单 · 批次 2 两发现（最小收尾）

**状态：✅ 已验收（2026-08-06）。** 执行指令见 [整体修复单-批次2发现scheduler接守卫与guard时区-Cursor执行指令.md](整体修复单-批次2发现scheduler接守卫与guard时区-Cursor执行指令.md)。

**验收备注（2026-08-06）：** R2/R1 逐条翻码核对 + harness 13/13 断言通过；铁律全绿（零 schema、guard 语义不变、不碰 ratelimit/台账/asset_taxonomy）。一处文档备注见下。

1. **改进日志重复条目（非阻塞）**：`docs/改进日志/2026-08.md` 中「整体修复：scheduler 接守卫 + guard UTC 口径」段落被粘贴两次（第一处写「全量 pytest 见下文回写 / app 待重启」，第二处为最终值「874 passed / 8 failed / app 已重启」）。内容一致、仅冗余，可留待日志整理时去重。
2. **scheduler 拦截幂等重臂**：被拦条目保持 queued 且不动 `scheduled_at`，每次调度 tick 重新过守卫、重新拦截（harness 三轮运行均命中），不烧重试、不误发——符合「只拦不排程」语义。

**已落地：**
1. **R2** guard `_today_local` → SQL `date('now')`（UTC，与 publish_log 同时钟）
2. **R1** `check_scheduled_publish` 接 diff 守卫，只拦不排程、语义对齐 app.py

批次 0-3 全部验收完毕后开的最小收尾，只修批次 2 验收记录的两处覆盖/口径问题，不含任何新功能：

1. **R1 scheduler 接 diff 守卫（覆盖缺口）**：`check_scheduled_publish`（定时发布）发布小红书时接入 `xhs_diff_guard.check`，拦截语义与 app.py `_enforce_xhs_diff_guard` 完全对齐——只拦不排程、不消耗重试、`error_msg` 写人话原因。
2. **R2 guard 统一 UTC 口径**：`xhs_diff_guard._today_local()`（本地 `datetime.now()`，UTC+8）替换为 SQL `date('now')`，与 `count_published_today` / `publish_log.published_at`（UTC `datetime('now')`）同一时钟对齐，消除本地 00:00–08:00 窗口单号日发超限漏洞。

**建议执行顺序**：R2 先行（口径统一）、R1 随后（守卫语义不变），两处独立提交、独立回滚。铁律：零 schema 改动、不改守卫语义、不碰 ratelimit/台账/asset_taxonomy。

---

## 开批检查清单（总指挥放行前）

- [ ] 已读并对齐《小红书图文种草链路-对抗优化版》第六节批次与「明确不做」
- [ ] 本批有独立 Cursor 执行指令（含验收命令与回滚点）
- [ ] 不引入视频质量子系统依赖
- [ ] 改进日志三处同步规矩已知悉

---

## 修订记录

| 时间 | 说明 |
|---|---|
| 2026-08-06 | 整体修复单验收通过：R2/R1 逐条翻码 + harness 13/13 + 铁律全绿；备注改进日志重复条目（非阻塞）、scheduler 拦截幂等重臂；小红书链路守卫覆盖与日期口径闭环 |
| 2026-08-06 | 整体修复单已执行：R2 `d864c6b`（UTC date('now')）→ R1 `c7296cd`（scheduler 接守卫）；定向 21 passed |
| 2026-08-06 | 整体修复单已开（批次 2 两发现最小收尾）：R1 `check_scheduled_publish` 接 diff 守卫（只拦不排程、语义对齐 app.py）+ R2 guard `_today_local()` 统一 UTC `date('now')`；零 schema、两处独立提交/回滚；待执行（批次 0-3 已全部验收） |
| 2026-08-06 | 对抗优化设计落地时立索引；代码未执行 |
| 2026-08-06 | 第 0 批已执行：PublishResult.category + attachment_missing + publish_log 扩列；待总指挥验收 |
| 2026-08-06 | 第 0 批验收通过：六项改动逐条核对 + harness 26/26；备注 assets.deprecated 范围串入、scheduler 合理扩展 |
| 2026-08-06 | 第 1 批已开：xhs_quality_gate 纯函数门禁 + 三层标题常量 + 渲染后完整性；truth_guard=软警告，语义红线重申 |
| 2026-08-06 | 第 1 批验收通过：门禁/重试/接线逐条翻码 + harness 11/11 + 重试 2 场景；备注 /api/douyin/render 误删已恢复、「唯一」词待运营期微调 |
| 2026-08-06 | 第 2 批已执行（B→C→A）：xhs_photo_match + xhs_diff_guard + xhs_seo_lexicon 种子/seo_meta；运营四层矩阵待校准 |
| 2026-08-06 | 第 2 批验收通过：改动 A-D 逐条翻码 + harness 30/30 + 铁律全绿；备注 scheduler 定时发布未接 diff guard、guard 本地时区 vs published_at UTC 口径不一致（均非阻塞，待小修单） |
| 2026-08-06 | 第 3 批已执行：xhs_ledger + 三处预建（含 scheduler）+ ledger.html + 周复盘 CSV；批次 2 两发现仍排除 |
| 2026-08-06 | 第 3 批验收通过：改动 A-E 逐条翻码 + harness 25/25 + 铁律全绿；备注 ledger API 范围串入 b4c9851（公众号提交）、手动发布路径非第四触点 |
| 2026-08-06 | 第 3 批已开：xhs_ledger 单表 + 人工录入 API + 发布成功三处触点自动预建 + 周复盘 CSV 导出（概览/Top3/Bottom3/封面分布/关键词表现）；台账日期一律 UTC 口径、不读入门禁 |
| 2026-08-06 | 第 2 批已开（框架）：xhs_photo_match 分类配图 + xhs_diff_guard 差异化守卫（零表结构新增）+ xhs_seo_lexicon 词库；2A 依赖运营词矩阵，建议 B→C→A |
| 2026-08-06 | 第 1 批已执行：门禁模块 + ai_engine 有界重试 + /api/generate|/api/xhs/render 接线；待总指挥验收 |
