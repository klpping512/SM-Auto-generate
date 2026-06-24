# SA-LogiFlow - 南非物流社交媒体自动化营销工具

AI 驱动的物流内容自动发布系统，支持一键生成多平台内容并自动发布。

## 功能特性

- **AI 内容生成** - DeepSeek API 驱动，6大物流主题 × 48个子话题
- **多平台发布** - 支持小红书、抖音、TikTok、Facebook、Twitter、Reddit 等
- **慧媒集成** - 通过 [慧媒](https://huimei.smaroot.tech) CLI 实现国内平台自动发布
- **发布队列** - 队列管理、定时发布、状态追踪
- **数据看板** - 实时统计、平台分布、发布趋势

## 技术栈

- **后端**: Python FastAPI + SQLite
- **前端**: 纯 HTML + TailwindCSS + ECharts
- **AI**: DeepSeek API
- **发布**: 慧媒 CLI (huimei)

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt
pip install huimei

# 启动服务
DEEPSEEK_API_KEY=your_key python3 app.py
```

访问 http://localhost:8080

## 项目结构

```
├── app.py              # FastAPI 后端
├── ai_engine.py        # DeepSeek AI 内容生成
├── publisher.py        # 慧媒发布封装
├── topic_library.py    # 南非物流主题库
├── database.py         # SQLite 数据库
├── models.py           # 数据模型
├── static/             # 前端页面
│   ├── home.html       # 分发总览
│   ├── editor.html     # 内容创作
│   ├── queue.html      # 发布队列
│   ├── accounts.html   # 账号管理
│   └── config.html     # 平台配置
└── requirements.txt
```

## 平台支持

| 平台 | 方式 | 状态 |
|------|------|------|
| 小红书 | 慧媒 CLI | ✅ |
| 抖音 | 慧媒 CLI | ✅ |
| TikTok | 慧媒 CLI | ✅ |
| Facebook | Graph API | 待接入 |
| Twitter | API v2 | 待接入 |
| Reddit | PRAW | 待接入 |

## License

MIT
