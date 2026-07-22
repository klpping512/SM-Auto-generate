# Hotspot Workbench Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将南非热点从内容资产页彻底拆出，建立独立、可解释、可恢复的“热点选题”四步工作台，并让内容资产页恢复为单纯的素材与灵感管理页面。

**Architecture:** 新建静态页面 `static/hotspots.html`，复用现有热点、品牌证据、证据包和样本生成 API。新增一张轻量抓取运行表持久化最近一次逐源健康度，并提供详情与历史样本读取接口；`static/assets.html` 删除热点职责，只保留素材操作。全程沿用现有 FastAPI、SQLite、原生 HTML/JavaScript 和 Buffalo 设计系统。

**Tech Stack:** FastAPI、SQLite、原生 HTML/CSS/JavaScript、pytest、Node.js 语法检查。

---

## File Responsibilities

- Create `static/hotspots.html`: 热点列表、采集解释、来源状态、四步工作流和样本结果。
- Create `tests/test_hotspot_workbench_ui.py`: 导航、页面信息架构、按钮层级和采集说明契约。
- Create `tests/test_hotspot_fetch_runs.py`: 抓取运行持久化、详情和历史样本 API 契约。
- Modify `static/common.js`: 新增“热点选题”导航并将“内容资产”移动到资源分组。
- Modify `static/assets.html`: 移除热点/品牌/来源/样本逻辑，整理素材页头和更多操作。
- Modify `database.py`: 新增 `hotspot_fetch_runs` 及查询函数，增加按热点列出样本函数。
- Modify `app.py`: 持久化抓取结果，新增抓取状态、热点详情和历史样本接口。
- Modify `docs/功能介绍.md`: 更新最终页面入口与操作链路。

### Task 1: Restructure navigation and content-assets responsibilities

**Files:**
- Modify: `static/common.js`
- Modify: `static/assets.html`
- Create: `tests/test_hotspot_workbench_ui.py`
- Modify: `tests/test_assets_header_layout.py`

- [ ] **Step 1: Write failing information-architecture tests**

```python
from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_sidebar_has_hotspot_workbench_and_assets_under_resources():
    common = (ROOT / "static/common.js").read_text(encoding="utf-8")
    assert "{ id: 'hotspots', label: '热点选题', href: '/hotspots.html' }" in common
    core = common.index("{ section: '核心' }")
    analysis = common.index("{ section: '分析' }")
    resources = common.index("{ section: '资源' }")
    assert core < common.index("id: 'hotspots'") < analysis
    assert resources < common.index("id: 'assets'")

def test_assets_page_contains_only_asset_and_inspiration_actions():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    for forbidden in ("南非热点", "添加可信源", "立即抓取", "品牌证据", "生成三份内部样本"):
        assert forbidden not in page
    assert "上传素材" in page
    assert "接入桌面素材" in page
    assert "更多操作" in page
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_workbench_ui.py tests/test_assets_header_layout.py`

Expected: FAIL because the hotspot nav is missing and hotspot actions still exist in `assets.html`.

- [ ] **Step 3: Implement the navigation order**

Update `NAV_ITEMS` in `static/common.js` to this exact order:

```javascript
const NAV_ITEMS = [
    { section: '核心' },
    { id: 'chat', label: 'AI 对话', href: '/chat.html' },
    { id: 'hotspots', label: '热点选题', href: '/hotspots.html' },
    { id: 'editor', label: '内容编辑器', href: '/editor.html' },
    { id: 'queue', label: '发布队列', href: '/queue.html' },
    { section: '分析' },
    { id: 'home', label: '经营驾驶舱', href: '/home.html' },
    { id: 'calendar', label: '发布日历', href: '/calendar.html' },
    { section: '资源' },
    { id: 'assets', label: '内容资产', href: '/assets.html' },
    { id: 'knowledge', label: '企业知识库', href: '/knowledge.html' },
    { id: 'templates', label: 'Prompt 模板', href: '/templates.html' },
    { section: '运营' },
    { id: 'accounts', label: '账号管理', href: '/accounts.html' },
    { section: '设置' },
    { id: 'config', label: '平台配置', href: '/config.html' },
];
```

