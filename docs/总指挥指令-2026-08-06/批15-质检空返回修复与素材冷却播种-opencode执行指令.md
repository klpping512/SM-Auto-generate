# 批15：质检空返回修复 + 素材冷却历史播种 — opencode 执行指令

> 总指挥交付日期：2026-08-07（周五）｜执行方：opencode（非 Cursor）
> 关联批：批13（块D使用计数+冷却）、批14（Qwen清理+TTS单轨）
> 本次整片目检后用户反馈两问题，根因已由总指挥定位，本指令为落地修复。

---

## 一、背景与根因（先读，别跳过）

重启（PID 3844，10:00:08）后整片端到端目检，用户看到两个问题：

1. **「待确认问题 视频质检服务暂不可用，请人工检查预览」持续出现，不能推发布器。**
2. **用的素材跟前两次测试一模一样，逐镜无区别。**

### 根因 1：质检空返回是「系统性」，块E1 只防瞬态，救不回

状态机表 `video_generation_jobs` 里最新 job `2e5f2946` 实测：
- `status=needs_review` / `stage=preview_quality_check`
- `quality_report.gate.issues = ["视频质检服务暂不可用，请人工检查预览"]`
- `quality_report.video_evaluation.error = "多模态模型返回了空内容"`
- app.log 10:13:48 / 10:14:49 / 10:15:53 三次 MiMo POST 均 **HTTP 200 但正文为空**

根因链（总指挥已核实代码）：

`model_router.py` 的 `_provider_request_options()`（:110-123）注释明写：
> MiMo V2.5 defaults thinking ON; MiniMax-style keys are ignored and the
> reasoning budget can exhaust max_tokens, leaving message.content empty.

即：**MiMo 默认开启 thinking，推理预算会耗尽 max_tokens，导致 `message.content` 为空。** `call_multimodal_json`（:465）会调用 `_provider_request_options(route)` 把 `enable_thinking` 翻译成 `thinking: {type: disabled}` 随请求发出。

对照角色配置（model_router.py ROUTES）：
- `planner`（:29）→ `request_options: {"reasoning_split": True, "enable_thinking": False}` ✅
- `critic`（:60）→ 同 ✅
- **`video_evaluator`（:47-53）→ 无 `request_options`，thinking 默认开 → 质检请求 content 恒空** ❌

块E1（model_router.py:481-484）只把「空返回」当**瞬态**重试（与 429/5xx 同级，最多 3 次）。但这是**每一次都空**的系统性失败，重试无意义。

**结论：修复 = 给 `video_evaluator` 角色补 `request_options: {"enable_thinking": False}`，与 critic/planner 对齐。** 需重启服务生效。

### 根因 2：块D 冷却「首次干净渲染」无历史 → 排序回退，素材逐镜一致

三次 job（`7faf811f`/`86909b47`/`2e5f2946`）的 `clip_refs` 全同：`[151, 69, 120, 63, 133, 9, 61, 121]`。

块D 排序（hotspot_video_planner.py `_owned_candidates.rank` :315-333）：
```python
return (
    branded,
    _owned_node_tag_relevance(item, brief),
    float(item.get("quality_score") or 0),
    -min(usage, 5),          # usage = asset_usage_count
    -fresh_penalty,          # 基于 asset_last_used_at
    -int(item.get("id") or 0),
)
```
`usage_count` 列是批13 新增的，**重启后从 0 开始**。本次是重启后首次生产，全部 875 个资产 `usage_count=0` → `-min(0,5)=0`、`fresh_penalty=0` → 排序只剩 `branded / tag_relevance / quality_score / -int(id)`，**与旧版逐镜一致**。

历史数据证明（video_render_jobs 90 个成功 job 统计）：asset 61/63 各用过 **74 次**、179 用 70 次、54 用 54 次、306 用 53 次——**这些历史使用完全没有回灌进 `assets.usage_count`**，所以块D 一上线等于「失忆」。

**结论：修复 = 播种历史 usage_count**：从 `video_render_jobs.quality_report.clip_refs` 统计每个资产历史被用次数，回灌 `assets.usage_count`（幂等覆盖，不是累加），`last_used_at` 取该资产最近一次被用时间。块D 从下一单起就有真实冷却依据，61/63 等霸榜素材会被降权。

> ⚠️ 验收陷阱：**渲染器技术表 `video_render_jobs` 的 status=succeeded ≠ 语义质检通过。** 用户/验收只看 `video_generation_jobs`（状态机表）。查 job 状态务必分清两张表：`video_generation_jobs`（用户 UI、质量门禁）vs `video_render_jobs`（FFmpeg 技术渲染），两者 id 同 UUID。

---

## 二、修复范围（两块）

### 块 F：质检空返回修复（enable_thinking）

**文件：`model_router.py`**

`ROUTES["video_evaluator"]`（约 :47-53）增加 `request_options`，与 critic 对齐：

