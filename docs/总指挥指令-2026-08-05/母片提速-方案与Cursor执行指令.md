# 母片管线提速 —— 方案与 Cursor 执行指令（保质量前提下加速）

> 拍板人：总指挥　执行：Cursor　日期：2026-08-05
> 铁律：**只压"管道"，不碰"判定"。** 慢在三段——下载 / 镜头分析 / MiMo 策展。质量只来自最后一段（MiMo 对不对口、Hook 判得准不准）。凡提速动作，只允许动前两段和"别做无用功"，**MiMo 用的模型、推理设置、Hook 判定逻辑一个字不改**。

---

## 0. 现状（已代码核验，别重复劳动）

母片下载走 `hotspot_media.download_authorized_video` → `inspiration_assets.download_authorized_media` → `build_ytdlp_options`（inspiration_assets.py:105）。它**已经**做了两件省流：

- 画质封顶 720p：`"format": "bv*[height<=720]+ba/b[height<=720]/b"`（:114）
- 长视频只下前 120 秒：`duration_seconds > 180` 时 `download_ranges=[(0,120)]`（:142-145）
- 走代理 `SA_HOTSPOT_PROXY / SA_YOUTUBE_PROXY`，默认 `http://127.0.0.1:7897`（:127-137）

所以**还能榨的空间**按 ROI 从高到低：① 先筛后下（别下音乐片/纯演播室）② 分析用低清 480/360 ③ 并发 ④ 双层模型。

**认知前提：** 这次 Transnet 回填慢，是因为我下令"每频道挖深 15–20 条 + 逐条预检"，那是**一次性建库成本**，不是日常。稳态每天只抓增量。别拿"回填一小时"当基准。

---

## P0 —— 先筛后下（ROI 最高，且提质不降质）

**为什么：** 现在是"先全量下载再判有没有 Hook"。日志里 `884 title=TNPA OM Music video hooks=0` 就是白下了一个音乐片。改成**下载前先用元数据（标题/时长/频道栏目）廉价预筛**，把"证明不出视觉 Hook"的先跳过。省的是最贵的整片下载，还把算力集中到对口素材。**预筛必须保守——只砍铁定没戏的，宁放勿误杀，否则伤召回。**

### Cursor 指令（可直接粘贴）

```
在 hotspot_media.py 里，母片下载入口 download_authorized_video 之前，新增一个纯元数据预筛函数 prefilter_mother_candidate(item) -> tuple[bool, str]，返回(是否下载, 跳过原因)。规则如下，全部只读已有的 title / duration_seconds / publisher 字段，不发起任何网络下载：

1. 标题命中"音乐/宣传曲"黑名单词（不区分大小写）直接跳过：
   music video, official audio, om music, jingle, anthem, song, lyric, 主题曲, 宣传曲, 片头曲
2. 时长异常：duration_seconds > 0 且 < 8 秒（太短出不了镜头）→ 跳过；> 3600 秒（超1小时，多为直播回放/发布会全程）→ 跳过。
3. 其余一律放行（True）。黑名单和阈值抽成模块顶部常量 PREFILTER_TITLE_BLOCKLIST / PREFILTER_MIN_SEC=8 / PREFILTER_MAX_SEC=3600，方便后续调。

在真正调用 download_authorized_video / materialize 的那处循环里，先调用 prefilter_mother_candidate；若返回 False，则：不下载、把该 media 标记状态为 prefiltered_skip（新增一个独立状态，不要复用 tech_fail，也不要进 no_qualified_hooks），并在日志打印 "prefilter skip id=... reason=..."。

严禁改动 MiMo 策展逻辑、Hook 判定阈值、授权门禁。改完补一个单测：喂一个 title 含 "OM Music video" 的 item 断言被跳过，喂一个正常港口作业标题断言放行。
```

**验收：** 重跑一批，日志出现 `prefilter skip` 且被跳过的都是音乐片/超长直播；`prefiltered_skip` 不进 H-hit 分母（复用铁律 E 的 `_is_tech_fail` 同款隔离思路，但它是"主动不下"不是"技术失败"，单列）。对口现场片一条都没被误杀。

---

## P0 —— 分析用低清代理，定稿才上高清（省带宽最狠，成片零损失）

**为什么：** 判 Hook 只要看得清画面，不需要成片画质。第一遍用 480/360 跑镜头分析 + MiMo 判定；**只有变成 confirmed hook 的那几条，才回头按精确时间段下 720p**。成片质量零损失（最终仍用高清片段），但分析阶段的下载字节数直接砍半以上。

### Cursor 指令（可直接粘贴）

