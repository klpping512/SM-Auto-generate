# SA-LogiFlow UI 重构与功能改进需求文档 v2

> 供 Claude Code 直接执行
> 核心改动：AI 工作区拆分为「AI 对话」+「编辑器」两个页面

---

## 一、页面架构调整

### 当前结构
```
editor.html（AI 工作区）← 所有功能堆在一起
├── 知识库选题
├── AI 对话输入
├── 内容编辑
├── 多平台预览
└── 提交发布
```

### 新结构
```
chat.html（AI 对话）← 新首页，轻量对话式
├── 知识库快速选题
├── 多轮 AI 对话
├── 快捷指令（/优化 /缩写 /扩写）
└── 生成结果 → 发送到编辑器

editor.html（内容编辑器）← 专注编辑发布
├── 内容编辑（标题/正文/标签）
├── 附件管理（图片/视频）
├── 多平台预览
└── 提交发布队列
```

### 导航结构
```
核心
├── AI 对话 (chat.html) ← 新主页！
├── 内容编辑器 (editor.html)
├── 发布队列 (queue.html)
分析
├── 经营驾驶舱 (home.html)
├── 内容资产 (assets.html)
├── 发布日历 (calendar.html)
资源
├── 企业知识库 (knowledge.html)
├── Prompt 模板 (templates.html)
运营
├── 审核中心 (review.html)
├── 账号管理 (accounts.html)
设置
├── 平台配置 (config.html)
```

---

## 二、AI 对话页（chat.html）- 新首页

### 页面布局

```
┌──────────────────────────────────────────────────────────────┐
│  Logo: Buffalo    SA-LogiFlow                          用户  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │                                                      │   │
│   │                   对话消息区域                        │   │
│   │                                                      │   │
│   │   AI: 你好！我是 LogiFlow AI 助手...                  │   │
│   │                                                      │   │
│   │   用户: 生成一篇关于德班港的小红书文案                  │   │
│   │                                                      │   │
│   │   AI: 好的，这是为小红书生成的文案...                   │   │
│   │       ┌────────────────────────────────┐             │   │
│   │       │ 预览卡片                        │             │   │
│   │       │ 标题：德班港拥堵预警...         │             │   │
│   │       │ 正文：...                      │             │   │
│   │       │ [发送到编辑器] [复制]           │             │   │
│   │       └────────────────────────────────┘             │   │
│   │                                                      │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  快捷入口：                                           │   │
│   │  [📦 德班港] [🛃 清关] [🚚 配送] [📊 市场] [自由输入]  │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  [语气: 专业严谨 ▾] [长度: 中 ▾] [平台: 全选 ▾]       │   │
│   ├──────────────────────────────────────────────────────┤   │
│   │  │ 输入需求或选择主题...                    │  发送  │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  快捷指令：                                           │   │
│   │  [/optimize 优化] [/shorten 缩写] [/expand 扩写]       │   │
│   │  [/translate 翻译] [/hashtags 标签]                    │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 核心功能

1. **多轮对话**
   - 上下文记忆
   - 支持追问和修改

2. **知识库快捷选题**
   - 6 个分类的快捷卡片
   - 点击直接生成

3. **生成结果卡片**
   - 预览生成的内容
   - 「发送到编辑器」按钮 → 跳转 editor.html 并填充内容
   - 「复制」按钮

4. **底部参数栏**
   - 语气选择
   - 长度选择
   - 平台选择

5. **快捷指令**
   - `/optimize` - 优化当前内容
   - `/shorten` - 精简内容
   - `/expand` - 扩展内容
   - `/translate` - 翻译
   - `/hashtags` - 生成标签

### 技术实现

```html
<!-- chat.html 核心结构 -->
<div class="chat-page">
    <!-- 对话区域 -->
    <div class="chat-messages" id="chatMessages">
        <!-- 消息列表 -->
    </div>
    
    <!-- 快捷选题 -->
    <div class="quick-topics">
        <button onclick="quickTopic('德班港拥堵')">📦 德班港</button>
        <button onclick="quickTopic('南非清关')">🛃 清关</button>
        <!-- ... -->
    </div>
    
    <!-- 参数栏 -->
    <div class="params-bar">
        <select id="toneSelect">...</select>
        <select id="lengthSelect">...</select>
        <div class="platform-select">...</div>
    </div>
    
    <!-- 输入框 -->
    <div class="chat-input">
        <textarea id="chatInput" placeholder="输入需求或选择主题..."></textarea>
        <button onclick="sendMessage()">发送</button>
    </div>
    
    <!-- 快捷指令 -->
    <div class="quick-commands">
        <button onclick="sendCommand('/optimize')">/optimize 优化</button>
        <!-- ... -->
    </div>
