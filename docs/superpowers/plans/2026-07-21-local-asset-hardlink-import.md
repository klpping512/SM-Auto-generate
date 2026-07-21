# Local Asset Hardlink Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将桌面 262 个图片和视频以硬链接接入本地素材库，提供持久化进度、取消和中断状态，并让视频匹配暂停原因可见。

**Architecture:** 新增独立 `local_asset_import.py` 负责受信任路径校验、文件发现和单文件硬链接入库；SQLite 保存批次状态，FastAPI 后台协程逐文件调用并复用现有素材分析队列。前端只轮询持久化任务，不以页面内计时器推测状态；IOL 只登记为受限发现链接。

**Tech Stack:** Python 3.12、FastAPI、SQLite、asyncio、原生 JavaScript、FFmpeg/ffprobe、pytest。

---

### Task 1: 硬链接入库原语

**Files:**
- Modify: `media_assets.py`
- Create: `local_asset_import.py`
- Create: `tests/test_local_asset_import.py`

- [ ] **Step 1: 写失败测试**

```python
def test_ingest_file_hardlinks_without_copy(tmp_path, tmp_db):
    from PIL import Image
    import media_assets
    source = tmp_path / "source" / "仓库照片.jpg"
    source.parent.mkdir()
    Image.new("RGB", (32, 24), "white").save(source)
    static_dir = tmp_path / "static"
    asset = media_assets.ingest_file(
        source, static_dir, category="warehouse", origin="local_directory",
        created_by=1, storage_mode="hardlink",
    )
    stored = static_dir / asset["filepath"]
    assert stored.stat().st_ino == source.stat().st_ino
    assert source.exists()


def test_resolve_local_root_rejects_path_outside_configured_root(tmp_path):
    import pytest
    from local_asset_import import resolve_source_path
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(ValueError, match="受信任素材目录"):
        resolve_source_path(tmp_path / "outside.jpg", allowed)
```

- [ ] **Step 2: 运行红灯测试**

Run: `pytest -q tests/test_local_asset_import.py`
Expected: FAIL，`storage_mode` 和 `local_asset_import` 尚不存在。

- [ ] **Step 3: 实现最小硬链接能力**

在 `media_assets.ingest_file` 增加 `storage_mode: str = "copy"`，正式存储分支限定为：

```python
if storage_mode == "hardlink":
    os.link(source, stored)
elif storage_mode == "copy":
    shutil.copy2(source, stored)
else:
    raise ValueError("不支持的素材存储方式")
```

新增 `local_asset_import.py`：

```python
from pathlib import Path
import media_assets

SUPPORTED_EXTS = media_assets.IMAGE_EXTS | media_assets.VIDEO_EXTS


def configured_root() -> Path:
    import os
    value = os.environ.get("LOCAL_ASSET_ROOT", "").strip()
    return Path(value).expanduser().resolve() if value else (Path.home() / "Desktop" / "视频&图片素材").resolve()


def resolve_source_path(path: Path, root: Path) -> Path:
    source, allowed = path.resolve(), root.resolve()
    if source == allowed or allowed not in source.parents:
        raise ValueError("文件不在受信任素材目录内")
    if not source.is_file():
        raise ValueError("素材文件不存在")
    return source


def discover(root: Path) -> tuple[list[Path], list[Path]]:
    files = sorted(path for path in root.rglob("*") if path.is_file() and not path.name.startswith("."))
    return ([path for path in files if path.suffix.lower() in SUPPORTED_EXTS],
            [path for path in files if path.suffix.lower() not in SUPPORTED_EXTS])


def ingest_one(path: Path, root: Path, static_dir: Path, user_id: int) -> dict:
    source = resolve_source_path(path, root)
    return media_assets.ingest_file(
        source, static_dir, category="auto", origin="local_directory",
        created_by=user_id, import_root=root, storage_mode="hardlink",
    )
```

- [ ] **Step 4: 运行绿灯测试**

Run: `pytest -q tests/test_local_asset_import.py tests/test_media_api.py`
Expected: PASS。

### Task 2: 持久化导入批次

**Files:**
- Modify: `database.py`
- Modify: `tests/test_local_asset_import.py`

- [ ] **Step 1: 写失败的数据库行为测试**