```
在 inspiration_assets.build_ytdlp_options 里，把写死的 720p 改成可配置的两档：
- 读环境变量 SA_HOTSPOT_ANALYSIS_HEIGHT（默认 480），分析阶段用它拼 format：
  f"bv*[height<={h}]+ba/b[height<={h}]/b"
- 新增参数 hi_res: bool = False；当 hi_res=True 时用 SA_HOTSPOT_FINAL_HEIGHT（默认 720）。
- 其余参数（max_filesize、download_ranges 120s 截断、proxy、retries）保持不变。

调用链默认走分析档（480）。在"某母片被确认为 confirmed hook"之后的定稿路径里，对该 hook 命中的精确时间段用 hi_res=True 重新下载一次高清片段，覆盖/补齐用于成片的素材。若定稿高清下载失败，回退用已有的 480 分析件并打 warning（不要因此丢 hook）。

不要改 720p 以外的任何逻辑，尤其不动 download_ranges 的 120s 截断和 MiMo。补单测：hi_res=False 时 format 含 height<=480；hi_res=True 时含 height<=720。
```

**验收：** 分析阶段下载文件明显变小、变快；`confirmed_hooks` 数量与画质基线一致（不掉）；抽查一个 confirmed hook 的成片素材确为 720p。

### ⚠️ 配套修正：长视频（>5–10 分钟）的 hook 不能漏

**问题：** 现在 `duration_seconds > 180` 只下前 120 秒（download_ranges `[(0,120)]`）。港口/现场片的有效 b-roll 完全可能在第 4/7 分钟，只看前 2 分钟会**系统性漏检**长视频里的 hook。这个洞必须在改低清的同时一起补——因为低清 480 字节小了，才有本钱把采样铺满整片。

**正解：低清 + 全片均匀多窗采样（不是下全片，也不是只下前 120s）。**

```
在 inspiration_assets.build_ytdlp_options 里，把写死的 [(0,120)] 单窗，改成按时长自适应的"全片均匀多窗采样"，仅在分析档（hi_res=False）生效：

- 读环境变量 SA_HOTSPOT_SAMPLE_WINDOW_SEC（默认 60，每个采样窗口时长）、SA_HOTSPOT_SAMPLE_MAX_TOTAL_SEC（默认 300，总采样秒数上限）。
- 时长 D <= 180s：照旧下整片（本就短）。
- D > 180s：在 [0, D] 上均匀取 N 个不重叠的 60s 窗口，N = min(floor(MAX_TOTAL/WINDOW), 合理值)，窗口起点均匀分布覆盖首/中/尾（例如 D=600、WINDOW=60、MAX_TOTAL=300 → 取 5 个窗口，起点约 0/135/270/405/540）。把这些窗口传给 download_range_func。
- 关键：记录每个采样窗口在原片的真实起始偏移，写进该分析件的元数据（sample_offsets）。镜头分析/策展在报告 hook 时间戳时，必须换算回"原片真实时间戳"，不能用分析件内部相对时间。
- hi_res=True（confirmed hook 定稿）时：按 hook 的原片真实时间段精确下载 720p 片段，不做多窗采样。

严禁改 MiMo 与 720p 定稿逻辑。补单测：D=600 时生成的采样窗口覆盖到 >300s 的时间点（证明尾段能被采到）；hook 时间戳换算回原片正确。
```

**为什么这样够用又不贵：** 现场实拍片的 b-roll 通常全片散布，均匀多窗几乎必采到；即便某个 hook 落在窗口缝隙里被漏，它下次抓取/别的同类片还会覆盖，代价是"偶发漏一条"而非"系统性只看前 2 分钟"。总采样封顶 300s×480p 仍比下整片 720p 便宜得多。若发现某类长视频漏检严重，调大 `SA_HOTSPOT_SAMPLE_MAX_TOTAL_SEC` 即可，不必改码。

**验收补充：** 造一个已知在第 6–7 分钟有明显现场镜头的长视频，确认能被采样窗口覆盖并策出 hook；确认 hook 报告的时间戳是原片真实时间（据此下的高清片段内容对得上）。

---

## P1 —— 有上限的并发（省串行挂机时间）

**为什么：** 日志 `STAT S` = 在等 I/O、CPU 闲着，说明卡在一条一条下。开一个**有上限**的并发池即可。注意 yt-dlp 下载是阻塞式，且要防代理限流——池子要小、加抖动。

### Cursor 指令（可直接粘贴）

