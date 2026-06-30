# AI 工作区对抗式审查报告

> 审查对象：`static/editor.html`（含 `static/common.js`、后端 `app.py` / `ai_engine.py` 契约对照）
> 审查日期：2026-06-30
> 方法：第一性原理 + 对抗式数据流追踪（重点查"看起来能用、实际丢数据"的问题）

---

## 一、第一性原理分析

### 这个工作区到底在解决什么问题？

剥离掉所有 UI，AI 工作区的**唯一核心价值**是：

> **把"一个物流主题"低成本地变成"多个平台可直接发布的合规文案"，并送进发布流水线。**

用户（运营专员）的真实任务链路只有 4 步：

1. **定主题** —— 从知识库选，或自己输入
2. **生成** —— AI 一次产出多平台差异化文案
3. **改稿** —— 人工微调（这是关键，AI 产出必然要改）
4. **提交** —— 进入审核队列，流向发布

### 由此推导出的硬性要求（验收基线）

| 要求 | 说明 | 当前是否满足 |
|---|---|---|
| R1 编辑结果必须是提交结果 | 用户改了什么，提交的就是什么 | ❌ **不满足（P0）** |
| R2 提交成功/失败必须真实反馈 | 不能假装成功 | ❌ **不满足（P0）** |
| R3 预览必须反映最新内容 | 所见即所发 | ❌ 部分不满足（P1） |
| R4 UI 上出现的功能必须可用 | 不放假按钮 | ❌ 多处假控件（P1） |
| R5 多平台覆盖一致 | Tab 与生成范围对齐 | ❌ 不一致（P1） |

**结论**：当前页面在"演示层"完成度很高（布局、配色、空状态都做了），但在**核心数据流（改稿 → 提交）上是断裂的**，违背了产品的第一性目的。这正是对抗式审查要揪出的"好看但不能用"。

---

## 二、发现的问题（P0/P1/P2）

### 🔴 P0 —— 破坏核心价值 / 数据正确性

#### P0-1　用户的编辑会被静默丢弃（最严重）

`submitToQueue()`（`editor.html:291-301`）：

```js
const title=document.getElementById('editorTitle').value, body=document.getElementById('editorContent').value;
if (!title||!body) { showToast('请先生成或输入内容','error'); return; }   // ← 用编辑器的值做校验
...
for (const [platform,c] of Object.entries(generatedContents)) {
    await apiFetch('/api/queue', { ... body:JSON.stringify({title:c.title,body:c.body,...}) }); // ← 却提交 generatedContents 的旧值
}
```

**矛盾点**：校验读的是编辑器 DOM 的值，提交发的是 `generatedContents` 里 AI 的原始值。
**后果**：用户在中栏辛苦改了标题/正文/标签，点"提交发布"——队列里进的是**没改过的 AI 原稿**。改稿这一步（R1/R3）等于白做，而且**没有任何报错**，用户完全不知道。这是工作区存在意义被直接否定的级别。

> 补充：编辑器是单文档，但 `generatedContents` 是多平台。即使想"把编辑器内容写回"，也存在"编辑器现在对应哪个平台"的归属缺失——见 P1-3 的根因。

#### P0-2　提交失败被吞，仍弹"成功"

同函数 `editor.html:298`：

```js
try { await apiFetch('/api/queue',{...}); added++; } catch(e){}   // 错误被吞
...
showToast(`已提交 ${added} 个平台到审核队列`);
```

**后果**：网络错误 / 401 过期 / 后端 500 时，循环静默跳过，最终若 `added===0` 仍然弹 `已提交 0 个平台到审核队列`——一个把"全部失败"显示成"已提交"的成功 Toast。运营会以为发了，实际什么都没进队列。**假成功比报错更危险**。

#### P0-3　`data.contents` 缺字段直接抛异常打断流程

`generateContent()`（`editor.html:274`）：

