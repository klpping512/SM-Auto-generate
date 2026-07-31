# 双语热点、双素材库与热点视频实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不迁移现有素材文件、不新增第二套视频处理系统的前提下，完成热点双语缓存、热点媒体候选、已授权视频素材化，以及按分镜职责执行的双库 Top 3 匹配。

**Architecture:** 新增独立 `hotspot_media` 数据表保存图片与视频候选，黄色和红色候选不提前写入正式 `assets`。媒体发现由纯本地 HTML 解析器完成；已授权视频下载后复用 `media_assets`、`asset_processing` 和 `asset_segments`。`sample_harness` 在分镜职责层选择热点库或原本库，保持事实画面与品牌证明隔离。

**Tech Stack:** FastAPI、SQLite、Pydantic、httpx、BeautifulSoup、yt-dlp、ffprobe/FFmpeg、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 热点双语与热点媒体数据模型

**Files:**
- Modify: `database.py`
- Test: `tests/test_hotspot_media_db.py`

- [ ] **Step 1: 写失败测试**

```python
def test_hotspot_translation_cache_and_media_round_trip(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot(make_hotspot())
    tmp_db.update_hotspot_translation(
        hotspot_id, "南非港口动态", "中文摘要", "sha-1", "mimo-v2.5"
    )
    translated = tmp_db.get_hotspot(hotspot_id)
    assert translated["title_zh"] == "南非港口动态"
    media_id, created = tmp_db.upsert_hotspot_media({
        "hotspot_id": hotspot_id,
        "media_kind": "video_link",
        "platform": "youtube",
        "source_page_url": translated["source_url"],
        "original_media_url": "https://www.youtube.com/watch?v=abc123def45",
        "rights_tier": "yellow",
    })
    assert created is True
    assert tmp_db.get_hotspot_media(media_id)["download_status"] == "metadata_ready"
```

- [ ] **Step 2: 运行并确认因缺少表与函数失败**

Run: `pytest -q tests/test_hotspot_media_db.py`
Expected: FAIL，提示 `update_hotspot_translation` 或 `upsert_hotspot_media` 不存在。

- [ ] **Step 3: 最小实现**

在 `hotspots` 增加 `title_zh`、`summary_zh`、`translation_status`、`translation_snapshot_sha256`、`translated_at`、`translation_model`；创建 `hotspot_media`，为 `(hotspot_id, original_media_url)` 建唯一索引。增加查询、筛选、翻译缓存更新、权利更新和下载/处理状态更新函数。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_media_db.py tests/test_semantic_assets_db.py`
Expected: PASS。

### Task 2: 新闻页视频发现与单条链接规范化

**Files:**
- Create: `hotspot_media.py`
- Test: `tests/test_hotspot_media_discovery.py`

- [ ] **Step 1: 写失败测试**

```python
def test_discover_video_candidates_deduplicates_html_sources():
    html = '''
    <meta property="og:video" content="https://cdn.example.org/news.mp4">
    <video><source src="https://cdn.example.org/news.mp4"></video>
    <iframe src="https://www.youtube.com/embed/abc123def45"></iframe>
    <script type="application/ld+json">{
      "@type":"VideoObject","contentUrl":"https://cdn.example.org/second.mp4"
    }</script>
    '''
    items = discover_media_candidates(html, "https://news.example.org/story")
    assert [item["platform"] for item in items if item["media_kind"] == "video_link"] == [
        "direct", "youtube", "direct"
    ]
```

- [ ] **Step 2: 运行并确认导入失败**

Run: `pytest -q tests/test_hotspot_media_discovery.py`
Expected: FAIL，提示 `hotspot_media` 不存在。

- [ ] **Step 3: 最小实现**

实现三个固定公共接口：`discover_media_candidates(html: str, source_page_url: str) -> list[dict]`、`normalize_video_url(url: str, base_url: str | None = None) -> tuple[str, str, str | None]`、`validate_single_video_url(url: str) -> str`。

仅识别公开 HTTPS 的 `og:image`、`og:video`、`video/source`、JSON-LD `VideoObject` 和 YouTube iframe；拒绝 localhost、内网 IP、YouTube 频道、播放列表和非 HTTPS 链接。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_media_discovery.py tests/test_inspiration_assets.py`
Expected: PASS。

### Task 3: 双语与热点媒体 API

**Files:**
- Modify: `models.py`
- Modify: `app.py`
- Test: `tests/test_hotspot_media_api.py`

- [ ] **Step 1: 写失败 API 测试**

覆盖：按需翻译复用缓存；媒体列表筛选；新闻页发现；单视频绑定；非管理员不能改权利；黄色未确认不能下载；红色始终不能素材化。

- [ ] **Step 2: 运行并确认路由 404**

Run: `pytest -q tests/test_hotspot_media_api.py`
Expected: FAIL，目标 API 返回 404。

- [ ] **Step 3: 最小实现路由**

实现：

```text
POST /api/hotspots/{id}/translate
GET  /api/hotspot-media
POST /api/hotspots/{id}/media/discover
POST /api/hotspots/{id}/media/attach
PUT  /api/hotspot-media/{id}/rights
POST /api/hotspot-media/{id}/materialize
```

