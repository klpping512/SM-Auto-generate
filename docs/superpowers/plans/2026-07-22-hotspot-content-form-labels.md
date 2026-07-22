# 热点内容形态标签实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在热点选题列表和当前选题中明确显示“视频、图片、纯文本”形态，并支持按形态筛选。

**Architecture:** 前端一次读取现有 `/api/hotspot-media?limit=1000`，按 `hotspot_id` 建立媒体类型索引；分类函数以视频优先、图片其次、无候选为纯文本。继续复用现有热点与媒体 API，不修改数据库、抓取器、下载门禁和右侧四步生成链路。

**Tech Stack:** 原生 HTML/CSS/JavaScript、FastAPI 现有只读 API、pytest 静态页面契约测试

---

### Task 1: 内容形态分类与筛选

**Files:**
- Modify: `static/hotspots.html`
- Test: `tests/test_hotspot_workbench_ui.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_hotspot_workbench_ui.py` 新增契约测试，断言页面会读取全量热点媒体、包含视频优先的分类函数、四个形式筛选选项，以及“视频、图片、纯文本”标签文案。

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest -q tests/test_hotspot_workbench_ui.py -k content_form`

Expected: FAIL，因为页面尚无全量媒体索引、内容形态筛选和标签渲染函数。

- [ ] **Step 3: 最小实现数据索引与分类**

在初始化、热点抓取完成和轮询完成时加载 `/api/hotspot-media?limit=1000`。新增 `hotspotContentForm(item)`：存在 `video_link` 或 `video_file` 返回 `video`；否则存在 `image` 返回 `image`；否则返回 `text`。

- [ ] **Step 4: 最小实现标签与筛选**

在热点工具栏增加 `mediaTypeFilter` 下拉框；在 `filteredHotspots()` 应用形态过滤；在卡片来源行和右侧当前选题标题处渲染带文字的类型标签，并把原“仅事实链接”改成对应的“视频候选、图片候选、纯文本事实来源”。

- [ ] **Step 5: 运行定向测试**

Run: `pytest -q tests/test_hotspot_workbench_ui.py`

Expected: PASS。

### Task 2: 回归与本地验收

**Files:**
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: 运行热点与媒体相关回归**

Run: `pytest -q tests/test_hotspot_workbench_ui.py tests/test_hotspot_media_ui.py tests/test_hotspot_media_api.py`

Expected: 全部 PASS。

- [ ] **Step 2: 运行静态与 HTTP 验证**

Run: `git diff --check && curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/hotspots.html`

Expected: diff 检查无输出，HTTP 状态为 `200`。

- [ ] **Step 3: 同步项目日志**

按项目规则记录目标、改动前问题、实现、文件、测试、8080 状态、Git 与待人工验证事项；不写入任何密钥、Cookie 或代理凭据。
