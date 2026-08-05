# Cursor 执行指令 · P3-A 人在环辅助重生成 v1（质量闭环·把断掉的最后一跳接上）

> 总指挥背景（先读，涉及两条会限定范围的硬事实）：
> 目标——让审片人看得见质检诊断、并能"按建议重生成"，且重跑结果**可度量、有护栏**。**不开盲目自动重生成**。
>
> **硬事实一：系统是"修订驱动+不可变"的。** 生成 = job 从不可变 `revision.payload` 读 scenes 跑状态机
> （`video_generation.py:588-594` planning 阶段读 payload）。`run_claimed_job`（`video_generation.py:422`）**无法注入
> 改后的脚本，也没有"局部重渲某几个镜头"的入口**（渲染是整支 `render_version`）。已有的 `resume` 端点
> （`routes/video_generation_routes.py:347`）就是标准"改脚本重生成"：收 payload→建新修订→建新 job 整支重跑。
> **∴ v1 只做整支重生成，复用 resume；「局部重渲」明确不做（无基建，是另一个大工程）。**
>
> **硬事实二：optimizer 输出对本管线部分空转。** `optimize_prompt`（`prompt_optimizer.py:7-29`）吐
> `revised_prompt`/`negative_prompt` 是**文生视频**语言；但本管线是 **FFmpeg 拼真实素材、无 AI 文生视频**，
> 这些 prompt 字段喂进去不改变画面。`segments_to_regenerate` 是 `list[Any]`、全仓无消费方。真正能改重跑
> 结果的是**重选素材 + 改写脚本/口播**——正是 `resume` 已在做的。**∴ v1 不自动套用 prompt/negative_prompt/
> segments_to_regenerate（会误导），而是把 issues[].suggested_fix 作为"给人看的修改建议"露出，由人编辑 payload。**
>
> **好消息（免费）**：`GET /api/video-generation/jobs/{job_id}`（`routes/video_generation_routes.py:195`）返回的
> `quality_report` 已含 `optimized_generation` + `regeneration_decision`（`video_generation.py:1133-1137,1170-1174`
> 合并写入）。这份"死数据"现在前端没渲染、没人用——救活成本极低。
>
> ⚠️ 行号可能漂移，落地前用函数名/装饰器/字符串锚点二次定位，以实际代码为准。前端组件位置需 Cursor 自行定位
> （审片视图，参考 `static/common.js` job 渲染 + 任何 needs_review 展示处）。

---

## v1 范围（三件，均低风险，均复用现有机制）

### 件 1：诊断露出 —— 把死数据变活（前端为主）

审片人在 `needs_review` 状态下应能一眼看到**可行动的质检诊断**。数据已在 `quality_report` 里，无需改后端取数，只需前端渲染以下子集：

- `quality_report.video_evaluation.overall_score` + `passed`；
- 逐轴分数（`scores` 10 轴，已是 global/focused 两轮**逐轴取 min** 的结果，见 `video_quality/service.py:44-75`）——高亮低于阈值的轴；
- `issues[]` 列表：每条的 `description` / `severity`（high 标红）/ `suggested_fix`；
- `regeneration_decision.action`（none/manual_review/regenerate）+ 其 reason（如 `automatic_regeneration_disabled`）；
- 若 `optimized_generation.required == true`，展示 `optimized_generation.revised_prompt` 等作为**参考建议**，并**明确标注**「以下为文生视频式建议，本管线以重选素材/改写口播为准」（避免审片人误以为改 prompt 能改画面）。

**硬边界**：件 1 纯展示，不改质检/生成逻辑。

### 件 2：「按质检建议重生成」动作 —— 复用 resume（后端）

新增一个审片人动作：在 needs_review 且质检失败时，一键发起重生成。**复用 `resume` 机制**（`routes/video_generation_routes.py:347`：`create_video_project_revision` → 取消旧 job → `create_or_get_video_generation_job`），而非新造生成通道。

两种实现，**取 A（更省、更诚实）**：
- **A（推荐）**：前端「重生成」按钮打开的就是现有 resume 的脚本编辑器，并把件 1 的 `issues[].suggested_fix` 作为**内联批注/预填提示**显示在对应 scene 旁，人工据此改 `payload` 后提交 resume。后端**几乎零改动**——只需 resume 接口能接收并透传这些（若已支持编辑 payload 即可）。
- B（不做）：服务端自动把 optimizer 建议翻译进 payload。放弃理由见硬事实二——prompt 字段空转，自动套用会产出无效重跑。

**件 2 的后端实质改动集中在件 3（history 接线），件 2 本身主要是前端把 resume 入口按"带质检建议"重新组织。**

### 件 3：接通 history —— 让重跑可度量、有护栏（后端核心，今天完全空转）