</div>
```

```javascript
// 核心逻辑
async function sendMessage() {
    const input = document.getElementById('chatInput');
    const msg = input.value.trim();
    if (!msg) return;
    
    // 添加用户消息
    appendMessage('user', msg);
    input.value = '';
    
    // 调用 AI
    const resp = await apiFetch('/api/ai/chat', {
        method: 'POST',
        body: JSON.stringify({
            messages: chatHistory,
            tone: document.getElementById('toneSelect').value,
            length: document.getElementById('lengthSelect').value,
            platforms: getSelectedPlatforms()
        })
    });
    const data = await resp.json();
    
    // 显示 AI 回复（带预览卡片）
    appendMessageWithCard('assistant', data);
    
    // 保存到历史
    chatHistory.push({role: 'user', content: msg});
    chatHistory.push({role: 'assistant', content: data.content});
}

// 发送到编辑器
function sendToEditor(content) {
    // 存到 localStorage
    localStorage.setItem('draft', JSON.stringify({
        title: content.title,
        body: content.body,
        hashtags: content.hashtags
    }));
    
    // 跳转到编辑器
    window.location.href = '/editor.html';
}
```

---

## 三、内容编辑器（editor.html）- 精细化编辑

### 页面布局

```
┌──────────────────────────────────────────────────────────────┐
│  Logo: Buffalo    SA-LogiFlow                          用户  │
├──────────────────────────────────────────────────────────────┤
│  [保存草稿]  [提交发布]                                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  当前主题：德班港拥堵预警                    [✕ 清除]  │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  标题                                                │   │
│   │  ┌────────────────────────────────────────────────┐  │   │
│   │  │ 德班港拥堵预警！卖家利润正在被吞噬⚡            │  │   │
│   │  └────────────────────────────────────────────────┘  │   │
│   ├──────────────────────────────────────────────────────┤   │
│   │  正文                                                │   │
│   │  ┌────────────────────────────────────────────────┐  │   │
│   │  │                                                │  │   │
│   │  │  各位跨境物流人注意，德班港拥堵指数已飙升至...  │  │   │
│   │  │                                                │  │   │
│   │  │                                                │  │   │
│   │  └────────────────────────────────────────────────┘  │   │
│   ├──────────────────────────────────────────────────────┤   │
│   │  标签                                                │   │
│   │  ┌────────────────────────────────────────────────┐  │   │
│   │  │ #南非物流, #德班港, #跨境货运                   │  │   │
│   │  └────────────────────────────────────────────────┘  │   │
│   ├──────────────────────────────────────────────────────┤   │
│   │  附件                                                │   │
│   │  [📎 添加图片/视频]                                   │   │
│   │  ┌────┐ ┌────┐ ┌────┐                               │   │
│   │  │ 图 │ │ 图 │ │ 视 │                               │   │
│   │  └────┘ └────┘ └────┘                               │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │  预览                                                │   │
│   │  [小红书] [抖音] [Facebook] [Twitter] [Reddit]        │   │
│   ├──────────────────────────────────────────────────────┤   │
│   │  ┌────────────────────────────────────────────────┐  │   │
│   │  │                                                │  │   │
│   │  │            小红书预览卡片                       │  │   │
│   │  │                                                │  │   │
│   │  └────────────────────────────────────────────────┘  │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 核心功能

