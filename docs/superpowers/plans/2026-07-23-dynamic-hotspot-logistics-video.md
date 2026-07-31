# 动态热点物流视频编排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将热点跟进从固定四段短片改为“热点类型 × 物流主题 × 证据素材”的动态 60 秒以上成片流程。

**Architecture:** 先由规则与 MiMo 生成结构化内容简报，再用简报约束热点事件片段和 Buffalo 原有素材的匹配；脚本规划器输出 8～10 个分镜，渲染器按真实语音时长生成字幕并在成片后执行技术与语义质检。热点母片只能通过事件片段引用，静态图仅作少量证据卡，不能作为视频主体。

**Tech Stack:** FastAPI、SQLite、Python、FFmpeg/FFprobe、MiMo 多模态/文本/TTS、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 建立动态热点×物流主题简报

**Files:**
- Create: `hotspot_logistics_planner.py`
- Modify: `app.py`（新增简报接口）
- Test: `tests/test_hotspot_logistics_planner.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_planner_returns_relevant_logistics_angles_for_risk_hotspot():
    from hotspot_logistics_planner import build_brief
    brief = build_brief({"title_zh": "约翰内斯堡道路安全事件", "summary_zh": "部分道路出现危险情况"}, [])
    assert brief["angle"]
    assert brief["logistics_topic"] in {"末端配送安全", "路线稳定性", "本地快递时效"}
    assert brief["required_evidence"]["hotspot_video"] >= 2

def test_planner_does_not_claim_unsupported_buffalo_capability():
    from hotspot_logistics_planner import build_brief
    brief = build_brief({"title_zh": "南非电商增长", "summary_zh": "电商订单增长"}, [])
    assert "百分百" not in brief["brand_claims"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q tests/test_hotspot_logistics_planner.py`
Expected: FAIL because `hotspot_logistics_planner` does not exist.

- [ ] **Step 3: Implement the planner**

Implement `classify_hotspot()` with explicit signal groups (`risk`, `strike`, `ecommerce_growth`, `infrastructure`, `policy`, `weather`) and `LOGISTICS_TOPICS` with evidence requirements. `build_brief()` must return `angle`, `logistics_topic`, `claim`, `narrative_beats`, `required_evidence`, `brand_claims`, `negative_claims`, `tone`, and `target_duration_ms=60000`. MiMo may refine the angle only after deterministic candidate filtering; invalid JSON falls back to the deterministic candidate.

- [ ] **Step 4: Add the API and run tests**

Add `POST /api/hotspot-events/{event_id}/logistics-brief`, return the brief plus the source event, and cache the brief in the project snapshot. Run: `pytest -q tests/test_hotspot_logistics_planner.py tests/test_video_followup_api.py`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hotspot_logistics_planner.py app.py tests/test_hotspot_logistics_planner.py
git commit -m "feat: build dynamic hotspot logistics briefs"
```

### Task 2: 动态素材证据编排与 60 秒脚本

**Files:**
- Create: `hotspot_video_planner.py`
- Modify: `video_generation.py`, `video_renderer.py`, `static/video-followup.html`
- Test: `tests/test_hotspot_video_planner.py`, `tests/test_video_generation_rendering.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_has_at_least_sixty_seconds_and_mixed_evidence():
    from hotspot_video_planner import plan_followup_scenes
    scenes = plan_followup_scenes(brief_fixture(), hotspot_events_fixture(), owned_segments_fixture())
    assert sum(scene["duration_ms"] for scene in scenes) >= 60_000
    assert sum(scene["scene_role"] == "hotspot_evidence" for scene in scenes) >= 3
    assert sum(scene["scene_role"] == "owned_proof" for scene in scenes) >= 4

