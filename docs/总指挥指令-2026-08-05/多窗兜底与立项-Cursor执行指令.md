# 多窗采样：兜底降级（合入前）+ 真修立项 —— Cursor 执行指令

> 拍板人：总指挥　执行：Cursor　日期：2026-08-05
> 背景：`34c66af` 的长视频"均匀多窗采样"未真正落地——`build_ytdlp_options` 按 5 窗写了 `_sample_offsets`，但 yt-dlp 多 `download_ranges` 没拼成，实际分析文件只有首窗 60s。副作用：①`sample_offsets` 虚报 5 窗覆盖；②原片 `duration_seconds` 被下载后的 60s 覆盖，导致重跑不再触发多窗、且下游拼片规划器（`hotspot_video_planner._usable_source_duration_ms`）误以为母片只有 60s。
> 铁律不变：只压管道，不碰 MiMo 判定 / 授权门禁 / `model_budgets` 上限数值。

---

## 第一部分（合入前必做）：兜底降级，消除下游回归

**目的：** 在多窗真修好之前，把长视频退回"单一连续窗"的诚实状态——覆盖能力等同"没做这个特性"（可接受，反正现在也没生效），但**彻底消除 `duration` 被焊死 60s 的下游风险**，且元数据不再虚报。

### Cursor 指令（可直接粘贴）

```
在 inspiration_assets.py 里，给多窗采样加一个默认关闭的开关，并把关闭时的行为退回单一连续窗；同时修掉 duration 覆盖与 offsets 虚报。

1. 新增环境开关 SA_HOTSPOT_MULTIWINDOW（默认 "0" 关闭）。加一个 helper multiwindow_enabled() -> bool 读它。

2. 改 compute_analysis_sample_windows（或其调用处）：
   - 当 multiwindow_enabled() 为 False 时：对 D>180 的长视频只返回单一连续窗 [(0.0, min(SA_HOTSPOT_SINGLE_WINDOW_SEC, D))]，SA_HOTSPOT_SINGLE_WINDOW_SEC 默认 120（沿用老行为）。D<=180 仍返回空列表（下整片）。
   - 当 multiwindow_enabled() 为 True 时：走真多窗（第二部分修好后才有意义）。
   - 单一连续窗 [(0,120)] 时，_sample_offsets 就写这一个真实窗；analysis_ms_to_original_ms 对单窗是恒等映射，remap 天然正确。

3. 修 duration 覆盖（关键，防下游回归）：
   下载/入库后，绝不能用分析件（可能只有 60/120s）的时长去覆盖母片记录里的原片 duration_seconds。定位到下载后写回 hotspot_media / asset duration 的那处：
   - 原片 duration_seconds（来自频道元数据）必须原样保留，不被分析件时长覆盖。
   - 分析件实际时长如需记录，另存独立字段（如 analysis_clip_seconds），不要污染 duration_seconds。
   - 补断言/单测：一条原片 duration_seconds=600 的母片，分析档只下 120s 后，读回该母片 duration_seconds 仍为 600。

4. .env.example 补 SA_HOTSPOT_MULTIWINDOW=0 与 SA_HOTSPOT_SINGLE_WINDOW_SEC=120，并注释"多窗真修完成前保持 0"。

严禁改 MiMo 判定 / 授权门禁 / max_calls。改完跑：
- 长视频（D>180）分析档 _sample_offsets 只含 1 个真实窗；
- duration_seconds 不被覆盖的单测通过；
- 既有 test_inspiration_assets / test_hotspot_video_materialization 全绿。
```

**验收：** 长视频跑一条，确认 ① `sample_offsets` 只报 1 窗（不再是 5 窗假覆盖）② 该母片 `duration_seconds` 仍是原片时长（不被写成 120）③ confirmed hook 的高清定稿片段内容对得上。过了这三条就可以 push。

---

## 第二部分（下一个工作项，单独排期）：多窗真落地

**目标：** 让长视频分析档真正覆盖首/中/尾多个时间窗，把第 4–7 分钟的 hook 也采到，且不损原片 duration、offsets 与实际内容严格对齐。

### 技术方案（推荐"分段下载 + ffmpeg 本地拼接"，比赌 yt-dlp 多 range 更可靠）

```
不要再依赖 yt-dlp 单次多 download_ranges 输出一个拼好的文件（当前就是它没拼成）。改成显式分段：

1. 由 compute_analysis_sample_windows 算出 N 个窗口 [(s1,e1),...,(sN,eN)]（沿用均匀铺窗逻辑：默认 60s 窗、总量 <=300s）。
2. 对每个窗口用 yt-dlp 单窗 download_ranges 分别下到独立临时文件 seg_i.mp4（单窗 yt-dlp 是可靠的）。
3. 用 ffmpeg concat（-f concat 或 filter concat，统一编码/时间基）把 N 段按顺序拼成一个分析文件 analysis.mp4。
4. _sample_offsets 记录每段在原片的真实 [start,end]，顺序与拼接顺序严格一致——这样 analysis_ms_to_original_ms 的顺序累加映射才成立。
5. 任一段下载失败：跳过该段、从 offsets 与拼接里剔除，其余段照常拼（缺一窗不致命）；全失败则回退单一连续窗。
6. 原片 duration_seconds 全程不动。

对齐校验（务必加）：拼好后逐窗校验——用已知在第 6 分钟有明显现场镜头的长视频，确认该镜头出现在拼接文件里、且策出的 hook 经 analysis_ms_to_original_ms 换算回的原片时间戳，高清定稿据此下的片段内容一致。

开关：全部就绪并过对齐校验后，把 SA_HOTSPOT_MULTIWINDOW 置 1 开启；默认仍从 0 起。
```

**验收（我拍板）：** 造一条已知中后段有现场镜头的长视频，① 拼接文件确含该镜头 ② hook 原片时间戳落在中后段窗口 ③ 高清片段内容对得上；三条全过才把默认开关翻到 1。

---

## 推进顺序
1. **先做第一部分兜底** → 过三条验收 → push（`c7c07f5` + `34c66af` + 本兜底一起进主线）。
2. **第二部分多窗真修**排下一个工作项，做完再开 `SA_HOTSPOT_MULTIWINDOW=1`。

## Mac 终端注意（沿用口径）
- 进目录：`cd "/Users/ylanlll/Desktop/商务部/distribution-manager"`；用 `python3`；命令不写 `#` 注释行、不用 `<...>` 尖括号占位。

## 铁律守线
- MiMo 策展模型 / 推理设置 / Hook 判定阈值 —— 不改。
- 授权门禁 / 信源清单 / `max_calls` 数值 —— 不动。
- 原片 `duration_seconds` —— 任何分析档下载都不得覆盖。
- 成片素材仍须高清（720p 定稿）；低清只用于分析。
