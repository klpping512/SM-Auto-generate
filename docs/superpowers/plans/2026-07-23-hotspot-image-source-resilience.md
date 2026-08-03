# 热点图片来源与预览韧性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让热点图片正确打开文章来源，优先采集可用原图，并在外站图片失效时提供清晰回退而不是破图。

**Architecture:** 图片发现仍由 `hotspot_media.py` 负责，但增加 WordPress 原图选择和显式发现时的轻量可用性检查；列表 API 在素材化成功后提供本地缩略图。`assets.html` 只消费后端字段，文章来源与图片文件分离，图片失败用同卡片占位，不在页面加载时代理或批量探测外站。

**Tech Stack:** FastAPI、httpx、BeautifulSoup、SQLite、原生 HTML/CSS/JavaScript、pytest

---

### Task 1: 图片原图选择与可用性检查

**Files:**
- Modify: `hotspot_media.py`
- Modify: `app.py`
- Test: `tests/test_hotspot_media_discovery.py`

- [x] **Step 1: 写失败测试**

新增两个测试：正文图片同时具有 `src`、`srcset` 和 `data-orig-file` 时必须选择 `data-orig-file`；`filter_reachable_image_candidates` 必须保留视频和 2xx 图片，过滤 502、非图片 MIME 与异常地址。

- [x] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_hotspot_media_discovery.py -k "original_image or reachable"`

Expected: FAIL，因为现有实现选择 `src` 且没有候选可用性过滤函数。

- [x] **Step 3: 最小实现**

实现 `_preferred_article_image(tag, base_url)`：按 `data-orig-file`、最大 `srcset`、懒加载字段和 `src` 的顺序返回公开 HTTPS URL。实现异步 `filter_reachable_image_candidates`，使用 `HEAD`，遇到 405/501 时用带 Range 的 `GET` 回退，只接受允许的图片 MIME；在 `discover_hotspot_media` 中调用并返回 `skipped_unavailable`。

- [x] **Step 4: 运行测试**

Run: `pytest -q tests/test_hotspot_media_discovery.py tests/test_hotspot_media_api.py`

Expected: 全部通过。

### Task 2: 正确打开原文与破图回退

**Files:**
- Modify: `static/assets.html`
- Test: `tests/test_hotspot_media_ui.py`

- [x] **Step 1: 写失败测试**

断言热点卡片使用 `item.source_page_url||item.original_media_url` 作为“查看原文”链接，图片包含 `handleHotspotImageError`，失败占位文案为“预览暂不可用”，不再把图片直链显示为“打开来源”。

- [x] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_hotspot_media_ui.py -k "source_page or preview_fallback"`

Expected: FAIL，因为卡片仍使用 `original_media_url` 且无图片错误处理。

- [x] **Step 3: 最小实现**

卡片链接改为文章来源优先；图片 `onerror` 调用集中函数，隐藏失败图片并显示 SVG 图标与“预览暂不可用”。视频仍使用原缩略图与原单视频链接。

- [x] **Step 4: 运行测试**

Run: `pytest -q tests/test_hotspot_media_ui.py tests/test_hotspot_workbench_ui.py`

Expected: 全部通过。

### Task 3: 素材化后优先本地预览与验收

**Files:**
- Modify: `app.py`
- Test: `tests/test_hotspot_media_api.py`
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [x] **Step 1: 写失败测试**

素材候选存在 `asset_id` 时，列表接口必须返回素材库的本地 `thumbnail_url` 或本地文件 URL 作为 `preview_url`；未素材化时回退外部 `thumbnail_url`。

- [x] **Step 2: 最小实现并回归**

在列表响应映射中读取 `db.get_asset`，生成 `preview_url`，不覆盖原始来源字段。运行：

`pytest -q tests/test_hotspot_media_ui.py tests/test_hotspot_media_discovery.py tests/test_hotspot_media_api.py tests/test_hotspot_video_materialization.py tests/test_hotspot_image_materialization.py`

Expected: 全部通过。

- [x] **Step 3: 本地验证与记录**

重启原 8080 服务；后台浏览器确认卡片“查看原文”指向文章页、破图显示明确占位、可用图片正常显示且没有控制台错误。不点击真实下载。执行 `git diff --check`，同步设计、计划和改进日志到 Obsidian；不推送服务器。
