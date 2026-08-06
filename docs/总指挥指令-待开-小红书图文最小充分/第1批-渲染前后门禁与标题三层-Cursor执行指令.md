# 小红书图文门禁：渲染前结构/合规 + 渲染后完整性 + 标题三层 —— Cursor 执行指令（第 1 批）

> 日期：2026-08-06
> 状态：**已执行**（2026-08-06 执行完毕，待总指挥验收）
> 设计真源：《小红书图文种草链路-对抗优化版》击倒 3/4、5.2 ②③⑤、第六节 第 1 批 + `README.md` 第 1 批范围。
> 拍板：总指挥对抗审阅确认——出图链"能生成 + 能渲染"，但坏结构/绝对化用语/无证据风险句**直接进渲染与队列**，全靠渲染层静默截断与审批人工兜底。本批上**三个便宜门禁**：渲染前结构+广告法（硬）+truth_guard 风险句（软），渲染后文件完整性；并把封面钩子/卡面 headline/笔记标题**三层标题规则**写入 prompt 与常量。
> 目标：坏结构/违规用语**进不了渲染**，渲染坏**看得见**，标题规则**不再各说各话**。不引入视频质量子系统、不做 OCR/版式评估、不宣称"事实已校验"。

## 一、背景与 Why

现网链路：`ai_engine.generate_content`（xhs 分支）→ `render_carousel`（app.py `/api/generate` 内联）→ 审批队列 → `publish_error`（发布前唯一事实门禁）→ RPA。

已核实的事实：

- `xhs_cards.normalize_pages`（L50-84）对坏输入**静默兜底**：headline 截断到 24、points 截断到 48、页数不足自动补到 5、超 7 截断——坏结构不报错，只悄悄降级。
- `ai_engine.generate_content` xhs 分支（L174-181 prompt）只写了"headline 不超过 18 字"，**没有**封面钩子 3-10、笔记标题分层，模型不知道运营的封面冲击力要求。
- `truth_guard.publish_error`（truth_guard.py L47-52）只在发布前按 `source_refs` 硬拦无证据风险句；**生成/渲染阶段没有任何信号**告诉运营"这条稿子有风险句待补证据"。
- 无广告法绝对化用语黑名单（全仓只有视频 SOP 的提示词软约束，无代码级检查）。

本批真实缺口：① 坏结构/违规用语无生成期门禁；② 渲染产物无完整性自检；③ 三层标题规则未写入 prompt/常量。

## 二、铁律（不做的事）

1. **truth_guard uncovered = 软警告，绝不 hard-error。** 风险句允许带证据或条件式表述；发布拦截仍由 `truth_guard.publish_error` 负责（审批时补 `source_refs`）。**语义红线**：注释/文档禁止写「过 truth_guard = 物流事实已校验」。
2. **门禁是纯函数**，`xhs_quality_gate.py` 内禁止调用 LLM/网络；有界重试只在 `ai_engine` 生成循环里做。
3. **只作用于新建生成**：ai_engine 的 xhs 生成分支 + `/api/generate` + `/api/xhs/render`。**不**回改历史队列存量条目、不 gate `pages_from_content` 产出的 legacy 文案（发布时有 `publish_error` 兜底）。
4. **fallback 内容不跑门禁、不重试**（已是降级模板，直接产出）。
5. **不 OCR / 不对比度 / 版式 AI 评估**；不移植 `weighted_actionable_score` / 视频 P3 评估器；不做封面 A/B。
6. **不碰配图**（`asset_taxonomy` 分类配图 = 第 2 批）；不碰 `TEMPLATE_VERSION` 可观测化（第 2 批）。
7. 不新增诊断双表；门禁结果只落到 `GeneratedContent.quality_warnings` + 日志。

## 三、改动清单

### 改动 A：新模块 `xhs_quality_gate.py` — 纯函数门禁 + 常量

**常量（三层标题 + 结构，唯一真源）：**

```python
XHS_PAGES_MIN = 5
XHS_PAGES_MAX = 7
XHS_TITLE_MAX = 20            # 笔记标题（平台限制，可含主词）——软检查
XHS_COVER_HOOK_MIN = 3        # 封面钩子下限（运营可见封面冲击力）——硬检查
XHS_COVER_HOOK_MAX = 10       # 封面钩子上限——硬检查
XHS_HEADLINE_MAX = 18         # 卡面 headline（渲染约束）——硬检查
XHS_POINTS_MAX = 48           # 与 xhs_cards._clean 截断上限对齐，防空转重试
XHS_GATE_MAX_CALLS = 2        # 有界重试次数
```

