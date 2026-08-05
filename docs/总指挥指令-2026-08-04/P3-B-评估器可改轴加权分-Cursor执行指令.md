# Cursor 执行指令 · P3-B 评估器可信度：可改轴加权分（并存，不替换 overall_score）

> 总指挥背景（先读，涉及两条决定范围的硬事实）：
> 目标——让「该不该重生成」的判定偏向**本管线真正改得动的轴**，而不是盲信模型给的单个 `overall_score`。
>
> **硬事实一：本管线是 FFmpeg 拼真实素材、无 AI 文生视频。** 因此 10 轴里「画面本身」那几轴
> （visual_quality / character_consistency / product_consistency / temporal_consistency /
> motion_quality / camera_quality）**基本改不动**——素材是现成的，重跑也换不出更好的画面像素。
> 真正**可改的轴**只有四个：`subtitle_audio_quality`（字幕/音画，能重烧）、`prompt_alignment`
> （脚本与画面对齐，能改脚本/重选素材）、`storytelling`（叙事，能改口播/顺序）、`platform_suitability`
> （平台适配，能改时长/字幕/画幅）。**加权分只对这四轴打分**——若一支片子失分全落在改不动的画面轴，
> 重生成是浪费；失分落在可改轴，才值得让人重生成。
>
> **硬事实二：`overall_score` 是全系统唯一门禁变量，10 轴子分今天没人读（除 merge 取 min）。** 已核实
> 5 处门禁全部只读 `overall_score`/`passed`：`quality_failed`(schemas.py:76)、`decide_regeneration`
> 的三道 P3-A 护栏(regeneration_controller.py:40/42/44)、`route_video_evaluation_quality`
> (video_generation.py:306/324/334)。逐轴 `scores` 仅在 `service.py:62-64` 做 global/focused 取 min，
> 之后**再无任何 gate 读它**。∴ 子分数据结构可靠（`QualityScores` 是 StrictModel、10 轴 required、
> 缺轴直接 ValidationError 触发重试，拿到 report 时保证 10 轴齐全且 0-100），**只是从没被信任**。
>
> **∴ 本指令 = 新增并存的 `weighted_actionable_score`（可改轴加权分），只用来 gate「重生成 vs 人工」这一路由，
> 绝不替换 `overall_score`、绝不改 pass/fail 语义。** 替换方案已否决（见下"为什么不替换"）。
>
> ⚠️ 行号可能漂移，落地前用函数名/字符串锚点二次定位，以实际代码为准。

---

## 为什么"并存"而非"替换"（这是本指令的核心设计约束，不得违背）

`overall_score` 牵动 5 处门禁 + merge + 归一化兜底（video_evaluator.py:642 把无证据误报的分抬到 ≥80）。
若把 `quality_failed`(schemas.py:81) 的 `overall_score < threshold` 换成加权分，会**同时**改动
「过没过质检(pass/fail)」和「该不该重生成(regenerate)」两套语义，且 `tests/test_video_quality_schemas.py`
的 6 条 `quality_failed` 断言（overall_score=92/79/86 等）必红，`route_video_evaluation_quality`
与 `optimize_prompt` 的一致性一并受牵连。

**并存则冲击面收敛到一处**：pass/fail 完全不变（`quality_failed` / `route_*` 一字不动），
只有「已判失败之后，走自动/人工重生成 vs 直接人工复核」这一步更聪明。系统本就有先例——
`route_video_evaluation_quality` 里的 `low_risk_categories = {camera_quality, temporal_consistency,
subtitle_audio_quality}` 白名单（video_generation.py:312）已经在按轴放行，加权分是它的自然延伸。

---

## 改动 ①：新建权重表 + 加权分计算（纯函数，可单测）

在 `video_quality/schemas.py`（`QualityScores` 定义约 L24-34、`quality_failed` 约 L76 附近）新增一个
**独立纯函数**和一张**显式权重表**（全仓今天无任何 `weighted` 符号，是全新概念）：