Add an inline SVG path for `热点选题` to `NAV_ICONS`.

- [ ] **Step 4: Reduce content-assets actions**

In `static/assets.html`:

- keep visible `上传素材` and `接入桌面素材` actions;
- keep `本地素材` and `灵感链接` as page tabs;
- move `分析存量素材` and `扫描项目导入目录` into a `更多操作` disclosure;
- remove `openHotspots`, `fetchHotspots`, `addHotspotSource`, `openBrandEvidence`, `createSampleBundle`, and related hotspot markup;
- rename the reused `hotspotModal` to `assetDetailModal` for mirror/segment details.

The visible action structure must be:

```html
<div class="asset-page-actions">
  <button class="btn btn-secondary" onclick="startLocalImport()">接入桌面素材</button>
  <details class="asset-more-menu">
    <summary class="btn btn-secondary">更多操作</summary>
    <div class="asset-more-panel">
      <button onclick="processPendingAssets()">分析存量素材</button>
      <button onclick="importDirectory()">扫描项目导入目录</button>
    </div>
  </details>
  <label class="btn btn-primary">上传素材
    <input type="file" multiple hidden onchange="uploadFiles(this.files);this.value=''"/>
  </label>
</div>
```

- [ ] **Step 5: Run focused tests**

Run: `pytest -q tests/test_hotspot_workbench_ui.py tests/test_assets_header_layout.py tests/test_local_asset_import_ui.py tests/test_media_api.py`

Expected: PASS.

- [ ] **Step 6: Commit focused UI restructure**

```bash
git add -p static/common.js static/assets.html tests/test_hotspot_workbench_ui.py tests/test_assets_header_layout.py
git commit -m "refactor: separate hotspot and asset navigation"
```

### Task 2: Persist hotspot fetch runs and expose readable status

**Files:**
- Modify: `database.py`
- Modify: `app.py`
- Create: `tests/test_hotspot_fetch_runs.py`

- [ ] **Step 1: Write failing database and API tests**

```python
HOTSPOT_FIXTURE = {
    "title": "Durban port operational update",
    "summary": "South Africa freight update",
    "source_url": "https://gov.za/durban",
    "publisher": "Official",
    "published_at": "2026-07-22T08:00:00Z",
    "retrieved_at": "2026-07-22T09:00:00Z",
    "snapshot_sha256": "a" * 64,
}
FETCH_RESULT = {
    "feeds": 1, "new": 1, "updated": 0, "assets": 0, "skipped": 0,
    "errors": [], "media_errors": [],
    "source_health": [{"name": "Official", "status": "ok", "items": 1, "error": ""}],
}

def test_fetch_run_persists_source_health(tmp_db):
    run_id = tmp_db.create_hotspot_fetch_run(1)
    result = {
        "feeds": 2,
        "new": 3,
        "updated": 1,
        "errors": [],
        "media_errors": [],
        "source_health": [
            {"name": "SAnews", "status": "ok", "items": 4, "error": ""},
            {"name": "SARS", "status": "ok", "items": 2, "error": ""},
        ],
    }
    tmp_db.finish_hotspot_fetch_run(run_id, result)
    latest = tmp_db.get_latest_hotspot_fetch_run()
    assert latest["status"] == "succeeded"
    assert latest["result"]["source_health"][0]["name"] == "SAnews"

def test_fetch_status_and_hotspot_detail_api(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    import app, auth
    user_id = tmp_db.create_user("hotspotadmin", auth.hash_password("pw12345"), "admin", "A")
    hotspot_id, _ = tmp_db.upsert_hotspot(HOTSPOT_FIXTURE)
    run_id = tmp_db.create_hotspot_fetch_run(user_id)
    tmp_db.finish_hotspot_fetch_run(run_id, FETCH_RESULT)
    client = TestClient(app.app)
    token = client.post("/api/auth/login", json={"username":"hotspotadmin","password":"pw12345"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/hotspots/fetch-status", headers=headers).status_code == 200
    assert client.get(f"/api/hotspots/{hotspot_id}", headers=headers).json()["id"] == hotspot_id
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_fetch_runs.py`

