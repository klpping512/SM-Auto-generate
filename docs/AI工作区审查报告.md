# AI 工作区对抗式审查报告

> **审查时间**: 2026-06-30  
> **审查文件**: `static/editor.html` + `static/common.js`  
> **审查方式**: 对抗式逐行审查 + 第一性原理分析  

---

## 一、第一性原理分析

### 这个页面的核心价值是什么？

用户来到 AI 工作区，要完成的任务链是：

```
选择知识主题 → 输入补充需求 → AI 生成多平台内容 → 预览确认 → 提交发布队列
```

**本质**：这是一个**内容生产流水线的起点**，用户期望的是「选主题 → 一键生成 → 微调 → 发布」的顺畅体验。

### 三栏布局的意图 vs 现实

| 栏位 | 设计意图 | 实际体验 |
|------|---------|---------|
| 左栏（知识库） | 灵感来源，快速选题 | ✅ 基本可用，但搜索框是摆设 |
| 中栏（编辑器） | 核心创作区 | ⚠️ 与预览不同步，编辑后预览不更新 |
| 右栏（预览） | 确认发布效果 | ❌ 平台与生成不匹配，抖音永远空白 |

---

## 二、发现的问题

### 🔴 P0 — 严重缺陷（影响核心功能）

#### P0-1: 平台集合不一致，抖音预览永远为空

```javascript
// 第 64-70 行：platformInfo 定义了 5 个平台（含抖音）
const platformInfo = {
    xiaohongshu, douyin, facebook, twitter, reddit
};

// 第 257 行：generateContent 只生成 4 个平台（无抖音）
const platforms = ['xiaohongshu','facebook','twitter','reddit'];
```

**后果**：用户看到 5 个预览 Tab，点击「抖音」永远显示「该平台暂无内容」。这是**功能性 Bug**，直接破坏用户信任。

#### P0-2: 编辑器与预览完全脱节

用户在编辑器中手动修改标题/正文后，`generatedContents` 对象不会更新。点击预览 Tab 仍然显示旧的生成内容，而编辑器显示新内容。**两个视图展示不同数据**，这是致命的 UX 问题。

#### P0-3: 提交发布队列只提交编辑器当前内容到第一个平台

```javascript
// 第 297-298 行：循环 generatedContents 的每个平台，但用的是 c.title/c.body（原始生成内容）
for (const [platform,c] of Object.entries(generatedContents)) {
    await apiFetch('/api/queue', {body: JSON.stringify({title:c.title, body:c.body, ...})});
}
```

如果用户编辑了内容，提交的仍然是**原始 AI 生成内容**，不是用户修改后的版本。用户的所有编辑都会被丢弃。

#### P0-4: 知识库搜索框是死的

```html
<!-- 第 98 行 -->
<input class="input" placeholder="搜索知识库..." style="font-size:12px;padding:6px 10px;"/>
```

没有 `id`、没有 `oninput` 事件、没有搜索逻辑。这是一个**纯装饰**的输入框，用户输入后无任何反应。

#### P0-5: 工具栏按钮是死的

```html
<!-- 第 122-123 行 -->
<button title="加粗">...</button>
<button title="列表">...</button>
```

点击后无任何效果。`<textarea>` 元素本身不支持富文本格式化，这两个按钮纯粹是误导。用户点击「加粗」什么都不会发生。

---

### 🟠 P1 — 重要问题（影响用户体验）

#### P1-1: 重复 ID 违反 HTML 规范

```javascript
// 侧边栏 AI 对话区的生成按钮
<button ... id="genBtn">   // 第 105 行

// 中栏编辑器顶部的生成按钮  
<button ... id="genBtnMain">  // 第 119 行
```

`genBtn` 被引用了两次（`btns` 数组中），虽然本次不是同名 ID，但侧边栏和编辑器的生成按钮共存容易让用户困惑：**两个生成按钮有什么区别？**

#### P1-2: 初始化时无加载状态