**现状**：`video_generation.py:1120-1129` 调 `run_quality_mvp` 时**不传 history**、且 `auto_regenerate=False` 写死
（`:1117`）。于是 `decide_regeneration`（`regeneration_controller.py:11`）的三道护栏——`max_attempts=2` /
`score_declined`（本次<上次）/ `no_meaningful_improvement`（提升<3）——**全部空转**，重跑与初次无差别、无人知道是否改善。

**改动**：让重生成 job 携带"前序质检历史"，使护栏真正生效。

1. **记血缘**：resume/重生成新建 job 时，在 job 记录（或其 revision payload 的 meta 字段）写入 `prior_job_id`
   与 `regen_attempt`（第几次重生成，初次=0）。请 Cursor 确认 `create_or_get_video_generation_job` /
   job 表可承载此字段（可能需加列或塞进 payload meta），选侵入最小的方案。
2. **回灌 history**：在预览质检阶段（`video_generation.py:1120` 一带，调 `run_quality_mvp` 处），若本 job 有
   `prior_job_id`，则沿链取出前序 job 的 `quality_report.video_evaluation`（可能多代，按 attempt 顺序），
   组装成 `history` 列表传入 `run_quality_mvp(..., request=VideoQualityInput(..., history=...))` /
   或直接传给内部 `decide_regeneration`（对齐 `service.py:174` 的 `history=history or []` 形参）。
3. **护栏生效 + 露出**：这样 `decide_regeneration` 能真正判 `maximum_attempts_reached` / `score_declined` /
   `no_meaningful_improvement`，并把结果写进 `regeneration_decision`。件 1 的前端据此展示：
   - 「本次 X 分 vs 上次 Y 分（Δ）」；
   - 若命中 `maximum_attempts_reached` 或 `no_meaningful_improvement`，**禁用「重生成」按钮并给出原因**
     （防止审片人无限空转重跑——这就是人在环的护栏）。
4. **`auto_regenerate` 保持 False 不动**：v1 是人触发，护栏用来"挡住无意义的人工重跑"，不是开自动循环。

---

## 硬边界（不得越界）

- **不开自动重生成**：`video_generation.py:1117` 的 `auto_regenerate=False` 保持不变。
- **不做局部重渲**：不碰 `render_version` 整支渲染结构，不实现 `segments_to_regenerate` 消费。
- **不自动套用 prompt/negative_prompt**：件 2 走人工编辑 payload（resume），不做服务端自动翻译。
- **不改评估器打分逻辑**：`overall_score` 仍由模型给（评估器可信度是 P3-B 另开，本次不碰）。
- **不改阈值**（80）、不改 `max_attempts`(2)/`minimum_improvement`(3) 默认值。

---

## 测试（闭环护栏是核心，必须证明）

新增/扩展测试，至少覆盖：

1. **history 回灌**：构造 job 链（prior_job_id 指向一份 overall_score=70 的前序报告），本次报告 72（提升<3）→
   断言 `decide_regeneration` 返回 `manual_review / no_meaningful_improvement`（护栏生效）。
2. **达上限**：`regen_attempt` 累计到 `max_attempts=2` → 断言 `maximum_attempts_reached`，前端「重生成」按钮态=禁用。
3. **分数下滑**：本次 65 < 上次 70 → `score_declined`。
4. **正常可重生成**：前序 70、本次 78（提升≥3、未达上限、无 high issue）→ decision `action` 不再是空转的
   `automatic_regeneration_disabled`，而是能反映"可继续"（注意 auto_enabled 仍 False，此处验证的是护栏判定链通了，
   动作展示给人，不自动执行）。
5. **血缘持久化**：resume 触发的新 job 能读到 `prior_job_id` / `regen_attempt`，且能沿链取回前序 quality_report。
6. **诊断露出**：`GET /jobs/{job_id}` 返回体含可被前端消费的 `quality_report.video_evaluation`（score/issues/
   suggested_fix）与 `regeneration_decision`（回归断言，确认没在重构中丢字段）。
7. 全量 `pytest` 绿（存量 8 个无关失败沿用基线，如实注明）。

---

## 回报格式（给总指挥验收）

- 三件 diff / commit hash（强调 auto_regenerate 未动、未做局部重渲）。
- history 回灌链路说明：血缘字段落在哪（job 列 / payload meta）、预览质检处如何取回前序报告。
- 护栏四态测试证据（no_meaningful_improvement / maximum_attempts_reached / score_declined / 可重生成）。
- 前端截图或结构说明：诊断露出 + 「重生成」按钮在达上限时的禁用态。
- 一句话确认：**人工重跑是否已"可度量（看得到 Δ 分）+ 有护栏（挡得住无意义重跑）"**。
