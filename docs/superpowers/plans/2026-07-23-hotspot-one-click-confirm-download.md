# 热点素材单次确认下载实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消热点素材连续权利输入框，让管理员点击一次“确认并下载到库”即可记录来源并进入现有下载分析队列。

**Architecture:** 保留数据库权利字段和旧权利 API 兼容性，但热点素材 UI 不再暴露权利状态筛选或登记表单。现有 `POST /api/hotspot-media/{id}/materialize` 在收到 `confirmed=true` 时自动补充确认记录，再复用当前图片/视频素材化与语义分析任务；已有 `red` 记录继续阻断。

**Tech Stack:** FastAPI、Pydantic、SQLite、原生 HTML/CSS/JavaScript、pytest

---

### Task 1: 后端单次确认并排队下载

**Files:**
- Modify: `app.py`
- Modify: `hotspot_media.py`
- Test: `tests/test_hotspot_video_materialization.py`
- Test: `tests/test_hotspot_image_materialization.py`

- [ ] **Step 1: 写失败测试**

新增接口测试：未确认的黄色图片或视频以 `confirmed=true` 调用素材化接口时返回 `202`，数据库写入 `confirmed_at`、发布方署名、原始页面证据链接，且 `license_name` 保持为空；`confirmed=false` 和已有红色候选仍被拒绝。

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest -q tests/test_hotspot_video_materialization.py tests/test_hotspot_image_materialization.py -k "one_click or guard"`

Expected: 未确认黄色候选仍被“权利证据不完整”拦截，新增测试失败。

- [ ] **Step 3: 最小实现自动确认**

调整 `validate_materialization`：保留管理员、显式 `confirmed=true`、媒体类型与红色阻断，不再要求用户预先填写许可证字段。在素材化接口中，对尚未确认的候选调用 `db.update_hotspot_media_rights`：`rights_tier="yellow"`、内部确认说明、`license_name=None`、署名取 `publisher`/`author`、证据 URL 取 `source_page_url`/`original_media_url`、确认人取当前管理员；重新读取记录后再排队。

- [ ] **Step 4: 运行后端测试**

Run: `pytest -q tests/test_hotspot_video_materialization.py tests/test_hotspot_image_materialization.py tests/test_hotspot_media_api.py`

Expected: 全部通过。

### Task 2: 前端取消输入框并合并动作

**Files:**
- Modify: `static/assets.html`
- Test: `tests/test_hotspot_media_ui.py`

- [ ] **Step 1: 写失败测试**

断言热点素材视图不再包含 `hotspotRightsTier`、`confirmHotspotMediaRights` 和权利字段 `prompt()` 文案；卡片只包含“确认并下载到库”，直接调用 `materializeHotspotMedia`，下载中或已下载时不再显示该按钮。

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest -q tests/test_hotspot_media_ui.py -k one_click`

Expected: FAIL，因为旧页面仍包含多次 `prompt()` 和两个独立按钮。

- [ ] **Step 3: 最小实现单一按钮**

删除权利状态筛选、卡片权利颜色标签和 `confirmHotspotMediaRights`。未下载的图片/视频候选显示一个“确认并下载到库”主按钮；点击后立即提交 `{confirmed:true}`，等待期间由现有 `pending/downloading` 状态禁用重复操作，成功后提示已进入下载分析队列。

- [ ] **Step 4: 运行前端与关联回归**

Run: `pytest -q tests/test_hotspot_media_ui.py tests/test_hotspot_workbench_ui.py tests/test_hotspot_media_api.py tests/test_hotspot_video_materialization.py tests/test_hotspot_image_materialization.py`

Expected: 全部通过。

### Task 3: 本地验收与日志

**Files:**
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: 静态与服务验证**

Run: `git diff --check && lsof -nP -iTCP:8080 -sTCP:LISTEN`

Expected: diff 检查无输出，8080 仅有当前 Python 服务监听。

- [ ] **Step 2: 浏览器验证**

在后台应用内浏览器打开 `http://127.0.0.1:8080/assets.html?library=hotspot`，确认页面没有权利输入弹窗入口、没有权利状态筛选，每个未下载候选只有“确认并下载到库”。不实际下载外部素材，避免未经用户指定选择就产生网络副作用。

- [ ] **Step 3: 同步改进日志**

记录目标、旧流程问题、自动确认字段、测试、浏览器结果、8080 状态、Git 状态和“已有红色仍阻断”的边界；不得写入密钥、Cookie 或代理凭据。
