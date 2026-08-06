# 总指挥指令 批4 ｜清债务（删除 / 归档 / 文案 / 依赖）

> **前置**：批 1（`265da5c`）、批 2（`299808f`）、批 3（`205aeb7`）已验收闭环。
> **性质**：纯清理，**零功能变更**。只移不删（脚本归档）、只改文案（Qwen 残留）、删确认的死代码。
> **红线**：不碰小红书线文件（`adapters/xiaohongshu.py`、`xhs_cards` 等）；不碰公众号线脚本（`*_article*` 系）；**kill 渲染进程改造另立项，本批不做**。
> **依据**：体检报告 `docs/链路体检报告-2026-08-06.md` 与批 2 文档 `#9` 决策。总指挥 2026-08-06 独立盘点复核。

---

## 指令 #12：删除 `/api/douyin/render` 三死路由

**目标**：前端零调用的三个死路由（app.py 当前行号）：`POST /api/douyin/render`(:4022)、`GET /api/douyin/render/{job_id}`(:4080)、`POST /api/douyin/render/{job_id}/retry`(:4097)。

**改动** — `app.py`：

1. 删除上述三个路由函数。**先看它们各自引用了哪些 helper**：
   - 若 helper 只被这三个路由使用 → 一并删除；
   - 若 helper 与其他活路由共用 → 保留，只删路由。
2. 删除后全局搜 `douyin/render`，确认无残留引用（前端 static/ 已确认零调用）。

**验收**：
1. `grep -rn "douyin/render" app.py static/ templates/ 2>/dev/null` → 无输出。
2. 启动 app，`/api/douyin/render` 返回 404（不再是 405/执行）。
3. 回归测试全量绿（现有测试无 douyin/render 依赖则直接通过）。

**回滚**：git 恢复删除块。

---

## 指令 #13：死文件清理

**目标**：已确认的死文件，删除或清空。

| 文件 | 现状 | 动作 |
|---|---|---|
| `video_renderer.py.bak` | 8K，备份文件 | 删除 |
| `.cursor/debug-34c455.log` | **139M** 巨型调试日志 | 删除 |
| `static/debug/` | 2.3M，运行时调试截图目录 | **清空内容，保留目录**（`adapters/xiaohongshu.py` 的 `_save_debug_shot` 会 `mkdir(parents=True)` 重建，目录本身不能删） |

**验收**：三个目标清理后，`git status` 显示删除；`static/debug/` 目录仍在但为空；重启 app 后 `_save_debug_shot` 仍能写（RPA 发布路径可截图）。

**回滚**：`.bak` 和日志从 git 历史/备份找回；static/debug 由运行时代码自动重建。

---

## 指令 #14：脚本归档（18 个 → `scripts/archive/`）

**目标**：一次性配置/迁移/诊断旧脚本移入 `scripts/archive/`（**只移动不删除，可回滚**）。脚本无相互 import 依赖（均为独立可执行脚本），移动安全。

**必归档清单**（当前存在于 `scripts/`）：

```
ab_compare_render_params.py
backfill_buffalo_brand_tags.py        # 已有 admin 端点
backfill_video_project_status.py
cap_existing_hotspot_events.py
cleanup_ineligible_hotspot_hooks.py   # 已有 admin 端点
configure_minimax_text_routes.py
configure_mimo_all_routes.py
configure_mimo_video_evaluator.py
configure_mimo_vision_tagger.py
configure_qwen37_text_routes.py
diagnose_minimax_json.py
dump_topic_docs.py
ingest_mixkit_asset.py
mark_generic_logistics_hooks.py       # 逻辑已并入 audit_playable_hooks.py
migrate_account_credentials.py
run_fast_visual_preview.py
run_sample_harness.py
run_video_quality_mvp.py
```

