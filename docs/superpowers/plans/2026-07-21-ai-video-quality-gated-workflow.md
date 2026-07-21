# AI Video Quality-Gated Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent, cancelable, idempotent AI-chat-to-video workflow that survives navigation and only produces a final MP4 after script, match, preview, and technical quality gates pass.

**Architecture:** Add a server-side video project aggregate and DB-backed job queue with leases, heartbeat, events, revision snapshots, and cancel requests. A focused `video_generation.py` service owns state transitions and orchestrates existing script generation, semantic matching, TTS, FFmpeg preview/final rendering, and quality routing; pages use project/job APIs rather than `localStorage` as the source of truth.

**Tech Stack:** Python 3.12, FastAPI, SQLite, asyncio worker loop, FFmpeg/ffprobe, vanilla HTML/CSS/JavaScript, pytest, Node syntax checks.

---

### Task 1: Persistent video project and job state

**Files:**
- Modify: `database.py`
- Create: `tests/test_video_generation_db.py`

- [ ] **Step 1: Write failing schema and repository tests**

Cover project creation, immutable revisions, idempotent active jobs, events, active-job lookup, legal status updates, cancel request, lease acquisition, heartbeat, lease expiry recovery, and terminal states.

```python
def test_generation_job_is_idempotent_for_same_revision(tmp_db):
    project = db.create_video_project(user_id=1, source_type="chat", source_snapshot={"topic": "海外仓"})
    revision = db.create_video_project_revision(project["id"], {"scenes": []})
    first, created = db.create_or_get_video_generation_job(project["id"], revision["id"], 1, "same-key")
    second, created_again = db.create_or_get_video_generation_job(project["id"], revision["id"], 1, "same-key")
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]
```

- [ ] **Step 2: Run the DB tests and confirm failure**

Run: `pytest -q tests/test_video_generation_db.py`
Expected: fail because project/job repository functions do not exist.

- [ ] **Step 3: Add tables and focused repository functions**

Create `video_projects`, `video_project_revisions`, `video_generation_jobs`, and `video_generation_events`. Use a partial unique index for active `idempotency_key`, store JSON through existing helpers, and keep legacy `video_render_jobs` untouched for migration compatibility.

- [ ] **Step 4: Run DB tests**

Run: `pytest -q tests/test_video_generation_db.py`
Expected: pass.

### Task 2: State machine, quality routing, and worker lease

**Files:**
- Create: `video_generation.py`
- Create: `tests/test_video_generation_state.py`

- [ ] **Step 1: Write failing pure state-machine tests**

```python
def test_low_match_quality_routes_to_review():
    result = route_match_quality([{"scene": 1, "score": 64, "hard_failures": []}])
    assert result.status == "needs_review"
    assert result.stage == "match_quality_check"
```

Cover legal transitions, illegal transitions, hard script failures, calibrated match thresholds, maximum two preview repairs, cancellation at every stage, and restart recovery.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_generation_state.py`
Expected: fail because `video_generation.py` does not exist.

- [ ] **Step 3: Implement the pure state machine and worker shell**

Define `PipelineStage`, `JobStatus`, `QualityDecision`, transition validation, cancellation checks, deterministic `idempotency_key`, lease owner identity, `claim_next_job()`, `renew_lease()`, and `run_claimed_job()` with one function per stage.

- [ ] **Step 4: Run state tests**

Run: `pytest -q tests/test_video_generation_state.py`
Expected: pass.

### Task 3: Cancelable and bounded rendering

**Files:**
- Modify: `video_renderer.py`
- Modify: `video_generation.py`
- Create: `tests/test_video_generation_rendering.py`
- Modify: `tests/test_media_api.py`

- [ ] **Step 1: Write failing command and cancellation tests**

Assert generated FFmpeg input enforces both `start_ms` and `end_ms`, preview output is 540×960, final output is 1080×1920, a cancel callback prevents the next scene, and an active process group is terminated.

```python
def test_scene_command_bounds_selected_shot():
    command = build_scene_command(source_start=6.0, source_end=12.4, duration=5.0)
    assert "-ss" in command
    assert "-t" in command
    assert selected_input_duration(command) <= 6.4
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_generation_rendering.py tests/test_media_api.py`
Expected: new assertions fail because end time and cancellation are not enforced.

- [ ] **Step 3: Refactor renderer around a cancel-aware process runner**

Use `subprocess.Popen(..., start_new_session=True)` and a registry keyed by job ID. Check cancellation before TTS, before/after every scene, before concatenation, and before quality gates. Terminate the process group gracefully, then force kill after a bounded wait. Keep completed TTS/scene artifacts; delete partial current outputs.

- [ ] **Step 4: Add preview and quality reports**

Produce 540×960 preview and 1080×1920 final output from the same revision. Technical gates cover streams, duration, subtitle cues, resolution, missing scenes, shot bounds, and output provenance.

- [ ] **Step 5: Run rendering tests**

Run: `pytest -q tests/test_video_generation_rendering.py tests/test_media_api.py`
Expected: pass.

### Task 4: Project and generation APIs

**Files:**
- Modify: `models.py`
- Modify: `app.py`
- Create: `tests/test_video_generation_api.py`

- [ ] **Step 1: Write failing API contract tests**

Cover create/read project, update revision, idempotent generate, active task recovery, cancel pending, request cancel running, resume reviewed job, retry failed job, authorization isolation, and legacy render compatibility.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_generation_api.py`
Expected: 404 for the new endpoints.

