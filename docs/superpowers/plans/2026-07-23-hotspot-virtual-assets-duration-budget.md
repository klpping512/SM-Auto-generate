# 热点事件虚拟素材与成片时长预算 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将热点母片的事件片段变成真正可匹配、可写入时间线、可按平台预算渲染的虚拟素材，禁止 120 秒母片直接参与成片。

**Architecture:** 保留 `assets` 中的热点视频作为母片，在 `hotspot_event_clips` 中补齐虚拟素材元数据和稳定引用；视频项目场景保存 `event_clip_id` 与裁剪范围，渲染前由预算服务规范化场景并由 FFmpeg 按范围裁剪。热点事件和 Buffalo 原有素材继续分开匹配。

**Tech Stack:** Python 3.12、SQLite、FastAPI、FFmpeg/FFprobe、静态 HTML/JavaScript、pytest。

---

### Task 1: 建立事件虚拟素材数据契约

**Files:**
- Modify: `database.py`（`hotspot_event_clips` DDL、迁移、事件读写函数）
- Modify: `hotspot_event_clips.py`（事件输出字段）
- Create: `tests/test_hotspot_virtual_assets.py`

- [ ] **Step 1: Write failing tests**

```python
def test_event_clip_has_virtual_ref_and_duration():
    event = db.list_hotspot_event_clips(asset_id=298)[0]
    assert event["virtual_asset_id"] == f"hotspot-event-{event['id']}"
    assert event["duration_ms"] == event["end_ms"] - event["start_ms"]
    assert event["library_origin"] == "hotspot_event"

def test_event_clip_range_is_within_mother_asset():
    event = db.list_hotspot_event_clips(asset_id=298)[0]
    asset = db.get_asset(event["asset_id"])
    assert event["start_ms"] >= 0
    assert event["end_ms"] <= round(float(asset["duration"] or 0) * 1000)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_hotspot_virtual_assets.py`
Expected: FAIL because the event table/output has no virtual asset fields.

- [ ] **Step 3: Implement migration and output fields**

Add nullable columns `virtual_asset_id`, `duration_ms`, `thumbnail_path`, and `library_origin` with `_ensure_column`. During event replacement set `duration_ms=end_ms-start_ms`, `virtual_asset_id=hotspot-event-{id}` after insert, `library_origin=hotspot_event`, and use the first linked segment thumbnail as `thumbnail_path` when available. `list_hotspot_event_clips` must return these fields and reject invalid ranges before insert.

- [ ] **Step 4: Run tests**

Run: `pytest -q tests/test_hotspot_virtual_assets.py tests/test_hotspot_event_clips.py`
Expected: PASS.

### Task 2: Add a pure duration-budget and clip-reference service

**Files:**
- Create: `video_clip_refs.py`
- Create: `video_duration_budget.py`
- Create: `tests/test_video_duration_budget.py`

- [ ] **Step 1: Write failing tests**

```python
def test_platform_budget_defaults():
    assert platform_budget_ms("douyin") == 30_000
    assert platform_budget_ms("xiaohongshu") == 45_000
    assert platform_budget_ms("youtube") == 60_000
    assert platform_budget_ms("wechat") == 90_000

def test_budget_trims_last_scene_without_using_mother_clip():
    scenes = [
        {"duration": 18, "asset_id": 298, "event_clip_id": 2, "asset_start_ms": 6000, "asset_end_ms": 12000},
        {"duration": 18, "asset_id": 140, "asset_segment_id": 9},
    ]
    result = fit_scenes_to_budget(scenes, 30_000)
    assert sum(item["duration_ms"] for item in result) == 30_000
    assert result[0]["event_clip_id"] == 2
    assert result[1]["duration_ms"] == 12_000

def test_hotspot_mother_asset_without_event_ref_is_rejected():
    with pytest.raises(ClipReferenceError, match="必须选择热点事件片段"):
        resolve_clip_ref({"asset_id": 298}, {"id": 298, "hotspot_id": 31, "file_type": "video"}, {})
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_duration_budget.py`
Expected: FAIL because the services do not exist.