Expected: FAIL because fetch-run functions and routes do not exist.

- [ ] **Step 3: Add the fetch-run table and functions**

Add to `database.py` initialization:

```sql
CREATE TABLE IF NOT EXISTS hotspot_fetch_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    FOREIGN KEY (created_by) REFERENCES users(id)
);
```

Implement exact functions:

```python
def _decode_hotspot_fetch_run(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["result"] = json.loads(item.pop("result_json") or "{}")
    return item


def create_hotspot_fetch_run(created_by: int | None) -> str:
    run_id = uuid4().hex
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO hotspot_fetch_runs (id,created_by) VALUES (?,?)",
            (run_id, created_by),
        )
    return run_id


def finish_hotspot_fetch_run(
    run_id: str, result: dict, status: str | None = None
) -> dict:
    if status is None:
        health = result.get("source_health") or []
        failed = sum(1 for item in health if item.get("status") == "error")
        status = "partial" if failed else "succeeded"
        if health and failed == len(health):
            status = "failed"
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspot_fetch_runs
               SET status=?,result_json=?,finished_at=datetime('now') WHERE id=?""",
            (status, json.dumps(result, ensure_ascii=False), run_id),
        )
        row = conn.execute(
            "SELECT * FROM hotspot_fetch_runs WHERE id=?", (run_id,)
        ).fetchone()
    return _decode_hotspot_fetch_run(row)


def get_latest_hotspot_fetch_run() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hotspot_fetch_runs ORDER BY started_at DESC,id DESC LIMIT 1"
        ).fetchone()
    return _decode_hotspot_fetch_run(row)


def list_sample_bundles_for_hotspot(
    hotspot_id: int, limit: int = 10
) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT sb.id FROM sample_bundles sb
               JOIN evidence_packages ep ON ep.id=sb.evidence_package_id
               WHERE ep.hotspot_id=? ORDER BY sb.created_at DESC LIMIT ?""",
            (hotspot_id, max(1, min(limit, 50))),
        ).fetchall()
    return [get_sample_bundle(row["id"]) for row in rows]
```

Decode `result_json` as `result` before returning.

- [ ] **Step 4: Persist fetch execution and add read APIs**

Update `POST /api/hotspots/fetch` in `app.py`:

```python
run_id = db.create_hotspot_fetch_run(user["id"])
try:
    result = await hotspot_fetcher.fetch_hotspots(STATIC_DIR, created_by=user["id"])
    run = db.finish_hotspot_fetch_run(run_id, result)
except Exception as exc:
    db.finish_hotspot_fetch_run(run_id, {"errors": [{"feed": "system", "error": str(exc)[:300]}]}, "failed")
    raise
return {**result, "run_id": run["id"], "run_status": run["status"]}
```

Add routes before the integer detail route:

```python
@app.get("/api/hotspots/fetch-status")
async def get_hotspot_fetch_status(user=Depends(get_current_user)):
    return db.get_latest_hotspot_fetch_run() or {"status": "never_run", "result": {}}

@app.get("/api/hotspots/{hotspot_id}")
async def get_hotspot_detail(hotspot_id: int, user=Depends(get_current_user)):
    item = db.get_hotspot(hotspot_id)
    if not item:
        raise HTTPException(404, "热点不存在")
    return item

@app.get("/api/hotspots/{hotspot_id}/sample-bundles")
async def list_hotspot_sample_bundles(hotspot_id: int, user=Depends(get_current_user)):
    return db.list_sample_bundles_for_hotspot(hotspot_id)
```

- [ ] **Step 5: Run API regression tests**

