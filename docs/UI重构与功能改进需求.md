# SA-LogiFlow UI 重构与功能改进需求文档

> 供 Claude Code 直接执行
> 目标：更有科技感、更简洁的 AI 工作流工具

---

## 一、核心改动

### 1.1 Logo 替换为 Buffalo

**需求**：左上角的卡车 logo 换成 buffalo（水牛）形象

**实现**：
1. 修改 `static/common.js` 中 `renderSidebar` 函数
2. 替换 SVG 路径为 buffalo 图标

```javascript
// common.js - renderSidebar 函数中的 sidebar-logo 部分
// 替换为 buffalo SVG（可用 iconify 的 mdi:bull 或自定义）
<svg width="27" height="27" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
    <!-- Buffalo icon path -->
    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/>
</svg>
```

**替代方案**：直接用 iconify 图标
```html
<iconify-icon icon="mdi:bull" width="27" style="color: white;"></iconify-icon>
```

---

### 1.2 导航结构调整：AI 工作区作为主页

**当前结构**：
```
工作台
├── 经营驾驶舱 (home.html) ← 当前主页
├── AI 工作区 (editor.html)
├── 内容资产 (assets.html)
├── 发布日历 (calendar.html)
资源库
├── 企业知识库 (knowledge.html)
├── Prompt 模板 (templates.html)
运营
├── 发布队列 (queue.html)
├── 审核中心 (review.html)
├── 账号管理 (accounts.html)
系统
├── 平台配置 (config.html)
```

**新结构**：
```
核心
├── AI 工作区 (editor.html) ← 新主页！
├── 发布队列 (queue.html)
分析
├── 经营驾驶舱 (home.html)
├── 内容资产 (assets.html)
├── 发布日历 (calendar.html)
资源
├── 企业知识库 (knowledge.html)
├── Prompt 模板 (templates.html)
├── 素材中心 (assets.html)
运营
├── 审核中心 (review.html)
├── 账号管理 (accounts.html)
设置
├── 平台配置 (config.html)
```

**实现**：

1. **修改 `static/common.js` 的 NAV_ITEMS**
```javascript
const NAV_ITEMS = [
    { section: '核心' },
    { id: 'editor', label: 'AI 工作区', href: '/editor.html' },
    { id: 'queue', label: '发布队列', href: '/queue.html' },
    { section: '分析' },
    { id: 'home', label: '经营驾驶舱', href: '/home.html' },
    { id: 'assets', label: '内容资产', href: '/assets.html' },
    { id: 'calendar', label: '发布日历', href: '/calendar.html' },
    { section: '资源' },
    { id: 'knowledge', label: '企业知识库', href: '/knowledge.html' },
    { id: 'templates', label: 'Prompt 模板', href: '/templates.html' },
    { section: '运营' },
    { id: 'review', label: '审核中心', href: '/review.html' },
    { id: 'accounts', label: '账号管理', href: '/accounts.html' },
    { section: '设置' },
    { id: 'config', label: '平台配置', href: '/config.html' },
];
```

2. **修改根路由**（`app.py`）
```python
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "editor.html")  # 改为 editor.html
```

3. **添加 NAV_ICONS**
```javascript
const NAV_ICONS = {
    'AI 工作区': '<path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>',
    '发布队列': '<path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67V7z"/>',
    '经营驾驶舱': '<path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z"/>',
    // ... 其他图标
};
```

---

### 1.3 AI 工作区重构：更清晰的布局

**当前问题**：
- 三栏堆叠太复杂
- 知识库、编辑器、预览、AI 对话全堆在一起
- 移动端体验差

**新布局设计**：

```
┌──────────────────────────────────────────────────────────────┐
│  顶部操作栏：主题选择 | 语气 | 长度 | 平台勾选 | AI 生成 按钮  │
├──────────────────────────────────────────────────────────────┤
│                    │                                         │
│   知识库侧边栏     │          主编辑区                        │
│   (可折叠)         │   ┌─────────────────────────────────┐   │
│                    │   │  标题输入                         │   │
│   搜索框           │   ├─────────────────────────────────┤   │
│                    │   │                                  │   │
│   分类列表         │   │  正文编辑区                       │   │
│   └─ 话题列表     │   │                                  │   │
│                    │   │                                  │   │
│                    │   ├─────────────────────────────────┤   │
│                    │   │  标签输入                         │   │
│                    │   └─────────────────────────────────┘   │
│                    │                                         │
│                    │   ┌─────────────────────────────────┐   │
│   AI 助手面板      │   │  预览区（Tab 切换平台）            │   │
│   (底部可展开)     │   │  小红书 | 抖音 | FB | Twitter    │   │
│                    │   └─────────────────────────────────┘   │
│                    │                                         │
└──────────────────────────────────────────────────────────────┘
```

**关键改进**：

1. **顶部操作栏**：主题/语气/长度/平台/生成按钮放在顶部，不占用编辑空间
2. **知识库可折叠**：左栏默认折叠，点击展开
3. **AI 助手独立面板**：底部可展开的对话区，不挤在角落
4. **预览区 Tab 化**：清晰的平台切换