- [ ] **Step 3: Implement minimal pure services**

`video_duration_budget.py` defines the platform defaults, validates 15–180 second custom budgets, accumulates scenes in order, and clips the final scene to remaining milliseconds. `video_clip_refs.py` defines `resolve_clip_ref(scene, asset, event_lookup)`; event refs resolve to mother path plus `start_ms/end_ms`, ordinary asset segments resolve to their segment range, and hotspot mothers without an event ref raise `ClipReferenceError`.

- [ ] **Step 4: Run tests**

Run: `pytest -q tests/test_video_duration_budget.py`
Expected: PASS.

### Task 3: Connect project revisions and generation planning to event refs and budgets

**Files:**
- Modify: `app.py` (project creation/revision validation and event ref API)
- Modify: `database.py` (event lookup helper)
- Modify: `video_generation.py` (revision normalization before rendering)
- Modify: `tests/test_video_generation_api.py`
- Modify: `tests/test_video_generation_state.py`

- [ ] **Step 1: Write failing API/state tests**

```python
def test_project_revision_rejects_hotspot_mother_scene_without_event_ref(client, project):
    response = client.put(f"/api/video-projects/{project['id']}/revision", json={"payload": {
        "scenes": [{"duration": 5, "asset_id": 298, "visual": "热点", "voiceover": "事件"}]
    }})
    assert response.status_code == 400
    assert "热点事件片段" in response.json()["detail"]

def test_generation_payload_contains_budget_metadata():
    payload = normalize_generation_payload({"platform": "xiaohongshu", "target_duration_ms": 45000}, {
        "scenes": [{"duration": 20, "event_clip_id": 2, "asset_id": 298}]
    })
    assert payload["duration_target_ms"] == 45000
    assert payload["scenes"][0]["event_clip_id"] == 2
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_generation_api.py tests/test_video_generation_state.py`
Expected: FAIL because revision/generation payloads do not validate event refs or expose budget metadata.

- [ ] **Step 3: Implement validation and normalization**

Add `db.get_hotspot_event_clip(event_id)` and a shared generation normalizer that loads the project platform/budget, resolves event refs, calls `fit_scenes_to_budget`, and writes `duration_target_ms`, `duration_used_ms`, `duration_remaining_ms`, and `clip_refs` into the revision payload. Reject a hotspot mother scene without `event_clip_id`; leave owned asset segment behavior unchanged.

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/test_video_generation_api.py tests/test_video_generation_state.py tests/test_video_duration_budget.py`
Expected: PASS.

### Task 4: Make FFmpeg rendering use only bounded clip references

**Files:**
- Modify: `video_renderer.py` (`normalize_script`, asset resolution and render metadata)
- Modify: `tests/test_video_generation_rendering.py`

- [ ] **Step 1: Write failing rendering tests**

```python
def test_normalize_script_preserves_event_clip_range():
    script = normalize_script({"duration_target_ms": 30_000, "scenes": [
        {"duration": 8, "asset_id": 298, "event_clip_id": 2}
    ]}, {298})
    assert script["scenes"][0]["event_clip_id"] == 2
    assert script["duration_target_ms"] == 30_000