```python
def test_local_import_job_is_idempotent_and_cancelable(tmp_db, tmp_path):
    root = str(tmp_path.resolve())
    first, created = tmp_db.create_or_get_local_asset_import_job(root, 1)
    second, created_again = tmp_db.create_or_get_local_asset_import_job(root, 1)
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]
    canceled = tmp_db.request_local_asset_import_cancel(first["id"], 1)
    assert canceled["status"] == "cancel_requested"
```

- [ ] **Step 2: 运行红灯测试**

Run: `pytest -q tests/test_local_asset_import.py::test_local_import_job_is_idempotent_and_cancelable`
Expected: FAIL，数据库函数不存在。

- [ ] **Step 3: 新增表和仓储函数**

新增 `local_asset_import_jobs` 表，字段为：`id,root_path,status,stage,total,scanned,imported,duplicated,skipped,failed,current_file,cancel_requested,errors,requested_by,created_at,started_at,updated_at,finished_at`。新增：

```python
create_or_get_local_asset_import_job(root_path: str, requested_by: int) -> tuple[dict, bool]
get_local_asset_import_job(job_id: str, requested_by: int | None = None) -> dict | None
update_local_asset_import_job(job_id: str, **fields) -> dict | None
request_local_asset_import_cancel(job_id: str, requested_by: int) -> dict | None
list_active_local_asset_import_jobs(requested_by: int) -> list[dict]
recover_interrupted_local_asset_import_jobs() -> int
```

活动状态限定为 `pending,scanning,importing,processing,cancel_requested`；同一用户和根目录只复用活动任务。

- [ ] **Step 4: 运行数据库测试**

Run: `pytest -q tests/test_local_asset_import.py tests/test_semantic_assets_db.py`
Expected: PASS。

### Task 3: 后台导入、进度和取消

**Files:**
- Modify: `app.py`
- Modify: `models.py`
- Modify: `tests/test_local_asset_import.py`

- [ ] **Step 1: 写失败的 API 测试**

```python
def test_local_import_api_returns_immediately_and_reports_progress(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ASSET_ROOT", str(tmp_path))
    response = client.post("/api/assets/local-imports", headers=auth_headers)
    assert response.status_code == 202
    job_id = response.json()["job"]["id"]
    detail = client.get(f"/api/assets/local-imports/{job_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["status"] in {"pending", "scanning", "importing", "processing", "succeeded"}
```

覆盖：管理员权限、活动任务复用、路径不存在、单文件失败继续、HEIC 计入跳过、取消、启动恢复。

- [ ] **Step 2: 运行红灯测试**

Run: `pytest -q tests/test_local_asset_import.py`
Expected: FAIL，新 API 返回 404。

- [ ] **Step 3: 实现后台任务和 API**

新增接口：

```text
POST /api/assets/local-imports
GET  /api/assets/local-imports/active
GET  /api/assets/local-imports/{job_id}
POST /api/assets/local-imports/{job_id}/cancel
```

后台循环按文件执行：更新 `current_file` → `asyncio.to_thread(ingest_one)` → 增加 imported/duplicated/failed → 新素材创建 `asset_processing_job` 并用现有并发上限 2 调度。每个文件结束检查 `cancel_requested`。生命周期启动时调用 `recover_interrupted_local_asset_import_jobs()`。

- [ ] **Step 4: 运行绿灯测试**

Run: `pytest -q tests/test_local_asset_import.py tests/test_media_api.py tests/test_semantic_assets_db.py`
Expected: PASS。

### Task 4: 素材库进度与取消界面

**Files:**
- Modify: `static/assets.html`
- Create: `tests/test_local_asset_import_ui.py`

- [ ] **Step 1: 写失败的前端契约测试**

```python
def test_assets_page_has_persistent_local_import_controls():
    page = Path("static/assets.html").read_text(encoding="utf-8")
    assert "/api/assets/local-imports" in page
    assert "取消导入" in page
    assert "current_file" in page
    assert "本地服务已断开" in page
    assert "已入库" in page and "已分析" in page
```

- [ ] **Step 2: 运行红灯测试**

Run: `pytest -q tests/test_local_asset_import_ui.py`
Expected: FAIL，页面没有持久化任务控件。

- [ ] **Step 3: 实现页面行为**

页面初始化读取 `/api/assets/local-imports/active`；导入按钮创建任务；活动时每 1500ms 获取详情；显示总数、已扫描、入库、重复、跳过、失败、当前文件和分析任务汇总；取消按钮调用 cancel API。连续三次轮询失败后停止轮询并显示“本地服务已断开，请重启 8080 后刷新”。

