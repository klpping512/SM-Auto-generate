# 热点媒体采集补齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐新闻正文图片、热点图片素材化、三个 YouTube 频道的视频热点发现，以及视频生成前的双素材库就绪检查。

**Architecture:** 继续使用 `hotspot_media` 作为候选层，图片与视频都经过相同权利门禁后复用现有 `assets` 和 `asset_processing`。YouTube 只用 `yt-dlp --flat-playlist` 发现最近单视频元数据，不下载频道；样本生成保留三种内容，但视频在素材不足时显式阻塞。

**Tech Stack:** FastAPI、SQLite、BeautifulSoup、httpx、yt-dlp、FFmpeg、pytest、原生 HTML/JavaScript。

---

### Task 1: 发现新闻正文图片

**Files:**
- Modify: `hotspot_media.py`
- Test: `tests/test_hotspot_media_discovery.py`

- [ ] **Step 1: 写失败测试**

添加包含正文图片、Logo、头像、广告和重复图的 HTML，断言只返回正文图片且最多 12 张。

- [ ] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_hotspot_media_discovery.py`
Expected: 正文 `<img>` 未出现在候选中。

- [ ] **Step 3: 最小实现**

在正文容器中读取 `src`、`data-src`、`data-lazy-src` 和 `srcset`，过滤非 HTTPS、页头页尾侧栏及带 `logo|icon|avatar|banner|advert|tracking|share` 特征的图片，规范化 URL 后去重并截断到 12 张。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_media_discovery.py tests/test_hotspot_fetcher.py`
Expected: PASS。

### Task 2: 热点图片确认下载和入库

**Files:**
- Modify: `hotspot_media.py`
- Modify: `app.py`
- Modify: `static/assets.html`
- Test: `tests/test_hotspot_image_materialization.py`
- Test: `tests/test_hotspot_media_ui.py`

- [ ] **Step 1: 写失败测试**

覆盖绿色/已确认黄色图片可下载、红色拒绝、MIME 与 10MB 限制、成功后写入 `assets.hotspot_id` 并创建处理任务；页面对已确认图片显示“下载并分析”。

- [ ] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_hotspot_image_materialization.py tests/test_hotspot_media_ui.py`
Expected: 当前校验只接受 `video_link`，图片按钮缺失。

- [ ] **Step 3: 最小实现**

增加 `download_authorized_image(item, static_dir, created_by)`，用无 Cookie 的 `httpx` 下载公开 HTTPS 图片，校验 Content-Type 和大小后调用 `media_assets.ingest_file`、`update_asset_provenance` 与现有处理任务；素材化后台任务按 `media_kind` 分派图片或视频。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_image_materialization.py tests/test_hotspot_video_materialization.py tests/test_hotspot_media_ui.py`
Expected: PASS。

### Task 3: 三个 YouTube 频道视频热点发现

**Files:**
- Create: `hotspot_video_sources.py`
- Modify: `hotspot_fetcher.py`
- Modify: `app.py`
- Modify: `scheduler.py`
- Test: `tests/test_hotspot_video_sources.py`
- Test: `tests/test_hotspot_fetch_runs.py`

- [ ] **Step 1: 写失败测试**

用伪造的 `yt-dlp --flat-playlist` JSON 断言每个频道只取最近 3 条、创建视频热点和黄色 `video_link`，命令不包含 Cookie 或下载选项；单频道失败只记录健康状态。

- [ ] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_hotspot_video_sources.py`
Expected: 模块或频道发现函数不存在。

- [ ] **Step 3: 最小实现**

固定三个频道 URL；子进程参数使用 `--flat-playlist --playlist-end 3 --dump-single-json --no-warnings`。将结果规范化为单视频热点和媒体候选，合并进六小时抓取结果的 `video_new`、`video_updated` 与 `source_health`。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_hotspot_video_sources.py tests/test_hotspot_fetcher.py tests/test_hotspot_fetch_runs.py`
Expected: PASS。

### Task 4: 视频样本素材就绪检查

**Files:**
- Modify: `sample_harness.py`
- Modify: `static/hotspots.html`
- Test: `tests/test_sample_harness.py`
- Test: `tests/test_hotspot_workbench_ui.py`

- [ ] **Step 1: 写失败测试**

无当前热点可用媒体时断言 `video.material_status == "blocked"` 且 `material_gaps` 指出热点素材；同时缺品牌候选时指出原本素材。两库候选齐全时状态为 `ready`。

- [ ] **Step 2: 运行并确认失败**

Run: `pytest -q tests/test_sample_harness.py tests/test_hotspot_workbench_ui.py`
Expected: 当前视频对象没有素材就绪状态。

- [ ] **Step 3: 最小实现**

根据五个职责分镜的候选结果生成 `material_status` 和 `material_gaps`，前端在 blocked 时显示缺口并隐藏可成片暗示；不阻止图文和公众号样本输出。

- [ ] **Step 4: 验证通过**

Run: `pytest -q tests/test_sample_harness.py tests/test_dual_library_matching.py tests/test_hotspot_workbench_ui.py`
Expected: PASS。

### Task 5: 回归、运行与文档同步

**Files:**
- Modify: `docs/功能介绍.md`
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: 运行专项与关联回归**

Run: `pytest -q tests/test_hotspot_*.py tests/test_sample_harness.py tests/test_dual_library_matching.py tests/test_semantic_matching.py tests/test_asset_processing.py`
Expected: 全部通过。

- [ ] **Step 2: 语法与差异检查**

Run: `python3 -m py_compile hotspot_media.py hotspot_video_sources.py hotspot_fetcher.py app.py scheduler.py sample_harness.py`

Run: `git diff --check -- hotspot_media.py hotspot_video_sources.py hotspot_fetcher.py app.py scheduler.py sample_harness.py static/assets.html static/hotspots.html`

Expected: exit 0。

- [ ] **Step 3: 重启并验证 8080**

重启 `com.logiflow.app`，确认 `/assets.html` 与 `/hotspots.html` 返回 200，执行一次视频信源元数据抓取并核对数据库候选数量。

- [ ] **Step 4: 同步记录**

将设计、计划、功能说明同步到 Obsidian；改进日志使用精确 `YYYY-MM-DD HH:mm:ss CST`，记录测试、Git、真实外部抓取和未完成事项，不写密钥。
