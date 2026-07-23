# 热点视频事件片段与双素材库联动实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将热点视频固定为热点来源，按事件生成双语命名片段，并为每个事件匹配 Buffalo 原有素材 Top 3。

**Architecture:** 保留现有 `assets` 与 `asset_segments`，新增事件层表 `hotspot_event_clips` 和关联表；事件只引用原视频的时间范围，不复制文件。新增本地规则服务从字幕、OCR、镜头标签和时间边界生成事件；复用 `semantic_matching` 分别检索热点镜头和自有素材。

**Tech Stack:** Python 3.12、FastAPI、SQLite、FFmpeg、pytest、现有 ASR/OCR 与语义匹配模块、静态 HTML/JavaScript。

---

### Task 1: 固定热点来源层并修正既有资产分类

**Files:**
- Modify: `database.py`
- Modify: `app.py`
- Modify: `asset_processing.py`
- Test: `tests/test_hotspot_event_clips.py`

- [ ] **Step 1: Write failing tests**

```python
def test_hotspot_asset_origin_is_not_business_category(tmp_db):
    asset = tmp_db.get_asset(298)
    assert asset["hotspot_id"]
    assert asset["library_origin"] == "hotspot"
    assert asset["primary_category"] == "other"

def test_hotspot_asset_api_exposes_source_library(tmp_db):
    item = client.get("/api/assets/298", headers=admin_headers).json()
    assert item["library_origin"] == "hotspot"
    assert item["source_label"] == "热点素材"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_event_clips.py -k origin`

Expected: `library_origin` missing or existing hotspot asset still reports `delivery`.

- [ ] **Step 3: Implement minimal source derivation**

Add a derived field in asset serializers and list queries:

```python
def asset_library_origin(asset: dict) -> str:
    return "hotspot" if asset.get("hotspot_id") else "owned"

def asset_source_label(asset: dict) -> str:
    return "热点素材" if asset_library_origin(asset) == "hotspot" else "Buffalo 原有素材"
```

When a hotspot asset is materialized, set asset-level `category` and `primary_category` to `other`; keep segment-level labels for content semantics. Do not overwrite existing `hotspot_id`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest -q tests/test_hotspot_event_clips.py -k origin tests/test_hotspot_media_api.py`

Expected: all selected tests pass.

### Task 2: Add event clip schema and local event extraction service

**Files:**
- Create: `hotspot_event_clips.py`
- Modify: `database.py`
- Test: `tests/test_hotspot_event_clips.py`

- [ ] **Step 1: Write failing event extraction tests**

```python
def test_build_event_clips_groups_short_shots_and_names_bilingually():
    segments = [
        {"start_ms": 0, "end_ms": 6000, "transcript": "Cape Town Transnet land"},
        {"start_ms": 6000, "end_ms": 16000, "ocr_text": "Cape Town Western Cape"},
        {"start_ms": 16000, "end_ms": 23000, "transcript": "Kgalagadi park"},
    ]
    events = build_event_clips(segments, date="2026-07-22", source="SA Today")
    assert len(events) == 2
    assert events[0]["title_zh"] == "2026-07-22｜开普敦｜Transnet 土地事件｜SA Today"
    assert events[0]["title_en"] == "2026-07-22 | Cape Town | Transnet Land Event | SA Today"
    assert events[0]["start_ms"] == 0
    assert events[0]["end_ms"] == 16000

def test_low_confidence_event_is_waiting_review():
    events = build_event_clips([{"start_ms": 0, "end_ms": 7000}], date="2026-07-22", source="SA Today")
    assert events[0]["review_status"] == "review_required"
    assert events[0]["title_zh"].startswith("待确认事件")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_event_clips.py -k event`

Expected: module/function/table missing.

- [ ] **Step 3: Implement bounded local extraction**

Create `build_event_clips(segments, date, source)` that:

1. normalizes each segment to 3–8 seconds;
2. extracts location/entity tokens from transcript, OCR and descriptions;
3. starts a new event on a new location/entity cluster or a 12-second boundary;
4. merges events shorter than 6 seconds;
5. caps events at 35 seconds;
6. creates bilingual names only from observed tokens, otherwise uses `待确认事件 NN｜MM:SS–MM:SS｜source`;
7. sets `confidence` and `review_status`.

Add SQLite tables `hotspot_event_clips` and `hotspot_event_segment_links`; expose CRUD helpers and preserve event records when the source asset is purged.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest -q tests/test_hotspot_event_clips.py -k event`

Expected: event grouping, names and low-confidence fallback pass.

### Task 3: Create event records after hotspot analysis

**Files:**
- Modify: `app.py`
- Modify: `asset_processing.py`
- Modify: `database.py`
- Test: `tests/test_hotspot_event_clips.py`

- [ ] **Step 1: Write failing integration test**

```python
def test_ready_hotspot_asset_creates_named_event_clips(tmp_db, monkeypatch):
    run_hotspot_processing(asset_id=298)
    events = tmp_db.list_hotspot_event_clips(asset_id=298)
    assert events
    assert all(event["asset_id"] == 298 for event in events)
    assert all(event["title_zh"] and event["title_en"] for event in events)
```

- [ ] **Step 2: Run test and verify RED**

Run: `pytest -q tests/test_hotspot_event_clips.py::test_ready_hotspot_asset_creates_named_event_clips`