```python
"video_evaluator": {
    "role": "video_evaluator", "provider": "mimo",
    "base_url": MIMO_BASE_URL,
    "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
    "capabilities": ["text", "vision"], "timeout": 120, "max_tokens": 1800,
    "cost_profile": "medium", "enabled": True,
    "request_options": {"reasoning_split": True, "enable_thinking": False},
},
```

要点：
- `enable_thinking: False` 经 `_provider_request_options`（mimo 分支 :114-122）翻译成 `thinking: {type: "disabled"}` 随请求发送，MiMo 不再消耗推理预算，`message.content` 恢复非空。
- `reasoning_split: True` 与 critic 一致，保险起见避免 `<think>` 混入 content 破坏 JSON 解析（`_visible_text_content` 会剥离 think 块，双保险）。
- **不要动 `max_tokens=1800`**——质检需要足够输出预算。

### 块 G：素材冷却历史播种（usage_count 回灌）

**新文件：`scripts/seed_asset_usage.py`**（幂等可重跑）

逻辑：
1. 读 `video_render_jobs` 全部 `quality_report`（status='succeeded' 且 clip_refs 非空）。
2. 对每个 job 的 `clip_refs`，**按 asset_id 去重**（一个 asset 同片只计一次），`counter[asset_id] += 1`，并记录该 asset 最近一次 `updated_at`（job 的完成时间）。
3. 汇总后：对每个有历史的 asset，**覆盖**写入 `assets.usage_count = counter[asset_id]`、`assets.last_used_at = 最近时间`。
   - 幂等：每次跑都是「重新统计后覆盖」，不累加。可重复执行验证。
   - 未在历史出现的资产保持不变（0）。
4. 打印摘要：处理了多少 job、多少资产、Top 使用榜（61/63 应约 74）。

**接入点（重要）**：块D 排序读取的是 `asset_usage_count` / `asset_last_used_at` 字段（来自 `list_asset_segments` SELECT 暴露，database.py:4423 附近）。播种脚本直接写 `assets.usage_count` / `assets.last_used_at` 即可，无需改排序代码。

**执行方式**：一次性脚本，`python scripts/seed_asset_usage.py`，可随时重跑。不接定时。

---

## 三、验收标准（逐条，全部通过才算完成）

### 验收 1：代码层（块 F）
- `model_router.py` 中 `video_evaluator` 角色含 `request_options` 且含 `enable_thinking: False`。
- 三个角色（planner/critic/video_evaluator）均有 `enable_thinking: False`。
- `pytest` 全量通过（沿用现有基线；新增/修改测试覆盖「video_evaluator 请求体含 thinking disabled」——可对 `_provider_request_options` 做单测断言）。

### 验收 2：代码+数据层（块 G）
- `scripts/seed_asset_usage.py` 存在，幂等（连跑两次 usage_count 不变）。
- 跑完后：asset 61/63 的 `usage_count ≈ 74`、179≈70、54≈54、306≈53；`last_used_at` 非空。
- 全量 pytest 不因新脚本破坏。

### 验收 3：运行态（块 F 真机）
1. **重启服务**，确认新代码加载（进程 PID 变化、health 200）。
2. 重新驱动一次完整生产渲染（chat 一键生产 → 生成视频）。
3. 目标状态：`video_generation_jobs` 该 job `status=succeeded`、`quality_report.video_evaluation.evaluation_status="completed"`、`overall_score≥80`、`gate.issues=[]`。
4. UI：不再出现「待确认问题 视频质检服务暂不可用」；可推发布器。

### 验收 4：运行态（块 G 素材变化）
1. 播种后连渲两次（或对比播种前）。
2. 两次 clip_refs 应明显不同（61/63 等霸榜素材被冷却降权，出现新素材）。
3. 检查 `assets.usage_count` 随渲染成功 bump（database.py bump_asset_usage 路径）。

### 验收 5：回归
- 旧项目重渲染不炸（enable_thinking 只影响请求体 thinking 字段，不动 TTS/旁白链路）。
- 发布链路：succeeded + publication.publish_allowed=true 后可进入发布器（editor.html publishDouyinVideo）。

---

## 四、非阻塞残留（本次不做，但记录）

- `video_evaluator.py:19` `PROMPT_VERSION="qwen-video-quality-v10"`（改它会动 model_router 审计的 prompt_version，建议批16 顺手改 mimo 命名）。
- `static/editor.html:1137` toast 仍提「配置百炼 API Key」（改 MiMo）。
- `ai_engine.py:1` 模块 docstring 说 legacy DashScope helpers retained（已删，过时）。

---

## 五、提交

- 一次提交或拆两块均可，message 写清楚：`feat(quality): video_evaluator 关闭 thinking + 素材使用历史播种`。
- **验收要加「git show 提交文件清单交叉核对」**：确认提交只含批15 文件，不夹带批13/批14 或无关改动。
