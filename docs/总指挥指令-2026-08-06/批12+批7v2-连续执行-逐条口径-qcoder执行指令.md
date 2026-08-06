# 总指挥执行包 ｜ 批12（hook 链路）+ 批7 v2（生产链路）连续执行 —— 逐条口径（qcoder）

> 日期：2026-08-06 ｜ 执行工具：**qcoder**（用户指定）
> 前置：批6 已独立验收通过（README 行 9 = 已验收）。
> 顺序：**批12 → 批7 v2 → 端到端**，同一工作区连续执行，**不并行**（两批同改 app.py 不同函数，并行必冲突/混交）。
> 完整改动细节以两份母文档为准：`批12-hook链路-常青开场池与匹配放宽-opencode执行指令.md`、`批7-v2-chat一键生产打通-生产链路-Cursor执行指令.md`。本文件是**逐条口径**（改动要点 + 验收逐条），qcoder 按本文件逐条执行、逐条打勾回报。

---

## 〇、执行前置（必须先做，缺一不可）

1. **批11 未提交改动先落地**。当前工作区 `app.py`(+12)/`hotspot_media.py`(+19)/`scripts/assert_hotspot_channel_set.py`(+10/−4) 是批11 门禁扩词/回纳现场源的改动，**尚未提交**。qcoder 开跑前：先让批11 会话提交落地（commit `批11 ...`），否则批11 的 app.py 改动会混进批12 的提交（重演批3 混交教训）。
2. 批11 提交后，`git status` 应只剩：docs README（批7 v2/批12/批6 验收登记，收尾时一并提交）+ 未跟踪的指令文档。
3. **定位纪律**：改 app.py 用**函数名/代码结构锚点**，不用绝对行号（批11 扩词会平移行号）。
4. 每批跑完**重启 app**（旧进程持旧代码），再进该批验收。

---

# 第一部分：批12（hook 链路）—— #33 + #34

## 改动要点

### #33 新脚本 `scripts/build_generic_logistics_pool.py`
- 数据源：`db.list_assets(status="active")` 过滤 `file_type=="video"`、`hotspot_id is None`、`deprecated != 1`、`category` 命中映射、`source` ∈ (local_directory/upload/za_stock/mixkit/directory)。
- 场景映射：`warehouse/facility → warehouse`、`delivery → last_mile`、`customs → border`。
- 段选择：`db.list_asset_segments(asset_id, status="active", limit=20000)`，`thumbnail_path` 非空 + `5000≤duration_ms≤12000`，不足放宽 `≥3000`；每资产 ≤2 段、segment_index 升序、时间不重叠。
- 预算：warehouse 6 / last_mile 4 / border 4（合计 14，12-20 区间内）；`--scene-budget` 可覆盖。
- 幂等：已有 `hook_kind='generic_logistics' AND clip_status='ready'` 片段的资产跳过。
- 父行：每场景 1 条 `db.upsert_hotspot`，`source_url=f"buffalo://generic-logistics/{scene}"`，publisher="Buffalo 内部素材库"，published/retrieved="1970-01-01T00:00:00"，snapshot_sha256=稳定哈希。
- 片段行：`db.replace_hotspot_event_clips(asset_id, hotspot_id, events)`，evidence = what_happened/hook_reason/logistics_question 模板（中性场景表述，hook_reason 固定"不构成任何服务能力声明"）+ `event_identity=f"generic-{scene}-{asset_id}"`；`review_status="confirmed"`、`hook_kind="generic_logistics"`、`logistics_scenes=[scene]`、`confidence=0.95`。
- **后置**：`UPDATE hotspot_event_clips SET clip_status='ready', clip_path=asset.filepath WHERE id=?`（replace 硬编码 pending）。
- **归属纪律**：za_stock 素材标题/what_happened 不写"南非"、不写"Buffalo 能力"。
- CLI：`--dry-run`(默认) / `--apply` / `--check` / `--scene-budget`。

### #34 `_marketing_hook_candidates` 加 `allow_broad_match`
- 签名加 `allow_broad_match: bool = False`。
- 过滤器① `if not topic_profile and not specific_terms:` → `if not allow_broad_match and not topic_profile and not specific_terms:`。
- 过滤器② `if not direct and not profile_overlap and not intent_bridge:` → `if not allow_broad_match and not direct and not profile_overlap and not intent_bridge:`。
- `_retrieve_confirmed_chat_hooks` 初次调用（use_generic 分支）加 `allow_broad_match=use_generic`；relaxed 重试调用加 `allow_broad_match=True`。
- **不放宽**：`strict_terms and not specific_direct` 过滤保留（点名事件/道路仍要求事实命中）；timely_event 路径（use_generic=False → allow_broad_match=False）与原来完全一致。

## 验收（逐条勾选回报）

- [ ] **B12.1 脚本自检**：`--dry-run` 打印 3 场景计划且数量在预算内；`--apply` 后 `--check` 全绿（门禁/文件存在/总量 12-20/幂等增量 0）。
- [ ] **B12.2 库内核对**：SQL 查 `hotspot_event_clips WHERE hook_kind='generic_logistics'`——review_status=confirmed、clip_status=ready、clip_path 非空、event_identity=`generic-{scene}-{asset_id}`、logistics_scenes_json 单场景；总量 12-20。
- [ ] **B12.3 门禁**：池内每条过 `_is_confirmed_renderable_hotspot_hook`，0 失败（`--check` 覆盖）。
- [ ] **B12.4 #34 定向**（app 重启后）：`_marketing_hook_candidates(brief, hook_kind="generic_logistics", allow_broad_match=True)` 对空 profile 话题（`raw_input="南非本地快递怎么选？关键维度科普"`）返回**非空**；`allow_broad_match=False` 返回空（原行为）。
- [ ] **B12.5 timely_event 零回归**：新闻话题（"Beitbridge 边境拥堵"/"R60 事故"）`allow_broad_match=False` 候选与改动前一致。
- [ ] **B12.6 端到端**（依赖批7 v2，见下 A4 之后）：chat 输入「海外仓是什么」→ use_generic 返回 `status='matched'` + `video.status='ready'` → 前端出现「创建60秒视频项目」按钮 → 渲染首段为选定 generic_logistics 开场。
- [ ] **B12.7 全量回归**：`pytest -q` 相对基线（863 passed / 8 存量失败）**不新增失败**。
- [ ] **B12.8 重启**：app 重启后验 B12.4-B12.6。