```python
# 可改轴权重表：只有这四轴在本管线（FFmpeg 拼真实素材）里重生成能真正改善。
# 画面本身的六轴(visual/character/product/temporal/motion/camera)权重=0——它们失分靠重跑救不了。
ACTIONABLE_AXIS_WEIGHTS = {
    "subtitle_audio_quality": 0.35,  # 字幕/音画，能重烧，最可控
    "prompt_alignment":       0.30,  # 脚本↔画面对齐，能改脚本/重选素材
    "storytelling":           0.20,  # 叙事，能改口播/顺序
    "platform_suitability":   0.15,  # 平台适配(时长/画幅/字幕)
}

def weighted_actionable_score(scores: QualityScores) -> float:
    """只对'重生成改得动'的四轴做加权，产出 0-100 的辅助分。
    纯函数、不读 overall_score、不改任何门禁；仅供 decide_regeneration 判'重生成是否值得'。"""
    total = 0.0
    for axis, w in ACTIONABLE_AXIS_WEIGHTS.items():
        total += float(getattr(scores, axis)) * w
    return round(total, 2)  # 权重和=1.0，天然落在 0-100
```

**红线**：
- 权重表数值可由总指挥后续调，但**四轴集合固定为这四个**（对应硬事实一的可改性），不得把画面轴掺进来。
- 该函数**只读 `scores` 逐轴分**，**绝不读/改 `overall_score`**。
- 权重和必须=1.0（便于加权分与 overall_score 同量纲对比）；若调权重，保持和为 1.0。

---

## 改动 ②：在 `decide_regeneration` 用加权分做"边缘决策"（唯一 gate 接入点）

文件 `video_quality/regeneration_controller.py`，函数 `decide_regeneration`（约 L17）。
**P3-A 的既有护栏优先级绝对不动**——加权分判定必须放在所有 P3-A 护栏**之后**、
在最终决定 `regenerate` vs `manual_review` 的那一步接入。

现状（P3-A 后）末尾逻辑大致是：护栏(quality_passed / maximum_attempts_reached / score_declined /
no_meaningful_improvement / automatic_regeneration_disabled)依次短路，**全部未命中**才走到
`return {"action":"regenerate", ...}`（或 auto 关闭时的 `automatic_regeneration_disabled`）。

**新增一道"值不值得重生成"判定，插在'护栏全过、即将判定动作'的位置**：

```python
# 在所有 P3-A 护栏(quality_passed / maximum_attempts_reached / score_declined /
# no_meaningful_improvement)之后、决定最终 action 之前：
actionable = weighted_actionable_score(report.scores)
base["weighted_actionable_score"] = actionable   # 无论走哪支都带出，供前端展示

# 失分是否落在'改得动'的轴上？加权分低=可改轴烂=重生成有价值；
# 加权分高(可改轴其实不差、是画面轴拖垮 overall)=重生成救不了→转人工复核。
ACTIONABLE_FLOOR = 70   # 可改轴加权分≥此值，视为'重跑改善空间有限'
if actionable >= ACTIONABLE_FLOOR:
    return {"action": "manual_review",
            "reason": "actionable_axes_healthy",   # 新 reason：可改轴其实达标，别浪费重跑
            **base}
# 否则维持 P3-A 原有的 auto/manual 判定（auto_enabled 决定 regenerate vs manual）
```

**语义**：
- `overall_score < 80` 触发失败（不变），进 `decide_regeneration`；
- P3-A 护栏（次数/下滑/提升不足）先跑（不变）；
- **新增**：护栏全过后，若「可改轴加权分」其实≥70（说明字幕/对齐/叙事/平台这些能改的轴并不差，
  overall 是被改不动的画面轴拖垮的）→ 判 `manual_review / actionable_axes_healthy`，
  **不浪费一次自动重生成**；否则维持 P3-A 原判定。
- `auto_enabled` 仍默认 False（不动），本判定同样服务于"挡住无意义的人工重跑"。

**红线**：
- 不改 `quality_failed`（schemas.py）、不改 `route_video_evaluation_quality`（video_generation.py）——
  pass/fail 与状态机路由零改动。
- 不改阈值 80、`max_attempts=2`、`minimum_improvement=3`、`auto_regenerate=False`。
- `ACTIONABLE_FLOOR` 作为具名常量，别硬编码散落；默认 70，可调。
- P3-A 五个既有 reason 的触发条件与优先级一字不动，新 reason 只在它们全不命中后出现。

