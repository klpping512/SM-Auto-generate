# Hotspot Asset Lifecycle and Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible hotspot dates, adjacent hotspot/assets navigation, safe low-disk retention previews, and scheduled cleanup without deleting Buffalo-owned library assets.

**Architecture:** Keep the existing FastAPI, SQLite, static HTML and APScheduler structure. Add lifecycle columns through the existing idempotent migration helpers, isolate retention rules in `media_retention.py`, expose a small admin preview API, and render the new controls inside the existing hotspot asset subview.

**Tech Stack:** Python 3.12, FastAPI, SQLite, APScheduler, vanilla HTML/CSS/JavaScript, pytest.

---

### Task 1: Navigation and hotspot date presentation

**Files:**
- Modify: `tests/test_hotspot_workbench_ui.py`
- Modify: `tests/test_hotspot_media_ui.py`
- Modify: `static/common.js`
- Modify: `static/hotspots.html`
- Modify: `static/assets.html`

- [ ] **Step 1: Write failing navigation and date tests**

Add assertions that `内容资产` appears between `热点选题` and `内容编辑器`, that `hotspots.html` restores `hotspot_id` from the URL, and that hotspot cards render `发布时间`、`抓取时间`、`入库时间` plus the five freshness options.

```python
def test_sidebar_places_assets_next_to_hotspots():
    page = COMMON_JS.read_text(encoding="utf-8")
    assert page.index("热点选题") < page.index("内容资产") < page.index("内容编辑器")


def test_hotspot_media_cards_show_dates_and_freshness_filters():
    page = ASSETS_HTML.read_text(encoding="utf-8")
    for text in ("发布时间", "抓取时间", "入库时间", "24 小时", "3 天", "7 天", "30 天", "已归档"):
        assert text in page
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest -q tests/test_hotspot_workbench_ui.py tests/test_hotspot_media_ui.py
```

Expected: failures for navigation order, missing URL restoration, and missing date/freshness labels.

- [ ] **Step 3: Implement the minimal UI behavior**

Move the existing assets nav item into the core group. Add shared date helpers and a freshness selector in `assets.html`; display dates using `published_at`, `created_at`, and `confirmed_at`. Parse `hotspot_id` in `hotspots.html` and add a return link in hotspot asset view.

```javascript
function formatHotspotDate(value) {
    if (!value) return '未记录';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
}
```

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 pytest command. Expected: all selected tests pass.

### Task 2: Lifecycle schema, filtering and reference protection

**Files:**
- Create: `tests/test_media_retention.py`
- Modify: `tests/test_hotspot_media_db.py`
- Modify: `database.py`

- [ ] **Step 1: Write failing database tests**

Test idempotent lifecycle columns, default `permanent` retention for existing assets, archived hotspot filtering, and conservative reference reporting across legacy render scripts, video revisions, sample manifests, queue attachments, hotspots and inspiration items.

```python
def test_unknown_or_active_reference_blocks_asset_cleanup(tmp_db):
    asset_id = seed_asset(retention_class="hotspot_source")
    assert db.asset_reference_reasons(asset_id) == []
    seed_active_video_revision(asset_id)
    assert "video_project_revision" in db.asset_reference_reasons(asset_id)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
pytest -q tests/test_media_retention.py tests/test_hotspot_media_db.py
```

Expected: missing lifecycle columns and helper functions.

- [ ] **Step 3: Add minimal schema and helpers**

Use `_ensure_column` for:

```python
_ensure_column(conn, "assets", "retention_class", "TEXT NOT NULL DEFAULT 'permanent'")
_ensure_column(conn, "assets", "last_used_at", "TEXT")
_ensure_column(conn, "assets", "pinned_at", "TEXT")
_ensure_column(conn, "assets", "purge_after", "TEXT")
_ensure_column(conn, "assets", "file_status", "TEXT NOT NULL DEFAULT 'available'")
_ensure_column(conn, "assets", "purged_at", "TEXT")
_ensure_column(conn, "hotspot_media", "lifecycle_status", "TEXT NOT NULL DEFAULT 'active'")
_ensure_column(conn, "video_generation_jobs", "output_pinned_at", "TEXT")
_ensure_column(conn, "video_generation_jobs", "output_purged_at", "TEXT")
```

Extend hotspot media listing with `freshness_days` and `lifecycle_status`. Add `asset_reference_reasons(asset_id)` that returns a non-empty list whenever any recognized business record references the asset; malformed JSON returns `unknown_reference` rather than allowing deletion.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 2 pytest command. Expected: all pass.

### Task 3: Retention preview and safe file purging

**Files:**
- Create: `media_retention.py`
- Modify: `tests/test_media_retention.py`
- Modify: `database.py`

- [ ] **Step 1: Write failing service tests**

Cover dry-run, seven-day hotspot retention, thirty-day final-output retention, permanent owned assets, pinned assets, active references, missing files, path traversal, idempotent repeated runs, and byte totals.

```python
def test_dry_run_reports_without_deleting(tmp_path, monkeypatch):
    candidate = seed_hotspot_asset(tmp_path, age_days=8)
    report = media_retention.run_cleanup(static_dir=tmp_path, dry_run=True, now=NOW)
    assert candidate.exists()
    assert report["candidate_count"] == 1
    assert report["estimated_bytes"] == candidate.stat().st_size
    assert report["deleted_count"] == 0
```

- [ ] **Step 2: Run service tests and verify RED**

```bash
pytest -q tests/test_media_retention.py
```

Expected: import or missing-function failure for `media_retention`.

