# 总指挥指令登记 —— 2026-08-06

> 前置诊断：见 memory `hotspot-server-stale-json-residual`。
> 第一动作：**重启 app**（08-04 13:03 旧进程未重启 → 08-05 修复生产未生效），重启后验三件事（5 台频道、duration 不覆盖、单条策展 JSON 可解析）。
> **收束存档**：[全套链路收束盘点](链路收束盘点-2026-08-06.md)——08-04/05/06 三批改动全链核验，链路设计已闭环。
> **批 4（清债务）**：[批4-清债务-Cursor执行指令.md](批4-清债务-Cursor执行指令.md)——#12 死路由 / #13 死文件 / #14 脚本归档 / #15 Qwen文案 / #16 依赖 / #17 optimized_generation 删除。前置批 1-3 已验收。
> **批 5（立项）**：[批5-kill渲染进程改造-Cursor执行指令.md](批5-kill渲染进程改造-Cursor执行指令.md)——#18 cleanup 超时真杀进程+标canceled / #19 回归测试。批 2 #10 遗留立项。**已验收。**
> **批 6（立项）**：[批6-RAG选片链去留-Cursor执行指令.md](批6-RAG选片链去留-Cursor执行指令.md)——体检报告 §8 拍板执行：删除模型 RAG 下载决策链（`select_for_hook_ingestion`+诊断设施+14 用例），保留证据检索 `retrieve_service_evidence` 与重策展工具。#20-#27。
> **批 7（立项）**：[批7-商务团队一键生产-对比自动降级科普-Cursor执行指令.md](批7-商务团队一键生产-对比自动降级科普-Cursor执行指令.md)——目标用户=商务团队（不懂技术）。对比题材无真实资料时自动降级科普视角直接生产视频（打字→出视频），替代「对比框架证据不足，暂不可创建视频项目」死胡同；不拆门禁。#28-#32。

## 指令列表