**实现要点**：

```html
<!-- 顶部操作栏 -->
<div class="editor-toolbar">
    <div class="toolbar-left">
        <select id="topicCategory">...</select>
        <select id="topicSelect">...</select>
    </div>
    <div class="toolbar-center">
        <select id="toneSelect">...</select>
        <select id="lengthSelect">...</select>
        <div class="platform-checkboxes">
            <label><input type="checkbox" value="xiaohongshu" checked> 小红书</label>
            <!-- ... -->
        </div>
    </div>
    <div class="toolbar-right">
        <button class="btn-primary" onclick="generateContent()">✨ AI 生成</button>
    </div>
</div>

<!-- 主体区域 -->
<div class="editor-body">
    <!-- 知识库侧边栏（可折叠） -->
    <aside class="knowledge-sidebar collapsed" id="knowledgeSidebar">
        <button class="toggle-btn" onclick="toggleKnowledge()">📚</button>
        <div class="knowledge-content">
            <input placeholder="搜索知识库..."/>
            <div class="category-list">...</div>
        </div>
    </aside>
    
    <!-- 编辑区 -->
    <main class="editor-main">
        <input id="editorTitle" placeholder="标题" class="title-input"/>
        <textarea id="editorContent" placeholder="输入或生成文案..." class="content-input"></textarea>
        <input id="editorHashtags" placeholder="标签（逗号分隔）" class="tags-input"/>
    </main>
    
    <!-- 预览区 -->
    <aside class="preview-panel">
        <div class="preview-tabs">
            <button class="active">小红书</button>
            <button>抖音</button>
            <button>Facebook</button>
            <button>Twitter</button>
        </div>
        <div class="preview-content" id="previewArea">...</div>
    </aside>
</div>

<!-- AI 助手面板（底部可展开） -->
<div class="ai-assistant-panel" id="aiPanel">
    <div class="ai-panel-header" onclick="toggleAiPanel()">
        <span>🤖 AI 助手</span>
        <iconify-icon icon="mdi:chevron-up"></iconify-icon>
    </div>
    <div class="ai-panel-body">
        <div class="chat-messages" id="chatMessages">...</div>
        <div class="quick-commands">
            <button onclick="sendCommand('/optimize')">✨ 优化</button>
            <button onclick="sendCommand('/shorten')">📝 缩写</button>
            <button onclick="sendCommand('/expand')">📖 扩写</button>
        </div>
        <div class="chat-input">
            <textarea id="aiChatInput" placeholder="输入需求..."></textarea>
            <button onclick="sendMessage()">发送</button>
        </div>
    </div>
</div>
```

---

## 二、UI 科技感增强

### 2.1 悬浮动画效果

**需求**：鼠标悬浮时有浮动、发光、渐变等动画

**实现**：

1. **卡片悬浮提升效果**
```css
/* design-system.css */
.card-hover {
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.card-hover:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 40px rgba(99, 102, 241, 0.15);
}
```

2. **按钮发光效果**
```css
.btn-glow {
    position: relative;
    overflow: hidden;
}

.btn-glow::before {
    content: '';
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(circle, rgba(255,255,255,0.3) 0%, transparent 60%);
    opacity: 0;
    transition: opacity 0.3s;
}

.btn-glow:hover::before {
    opacity: 1;
    animation: glow-rotate 2s linear infinite;
}

@keyframes glow-rotate {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}
```

3. **导航项悬浮动画**
```css
.nav-item {
    position: relative;
    transition: all 0.2s ease;
}

.nav-item::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    width: 3px;
    height: 100%;
    background: var(--primary);
    transform: scaleY(0);
    transition: transform 0.2s ease;
}

.nav-item:hover {
    background: var(--primary-light);
    transform: translateX(4px);
}

.nav-item:hover::before {
    transform: scaleY(1);
}

.nav-item.active {
    background: var(--primary-light);
}

.nav-item.active::before {
    transform: scaleY(1);
}
```

4. **数据卡片数字跳动效果**
```css
.stat-number {
    transition: all 0.3s ease;
}

.stat-card:hover .stat-number {
    transform: scale(1.05);
    color: var(--primary);
}
```

5. **平台图标悬浮缩放**
```css
.platform-icon {
    transition: all 0.2s ease;
}

.platform-icon:hover {
    transform: scale(1.2) rotate(5deg);
}
```

---

### 2.2 科技感配色方案

```css
:root {
    /* 主色调 - 科技蓝紫 */
    --primary: #6366f1;
    --primary-light: rgba(99, 102, 241, 0.1);
    --primary-glow: rgba(99, 102, 241, 0.4);
    
    /* 背景 - 深色科技 */
    --bg-primary: #0a0a0f;
    --bg-secondary: #111827;
    --bg-card: rgba(17, 24, 39, 0.8);
    
    /* 玻璃拟态 */
    --glass-bg: rgba(17, 24, 39, 0.6);
    --glass-border: rgba(99, 102, 241, 0.1);
    --glass-blur: blur(12px);
    
    /* 霓虹色 */
    --neon-blue: #00d4ff;
    --neon-purple: #a855f7;
    --neon-green: #22c55e;
    
    /* 渐变 */
    --gradient-primary: linear-gradient(135deg, #6366f1, #a855f7);
    --gradient-glow: linear-gradient(135deg, rgba(99,102,241,0.3), rgba(168,85,247,0.3));
}
```