```js
generatedContents = {}; data.contents.forEach(c=>generatedContents[c.platform]=c);
const first = data.contents[0];
```

后端正常返回 `contents`，但一旦 AI 解析异常 / 后端契约变动导致 `data.contents` 为 `undefined`，这里 `.forEach` / `[0]` 直接 `TypeError`，被外层 `catch` 兜成 `生成失败: Cannot read ...`，用户看到的是看不懂的报错。属于**对外部响应零防御**。

---

### 🟠 P1 —— 功能缺陷 / 体验断裂

#### P1-1　多处"假控件"（违背 R4）

- **知识库搜索框**（`editor.html:98`）：有 `placeholder="搜索知识库..."`，**无任何事件绑定**，输入无效。
- **工具栏 加粗 / 列表 按钮**（`editor.html:122-123`）：无 `onclick`，点了什么都不发生。`textarea` 是纯文本，也不可能"加粗"。
- **草稿功能半残**（`editor.html:303`）：`saveDraft()` 写 `localStorage['draft']`，但 `init()` 从不读取、不回填。刷新后草稿永远拿不回来 → 等于没有草稿功能，反而误导用户以为内容安全。

假控件让用户对系统能力产生错误预期，是 SaaS 工具的信任杀手。

#### P1-2　预览平台 Tab 与生成范围不一致（违背 R5）

- `platformInfo` 定义 5 个平台（含 **抖音 douyin**，`editor.html:64-70`），渲染出 5 个预览 Tab。
- 但 `generateContent()` 只生成 4 个：`['xiaohongshu','facebook','twitter','reddit']`（`editor.html:257`）。
- **后果**：抖音 Tab 永远是"该平台暂无内容"空状态，是一个**结构性永远点不亮的 Tab**。要么补上 douyin 的生成，要么从 Tab 里去掉。

#### P1-3　编辑器 ↔ 预览单向且不回流（违背 R3，也是 P0-1 的根因）

- 预览卡片"编辑"按钮 `editPlatform()`（`editor.html:248`）把平台内容灌进编辑器；
- 但编辑器 `input` 事件只更新字数（`editor.html:149`），**不会写回 `generatedContents`，也不会刷新预览**。
- 数据流是断的：`generatedContents → 编辑器`（单向），编辑器的修改无处可去。这就是 P0-1 发生的结构性原因。
- 正确模型应是：编辑器始终绑定"当前平台"，输入即同步回 `generatedContents[currentPreview]` 并重渲染预览，提交时直接用 `generatedContents`。

#### P1-4　主题选定后无法清除，被"锁死"在知识库主题

`generateContent()` 用 `selectedTopic || aiReq`（`editor.html:254`）。一旦点过知识库主题，`selectedTopic` 永久有值，之后即使用户想纯靠 AI 对话框自由输入主题，也始终走旧主题。没有"取消选择/清除"入口。

#### P1-5　左栏"AI 对话"名不副实

标签写"AI 对话"（`editor.html:102`），实为一个把输入塞进 `instruction` 的单次生成框，无多轮、无历史。命名与心智模型不符，用户会期待对话能力。

---

### 🟡 P2 —— 健壮性 / 一致性 / 可访问性

- **P2-1 双生成按钮无统一 loading**：左栏 `genBtn` 只 `disabled`，中栏 `genBtnMain` 才转圈（`editor.html:259-261`）。从左栏触发时无视觉反馈，疑似卡死。
- **P2-2 `cat.icon` 未转义注入属性**（`editor.html:163`）：`icon="${cat.icon}"` 直接拼接。当前是后端可信配置，风险低，但与全站 `escapeHtml` 纪律不一致，留隐患。
- **P2-3 可访问性缺失**：标题/正文/标签输入框无 `<label>`，纯图标按钮（加粗、列表、生成、退出）无 `aria-label`，屏幕阅读器不可用。
- **P2-4 死代码**：`toggleCategory()`（`editor.html:216`）已被 `renderKnowledgeBase` 的 DOM 绑定取代，无人调用，应删除。
- **P2-5 字数统计口径**：用 `value.length`（UTF-16 码元），emoji / 部分中文符号会算成 2，与平台真实字数限制有偏差；若后续要做"超限提示"需改用 `Array.from(str).length` 或按平台规则计算。
- **P2-6 提交状态冗余且语义易错**：前端硬塞 `status:'pending_review'`（`editor.html:298`），后端 `add_queue` 又按角色重算（`app.py:274-276`）。两处真相源，reviewer/admin 提交时语义可能偏离预期，应由后端单一决定。