Run: `pytest -q tests/test_hotspot_fetch_runs.py tests/test_hotspot_fetcher.py tests/test_evidence_harness.py tests/test_sample_harness.py`

Expected: PASS.

- [ ] **Step 6: Commit fetch state**

```bash
git add -p database.py app.py tests/test_hotspot_fetch_runs.py
git commit -m "feat: persist hotspot fetch status"
```

### Task 3: Build the standalone hotspot workbench shell

**Files:**
- Create: `static/hotspots.html`
- Modify: `tests/test_hotspot_workbench_ui.py`

- [ ] **Step 1: Extend failing UI contract tests**

```python
def test_hotspot_page_explains_collection_and_has_one_primary_fetch_action():
    page = (ROOT / "static/hotspots.html").read_text(encoding="utf-8")
    assert 'const PAGE_ID = \'hotspots\'' in page
    assert page.count("抓取最新热点") == 1
    for text in ("系统如何采集？", "信源管理", "上次抓取", "信源状态", "本次结果"):
        assert text in page
    for source in ("SAnews", "SARS", "南非交通部", "南非政府", "南非储备银行"):
        assert source in page
    assert "抓取不调用文本大模型" in page
    assert "版权不明确的媒体不会自动下载" in page

def test_hotspot_page_has_master_detail_and_four_steps():
    page = (ROOT / "static/hotspots.html").read_text(encoding="utf-8")
    assert "hotspot-list-panel" in page
    assert "hotspot-workspace" in page
    for step in ("选择热点", "核验证据", "检查素材", "生成样本"):
        assert step in page
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_workbench_ui.py`

Expected: FAIL because `static/hotspots.html` does not exist.

- [ ] **Step 3: Implement the page shell and loading states**

Create `static/hotspots.html` with:

- shared `design-system.css` and `common.js`;
- one header primary button `fetchLatestHotspots()`;
- fetch summary region with `aria-live="polite"`;
- searchable/filterable left list;
- right workspace empty state and four-step header;
- source rules and source management drawers with close buttons and Escape handling;
- inline SVG icons only.

On initialization, load these requests in parallel:

```javascript
await Promise.all([
  loadHotspots(),
  loadFetchStatus(),
  loadSources(),
  loadBrandEvidence(),
]);
```

The source drawer must render `source_health`; raw exceptions must be converted to readable source rows.

- [ ] **Step 4: Add responsive and accessibility rules**

Use CSS grid `minmax(320px, 38%) minmax(0, 1fr)` above 1100px and one column below. Add `min-width:0`, no horizontal scrolling, 44px controls, visible focus, and no color-only status.

- [ ] **Step 5: Verify page shell**

Run:

```bash
pytest -q tests/test_hotspot_workbench_ui.py
node - <<'NODE'
const fs=require('fs');const s=fs.readFileSync('static/hotspots.html','utf8');
const m=s.match(/<script>([\s\S]*?)<\/script>/);new Function(m[1]);
console.log('hotspots inline script syntax ok');
NODE
```

Expected: PASS and syntax message.

- [ ] **Step 6: Commit page shell**

```bash
git add static/hotspots.html tests/test_hotspot_workbench_ui.py
git commit -m "feat: add standalone hotspot workbench"
```

### Task 4: Implement the four-step evidence-to-sample workflow

**Files:**
- Modify: `static/hotspots.html`
- Modify: `tests/test_hotspot_workbench_ui.py`
- Reuse: `semantic_matching.py`, `evidence_harness.py`, `sample_harness.py`

- [ ] **Step 1: Write failing workflow tests**

```python
def test_hotspot_workbench_calls_existing_evidence_and_sample_apis():
    page = (ROOT / "static/hotspots.html").read_text(encoding="utf-8")
    assert "/evidence-package" in page
    assert "/sample-bundle" in page
    assert "/api/brand-evidence" in page
    assert "仍可生成不含品牌能力承诺的内部样本" in page
    assert "内部测试，不可发布" in page
    assert "视频" in page and "图文" in page and "公众号" in page

def test_hotspot_selection_is_restored_without_refetching():
    page = (ROOT / "static/hotspots.html").read_text(encoding="utf-8")
    assert "selectedHotspotId" in page
    assert "sessionStorage" in page
    assert "/sample-bundles" in page
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_workbench_ui.py`