| # | 指令 | 状态 | 说明 |
|---|------|------|------|
| 1 | 策展 JSON 失败：原始返回 dump + 一次性重试 | **已执行** | 落库诊断 + max_calls=2 一次性重试（use_cache=False）；保留频道 10 条 requeue 后全出合法 JSON（0 hook）；诊断表暂无现场（未触发解析失败）。文件：`策展JSON失败-原始返回dump+一次性重试-Cursor执行指令.md` |
| 2 | za-stock 受控开闸 + 文案门禁兜底 + cap→20000 | **已执行+验收过** | `za_stock_license` 受控进池 + 口播强制安全模板 + cap 20000；清关验收 category_match↑、za customs 可用资产 24。**总指挥独立复核**（复跑真实 `diagnose_owned_matching`）：category_match=351 / after_dedup=155、category_inventory={facility:53,brand:3} 无 customs、verdict=healthy；新测 4 条，全量 811 passed。commit `46ea006`。文件：`za-stock-受控开闸-Cursor执行指令.md` |
| 3 | za-stock 素材库展示层修复 | **已执行+验收过** | source_label 三元（免版权素材）+ assets.html 拆两节（ownedAssets/materialSections 独立守卫 + 空态兜底）+ 蓝色 za-stock pill tag + 新测试；总指挥复核打回后补齐空态回归与规格测试，复验通过。文件：`za-stock-素材库展示层修复-Cursor执行指令.md`。 |
| 4 | 入库选片 JSON 加固 | **已执行（待验收）** | selection/audit 两段：parse 升级 `_extract_json`（剥 think/平衡 JSON，行级校验未动）+ 一次性重试（use_cache=False）+ `hook_intake_diagnostics` 落库（job_id+stage）+ dump 定性脚本；budget 单点 max_calls=2。全量 817 passed（8 存量 UI 失败不变）；dry_run 真跑 retries 全 0。文件：`入库选片JSON加固-Cursor执行指令.md`。 |
| 5 | 公众号图文发布 — 方案设计 + 阶段0可执行指令 | **v3·6项决策全拍板，阶段0已落地（113ce73）** | 新内容单元（图文长文，独立选题，非短视频复用）；参考同行范文拆解出模板规律。**v2 修订**：对抗审查发现 `evidence_harness.py` 无独立抓取能力，独立信源采集+RAG证据库在产能1–2篇/周时ROI为负（呼应08-04"把力气使错层"）——改为人工投喂资料包+3态状态机。**v3**：§5.5 固定物料视觉稿由总指挥起草+拼占位图（另行交付）；§5.6 账号分散、各商务团队各自运营，阶段0不受影响，阶段1复用现有 `accounts` 表多账号设计即可。阶段0可执行指令已产出（`articles`表+人工投喂CLI+生成模块+人工选图CLI+渲染CLI+发布标记CLI，含事实锚定硬约束的正则兜底扫描），**2026-08-06 已由 opencode 落地（commit `113ce73`+`5480f1a`，总指挥独立复核通过）**。文件：`公众号图文发布-方案设计.md`、`公众号图文发布-阶段0-可执行指令.md`。 |
| 6 | 平台路由与公众号工作台 — 前端改造 | **已产出，待 opencode 落地** | 把平台选择器升级为**生产路由**：侧边栏全局「生产平台」上下文（localStorage）+ 新前端 `articles.html` 公众号图文工作台（清单/新建/生成/选图/渲染/发布）+ 后端 `/api/articles/*` 端点与 `/article-assets` 挂载 + CLI 函数抽取（行为不变）+ chat/video-project 生产护栏。默认平台 douyin 零回归。文件：`平台路由与公众号工作台-前端改造-可执行指令.md`。 |
| 7 | 批 4 清债务（删除/归档/文案/依赖） | **已执行（待验收）** | #12 删 /api/douyin/render 三死路由+独占 helper+3 个依赖测试（`9475327`+`a1ea0ac`；曾被并行会话以“误删”恢复过一次，已重删并注明）；#13 死文件（.bak/138M 日志/debug 截图，`4c6ef13`）；#14 脚本归档 18 个→scripts/archive/（`0b5a794`）；#15 Qwen 文案中性化（`1c6df36`）；#16 依赖修正（`8895eb1`）；#17 optimized_generation 整链删除（`7e1c378`，前端展示块随 `b4c9851` 入库）。全量 854 passed / 8 存量基线失败；重启后 /api/douyin/render 404。kill 渲染进程另立项。文件：`批4-清债务-Cursor执行指令.md`。 |
| 8 | 批 5 kill 渲染进程改造（批 2 #10 遗留立项） | **已验收** | #18 cleanup_stale_jobs running 超时分支改为 cancel_render 真杀进程组 + 标 canceled（原标 failed 时 is_canceled 不认，渲染线程照跑并在完成后覆盖成 succeeded，清理实际无效；pending 分支保持 failed）；#19 新增回归用例 test_cleanup_stale_jobs_kills_running_render（真 sleep 进程被杀+状态断言+注册表清理）。定向 26 passed；全量 875 passed / 8 存量基线失败；运行时验证：6 分钟前 running 记录在 60s 周期内被清成 canceled/超时清理/渲染超过 300 秒自动终止。commit `c7ee720`。总指挥代码层核验：c7ee720 恰改 2 文件（video_renderer.py +19/-5、测试 +47），与指令 #18/#19 逐条一致。边界：app 重启前的孤儿进程不在 _ACTIVE_PROCESSES，需系统级清理，不在本批。文件：`批5-kill渲染进程改造-Cursor执行指令.md`。 |
| 9 | 批 6 RAG 选片链去留（体检报告 §8 立项） | **已验收**（总指挥独立核验通过） | 拍板：**去**——删除模型 RAG 下载决策链。执行：`e871ca3`（#20-22 删 hotspot_hook_intake.py+dry_run/dump 脚本+hook_intake_diagnostics 表/2 函数）；`5977b79`（#23 精简 intake_sop 保留 retrieve_service_evidence / #24 摘 reprocess 死 flag 保重策展主体）；`8f9f837`（#25 删 14 条 intake 用例保留 11 条策展 / #26 补 2 条活函数单测 / #27 文档注释收敛）。**总指挥独立核验**：① diff stat 与声明逐字吻合（−549 / +8−70 / +56−421，批4教训=看 diff 不看提交信息）；② 删除文件从磁盘消失、database.py 无 hook_intake_diagnostic 残留、DDL/索引/2 函数确为删除对象；③ intake_sop 90 行、活函数保留齐全、reprocess 四活函数保留且签名正确；④ 14 删/11 留/2 增测试函数名逐一对应（行号=指令预测−16）；⑤ 两条新测试**直接执行通过**、活依赖调用方完好；⑥ 运行时：诊断表已 DROP、核心三文件零引用、空 corpus 返 []、reprocess --help 无死 flag；⑦ 定向 17=11+2+3+1、全量 863=875−14+2 账目自洽。**环境限制**：沙箱无 fastapi/pytest 且 pip 代理 403，全量 pytest 863/8 未能在此复跑，建议本机或 pip 可用环境终验一次。文件：`批6-RAG选片链去留-Cursor执行指令.md`。 |
| 10 | 批 7 v2 商务团队一键生产（**生产链路**：对比自动降级科普 + 生产接线） | **已执行+验收过**（qcoder 连续执行） | 拍板：hook 链路分开解决（批12）。本批只做生产视频链路 #28-#32：`comparison_to_evergreen_topic` 确定性改写（零 COMPARISON_MARKERS 防死循环）+ app.py `ai_chat` 降级走正常生产链 + enforce 加固 + 响应字段 `degraded_from_comparison` + 前端降级提示条 + 测试更新。**端到端验收（按钮出现）依赖批12（hook 链路）**。执行顺序：与批12 同一会话连续执行（同改 app.py 不同函数，防并行冲突）。文件：`批7-v2-chat一键生产打通-生产链路-Cursor执行指令.md`（取代原 `批7-商务团队一键生产-对比自动降级科普-Cursor执行指令.md`）。**qcoder 执行结果（2026-08-06，commit `e2962f2`）**：#28 comparison_to_evergreen_topic 零 COMPARISON_MARKERS 无死循环；#29 ai_chat 门禁触发自动降级走生产链（框架分支留安全网）；#30 enforce 加固 + degraded_from_comparison/degradation_message 响应字段；#31 前端降级提示条；#32 测试（test_chat_intent 18 passed）。A2 定向 18 passed；A3 全量 865 passed / 8 存量失败零新增；A4 运行时：无资料对比→degraded=true/evergreen/formal_content（非 framework_pending_evidence），带资料对比→degraded=false 仍走正式对比。端到端按钮随批12 一次验过。 |
| 11 | 热点素材库修复整合（门禁扩词 + 回纳现场源 + 清理残留 + 管线终态） | **已执行+总指挥复核通过**（验收 1-5/7-8 过，6 待用户浏览器目检） | 总指挥拍板"三件事一起做"。A) 门禁 `out_of_scope` 扩词 → 可显示 122→**84**（opencode 快照 151 行算得 83；当前库 154 confirmed 用 app.py 真实函数重算 84）；被挡 39 条逐条人工核对全属公共/社会类、**零物流误杀**（强相关现场 ≥37，opencode 口径 46）；`发布会`/`品牌` 裸词仅挡 4 条非物流，维持 A1 原样。B) `.env` 回纳 SA Today(`@SAtoday`)+South Africa Now(`@SouthAfricaNow1`) 共 7 台 + `PREFILTER_NOISE_TOPIC_BLOCKLIST`（5 类噪音全拦/7 类现场全放，yt-dlp 实抓两频道可达）；**配套**：`assert_hotspot_channel_set.py` EXPECTED 5→7、FORBIDDEN 移除两频道（指令验收 B2 要求 7 台，属执行必需补漏）。C) 删 8 条 SABC 残留（1034/1043/1135-1140）→ SABC 259→251、终态 0 残留。D) za-stock 终态确认、Transnet 7 条未到期不强制。**注意（记录更正）**：README 曾误植"总指挥核验'8 条 id 已不存在/删 0 行'"——指令编写时点实查 8 条全部存在、指令精确列 id 待删，opencode 删 8 行与指令意图一致。回纳后新 Hook 已在产（id 265-272，265 海峡货轮/266 聋哑学员坐卡车过门禁）。**观察**：271 协会代表抵制审计、272 反对党签约过门禁（公共事务类 2 条漏网，建议暂不加词，宁放勿杀）；prewarm 全链 15min 超时=成本特性非故障。git 已提交未推送。文件：`整合-回纳现场源与门禁扩词-opencode执行指令.md`。 |
| 12 | 批 12 hook 链路：常青开场池（#33）+ 空 profile 匹配放宽（#34） | **已执行+验收过**（qcoder 连续执行） | hook 链路（用户拍板与生产链路分开解决），是批7 v2 的**跨链前置**。#33 `scripts/build_generic_logistics_pool.py`：从 za/自有 active 视频资产按场景（warehouse/last_mile/border）建 12-20 条 `generic_logistics` 确认 Hook（父行 upsert_hotspot + `replace_hotspot_event_clips` + 置 ready/clip_path，evidence 中性场景表述+`event_identity`，za_stock 不写"南非"）；#34 `_marketing_hook_candidates` 加 `allow_broad_match=False` 参数，仅 use_generic 分支传 True，放宽 :1969/:1986 两个空 profile 过滤器，timely_event 绝不放宽。与批7 v2 同一会话连续执行。文件：`批12-hook链路-常青开场池与匹配放宽-opencode执行指令.md`。**qcoder 执行结果（2026-08-06，commit `e59d4a9`）**：#33 建 14 条 generic_logistics 开场池（warehouse 6/last_mile 4/border 4，门禁/文件存在/总量 12-20 全绿，幂等增量 0，归属纪律零"南非/Buffalo能力"字样）；#34 allow_broad_match 仅放宽 use_generic 两过滤器 + 补 generic 父热点 top-100 可见性（timely_event 零回归）。**口径修正**：母文档 source 写 za_stock/mixkit，生产库实为 za_stock_license/mixkit_license，脚本两种都认。B12.1-5/7 过；B12.6/8 与端到端一次验过（3 常青话题均 matched+video ready+generic 开场）。 |