```javascript
async function init() {
    try { const r = await apiFetch('/api/topics'); categories = await r.json(); } catch(e) { categories = []; }
    // 直接渲染，无 skeleton / loading 指示
    document.getElementById('app').innerHTML = `...`;
}
```

在知识库数据加载完成前，整个页面是空白的。网络慢时用户看到的是一个空壳。

#### P1-3: 知识库选主题后的 Toast 轰炸

```javascript
// 第 198 行
showToast(`已选择主题：${t}`, 'info');
```

每次点击主题都弹 Toast。用户浏览多个主题做比较时，会收到一连串通知。应该只在确认使用时才提示，而非每次点击。

#### P1-4: AI 对话区空间极度压缩

左栏底部的 AI 对话区只有：
```html
<div style="padding:12px;border-top:1px solid var(--border-light);">
    <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:6px;">AI 对话</div>
    <div style="display:flex;gap:6px;">
        <input id="aiInput" ... placeholder="输入需求..." />
        <button ... onclick="generateContent()">...</button>
    </div>
</div>
```

**这是整个工作区最重要的交互入口**（输入需求让 AI 生成），却被压缩到约 60px 高度的角落里，与「知识库」标签混在一起，用户很难注意到。

#### P1-5: 保存草稿只存 localStorage，不存服务端

```javascript
function saveDraft() {
    localStorage.setItem('draft', JSON.stringify({...}));
    showToast('草稿已保存');
}
```

- 切换浏览器/清缓存即丢失
- 没有加载草稿的逻辑（`init()` 中没有读取 `draft`）
- 保存了但永远恢复不了，这是**假功能**

#### P1-6: 响应式布局断点后三栏堆叠，体验崩溃

```css
@media (max-width: 1180px) {
    .workspace-grid { grid-template-columns: 1fr; overflow: visible; }
    .col-knowledge, .col-editor, .col-preview { min-height: 360px; }
}
```

在 1180px 以下，三栏变成纵向堆叠。用户需要**大量滚动**才能在知识库、编辑器、预览之间切换。对于笔记本用户（常见 1366px 宽度）来说，左右栏各占 280px+360px=640px，编辑器只剩约 400px，输入长文案体验极差。

#### P1-7: `submitToQueue` 静默吞掉错误

```javascript
for (const [platform,c] of Object.entries(generatedContents)) {
    try { await apiFetch('/api/queue', {...}); added++; } catch(e){}  // 错误被静默忽略
}
```

如果某个平台提交失败，用户完全不知道。最后的 Toast 只显示「已提交 N 个平台」，用户无法知道哪个失败了。

#### P1-8: 没有字数限制校验

各平台字数限制差异巨大（Twitter 280字符、小红书 1000字、Facebook 长文不限），但编辑器只有字数统计，没有任何校验或警告。用户可能为 Twitter 生成了 500 字的长文而不自知。

---

### 🟡 P2 — 改进项（影响专业度和可维护性）

#### P2-1: 内联样式泛滥，违反关注点分离

editor.html 中有 **60+ 处内联 `style`** 属性，例如：

```html
style="padding:16px;border-bottom:1px solid var(--border-light);"
style="font-size:13px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;gap:6px;"
```

这导致：
- 无法复用样式
- 无法通过 CSS 媒体查询统一调整
- 维护噩梦

#### P2-2: 知识库 CSS 类未被使用

```css
/* 第 29-49 行定义了 .kb-category 和 .kb-topic 样式 */
.kb-category { ... }
.kb-topic { ... }
```

但 `renderKnowledgeBase()` 函数（第 155-214 行）完全用内联样式和 `onmouseover/onmouseout` 事件替代了这些类。CSS 中的样式成了死代码。

#### P2-3: `toggleCategory` 函数是死代码

```javascript
// 第 216-219 行
function toggleCategory(el) {
    const topics = el.nextElementSibling;
    topics.style.display = topics.style.display === 'none' ? 'block' : 'none';
}
```