翻译调用现有 `model_router` 的 `planner_text`，缓存键含热点快照哈希；发现接口使用已有 SSRF 域名检查和 8 秒超时。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_media_api.py tests/test_inspiration_api.py`
Expected: PASS。

### Task 4: 已授权热点视频素材化与现有分析链路复用

**Files:**
- Modify: `hotspot_media.py`
- Modify: `app.py`
- Test: `tests/test_hotspot_video_materialization.py`

- [ ] **Step 1: 写失败测试**

覆盖 `green`/已确认 `yellow` 可下载，未确认黄色和红色拒绝；下载选项必须含 `noplaylist=True`、无 Cookie、MP4 合并、文件大小限制；成功后写入 `assets.hotspot_id` 并创建现有素材处理任务。

- [ ] **Step 2: 运行并确认缺少素材化函数**

Run: `pytest -q tests/test_hotspot_video_materialization.py`
Expected: FAIL。

- [ ] **Step 3: 最小实现**

复用 `inspiration_assets.download_authorized_media` 的下载约束和 `media_assets.ingest_file`。下载成功后调用 `update_asset_provenance`、`create_asset_processing_job` 和 `_run_asset_processing_job`；`hotspot_media.asset_id` 指向正式资产，状态转为 `ready`。任何异常只更新该候选的错误状态，不影响热点或其他素材。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_video_materialization.py tests/test_asset_processing.py`
Expected: PASS。

### Task 5: 内容资产页双库界面与热点视频卡片

**Files:**
- Modify: `static/assets.html`
- Modify: `static/design-system.css`
- Test: `tests/test_hotspot_media_ui.py`

- [ ] **Step 1: 写失败静态契约测试**

断言页面包含“原本素材库”“热点素材库”、热点/媒体类型/权利筛选、视频时长、权利确认、来源链接、下载与处理状态，并且未确认视频的应用按钮由状态判断禁用。

- [ ] **Step 2: 运行并确认文案和函数缺失**

Run: `pytest -q tests/test_hotspot_media_ui.py`
Expected: FAIL。

- [ ] **Step 3: 最小实现界面**

保留原本素材库现有 DOM 与事件函数；新增热点素材视图和按需 API 请求，不复制资产列表。视频卡片用现有缩略图或统一 SVG 视频占位，不使用 emoji 图标。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_media_ui.py tests/test_assets_header_layout.py tests/test_local_asset_import_ui.py`
Expected: PASS。

### Task 6: 分镜职责与双库 Top 3

**Files:**
- Modify: `semantic_matching.py`
- Modify: `sample_harness.py`
- Modify: `static/hotspots.html`
- Test: `tests/test_dual_library_matching.py`
- Test: `tests/test_hotspot_workbench_ui.py`

- [ ] **Step 1: 写失败匹配测试**

覆盖：`hotspot_hook`/`fact_context` 只返回当前热点素材；`brand_proof`/`brand_close` 只返回自有素材；视频以 `segment_id/start_ms/end_ms` 输出；同一视频最多占一个 Top 3；未确认权利不返回。

- [ ] **Step 2: 运行并确认当前统一召回导致失败**

Run: `pytest -q tests/test_dual_library_matching.py`
Expected: FAIL。

- [ ] **Step 3: 最小实现匹配上下文**

在匹配入口增加 `scene_role`、`hotspot_id`，先做库来源和权利硬过滤，再调用现有评分。输出增加 `library_origin`、`hotspot_id`、`media_kind`、来源和署名。样本时间线默认前三个职责为热点钩子、标题卡、影响解释，后两个职责为品牌证明和品牌收尾，实现约 30/70，而不是混合候选后随机凑比例。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_dual_library_matching.py tests/test_semantic_matching.py tests/test_sample_harness.py tests/test_hotspot_workbench_ui.py`
Expected: PASS。

### Task 7: 完整验证、文档同步与运行说明

**Files:**
- Modify: `docs/功能介绍.md`
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: 运行专项测试**

Run: `pytest -q tests/test_hotspot_media_*.py tests/test_hotspot_video_materialization.py tests/test_dual_library_matching.py`
Expected: 全部通过。

- [ ] **Step 2: 运行关联回归**

Run: `pytest -q tests/test_hotspot_fetcher.py tests/test_hotspot_fetch_runs.py tests/test_inspiration_api.py tests/test_asset_processing.py tests/test_semantic_matching.py tests/test_sample_harness.py tests/test_hotspot_workbench_ui.py`
Expected: 全部通过。

- [ ] **Step 3: 静态与语法验证**

Run: `python3 -m py_compile app.py database.py models.py hotspot_media.py semantic_matching.py sample_harness.py`
Expected: exit 0。

Run: `git diff --check`
Expected: exit 0。

- [ ] **Step 4: 本地浏览器验收**

在 `http://localhost:8080/assets.html` 验证双库切换、视频链接绑定、权利门禁和处理状态；在 `http://localhost:8080/hotspots.html` 验证双语显示、候选数量和 Top 3 来源。

- [ ] **Step 5: 同步文档与改进日志**

将设计、计划和功能说明复制到 Obsidian 项目目录；日志标题使用精确 `YYYY-MM-DD HH:mm:ss CST`，记录测试、Git 和未完成事项，不写入密钥。