```
找到母片批处理里"逐条下载+分析"的那个 for 循环（backfill_transnet_mothers.py 及/或 run_authorized_hotspot_prewarm.py 里的主循环）。把逐条串行改成 concurrent.futures.ThreadPoolExecutor 的有界并发：

- max_workers 读环境变量 SA_HOTSPOT_DL_CONCURRENCY，默认 3，硬上限 5（超过按 5 截断）。
- 每个 worker 内部保持原来的：prefilter → 下载 → 分析 → 策展 全流程不变。
- 每次提交任务前加 0.5–1.5s 随机抖动，避免同一代理瞬时并发打满被限流。
- MiMo 策展调用如果本身已有额度/速率限制，并发数以下载为准即可，不要为并发去放宽任何 MiMo 限额或预算护栏。
- 汇总仍按 ready / in_flight / pending / confirmed_hooks 口径打印。

严禁：不要为了并发去改 model_budgets 的 max_calls、不要关任何门禁。改完跑一小批验证结果条数与串行一致、无重复入库。
```

**验收：** 同一批母片总耗时下降；`confirmed_hooks` 与串行跑法数量一致；无重复 media 入库、无代理被封报错激增。并发数可用 `SA_HOTSPOT_DL_CONCURRENCY=1` 一键退回串行。

---

## P2 —— 双层模型分工（省 MiMo 慢调用，最后再动，谨慎）

**为什么：** 现在预审(audit)和策展都走推理模型 mimo-v2.5-pro，推理模型天生慢（~30s/次）。把**便宜的粗筛/预审交给快的非推理模型**，**推理模型只留给真正的 Hook 判定**。质量守在关键那一步。**排最后做，因为最容易把判定质量带下来，必须 A/B 比对确认不掉质再合入。**

### Cursor 指令（可直接粘贴）

```
仅针对 hotspot_hook_curator 里的 audit/预审这一路（约 :224 的 audit 预算路由），评估把它从 mimo-v2.5-pro 切到一个更快的非推理模型（如 minimax 文本快速档）；真正的 Hook 策展路由（约 :360 planner_text）保持 mimo-v2.5-pro 不动。

做法：先不改代码，先跑一次 A/B —— 同一组母片，audit 分别用推理模型 vs 快速模型，比对最终 confirmed_hooks 集合是否一致（差异<5% 视为不掉质）。把 A/B 结果写进 docs/总指挥指令-2026-08-05/audit-model-ab.md 交我拍板。我确认不掉质后，再把 audit 路由改成快速模型并加环境开关 SA_HOTSPOT_AUDIT_FAST=1（默认关，绿灯后再开）。
```

**验收（我拍板）：** A/B 报告里 confirmed_hooks 集合差异 <5%，且被快速模型放过/砍掉的边界案例我人工看过没问题——才放行。否则回退。

---

## 推进顺序与回退

| 优先级 | 动作 | 提速来源 | 质量风险 | 回退开关 |
|---|---|---|---|---|
| P0 | 先筛后下 | 省整片无用下载 | 极低（保守黑名单） | 清空 PREFILTER_TITLE_BLOCKLIST |
| P0 | 低清分析+高清定稿 | 省带宽/时间 | 无（成片仍高清） | SA_HOTSPOT_ANALYSIS_HEIGHT=720 |
| P1 | 有界并发 | 省串行挂机 | 低（限流） | SA_HOTSPOT_DL_CONCURRENCY=1 |
| P2 | 双层模型 | 省 MiMo 慢调 | 中（须 A/B） | SA_HOTSPOT_AUDIT_FAST=0 |

**建议节奏：** 先上两个 P0（又快又不掉质），跑一批看提速与 confirmed_hooks 是否稳；再上 P1 并发；P2 双层模型最后、且必须 A/B 报告过我这关。

## Mac 终端注意（沿用一贯口径）
- 进目录：`cd "/Users/ylanlll/Desktop/商务部/distribution-manager"`
- 用 `python3`（不是 `python`）
- 命令里**不要**写 `#` 注释行、**不要**用 `<...>` 尖括号占位（zsh 会解析报错）
- 每步跑完把 summary（ready / confirmed_hooks / 耗时）贴回给我验收

## 铁律守线（提速不许碰的红线）
1. MiMo 策展模型、推理设置、Hook 判定阈值 —— 一个字不改。
2. 授权门禁 `HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED`、信源清单 —— 不动。
3. `model_budgets` 的 max_calls / 预算护栏 —— 不为并发放宽。
4. 成片素材必须是高清（720p 定稿），低清只用于分析阶段。
5. 任何"跳过/不下"都要单列状态（prefiltered_skip），**不得混进 H-hit 分母**，也不得伪装成 tech_fail。