Expected: no event records are created after existing asset processing.

- [ ] **Step 3: Implement integration**

After `asset_processing.process_asset` creates/updates `asset_segments`, call `hotspot_event_clips.rebuild_for_asset(asset_id)` only when `assets.hotspot_id IS NOT NULL`. Delete/rebuild event links for the current processing version, never duplicate media files, and keep `asset_segments` unchanged.

- [ ] **Step 4: Run test and verify GREEN**

Run: `pytest -q tests/test_hotspot_event_clips.py::test_ready_hotspot_asset_creates_named_event_clips`

Expected: named event records exist and processing remains local.

### Task 4: Match each event with hotspot and Buffalo-owned Top 3

**Files:**
- Modify: `semantic_matching.py`
- Create: `hotspot_event_matching.py`
- Modify: `database.py`
- Test: `tests/test_hotspot_event_matching.py`

- [ ] **Step 1: Write failing matching tests**

```python
def test_event_matching_keeps_hotspot_and_owned_libraries_separate():
    result = match_event(event, segments)
    assert len(result["hotspot_candidates"]) <= 3
    assert len(result["owned_candidates"]) <= 3
    assert all(item["library_origin"] == "hotspot" for item in result["hotspot_candidates"])
    assert all(item["library_origin"] == "owned" for item in result["owned_candidates"])

def test_event_with_no_brand_match_returns_reason():
    result = match_event(event_without_business_link, segments)
    assert result["owned_candidates"] == []
    assert result["owned_match_reason"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_event_matching.py`

Expected: matching service missing.

- [ ] **Step 3: Implement separated matching**

Build one semantic atom from event title, entities, location and keywords. Call existing ranking twice with hard constraints:

```python
hotspot_segments = [s for s in segments if s.get("asset_hotspot_id") == event["hotspot_id"]]
owned_segments = [s for s in segments if not s.get("asset_hotspot_id")]
```

Return independent Top 3 lists, reasons, and `suggested_role` (`hotspot_hook`, `brand_proof`, `not_recommended`). Do not force an owned match when no evidence overlaps.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest -q tests/test_hotspot_event_matching.py tests/test_semantic_matching.py`

Expected: separated candidates and no-match reason pass.

### Task 5: Expose event clips and matching in the frontend

**Files:**
- Modify: `app.py`
- Modify: `static/assets.html`
- Modify: `static/video-workbench.html`
- Test: `tests/test_hotspot_event_clips_ui.py`

- [ ] **Step 1: Write failing UI/API tests**

```python
def test_hotspot_card_exposes_event_clip_entry_and_bilingual_title():
    page = ASSETS_HTML.read_text(encoding="utf-8")
    assert "事件片段" in page
    assert "title_zh" in page and "title_en" in page
    assert "热点素材" in page

def test_video_workbench_uses_event_matches():
    page = VIDEO_WORKBENCH.read_text(encoding="utf-8")
    assert "/api/hotspot-events" in page
    assert "Buffalo 原有素材" in page
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_hotspot_event_clips_ui.py`

Expected: event entry and matching UI labels missing.

- [ ] **Step 3: Implement UI/API**

Add:

```text
GET /api/hotspot-events?asset_id=298
GET /api/hotspot-events/{event_id}/matches
```

Render event cards with bilingual title, time range, preview, confidence, review state, hotspot Top 3 and owned Top 3. Add `应用到成片` only when the event is confirmed or has confidence >= 0.75.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest -q tests/test_hotspot_event_clips_ui.py tests/test_hotspot_media_ui.py tests/test_video_workbench_ui.py`

Expected: all selected UI tests pass.

### Task 6: Rebuild asset 298 and complete acceptance

**Files:**
- Modify: `scripts/run_hotspot_event_rebuild.py`
- Modify: `docs/产研部署交接说明.md`
- Test: `tests/test_hotspot_event_acceptance.py`

- [ ] **Step 1: Write acceptance test**

```python
def test_asset_298_acceptance_bundle(tmp_db):
    asset = tmp_db.get_asset(298)
    events = tmp_db.list_hotspot_event_clips(asset_id=298)
    assert asset["hotspot_id"] and asset["primary_category"] == "other"
    assert len(events) >= 2
    assert all(event["title_zh"] and event["title_en"] for event in events)
```

- [ ] **Step 2: Run test and verify RED if rebuild is missing**

Run: `pytest -q tests/test_hotspot_event_acceptance.py`

- [ ] **Step 3: Implement a no-MiMo rebuild command**

Create a script that loads the existing asset, resets only the asset-level business category, rebuilds local event records, and writes no new video files.

- [ ] **Step 4: Run the rebuild and verify output**

Run: `python3 scripts/run_hotspot_event_rebuild.py --asset-id 298`

Expected: output includes event count, bilingual names, time ranges, and owned/hotspot candidate counts; MiMo call count remains zero.

- [ ] **Step 5: Update deployment handoff**

Document event rebuild, source/library separation, no-copy previews, and the manual review rule for low-confidence events.

- [ ] **Step 6: Run final regression**

Run: `pytest -q tests/test_hotspot_event_clips.py tests/test_hotspot_event_matching.py tests/test_hotspot_event_clips_ui.py tests/test_hotspot_media_api.py tests/test_semantic_matching.py`

Expected: all event, matching, API and UI tests pass.

