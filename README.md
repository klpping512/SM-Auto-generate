# SA-LogiFlow v3.0 - 社媒内容自动生成发布工具

AI 驱动的物流内容自动发布系统，支持一键生成多平台内容并自动发布。

## 功能特性

- **AI 内容生成** - Xiaomi MiMo v2.5 驱动，支持文本生成及多模态素材理解
- **小红书轮播图** - 自动生成 3:4 品牌卡片并支持全屏翻页预览
- **抖音视频成片** - 素材分镜、MiMo TTS、字幕与 FFmpeg 竖屏 MP4 合成
- **多平台发布** - 支持小红书、抖音、TikTok、Facebook、Twitter、Reddit 等
- **慧媒集成** - 通过 [慧媒](https://huimei.smaroot.tech) CLI 实现国内平台自动发布
- **发布队列** - 队列管理、定时发布、状态追踪
- **数据看板** - 实时统计、平台分布、发布趋势

## 技术栈

- **后端**: Python FastAPI + SQLite
- **前端**: 纯 HTML + TailwindCSS + ECharts
- **AI**: Xiaomi MiMo v2.5 / MiMo v2.5 TTS
- **发布**: 慧媒 CLI (huimei)

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt
pip install huimei

# 启动服务
MIMO_API_KEY=your_key python3 app.py
```

访问 http://localhost:8080

## 项目结构

```
├── app.py              # FastAPI 后端
├── ai_engine.py        # MiMo v2.5 内容与分镜生成
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