## 拍板（2026-08-06）

- **不调 `max_output_tokens=1000`，先观察。** 依据：诊断表空表无截断样本；调大会同步放大 `required_output_budget` 成本；新机制已能接住下次失败。触发：偶发失败 → dump 分类；截断为主再开下一条指令。
- **za-stock 受控开闸 + cap→20000。** 依据：za-stock 是受控来源（attribution 已写"通用背景/非南非现场/非Buffalo能力"，brand 标签为空永远排真素材后），画面补洞；口播由 `apply_overclaim_guard` 对 za-stock 来源一律强制安全模板，不构成 Buffalo 能力证明。cap 2000 只覆盖 asset_id≤314，是截断真 bug，提到 20000 覆盖全库约 1.9 万段。
- commits `94fe241` + `9391552`：已推送（push 不动运行态；新码已在 PID 39848）。

## 拍板（2026-08-06）

- **不调 `max_output_tokens=1000`，先观察。** 依据：诊断表空表无截断样本；调大会同步放大 `required_output_budget` 成本；新机制已能接住下次失败。触发：偶发失败 → dump 分类；截断为主再开下一条指令。
- commits `94fe241` + `9391552`：已推送（push 不动运行态；新码已在 PID 39848）。

## 拍板（2026-08-06 · 晚）