- [ ] **Step 3: Implement Pydantic contracts and endpoints**

Add:

```text
POST /api/video-projects
GET  /api/video-projects/{id}
PUT  /api/video-projects/{id}/revision
POST /api/video-projects/{id}/generate
GET  /api/video-generation/jobs/{id}
GET  /api/video-generation/jobs/active
POST /api/video-generation/jobs/{id}/cancel
POST /api/video-generation/jobs/{id}/resume
POST /api/video-generation/jobs/{id}/retry
```

Use the authenticated user for every query. Keep `/api/douyin/render` as a compatibility adapter that creates or reuses a project job.

- [ ] **Step 4: Start and stop the DB worker through app lifespan**

Start one lease-based worker loop, recover expired leases, and stop without claiming more work during shutdown.

- [ ] **Step 5: Run API tests**

Run: `pytest -q tests/test_video_generation_api.py`
Expected: pass.

### Task 5: AI chat entry and persistent global task center

**Files:**
- Modify: `static/chat.html`
- Modify: `static/common.js`
- Modify: `static/common.css`
- Create: `tests/test_video_generation_ui.py`

- [ ] **Step 1: Write failing front-end contract tests**

Assert AI chat creates a project before generation, repeated clicks reuse the active job, the task center loads `/active`, cancel calls the cancel endpoint, and no generation state depends solely on page-local variables.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_video_generation_ui.py`
Expected: fail because task center and APIs are absent.

- [ ] **Step 3: Implement the AI chat project card**

The Douyin result exposes one primary “生成视频” action. It posts the conversation/result snapshot to `/api/video-projects`, starts generation, and renders “查看项目/取消生成” against the returned IDs.

- [ ] **Step 4: Implement the global task center**

Initialize from `common.js` on every authenticated page. Poll only while active, restore after refresh, display real stage/progress/queue state, and disable cancellation for terminal jobs.

- [ ] **Step 5: Run UI tests and Node syntax checks**

Run: `pytest -q tests/test_video_generation_ui.py`
Expected: pass.

### Task 6: Unified video project workspace

**Files:**
- Create: `static/video-project.html`
- Modify: `static/common.js`
- Modify: `static/editor.html`
- Modify: `static/video-workbench.html`
- Modify: `tests/test_video_generation_ui.py`

- [ ] **Step 1: Extend failing UI contracts**

Assert project page loads by ID, shows the revision storyboard, issue-only review list, voice selection, preview/final player, retry/resume/cancel controls, and advanced editor/matcher links containing the project ID.

- [ ] **Step 2: Implement the project page**

Render project status, script, scenes, selected segment boundaries, per-scene quality, low-resolution preview, final MP4, and actionable issue cards. Resume posts a new revision and continues from the nearest invalidated stage.

- [ ] **Step 3: Connect legacy advanced views**

When `project_id` exists, editor and matcher read/write the server revision. The direct matcher route creates a project/revision instead of redirecting to an empty editor. Keep legacy local draft behavior only for non-project content.

- [ ] **Step 4: Run UI tests**

Run: `pytest -q tests/test_video_generation_ui.py tests/test_video_workbench_ui.py`
Expected: pass.

### Task 7: End-to-end and regression verification

**Files:**
- Modify: `docs/功能介绍.md`
- Create: `docs/AI视频一键生成与质量门禁操作说明.md`
- Modify: relevant tests only if verification exposes a regression

- [ ] **Step 1: Run focused tests**

Run: `pytest -q tests/test_video_generation_db.py tests/test_video_generation_state.py tests/test_video_generation_rendering.py tests/test_video_generation_api.py tests/test_video_generation_ui.py tests/test_video_workbench_ui.py tests/test_semantic_matching.py`
Expected: pass.

- [ ] **Step 2: Run the existing suite excluding the known Twitter collection conflict**

Run: `pytest -q --ignore=tests/test_twitter_adapter.py`
Expected: pass.

- [ ] **Step 3: Perform real browser acceptance on a non-production local port**

Generate “南非海外仓如何完成货物入库和包裹检查”; navigate away and return; triple-click generation; cancel during render; resume; verify issue-only review; complete preview/final render; check console errors and final MP4.

- [ ] **Step 4: Update docs and Obsidian**

Document the single user workflow, cancellation semantics, quality states, known material limitations, test outputs, browser results, Git state, and any manual review requirements. Synchronize changed docs before any implementation commit.