1. **内容编辑**
   - 标题输入
   - 正文编辑
   - 标签输入

2. **附件管理**
   - 图片/视频上传
   - 预览缩略图
   - 删除附件

3. **多平台预览**
   - Tab 切换
   - 各平台样式适配

4. **提交发布**
   - 保存草稿
   - 提交审核队列

### 从 AI 对话页接收数据

```javascript
// editor.html 初始化时
function init() {
    // 检查是否有从 chat.html 传来的草稿
    const draft = localStorage.getItem('draft');
    if (draft) {
        const data = JSON.parse(draft);
        document.getElementById('editorTitle').value = data.title || '';
        document.getElementById('editorContent').value = data.body || '';
        document.getElementById('editorHashtags').value = (data.hashtags || []).join(', ');
        
        // 清除草稿（已使用）
        localStorage.removeItem('draft');
        
        showToast('已从 AI 对话导入内容');
    }
}
```

---

## 四、后端 API 调整

### 1. AI 对话 API

```python
@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest):
    """
    req.messages: 对话历史
    req.tone: 语气
    req.length: 长度
    req.platforms: 目标平台
    req.topic: 主题（可选）
    """
    # 构建 prompt
    system_prompt = build_system_prompt(req.tone, req.length, req.platforms)
    
    messages = [{"role": "system", "content": system_prompt}] + req.messages
    
    # 调用 MiMo
    response = await call_mimo(messages)
    
    # 解析返回内容
    content = parse_ai_response(response)
    
    return {
        "content": content,
        "title": content.get("title"),
        "body": content.get("body"),
        "hashtags": content.get("hashtags", [])
    }
```

### 2. 根路由修改

```python
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "chat.html")  # 新首页
```

---

## 五、UI 科技感增强

### 5.1 悬浮动画

```css
/* 卡片悬浮提升 */
.card-hover {
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.card-hover:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 40px rgba(99, 102, 241, 0.15);
}

/* 导航项滑入 */
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

.nav-item:hover::before,
.nav-item.active::before {
    transform: scaleY(1);
}

/* 按钮发光 */
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

/* 输入框聚焦发光 */
.input-glow:focus {
    box-shadow: 0 0 0 3px var(--primary-light), 0 0 20px var(--primary-glow);
    border-color: var(--primary);
}
```

### 5.2 配色方案

```css
:root {
    --primary: #6366f1;
    --primary-light: rgba(99, 102, 241, 0.1);
    --primary-glow: rgba(99, 102, 241, 0.4);
    
    --bg-primary: #0a0a0f;
    --bg-secondary: #111827;
    --bg-card: rgba(17, 24, 39, 0.8);
    
    --glass-bg: rgba(17, 24, 39, 0.6);
    --glass-border: rgba(99, 102, 241, 0.1);
}
```

---

## 六、执行清单

### P0（立即做）
- [ ] 创建 `chat.html`（AI 对话页）
- [ ] 修改 `common.js` NAV_ITEMS（chat 置顶）
- [ ] 修改 `app.py` 根路由指向 chat.html
- [ ] 实现「发送到编辑器」功能（localStorage 传递）

### P1（本周做）
- [ ] 简化 `editor.html`（删除 AI 对话部分）
- [ ] 添加悬浮动画 CSS
- [ ] 实现科技感配色
- [ ] 添加快捷选题卡片

### P2（下周做）
- [ ] 优化对话 UI（Markdown 渲染、代码高亮）
- [ ] 添加对话历史保存
- [ ] 添加页面切换动画

---

*文档版本：v2*
*更新时间：2026-07-01*
*核心改动：AI 工作区拆分为「AI 对话」+「编辑器」*