---

# 第二部分：批7 v2（生产链路）—— #28-#32

## 改动要点

### #28 `chat_intent.py` 新增 `comparison_to_evergreen_topic`
确定性改写（南非+物流关键词 → 「南非本地快递怎么选？关键维度科普」/「南非物流怎么选？关键维度科普」；仅物流 → 「本地快递/物流怎么选？关键维度科普」；兜底「物流服务怎么选？关键维度科普」）。**输出必须零 COMPARISON_MARKERS**，重走 classify_content_mode 不再进对比门禁（无死循环）。

### #29 `app.py` `ai_chat` 降级块
- `authenticity_blocked = False` 旁加 `degraded_from_comparison = False`。
- 在现有 `if content_mode == "comparison_research" and evidence["evidence_state"] != "sufficient":` **之前**插入同条件降级块：置 `degraded_from_comparison=True`、`latest_topic = comparison_to_evergreen_topic(latest_topic)`、改写 `messages[-1]`、重算 `content_mode = classify_content_mode(...)`、`event_anchor = assess_event_anchor(...)`。
- 原框架分支保留为安全网（不删）。

### #30 `app.py` 加固 + 响应字段
- if/else 生产分支后、`for item in outputs:` 前：`if degraded_from_comparison: outputs, _ = ai_engine.enforce_comparison_authenticity(outputs, {"sufficient": False, "evidence_state": "insufficient"})`。
- 返回 dict 加 `"degraded_from_comparison"` + `"degradation_message"`（文案按母文档）。

### #31 `static/chat.html` 降级提示条
`resultCardMarkup` 构建 `degradedNotice`（含「补充评测资料」按钮），在 `${resultStateCard(result,id)}` 前插入。

### #32 测试更新
`tests/test_chat_intent.py` 加 2 条（never_reenters_comparison_gate / returns_safe_defaults），更新 1 条（comparison_without_evidence → degrades_to_evergreen_production，断言 degraded_from_comparison=true / content_mode=evergreen / result_state≠framework_pending_evidence / called hooks=1 / 不 enqueue discovery）。框架安全网测试保留。

## 验收（逐条勾选回报）

- [ ] **A1** 按 #28→#32 顺序执行，每步自检通过。
- [ ] **A2** 定向：`pytest tests/test_chat_intent.py -q` 全绿（含更新后的降级用例）。
- [ ] **A3** 全量：`pytest -q` 相对基线（863 passed / 8 存量失败）**不新增失败**，8 个存量失败逐条一致。
- [ ] **A4 运行时**（重启后，浏览器）：
  - 输入 `南非本地快递对比评测` → 「已自动切换科普视角」提示条 + 文案「南非本地快递怎么选」类 + **不再出现**「对比框架证据不足，暂不可创建视频项目」；接口 `degraded_from_comparison=true`、`content_mode=evergreen`、`result_state≠framework_pending_evidence`。
  - 输入 `对比 The Courier Guy 和 Aramex，官网报价隔日达 R89，来源官网价目表 2026-07-01` → 仍走**正式对比路径**，`degraded_from_comparison=false`。

---

# 第三部分：端到端（批12 B12.6 + 批7 v2 B5，同一项）

- [ ] **E2E** 重启后：输入 `海外仓是什么` / `南非本地快递怎么选？关键维度科普` / `南非跨境物流怎么入门` → 均出现「创建60秒视频项目」按钮 → 进入视频工作台可渲染，渲染首段=选定的 generic_logistics 开场。此步 = 批12 端到端与批7 v2 端到端的共同验收，**两批都落地后一次验证**。

---

## 提交（分两笔，防混交）

| 笔 | 内容 | 提交信息 |
|---|---|---|
| 批12 | 新脚本 `scripts/build_generic_logistics_pool.py` + app.py `_marketing_hook_candidates`/`_retrieve_confirmed_chat_hooks` 改动 | `批12 hook链路：#33常青开场池脚本 #34空profile匹配放宽` |
| 批7 v2 | chat_intent.py + app.py `ai_chat` + static/chat.html + tests | `批7 v2 生产链路：#28改写函数 #29-30降级走生产链+加固+响应字段 #31前端提示条 #32测试` |
| 收尾 | docs README 登记（批12/批7 v2/批6 已验收）+ Obsidian 改进日志 | `批12+批7v2 收尾：指令索引登记 + 改进日志` |

提交前 `git status` 确认批11 改动已不在工作区（批11 已单独提交）。

## 回滚

- 批12：删 `scripts/build_generic_logistics_pool.py` + `git revert <批12提交>`（恢复 allow_broad_match）。
- 批7 v2：`git revert <批7 v2提交>`。
- 回滚前提：与批11 无同函数冲突（批11 只碰门禁词表/prefilter，`_marketing_hook_candidates`/`ai_chat` 无交集，应无冲突）。

## 收尾

- 每批验收逐条打勾，把勾选结果贴回给总指挥逐条核。
- 同步 Obsidian 改进日志 + README 登记。