---

## 三、修复建议

### 立即修（P0，合并为一次数据流重构）

1. **统一单一数据源**：让编辑器始终代表"当前平台"。
   - 编辑器 `input` 时同步写回：`generatedContents[currentPreview] = {title, body, hashtags}`，并 `renderPreview(currentPreview)`。
   - `submitToQueue` 直接用 `generatedContents`，彻底消除"校验用新值、提交用旧值"。

2. **提交真实反馈**：

```js
let added = 0, failed = [];
for (const [platform, c] of Object.entries(generatedContents)) {
    try { await apiFetch('/api/queue', {...}); added++; }
    catch (e) { failed.push(platform); }
}
if (added === 0)        showToast('提交失败，未进入队列', 'error');
else if (failed.length) showToast(`已提交 ${added} 个，失败：${failed.join('、')}`, 'info');
else                    showToast(`已提交 ${added} 个平台到审核队列`);
```

3. **响应防御**：`const list = Array.isArray(data?.contents) ? data.contents : []; if(!list.length){showToast('生成结果为空','error');return;}`

### 排期修（P1）

- 知识库搜索：绑定 `input` 事件，按 `name_zh` / 主题文本前端过滤 `renderKnowledgeBase`。
- 工具栏：要么实现（切到富文本/Markdown）、要么直接删除两个假按钮。
- 草稿：`init()` 末尾读 `localStorage['draft']` 回填，并在有草稿时提示"检测到未完成草稿，是否恢复"。
- 抖音：补 douyin 进生成数组，或从 `platformInfo` 移除——二选一，消除永空 Tab。
- 主题选择：在"当前主题"标签上加一个 ✕ 清除按钮，重置 `selectedTopic=''`、`selectedCategory='custom'`。
- "AI 对话"改名为"AI 指令 / 补充要求"，与实际能力对齐。

### 优化项（P2）

- 两个生成按钮抽成 `setGenerating(bool)` 统一控制 loading/disabled。
- 补 `aria-label` 与 `<label>`；`cat.icon` 走 `escapeHtml`。
- 删除死函数 `toggleCategory`。
- 字数统计与"提交状态决策权"分别按上文规则收敛到正确口径 / 后端单一决定。

---

## 四、总结

| 维度 | 评价 |
|---|---|
| 视觉 / 布局 | ✅ 完成度高，三栏结构清晰，空状态、配色到位 |
| 核心数据流（改稿→提交） | ❌ **断裂**，存在静默丢数据 + 假成功，否定产品核心价值 |
| 功能真实性 | ⚠️ 多处假控件（搜索、工具栏、草稿），透支用户信任 |
| 健壮性 | ⚠️ 对后端响应、提交失败几乎零防御 |
| 可访问性 | ❌ 基本缺失 |

**一句话结论**：这是一个**"演示态完成、生产态未完成"**的页面。Demo 流程（选主题→生成→看预览）能跑通，但只要用户真的去**改稿并提交**——也就是这个工作区唯一不可替代的价值——数据就会丢失且无感知。

**优先级**：P0 三项必须在任何真实使用前修复，且应合并为一次"单一数据源"重构（P0-1 与 P1-3 同根）。其余按排期推进。修完 P0 后，本页才从"能演示"变成"能用"。