- [ ] **Step 4: 验证前端**

Run: `pytest -q tests/test_local_asset_import_ui.py && node --check static/assets.html`
Expected: pytest PASS；若 Node 不直接解析 HTML，则提取页面脚本后执行 `node --check`。

### Task 5: 视频第三步暂停说明

**Files:**
- Modify: `static/common.js`
- Modify: `static/video-project.html`
- Modify: `tests/test_video_generation_ui.py`

- [ ] **Step 1: 写失败测试**

```python
def test_needs_review_is_not_rendered_as_active_generation():
    common = Path("static/common.js").read_text(encoding="utf-8")
    project = Path("static/video-project.html").read_text(encoding="utf-8")
    assert "匹配质量不足，等待人工处理" in common + project
    assert "本地服务已断开" in common + project
    assert "gate.issues" in project or "gate?.issues" in project
```

- [ ] **Step 2: 运行红灯测试**

Run: `pytest -q tests/test_video_generation_ui.py`
Expected: 新断言失败。

- [ ] **Step 3: 实现明确状态**

`needs_review + match_quality_check` 显示固定暂停文案、最低分和 `quality_report.gate.issues`；不继续显示旋转进度。连续三次任务轮询失败则显示服务断开并停止轮询；用户刷新或点击重试后恢复请求。

- [ ] **Step 4: 运行 UI 回归**

Run: `pytest -q tests/test_video_generation_ui.py tests/test_video_workbench_ui.py`
Expected: PASS。

### Task 6: IOL 受限发现链接

**Files:**
- Modify: `docs/superpowers/specs/2026-07-21-sample-first-evidence-harness-design.md`
- Data: local `inspiration_items` record
- Test: `tests/test_inspiration_api.py`

- [ ] **Step 1: 写来源边界测试**

```python
def test_iol_discovery_link_cannot_be_materialized_without_rights(client, auth_headers):
    created = client.post("/api/inspirations", headers=auth_headers, json={
        "url": "https://iol.co.za/news/south-africa/", "platform": "website",
        "title": "IOL South Africa News", "rights_status": "restricted"
    })
    item_id = created.json()["id"]
    response = client.post(f"/api/inspirations/{item_id}/materialize", headers=auth_headers,
                           json={"confirmed": False})
    assert response.status_code == 409
```

- [ ] **Step 2: 运行测试并确认现有版权门禁行为**

Run: `pytest -q tests/test_inspiration_api.py`
Expected: 若现有门禁已覆盖则 PASS；若未覆盖则测试失败并只补最小版权校验。

- [ ] **Step 3: 更新设计并登记链接**

将 IOL 标为 `secondary_discovery`、`rights_status=restricted`，只保存 `https://iol.co.za/news/south-africa/`。不创建 `hotspot_sources` 自动抓取记录，不下载媒体。

- [ ] **Step 4: 运行灵感来源回归**

Run: `pytest -q tests/test_inspiration_api.py tests/test_inspiration_assets.py tests/test_truth_guard.py`
Expected: PASS。

### Task 7: 本地真实导入与验收

**Files:**
- Modify: `docs/功能介绍.md`
- Modify: Obsidian `改进日志.md`

- [ ] **Step 1: 运行自动化测试**

Run: `pytest -q tests/test_local_asset_import.py tests/test_local_asset_import_ui.py tests/test_media_api.py tests/test_asset_processing.py tests/test_semantic_matching.py tests/test_video_generation_ui.py tests/test_inspiration_api.py`
Expected: PASS。

- [ ] **Step 2: 启动原本地端口**

Run: `./start.sh`
Expected: `http://127.0.0.1:8080` 返回 200，未启动第二端口。

- [ ] **Step 3: 创建真实导入任务并轮询**

通过管理员登录后创建任务，确认 262 个文件全部进入 imported、duplicated、skipped、failed 之一；确认 1 个 HEIC 计入 skipped；抽样比较源文件与应用文件 inode。

- [ ] **Step 4: 浏览器验收**

在素材库观察进度、取消一个测试批次、恢复真实批次；在视频项目页确认旧任务显示“匹配质量不足，等待人工处理”，新素材分析完成后重新匹配。

- [ ] **Step 5: 同步文档和提交**

先同步 `docs/` 和 Obsidian 改进日志，再只暂存本次涉及文件提交；不推送远程、不部署服务器。