- [ ] **Step 3: Implement the bounded service**

Implement:

```python
def preview_cleanup(static_dir: Path, now: datetime | None = None) -> dict:
    return _cleanup(static_dir=static_dir, dry_run=True, now=now)


def run_cleanup(static_dir: Path, dry_run: bool = True, now: datetime | None = None) -> dict:
    return _cleanup(static_dir=static_dir, dry_run=dry_run, now=now)


def disk_guard(static_dir: Path) -> dict:
    usage = shutil.disk_usage(static_dir)
    free_percent = (usage.free / usage.total * 100) if usage.total else 0
    stop_percent = float(os.environ.get("MEDIA_DISK_STOP_PERCENT", "5"))
    return {
        "free_bytes": usage.free,
        "free_percent": round(free_percent, 2),
        "blocked": free_percent < stop_percent,
    }
```

Only resolve and delete paths below `static_dir`; never hard-delete asset rows. On success mark `file_status='purged'`, clear local paths that would otherwise produce broken previews, record `purged_at`, and append an audit record. Archive unconfirmed hotspot candidates older than 30 days independently from file deletion.

- [ ] **Step 4: Run service tests and verify GREEN**

Run the Task 3 pytest command. Expected: all pass.

### Task 4: Admin preview API, UI controls and download guard

**Files:**
- Modify: `tests/test_media_retention.py`
- Modify: `tests/test_hotspot_media_api.py`
- Modify: `tests/test_hotspot_media_ui.py`
- Modify: `app.py`
- Modify: `static/assets.html`

- [ ] **Step 1: Write failing API/UI tests**

Require admin-only preview and pin endpoints, a compact storage summary, a cleanup preview drawer, and HTTP 507 when remaining disk is below the configured hard threshold.

```python
def test_cleanup_preview_is_admin_only(client, admin_headers, editor_headers):
    assert client.get("/api/media-retention/preview", headers=editor_headers).status_code == 403
    assert client.get("/api/media-retention/preview", headers=admin_headers).status_code == 200
```

- [ ] **Step 2: Run tests and verify RED**

```bash
pytest -q tests/test_media_retention.py tests/test_hotspot_media_api.py tests/test_hotspot_media_ui.py
```

Expected: 404 for the preview endpoint and missing UI controls.

- [ ] **Step 3: Implement endpoints and UI**

Add:

```text
GET  /api/media-retention/preview
POST /api/assets/{asset_id}/pin
POST /api/assets/{asset_id}/unpin
POST /api/video-generation/jobs/{job_id}/output-pin
POST /api/video-generation/jobs/{job_id}/output-unpin
```

Before hotspot materialization or new video generation, call `disk_guard`; return a clear 507 response only below the hard threshold. Render preview data in an existing-style drawer without adding a new navigation page.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 4 pytest command. Expected: all pass.

### Task 5: Scheduler, deployment settings and startup portability

**Files:**
- Modify: `tests/test_media_retention.py`
- Modify: `scheduler.py`
- Modify: `.env.example`
- Modify: `start.sh`
- Modify: `docs/产研部署交接说明.md`

- [ ] **Step 1: Write failing scheduling/config tests**

Assert a daily `media_retention_cleanup` job exists, defaults to dry-run, and `start.sh` changes to its own directory rather than `~/Desktop/distribution-manager`.

- [ ] **Step 2: Run tests and verify RED**

```bash
pytest -q tests/test_media_retention.py
```

Expected: missing scheduled job/config contract.

- [ ] **Step 3: Add scheduler and documented settings**

Schedule one daily job with `max_instances=1` and `coalesce=True`. Add the approved defaults:

```dotenv
MEDIA_CLEANUP_ENABLED=1
MEDIA_CLEANUP_DRY_RUN=1
HOTSPOT_SOURCE_RETENTION_DAYS=7
FINAL_VIDEO_RETENTION_DAYS=30
INTERMEDIATE_RETENTION_DAYS=7
FAILED_TEMP_RETENTION_HOURS=24
MEDIA_DISK_WARN_PERCENT=15
MEDIA_DISK_STOP_PERCENT=5
LOCAL_ASSET_ROOT=
```

Use `cd "$(dirname "$0")"` semantics in `start.sh` and document switching dry-run off only after reviewing reports.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 5 pytest command. Expected: all pass.

### Task 6: Regression and browser verification

**Files:**
- Modify: `docs/superpowers/plans/2026-07-23-hotspot-asset-lifecycle-navigation.md`
- Update externally: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: Run focused regression**

```bash
pytest -q tests/test_media_retention.py tests/test_hotspot_media_db.py tests/test_hotspot_media_api.py tests/test_hotspot_media_ui.py tests/test_hotspot_workbench_ui.py tests/test_video_generation_api.py tests/test_video_generation_db.py
```

Expected: all pass.

- [ ] **Step 2: Run full collection and test suite**

```bash
pytest --collect-only -q
pytest -q
```

Expected: collection and suite pass. If the existing `_oauth1_header` collection failure remains, report it explicitly and do not claim a fully green suite.

- [ ] **Step 3: Validate local 8080 without destructive cleanup**

Use the existing single 8080 process. Verify navigation order, URL round-trip, date display, freshness filtering and dry-run preview. Do not enable real deletion or click destructive controls.

- [ ] **Step 4: Sync documentation and record exact verification**

Synchronize the implementation plan/deployment doc to Obsidian and append the required `YYYY-MM-DD HH:mm:ss CST` log entry before any implementation commit.