def test_render_command_never_uses_full_hotspot_mother():
    with pytest.raises(ValueError, match="热点事件片段"):
        normalize_script({"scenes": [{"duration": 5, "asset_id": 298}]}, {298},
                         asset_lookup={298: {"id": 298, "hotspot_id": 31, "file_type": "video"}})
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_generation_rendering.py`
Expected: FAIL because `normalize_script` currently accepts hotspot mother IDs and always forces a 30-second target.

- [ ] **Step 3: Implement bounded rendering**

Extend `normalize_script` to preserve `event_clip_id`, `duration_target_ms`, and per-scene `duration_ms`. In `render_job`, resolve event clips through `video_clip_refs`, pass their exact source range into `_scene_command`, and record `clip_refs` in `video_render_jobs.clips`/`quality_report`. Before concat, assert the sum of scene durations is no greater than the target budget and never use a hotspot asset without an event ref.

- [ ] **Step 4: Run rendering tests**

Run: `pytest -q tests/test_video_generation_rendering.py tests/test_video_duration_budget.py`
Expected: PASS. If FFmpeg is unavailable, command-construction tests must still pass without invoking FFmpeg.

### Task 5: Expose event cards and budget status in the UI

**Files:**
- Modify: `app.py` (event card response includes mother/virtual asset labels)
- Modify: `static/assets.html` (show event cards under hotspot library, hide direct mother “使用”)
- Modify: `static/video-workbench.html` (select event clip and show source range)
- Modify: `static/video-project.html` (show platform budget/used/remaining)
- Create: `tests/test_hotspot_virtual_assets_ui.py`

- [ ] **Step 1: Write failing UI tests**

```python
def test_hotspot_library_labels_virtual_event_cards():
    html = Path("static/assets.html").read_text()
    assert "热点事件素材" in html
    assert "event_clip_id" in html
    assert "热点原始母片" in html

def test_video_project_shows_duration_budget():
    html = Path("static/video-project.html").read_text()
    assert "剩余时长" in html
    assert "duration_remaining_ms" in html
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_hotspot_virtual_assets_ui.py`
Expected: FAIL because the UI currently only shows the mother asset and event matching placeholder.

- [ ] **Step 3: Implement UI**

Render hotspot event cards with bilingual title, thumbnail, `start_ms/end_ms`, clip duration, review status, and “加入成片”. The mother card becomes a collapsible “热点原始母片” card without direct “使用”. The workbench writes `event_clip_id`, `asset_id`, and range into the scene mapping. The project page shows platform budget, used duration, and remaining duration from the revision/job payload.

- [ ] **Step 4: Run UI tests**

Run: `pytest -q tests/test_hotspot_virtual_assets_ui.py tests/test_hotspot_event_clips_ui.py tests/test_video_workbench_ui.py tests/test_video_generation_ui.py`
Expected: PASS.

### Task 6: Full verification and Graphify refresh

**Files:**
- Modify: `docs/产研部署交接说明.md`
- Create/update: `docs/graphify-out/graph.html`, `docs/graphify-out/graph.json`, `docs/graphify-out/GRAPH_REPORT.md`
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: Run the complete regression suite**

Run: `pytest -q`
Expected: all tests pass; warnings are acceptable if no failures occur.

- [ ] **Step 2: Run syntax and diff checks**

Run: `python3 -m py_compile app.py database.py video_generation.py video_renderer.py video_clip_refs.py video_duration_budget.py && git diff --check`
Expected: exit code 0.

- [ ] **Step 3: Rebuild Graphify code map without semantic model calls**

Run: `uvx --from graphifyy graphify extract . --code-only --out /private/tmp/salogiflow-graphify-final --no-cluster && uvx --from graphifyy graphify cluster-only /private/tmp/salogiflow-graphify-final`
Expected: graph.html, graph.json and GRAPH_REPORT.md generated; no MiMo/API calls.

- [ ] **Step 4: Copy the map and update handoff docs**

Copy the three Graphify outputs to `docs/graphify-out/`, document the new virtual asset/budget behavior in `docs/产研部署交接说明.md`, sync both Markdown docs to the Obsidian project folder, and append a timestamped entry to `改进日志.md` before any commit attempt.

- [ ] **Step 5: Verify local 8080**

Run: `curl --max-time 5 -sS http://127.0.0.1:8080/assets.html | rg '热点事件素材|事件片段|duration_remaining_ms'`
Expected: all three markers present after refreshing the browser with `Cmd+Shift+R`.