Expected: FAIL because the workbench shell does not yet call the evidence/sample endpoints.

- [ ] **Step 3: Implement step 1 and step 2**

Selecting a hotspot stores `selectedHotspotId` in `sessionStorage`, loads detail, and renders the source boundary. `createEvidencePackage()` posts all confirmed public brand evidence IDs to `/api/hotspots/{id}/evidence-package`, then renders fact and brand claims separately.

- [ ] **Step 4: Implement step 3 material review**

Render the five video scenes from the generated sample's existing semantic assignments. Each scene shows selected asset ID, segment boundaries, match score and candidate explanation. If no candidate exists, show the material gap text and keep publication blocked.

- [ ] **Step 5: Implement step 4 and sample tabs**

`generateSampleBundle()` calls `/api/evidence-packages/{id}/sample-bundle`, then renders three tabs from the returned bundle. Technical details are inside a closed `<details>` and contain output directory, claim IDs, budget and issues.

Restore the latest bundle with `GET /api/hotspots/{id}/sample-bundles` after refresh without calling fetch or generating again.

- [ ] **Step 6: Verify workflow UI and API tests**

Run: `pytest -q tests/test_hotspot_workbench_ui.py tests/test_evidence_harness.py tests/test_sample_harness.py tests/test_semantic_matching.py`

Expected: PASS.

- [ ] **Step 7: Commit workflow**

```bash
git add static/hotspots.html tests/test_hotspot_workbench_ui.py
git commit -m "feat: connect hotspot evidence workflow"
```

### Task 5: Documentation, browser verification, and final regression

**Files:**
- Modify: `docs/功能介绍.md`
- Modify: `docs/AI视频一键生成与质量门禁操作说明.md`
- Sync: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/`
- Append: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: Update user documentation**

Document the exact entry `http://localhost:8080/hotspots.html`, the four steps, the five official sources, IOL boundary, and the fact that fetching uses no text model.

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
pytest -q \
  tests/test_hotspot_workbench_ui.py \
  tests/test_hotspot_fetch_runs.py \
  tests/test_hotspot_fetcher.py \
  tests/test_evidence_harness.py \
  tests/test_sample_harness.py \
  tests/test_assets_header_layout.py \
  tests/test_local_asset_import_ui.py \
  tests/test_video_workbench_ui.py
```

Expected: PASS.

- [ ] **Step 3: Run syntax and whitespace checks**

Run:

```bash
python3 -m py_compile app.py database.py hotspot_fetcher.py evidence_harness.py sample_harness.py
git diff --check
```

Extract and compile inline scripts from `static/hotspots.html` and `static/assets.html` with Node.js. Expected: exit 0.

- [ ] **Step 4: Verify local 8080 without stealing focus**

Use HTTP checks for:

```text
GET /hotspots.html -> 200
GET /assets.html -> 200
```

Use a headless/local browser viewport at 1440px, 1280px and 1024px. Verify no horizontal overflow, a single visible primary action per header, readable title, source rule drawer, hotspot selection and the four-step progression. Do not open or focus the user's Chrome window.

- [ ] **Step 5: Sync documentation and append the required project log**

Copy modified Markdown documents to the Obsidian Distribution Manager directory. Append a `YYYY-MM-DD HH:mm:ss CST｜热点选题工作台重构` entry covering target, before state, exact changes, files, test/browser evidence, Git status, and remaining manual review.

- [ ] **Step 6: Commit verified implementation without pushing**

Stage only reviewed hotspot-workbench hunks and new files. Do not include unrelated Twitter/publisher changes.

```bash
git commit -m "feat: restructure hotspot selection workflow"
```

Do not push or deploy; keep the verified build on local port 8080.