---

### 2.3 动态背景效果

```css
/* 科技感网格背景 */
body {
    background: var(--bg-primary);
    background-image: 
        linear-gradient(rgba(99, 102, 241, 0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(99, 102, 241, 0.03) 1px, transparent 1px);
    background-size: 40px 40px;
}

/* 渐变光晕背景 */
.hero-glow {
    position: relative;
}

.hero-glow::before {
    content: '';
    position: absolute;
    top: -200px;
    left: -200px;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, var(--primary-glow) 0%, transparent 70%);
    opacity: 0.3;
    pointer-events: none;
}
```

---

### 2.4 交互动画

```css
/* 页面切换动画 */
.page-enter {
    animation: fadeInUp 0.3s ease-out;
}

@keyframes fadeInUp {
    from {
        opacity: 0;
        transform: translateY(20px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

/* 按钮点击涟漪效果 */
.btn-ripple {
    position: relative;
    overflow: hidden;
}

.btn-ripple::after {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: 0;
    height: 0;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.3);
    transform: translate(-50%, -50%);
    transition: width 0.6s, height 0.6s, opacity 0.6s;
    opacity: 0;
}

.btn-ripple:active::after {
    width: 300px;
    height: 300px;
    opacity: 0;
}

/* 输入框聚焦发光 */
.input-glow:focus {
    box-shadow: 0 0 0 3px var(--primary-light), 0 0 20px var(--primary-glow);
    border-color: var(--primary);
}
```

---

## 三、知识库侧边栏折叠

**需求**：知识库默认折叠，给编辑区更多空间

**实现**：

```javascript
function toggleKnowledge() {
    const sidebar = document.getElementById('knowledgeSidebar');
    sidebar.classList.toggle('collapsed');
    
    // 保存状态到 localStorage
    localStorage.setItem('knowledgeCollapsed', sidebar.classList.contains('collapsed'));
}

// 初始化时恢复状态
function initKnowledgeState() {
    const collapsed = localStorage.getItem('knowledgeCollapsed') === 'true';
    if (collapsed) {
        document.getElementById('knowledgeSidebar').classList.add('collapsed');
    }
}
```

```css
.knowledge-sidebar {
    width: 280px;
    transition: width 0.3s ease;
    overflow: hidden;
}

.knowledge-sidebar.collapsed {
    width: 48px;
}

.knowledge-sidebar.collapsed .knowledge-content {
    opacity: 0;
    pointer-events: none;
}

.knowledge-sidebar .toggle-btn {
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: none;
    cursor: pointer;
    color: var(--text-secondary);
    transition: all 0.2s;
}

.knowledge-sidebar .toggle-btn:hover {
    background: var(--primary-light);
    color: var(--primary);
}
```

---

## 四、AI 助手面板

**需求**：AI 对话不挤在角落，底部可展开的独立面板

**实现**：

```css
.ai-assistant-panel {
    position: fixed;
    bottom: 0;
    right: 20px;
    width: 380px;
    max-height: 500px;
    background: var(--glass-bg);
    backdrop-filter: var(--glass-blur);
    border: 1px solid var(--glass-border);
    border-radius: 16px 16px 0 0;
    transform: translateY(calc(100% - 48px));
    transition: transform 0.3s ease;
    z-index: 100;
}

.ai-assistant-panel.expanded {
    transform: translateY(0);
}

.ai-panel-header {
    padding: 12px 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--glass-border);
}

.ai-panel-header:hover {
    background: var(--primary-light);
}

.ai-panel-body {
    padding: 16px;
    max-height: 400px;
    overflow-y: auto;
}
```

```javascript
function toggleAiPanel() {
    const panel = document.getElementById('aiPanel');
    panel.classList.toggle('expanded');
}
```

---

## 五、执行清单

### 优先级 P0（立即做）
- [ ] 替换 Logo 为 Buffalo
- [ ] 修改 NAV_ITEMS 顺序（AI 工作区置顶）
- [ ] 修改根路由指向 editor.html
- [ ] AI 工作区布局重构（顶部操作栏 + 可折叠知识库）

### 优先级 P1（本周做）
- [ ] 添加悬浮动画 CSS
- [ ] 添加科技感配色变量
- [ ] 实现 AI 助手面板
- [ ] 实现知识库折叠功能

### 优先级 P2（下周做）
- [ ] 添加动态背景效果
- [ ] 添加页面切换动画
- [ ] 添加按钮涟漪效果
- [ ] 优化移动端响应式

---

*文档版本：v1*
*更新时间：2026-07-01*
*供 Claude Code 直接执行*