**明确保留（白名单，不归档）**：`mark_legacy_channel_assets.py`（批3 运维）、`dump_hook_curation_diagnostics.py` / `dump_hook_intake_diagnostics.py`（指令#1/#4 诊断）、za-stock 管线四件（`pull_za_stock`/`ingest_za_stock`/`process_za_stock`/`rebuild_asset_taxonomy` 及 `queue_taxonomy_rebuild_batches`）、audit 系列（`audit_playable_hooks`/`audit_eligible_hotspot_hook_pairs`/`audit_existing_dual_preview`）、`dry_run_hotspot_intake_sop.py`、素材/热点运维（`materialize_hotspot_event_clips`/`backfill_transnet_mothers`/`reprocess_hotspot_hook_source`/`reseed_hotspot_sources`/`run_hotspot_event_rebuild`/`run_dual_library_preview`/`run_authorized_hotspot_prewarm`/`run_c_end_video_closure`/`measure_source_hhit`/`sweep_matching_diagnostics`/`assert_hotspot_channel_set`/`check_source_candidates`）、**公众号线全部 `*_article*`/`generate_article`/`list_articles`/`render_article_package` 系不动**、`gen_icons.py`/`download_asr_model.py`/`preview_mimo_tts.py`（不确定，本批不动）。

**改动**：`mkdir -p scripts/archive && git mv <file> scripts/archive/`。

**验收**：`scripts/` 剩余 34 个（52-18），全部在白名单内；`scripts/archive/` 18 个；全量测试绿（无测试 import 已归档脚本）。

**回滚**：`git mv scripts/archive/<file> scripts/`。

---

## 指令 #15：Qwen-VL 文案统一（只改注释/docstring）

**目标**：模型已全部切 MiMo（`model_router.py` 全角色 `provider: mimo`），Qwen-VL 仅是注释/常量文案残留。**只改人类可读文案，不改逻辑、不改常量值**。

**改动**：

1. `model_router.py:391` 注释：`# Qwen-VL 的图片 token 会在响应里返回...` → 改为 `# MiMo 视觉模型（mimo-v2.5）的图片 token 会在响应里返回...`
2. `video_quality/video_evaluator.py`：
   - 第 1 行 docstring `Evidence-grounded two-stage Qwen-VL video evaluation.` → `Evidence-grounded two-stage video evaluation.`
   - 其余注释含 `Qwen`/`Qwen-VL` 字样（约 L150/403/408/413/418/421/466/478/551 附近）→ 一律改为「评估模型」中性表述。
3. `asset_processing.py:518/574` 注释：`用 Qwen-VL 标注一个片段...` → `用视觉标注模型（mimo-v2.5）标注一个片段...`；`兼容 Qwen-VL 偶发包裹在 Markdown 代码块中的 JSON` → `兼容视觉模型偶发包裹在 Markdown 代码块中的 JSON`。

**明确不做**：
- ❌ 不改 `PROMPT_VERSION = "qwen-video-quality-v10"`（常量，改名无收益且有缓存/版本耦合风险，保留原名）。
- ❌ 不改任何函数名/变量名/导入。

**验收**：`grep -rn "Qwen\|qwen" model_router.py video_quality/video_evaluator.py asset_processing.py` → 仅剩 `PROMPT_VERSION` 常量名一处；全量测试绿（文案改动不触发逻辑变化）。

**回滚**：git 恢复文案。

---

## 指令 #16：依赖修正

**目标**：补齐真依赖、清掉已迁移的环境变量残留。

**改动**：

1. `requirements.txt`：追加 `python-dotenv`（代码实际使用：`app.py:14 from dotenv import load_dotenv` + 多个 scripts 用，但 requirements 缺失 → 新环境 `pip install -r` 后 app 起不来）。
2. `.env.example` 第 7 行：删除 `MINIMAX_API_KEY=your_minimax_token_plan_key`（已迁移 MiMo，此 key 无消费者）。

**验收**：
1. `grep python-dotenv requirements.txt` → 有。
2. `grep MINIMAX .env.example` → 无输出。
3. 全量测试绿。

**回滚**：git 恢复。

---

## 指令 #17：`optimized_generation` 死数据删除（#9 决策执行）

