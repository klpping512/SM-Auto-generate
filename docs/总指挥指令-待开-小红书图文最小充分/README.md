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
| 1 | P1 | 渲染前/后门禁 + 标题三层规则 | 1–2 天 | ✅ 已执行（[执行指令](第1批-渲染前后门禁与标题三层-Cursor执行指令.md)，待验收） |
| 2 | P2 | SEO 词库 / 分类配图 / 差异化守卫 | ~1 周 | 待开 |
| 3 | P3 | 人工台账 + 周复盘导出 | 运营为主 | 待开 |

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

**状态：✅ 已执行（2026-08-06，待验收）。** 正式执行指令见 [第1批-渲染前后门禁与标题三层-Cursor执行指令.md](第1批-渲染前后门禁与标题三层-Cursor执行指令.md)。

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

**目标：** 词库与选题进系统；配图跟分类；矩阵差异化可拦。

**范围草稿：**

1. 独立 SEO 词库/表（`xhs_seo_lexicon` 或配置表）→ 生成注入 `seo_meta`（主词/长尾/埋点位置）。
2. `xhs_cards` 配图：按选题分类匹配 `asset_taxonomy`（warehouse/delivery/customs/facility/brand）；匹配不上再兜底。
3. 差异化守卫：同文案指纹；同素材同日账号上限（对齐运营硬规则：单号≤2/日、同素材全矩阵≤3 号/日）。

**明确不做：** `from hotspot_lexicon import ...` 当 SEO；封面 A/B 框架。

---

## 第 3 批 · 台账与复盘

**目标：** 人驱动闭环可落库、可导出。

**范围草稿：**

1. 发布台账字段 + 人工录入接口/表（阅读/赞藏/评论/涨粉/48h 判定）。
2. 周复盘导出：Top/Bottom 选题、封面类型、关键词。

**明确不做：** 自动 RPA 扒小红书后台数据；根据数据自动改模板版本。

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
| 2026-08-06 | 对抗优化设计落地时立索引；代码未执行 |
| 2026-08-06 | 第 0 批已执行：PublishResult.category + attachment_missing + publish_log 扩列；待总指挥验收 |
| 2026-08-06 | 第 0 批验收通过：六项改动逐条核对 + harness 26/26；备注 assets.deprecated 范围串入、scheduler 合理扩展 |
| 2026-08-06 | 第 1 批已开：xhs_quality_gate 纯函数门禁 + 三层标题常量 + 渲染后完整性；truth_guard=软警告，语义红线重申 |
| 2026-08-06 | 第 1 批已执行：门禁模块 + ai_engine 有界重试 + /api/generate|/api/xhs/render 接线；待总指挥验收 |