def test_unrelated_hotspot_is_excluded():
    from hotspot_video_planner import plan_followup_scenes
    scenes = plan_followup_scenes(brief_fixture(), unrelated_events_fixture(), owned_segments_fixture())
    assert all(scene.get("event_clip_id") != 999 for scene in scenes)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q tests/test_hotspot_video_planner.py`
Expected: FAIL because the planner does not exist.

- [ ] **Step 3: Implement evidence allocation**

Implement `plan_followup_scenes(brief, hotspot_events, owned_segments)` with a 60–75 second budget, 8–10 scenes, at least 3 relevant hotspot video clips and 4 owned video segments. Use event keyword/entity overlap plus topic compatibility; reject unrelated events instead of filling empty slots. Images may fill at most 15% of planned duration and never two consecutive scenes. Each scene carries `scene_role`, `evidence_type`, `event_clip_id` or `asset_segment_id`, `duration_ms`, `visual`, `voiceover`, `text_overlay`, and `match_reasons`.

- [ ] **Step 4: Replace fixed frontend draft creation**

Change `static/video-followup.html` to request the logistics brief and plan, show the selected angle and evidence list, and submit the generated scenes with `target_duration_ms=60000`. Remove the hardcoded four voiceovers. Disable generation when evidence requirements are not met and show the missing evidence instead.

- [ ] **Step 5: Make the backend preserve dynamic scene fields**

Update normalization and planning so `scene_role`, `evidence_type`, `match_reasons`, and the dynamic brief remain in the revision and job report. Keep the inactive-mother/event-clip boundary already implemented.

- [ ] **Step 6: Run tests and commit**

Run: `pytest -q tests/test_hotspot_video_planner.py tests/test_video_generation_rendering.py tests/test_video_followup_api.py`. Expected: PASS.

```bash
git add hotspot_video_planner.py video_generation.py video_renderer.py static/video-followup.html tests/test_hotspot_video_planner.py tests/test_video_generation_rendering.py
git commit -m "feat: plan 60-second hotspot videos from mixed evidence"
```

### Task 3: 音画同步与动态素材质量门禁

**Files:**
- Modify: `video_renderer.py`, `video_generation.py`, `video_quality/service.py`, `video_quality/video_evaluator.py`
- Test: `tests/test_video_audio_sync.py`, `tests/test_video_generation_state.py`

- [ ] **Step 1: Write failing tests**

```python
def test_subtitle_cues_use_measured_audio_duration():
    from video_renderer import build_subtitle_cues
    cues = build_subtitle_cues("第一句。第二句。", 4.2)
    assert cues[-1]["end"] == 4.2
    assert cues[0]["end"] < cues[1]["end"]

def test_quality_gate_rejects_short_or_static_hotspot_followup():
    from video_generation import route_video_evaluation_quality
    decision = route_video_evaluation_quality({"evaluation_status": "completed", "overall_score": 86,
        "passed": True, "technical_issues": [{"category": "duration", "severity": "high"}], "issues": []})
    assert decision.status.value == "needs_review"
```

- [ ] **Step 2: Implement measurable checks**

Add checks for target duration (>=60 seconds for `hotspot_followup`), hotspot video count, owned dynamic video count, image ratio, event/topic compatibility, subtitle cue coverage, and audio/video duration delta <=0.25 seconds. A static image fallback is a hard failure when the evidence plan requires a video.

- [ ] **Step 3: Preserve evaluator errors**

Fix `run_claimed_job()` to reload the latest job report after a stage handler before merging the gate, so `video_evaluation`, artifacts, and exact MiMo errors cannot be overwritten by stale state.

- [ ] **Step 4: Make MiMo evaluation actionable**

Pass the dynamic brief and evidence manifest to `video_evaluator`; require issue evidence to reference frame IDs and scene roles. If MiMo is unavailable, store `evaluation_status=unavailable`, the exact error, and keep the job in `needs_review` with a visible retry reason.

- [ ] **Step 5: Run tests and commit**

Run: `pytest -q tests/test_video_audio_sync.py tests/test_video_generation_state.py tests/test_video_quality_api.py`. Expected: PASS.

```bash
git add video_renderer.py video_generation.py video_quality/service.py video_quality/video_evaluator.py tests/test_video_audio_sync.py tests/test_video_generation_state.py
git commit -m "feat: enforce duration, evidence, and audio sync gates"
```

### Task 4: 浏览器验收与样本回归

**Files:**
- Modify: `docs/产研部署交接说明.md`
- Sync: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`
- Test: `scripts/run_sample_harness.py`

- [ ] **Step 1: Run a local sample**

Use one confirmed hotspot event and the existing Buffalo library. Verify the project snapshot contains a dynamic brief, 60–75 seconds of scenes, at least 3 hotspot video clips, at least 4 owned video segments, and no unrelated event IDs.

- [ ] **Step 2: Verify the rendered artifact**

Run FFprobe and the quality harness. Expected: duration >=60 seconds, audio present, subtitle cues present, no consecutive static scenes, and a saved MiMo report or an explicit unavailable error.

- [ ] **Step 3: Verify the 8080 browser workflow**

Force refresh, select a hotspot, inspect the generated angle/evidence plan, create the draft, cancel once, resume once, and confirm the project page exposes the brief, scene evidence, duration budget, and quality decision.

- [ ] **Step 4: Update handoff docs and commit**

Document the new dynamic workflow, evidence thresholds, MiMo failure behavior, and the exact local test commands. Append a timestamped CST entry to the Obsidian improvement log before committing.

```bash
git add docs/产研部署交接说明.md docs/superpowers/plans/2026-07-23-dynamic-hotspot-logistics-video.md
git commit -m "docs: document dynamic hotspot video workflow"
```