**决策依据**（批 2 文档 #9 + 体检报告）：`optimize_prompt()` 每次质检烧一次模型调用，产出 `optimized-generation.json` **永无回灌消费者**（本管线拼真实素材，无文生视频）；前端仅作「参考提示词」展示且已带「改提示词不会改变画面」警告。**决策：删除整条链，连同前端展示块。** 质量门禁本身（`decide_regeneration` 血缘护栏）独立于此链，不受影响。

**改动**（按依赖顺序）：

1. `video_quality/service.py`：
   - :173 `optimized = optimize_prompt(request, final_report)` → 删除（省一次模型调用）；
   - :198 `write_json(run_dir / "optimized-generation.json", optimized)` → 删除；
   - :219 manifest `artifacts` 中 `"optimized_generation": "optimized-generation.json",` → 删除；
   - :229 返回 dict 中 `"optimized_generation": optimized,` → 删除。
   - 若删除后 `optimized` 变量在 :224-231 之间无其他使用（已确认），一并清掉 `from .prompt_optimizer import optimize_prompt`（:10）。
2. `video_generation.py:1161`：`full_report["optimized_generation"] = result["optimized_generation"]` → 删除（若该行前后块因此变空，合并相邻逻辑）。
3. 前端 `static/video-project.html`：定位「参考提示词/optimized_generation」展示块（约 :464/:493 附近，含「改提示词不会改变画面」警告）→ 整体删除该块。**删除后确认无 JS 引用 `optimized_generation` 变量**。
4. 模块删除：`video_quality/prompt_optimizer.py` 整个文件删除（唯一调用方就是 service.py，删除后无引用）。
5. 测试同步：
   - `tests/test_video_quality_service.py:188-190` 直接调 `optimize_prompt` 的用例 → 删除该用例；
   - `tests/test_video_quality_api.py:45` schema 断言 `"optimized_generation": {"required": False}` → 删除该键（若 schema 定义里也有对应字段则一并删）。
6. 全局搜 `optimized_generation` / `optimize_prompt`：确认仅剩 `.qoder/`（AI 知识库，不动）与 docs 历史文档（不动）。

**验收**：
1. `grep -rn "optimized_generation\|optimize_prompt" --include="*.py" --include="*.html" .` → 除 `.qoder/` 和 docs 外**零命中**。
2. 全量测试绿（含删用例后的调整）。
3. 真实跑一次质检（或 dry-run）：`run_dir` 不再产出 `optimized-generation.json`，`manifest.json` 无该键。
4. 质检日志/调用计数：评估阶段模型调用次数减少（省掉 optimize 一次）。

**回滚**：git 恢复 + 恢复 `prompt_optimizer.py`。

---

## 明确不做（防漂移）

- ❌ 不做 kill 渲染进程改造（`#10` 分析：需加 pid 列 + 渲染生命周期管理，是功能改造非清理，**另立项**）。
- ❌ 不碰小红书线（`xhs*`、`adapters/xiaohongshu.py`、小红书第 0 批已提交文件）。
- ❌ 不碰公众号线脚本（`*_article*` 系）。
- ❌ 不改 `PROMPT_VERSION` 常量、不改函数名。
- ❌ 不删 `static/debug/` 目录本身（运行时重建依赖）。
- ❌ 不对 DB 做任何变更。

## 执行顺序与验收总口径

1. 顺序：#12 → #13 → #14 → #15 → #16 → #17，每步完成即提交，禁止一把梭。
2. 每步验收命令执行后，**全量回归** `pytest -q`，与基线 830 passed / 8 failed 对比：8 个存量失败必须逐条一致，不得新增。
3. 全部完成后**重启 app**，`/api/douyin/render` 404、`/api/publish/logs` 正常、质检链路可跑。
4. 同步 Obsidian 改进日志（三处）+ 更新本批状态到指令索引。
5. 提交信息规范：`批4 清债务：#12死路由 #13死文件 #14脚本归档 #15Qwen文案 #16依赖 #17optimized_generation删除`。

## 回滚总策略

每指令独立可回滚（git 恢复对应提交/文件）。#17 需连 `prompt_optimizer.py` 一起恢复。