**广告法黑名单 `ADLAW_TERMS`（精确词，禁用裸「最」）：**

```python
ADLAW_TERMS = [
    "国家级", "世界级", "全球第一", "全国第一", "全网第一", "销量第一", "行业第一",
    "世界领先", "全球领先", "行业领先", "顶级", "顶尖", "极致", "首选", "唯一",
    "100%", "百分之百", "绝对", "保证", "根治", "零风险", "包赚", "最低价", "永久免费",
]
```

> 设计表写「最」「第一」「100%」等，但裸「最」会误伤「最近/最优/最早/第一时间」这类正常物流文案，故用上表精确词 + 首/尾边界。执行时可增删，但**不得**退回裸「最」子串匹配。

**`GateResult` dataclass：**

```python
@dataclass
class GateResult:
    errors: list[str]      # 硬失败 → 触发有界重试；重试耗尽后降级为 warning 打回审批
    warnings: list[str]    # 软警告 → 落 quality_warnings
```

**`check_before_render(title: str, body: str, pages: list[dict]) -> GateResult`：**

硬检查（errors）：
- 页数：`XHS_PAGES_MIN <= len(pages) <= XHS_PAGES_MAX`。
- 首页：`pages[0].get("type") == "cover"`。
- 封面钩子：`XHS_COVER_HOOK_MIN <= len(pages[0]["headline"]) <= XHS_COVER_HOOK_MAX`。
- 卡面 headline：每页 `headline` 非空且 `len(headline) <= XHS_HEADLINE_MAX`（封面页只查上限，下限由钩子规则管）。
- points：content 页 `1 <= len(points) <= 4`，每条 `1 <= len <= XHS_POINTS_MAX`。

软检查（warnings）：
- 笔记标题长度：`len(title) > XHS_TITLE_MAX` → warning（不阻断）。
- **广告法命中 → errors**：扫描 `title + body + 拼接 pages 文本`（headline/subheadline/points），命中 `ADLAW_TERMS` 任一 → error，文案注明命中词。
- **truth_guard → warnings**：拼接 `body_text = f"{title}。{body} " + " ".join(headline + points...)`，调 `truth_guard.evaluate(title, body_text, None)`；`uncovered` 每条 → warning「风险表述，发布前须补证据或改条件式」。**这不是 error**（见铁律 1）。

**`check_rendered(image_pages: list[dict], attachments: list[dict], static_dir: Path) -> list[str]`：**

返回 error 列表，全通过返回 `[]`：
- `len(attachments) == len(image_pages)`。
- 每个 attachment 的 `path` 相对 `static_dir` 存在。
- PNG 可解码：`PIL.Image.open(f)` + `verify()`（或 open 后读 size）。
- 尺寸恰为 `1242 × 1660`（对齐 `xhs_cards.WIDTH/HEIGHT`，常量从 `xhs_cards` 导入而非硬编码）。

### 改动 B：`ai_engine.py` — 三层标题入 prompt + 有界重试

1. **prompt 强化**（xhs `asset_instruction`，L174-181 附近）：在原"headline 不超过 18 字"基础上补三层，**引用 `xhs_quality_gate` 常量**（避免注释与代码两套数字）：
   - 笔记标题：≤20 字、可含主词（搜索与点击）。
   - 封面钩子（第 1 页 headline）：3–10 字，冲击力优先。
   - 内页 headline：≤18 字。
2. **有界重试**：把 xhs 分支的「模型调用 → 解析 → 门禁」包成循环（建议新增私有 `_generate_xhs_with_gate(...)`，不展开为公共 API）：
   - `attempts = 0`；每次 `_complete_json_messages` + `_parse_json_response` 后调 `check_before_render`。
   - `errors` 非空且 `attempts + 1 < XHS_GATE_MAX_CALLS` → 把 errors 逐条作为「【门禁打回】请修正以下问题后重新生成」追加到 user_prompt，重试。
   - 重试耗尽仍 errors → 不丢弃，**errors 并入 quality_warnings**（打回给审批，保持"有产出可看"）。
   - 每次成功/耗尽后：`gate.warnings + 剩余 errors` 写入 `GeneratedContent.quality_warnings`。