从未被调用。知识库的展开/收起在 `renderKnowledgeBase()` 中用 `catDiv.onclick` 内联处理。

#### P2-4: 预览区没有平台特性适配

预览只是一个通用的卡片展示，没有体现各平台的 UI 特性：
- 小红书：应有图片轮播、标题封面预览
- Twitter：应有字符数进度条
- Facebook：应有链接预览卡片
- 抖音：应有竖版视频文案展示

#### P2-5: 知识库主题缺乏上下文

知识库只显示主题名称（如「PAT注册流程详解」），没有：
- 主题简介/摘要
- 历史使用次数
- 推荐标签
- 相关主题推荐

用户无法判断哪个主题适合当前需求。

#### P2-6: 没有快捷键支持

编辑器没有常见快捷键：
- `Ctrl+Enter` 快速生成
- `Ctrl+S` 保存草稿
- `Ctrl+B` 加粗（虽然 textarea 不支持，但应该有提示）

#### P2-7: `platformInfo` 和 `platforms` 数组硬编码在前端

平台列表应该从后端获取，而不是前端硬编码。新增平台时需要改两处（`platformInfo` 和 `generateContent` 中的 `platforms`），这就是 P0-1 的根因。

---

## 三、修复建议

### 针对 P0（立即修复）

| 问题 | 修复方案 |
|------|---------|
| P0-1 平台不一致 | 统一平台列表为单一数据源，或从后端 API 获取 |
| P0-2 编辑器与预览脱节 | 编辑器 `oninput` 事件同步更新 `generatedContents[currentPreview]` |
| P0-3 提交内容不一致 | `submitToQueue` 读取编辑器当前值而非 `generatedContents` |
| P0-4 搜索框是死的 | 实现 `filterKnowledgeBase(keyword)` 函数，绑定 `oninput` |
| P0-5 工具栏是死的 | 删除假按钮，或实现为 Markdown 快捷插入（`**bold**`、`- list`） |

### 针对 P1（优先修复）

| 问题 | 修复方案 |
|------|---------|
| P1-4 AI 对话入口被压缩 | 将 AI 输入区提升为独立的顶部操作栏，或改为浮动面板 |
| P1-5 假草稿功能 | 补全加载逻辑，或在 init 中读取并填充 |
| P1-7 静默错误 | 收集失败列表，Toast 显示「X/Y 个平台提交失败：...」 |
| P1-8 无字数校验 | 按平台显示字数限制，超限时警告 |

### 架构层面建议

1. **引入状态管理**：用一个 `state` 对象管理 `{title, body, hashtags, generatedContents, selectedTopic}`，编辑器和预览都从 state 读取，双向绑定。
2. **平台列表后端化**：新增 `/api/platforms` 接口，前端统一从 API 获取。
3. **三栏布局改为可折叠**：左栏和右栏应可收起，让编辑器获得全宽空间。
4. **内联样式抽离**：将所有内联样式迁移到 `design-system.css` 或页面级 `<style>` 块。

---

## 四、总结

editor.html 的三栏布局改造在**架构意图**上是正确的（知识库 + 编辑器 + 预览的分离），但**实现质量严重不足**：

- **5 个 P0 级 Bug**：平台不一致、编辑器与预览脱节、提交内容错误、搜索框和工具栏是装饰品
- **8 个 P1 级问题**：AI 入口被边缘化、草稿功能不完整、错误被吞、无字数校验
- **7 个 P2 级改进**：内联样式泛滥、死代码、缺少平台特性适配

**核心矛盾**：这个页面的设计目标是「高效的 AI 内容创作工作流」，但当前实现中，**用户输入 → AI 生成 → 编辑微调 → 预览确认 → 提交发布**这条主链路存在多个断裂点。用户最容易遇到的情况是：编辑了半天内容，提交时发现发的是 AI 原始版本。

**建议优先级**：先修 P0（1-2 天），再修 P1（2-3 天），最后处理 P2（持续优化）。