- **热点素材库修复三件事整合一起做**（指令 #11）。依据：①门禁 122 条里漏网 35 条公共/体育/社会内容，真实物流画面 ≈87（强相关 32）；②SA Today / South Africa Now 是现场画面主力（≈70% 公路/卡车/边境实拍），被砍是"先停田、地没翻"；③SABC 8 条残留是已砍频道死重。铁律：门禁扩词不删 DB 行、prefilter 宁放勿杀不碰现场词、Transnet 7 条未到期不强制、改完重启。
- **执行工具切换为 qcoder**（用户 2026-08-06 晚指定，批12/批7 v2 由 qcoder 连续执行；此前为 opencode）。执行包：`批12+批7v2-连续执行-逐条口径-qcoder执行指令.md`——**执行前置：批11 未提交改动（app.py/hotspot_media.py/assert_hotspot_channel_set.py）必须先提交落地**，否则混交。

## 待办（残余 / 另立）

- 914 下载 300s 超时（BDTV 演播室片，低价值）暂挂。
- ~~入库选片路径（hotspot_hook_intake `_parse_selections`/`_parse_audit`）JSON 加固另立项目~~：已执行（指令 #4），诊断表 `hook_intake_diagnostics` 已就位，后续用 `scripts/dump_hook_intake_diagnostics.py` 采样定性。
- ~~za-stock 管线~~：pull+ingest+定点处理+**受控开闸已执行**；清关诊断 `after_dedup` 含 za-stock，customs 可用资产 24。
- 可选：每周 dump 巡检（total>0 时报分类）——未默认开启。

## za-stock 定点处理验收（2026-08-06）

- `process_za_stock.py`：**61/61 ok，fail=0**；assets `ready`；primary 保持 manual（customs 24 / facility 22 / delivery 15）；产出 segments 142。
- **owned-matching 仍看不到 customs**：两道闸——① `_is_buffalo_usable_source` 排除 `za_stock_license`（合规：免版权不得当 Buffalo 自有证明）；② `list_asset_segments` 硬上限 2000 + 按 asset_id 升序，扫描到 max asset_id≈299，轮不到 866+。
- **拍板（2026-08-06）**：**受控开闸** + **cap→20000** 已执行并复验：`funnel.category_match=351`、`after_dedup=155`、za customs 可用资产 24；`category_inventory` 仍仅为被踢类别（facility/brand），不含 customs。