3. `models.py`：`GeneratedContent` 增加 `quality_warnings: list[str] = []`（向后兼容，门禁警告随响应可见）。
4. `_fallback_content` 的 xhs 分支**不跑门禁**（铁律 4）。
5. chat 路径 xhs prompt（L818）顺手同步三层标题（一行，复用常量）。

### 改动 C：`app.py` — 渲染后完整性接线

- `/api/generate` 两个分支（fallback L296-299、ai L307-312）：`render_carousel` 后调 `check_rendered(content.image_pages, content.attachments, STATIC_DIR)`；errors 非空 → `content.quality_warnings.extend([f"渲染完整性: {e}" for e in errors])` + `logger.error`。**不阻断**（文件已产出，阻断会打断预览）。
- `/api/xhs/render`（L596-598）：同样接线，响应加 `"render_warnings": errors`。
- **不做**：对队列存量条目做渲染前门禁（铁律 3）。

### 改动 D：测试 `tests/test_xhs_quality_gate.py`（新）

- 结构硬门：合格 5–7 页通过；4 页 error；首页非 cover error；cover 2 字 / 11 字 error、5 字通过；content headline 19 字 error、18 字通过；content 页 points 空 error；单条 point 49 字 error、48 字通过。
- 广告法：title/body/pages 含「国家级」「保证」「100%」→ error 且文案含命中词；「最近」「最优」「第一时间」→ **不误伤**（证明不是裸「最」）。
- truth_guard 软警告：含时效/港口风险句 → warning 非 error；无风险句 → 无 warning；常量 `XHS_GATE_MAX_CALLS == 2`。
- 渲染后完整性：伪造 attachment 缺失文件 / 错误尺寸（如 100×100）/ 非 PNG → 各报对应 error；用 `tmp_path` 真渲染一轮（仿 `tests/test_xhs_cards.py::test_render_carousel_creates_publishable_pngs`）→ `check_rendered` 返回 `[]`。
- 三层常量导出断言。

跑：`python3 -m pytest tests/test_xhs_quality_gate.py tests/test_xhs_cards.py tests/test_xiaohongshu_publish_observability.py -q`，再全量 `python3 -m pytest -q`。

## 四、验收清单

1. pytest：新用例全绿；`test_xhs_cards` / 发布可观测性无回归；全量无新增破坏（基线失败项保持基线）。
2. **重启 app**（`ai_engine.py` / `xhs_quality_gate.py` / `models.py` / `app.py` 均服务端改动）。
3. `/api/generate` 选小红书平台：正常主题 → 响应 `quality_warnings` 为空或仅 truth 软警告。
4. 构造封面钩子 >10 字的上游输入（或在 prompt 里强引导）→ 观察自动重试 ≤2 次，最终产物封面 3–10 字或 `quality_warnings` 带「封面钩子」打回说明。
5. `/api/xhs/render` 传含「国家级」「100%」的文案 → 响应/日志出现广告法命中（渲染前门禁），**不**渲染出违规卡。
6. `/api/xhs/render` 正常 → `render_warnings` 为空；人为删一张已渲染 PNG 再调 → `render_warnings` 报缺失/解码失败。
7. 语义红线抽查：`grep -rn "事实已校验\|已校验" xhs_quality_gate.py ai_engine.py` 无命中。

## 五、回滚

`git revert` 本次改动（`xhs_quality_gate.py` / `ai_engine.py` / `models.py` / `app.py` / `tests/test_xhs_quality_gate.py`）。回滚后生成回到「渲染层静默截断 + 兜底补页」现状，`GeneratedContent.quality_warnings` 字段回滚即忽略（Pydantic 向后兼容），无数据/发布语义副作用。

## 六、备注

- 门禁常量是「唯一真源」：prompt、`xhs_cards` 渲染约束、测试三处读同一份数字，禁止各写各的。
- 若执行中发现 `pages_from_content` 的 legacy 标题被门禁误伤，**不要**放宽门禁，而是确认接线点只覆盖新建生成（铁律 3）。
- truth_guard 软警告的目的：让运营在审批阶段就看见"这条稿子有风险句待补证据"，而非发布前最后一刻被 `publish_error` 拦下返工。
- 配图分类、SEO 词库、差异化守卫、模板版本可观测属第 2 批，本批不触碰。