---

## 改动 ③：加权分随 report 带出到前端（复用 P3-A 诊断面板，纯透传）

P3-A 已在审片页渲染 `regeneration_decision`。本次 `decide_regeneration` 的返回里已含
`weighted_actionable_score` 与可能的 `actionable_axes_healthy` reason（改动②的 `base` 注入）。
前端 P3-A 面板**只需多显示一行**：

- 「可改轴加权分：X / 100」（并列在 overall_score 旁，注明"衡量重生成能改善多少"）；
- 若 reason==`actionable_axes_healthy`，提示「可改轴已达标，本片失分主要在画面素材本身，
  重生成难改善——建议人工复核或换素材/话题」，并**禁用「重生成」按钮**（与 P3-A 达上限禁用一致的处理）。

**红线**：件③纯展示/透传，不新增后端取数通道。

---

## 硬边界（不得越界）

- **不替换 `overall_score`**：`quality_failed`、`route_video_evaluation_quality` 一字不动，pass/fail 语义零变化。
- **不改 P3-A 护栏**：五个既有 reason 的条件/优先级/默认值(80/2/3)全部保持。
- **可改轴固定四个**：subtitle_audio_quality / prompt_alignment / storytelling / platform_suitability；画面六轴权重=0，不得掺入。
- **加权分只 gate「重生成 vs 人工」**，不参与「过没过质检」。
- 不动 `_merge_reports` 取 min 逻辑、不动 evaluator 打分、不动归一化兜底。

---

## 测试（必须证明"并存不破坏存量 + 加权分真生效"）

新增 `tests/test_weighted_actionable_score.py` 及扩展 `test_regeneration_guardrails.py`，至少覆盖：

1. **加权分算对**：构造 scores，subtitle_audio=80/prompt_alignment=70/storytelling=60/platform=50、
   画面六轴全 100 → 断言 `weighted_actionable_score` == 0.35*80+0.30*70+0.20*60+0.15*50（=68.0），
   **与画面轴无关**（把画面轴改成 0，加权分不变）。
2. **可改轴健康→不浪费重跑**：`overall_score=76`（<80 触发失败）、无 history、可改轴加权分≥70 →
   断言 `decide_regeneration` 返回 `manual_review / actionable_axes_healthy`（新判定生效）。
3. **可改轴烂→维持 P3-A 原判定**：`overall_score=70`、可改轴加权分<70、auto_enabled=True、无 history →
   断言仍返回 `action=regenerate`（新判定不误伤"该重跑"的情形）。
4. **P3-A 护栏优先级不破**：`test_regeneration_guardrails.py` 现有四态断言
   （no_meaningful_improvement / maximum_attempts_reached / score_declined / quality_passed）
   **全部维持原 reason**——加权分判定不得抢在它们之前。⚠️ 该文件 `_report` helper（约 L14-21）
   与 `test_video_quality_service.py` 的 `_report` helper（约 L22-34）只填了部分 scores 轴，
   **若加权分依赖的四轴未被 helper 填全会 ValidationError**——需补齐 helper 的四个可改轴字段（用中性默认如 80）。
5. **pass/fail 零变化**：`test_video_quality_schemas.py` 的 6 条 `quality_failed` 断言、
   `test_video_generation_state.py` 的 `route_video_evaluation_quality` 断言**全部照旧通过**
   （回归证明并存未动门禁）。
6. 全量 `pytest` 绿（存量 8 个无关失败沿用基线，如实注明）。

---

## 回报格式（给总指挥验收）

- 改动 diff / commit hash（强调 `quality_failed`/`route_*` 一字未动、pass/fail 语义零变化）。
- 权重表数值 + 加权分四轴集合确认（画面六轴权重=0 的证据）。
- `decide_regeneration` 新判定的插入位置截图/片段，证明在 P3-A 五护栏之后。
- 测试证据：加权分与画面轴无关的断言输出 + `actionable_axes_healthy` 命中 + P3-A 四态未变 + 门禁回归全绿。
- 一句话确认：**「该不该重生成」是否已从'盲信单个 overall_score'变为'看重生成改得动的四轴'，且 pass/fail 未受任何影响。**
