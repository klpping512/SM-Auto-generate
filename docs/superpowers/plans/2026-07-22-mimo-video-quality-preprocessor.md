# MiMo Video Quality Preprocessor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed the model-independent parts of `claude-video` as a bounded video preprocessing and MiMo two-stage quality-evaluation service without replacing SA-LogiFlow's existing generation queue or renderer.

**Architecture:** A new `video_quality` package resolves local/URL sources, performs cancelable FFmpeg preprocessing, produces timestamped evidence, validates MiMo JSON, and derives regeneration guidance. Existing `preview_quality_check` invokes the service after the deterministic renderer passes, while the existing DB job, revision, budget, cancel and manual-review paths remain authoritative.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, httpx, FFmpeg/ffprobe, optional yt-dlp and faster-whisper, SQLite, pytest.

---

### Task 1: Strict contracts and licensed source boundary

**Files:**
- Create: `video_quality/__init__.py`
- Create: `video_quality/schemas.py`
- Create: `third_party/claude_video/LICENSE`
- Create: `third_party/claude_video/NOTICE.md`
- Create: `tests/test_video_quality_schemas.py`

- [ ] **Step 1: Write failing contract tests**

Define tests that instantiate `VideoQualityInput`, reject scores outside 0–100, reject invalid severities, and make `quality_failed()` true below 80 or when any issue is high.

```python
def test_high_issue_fails_even_with_high_score():
    report = valid_report(overall_score=92, passed=True)
    report["issues"] = [{**valid_issue(), "severity": "high"}]
    parsed = VideoEvaluationReport.model_validate(report)
    assert quality_failed(parsed, threshold=80) is True
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest -q tests/test_video_quality_schemas.py`
Expected: import failure because `video_quality.schemas` does not exist.

- [ ] **Step 3: Implement Pydantic contracts and license files**

Use strict fields matching the requested JSON structure. Add `evaluation_status`, `review_stage`, `frame_index`, and `transcript_status` as additive audit fields. Copy the upstream MIT license verbatim and state the fixed source commit in `NOTICE.md`.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `pytest -q tests/test_video_quality_schemas.py`
Expected: all contract tests pass.

### Task 2: Cancelable source, metadata and technical validation

**Files:**
- Create: `video_quality/process_runner.py`
- Create: `video_quality/source_resolver.py`
- Create: `video_quality/technical_validator.py`
- Create: `tests/test_video_quality_technical.py`

- [ ] **Step 1: Write failing parser and safety tests**

Cover local-path resolution, disallowed URL schemes, missing yt-dlp, subprocess timeout/cancellation, ffprobe frame-rate parsing, black/freeze/silence stderr parsing, and a corrupt-video failure that prevents model evaluation.

```python
def test_resolver_rejects_non_https_remote_source(tmp_path):
    with pytest.raises(VideoSourceError, match="HTTPS"):
        resolve_video_source("http://example.com/a.mp4", tmp_path)
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_video_quality_technical.py`
Expected: imports fail for missing modules.

- [ ] **Step 3: Implement bounded media execution and parsing**

`run_process()` uses `Popen(start_new_session=True)`, polls every 0.2 seconds, terminates the process group on timeout/cancel, and raises typed errors with at most 1,000 stderr characters. `resolve_video_source()` accepts existing video files or HTTPS URLs and runs yt-dlp with `--no-playlist`, `--max-filesize 300M`, VTT subtitles, metadata and a fixed timeout. `validate_video()` runs ffprobe, a decode check, and FFmpeg black/freeze/silence filters.

- [ ] **Step 4: Run and verify GREEN**

Run: `pytest -q tests/test_video_quality_technical.py`
Expected: all tests pass.

### Task 3: Frame modes, timestamps and near-duplicate removal

**Files:**
- Create: `video_quality/frame_extractor.py`
- Create: `tests/test_video_quality_frames.py`

- [ ] **Step 1: Write failing algorithm tests**

Cover automatic budgets, first/last even sampling, mean-pixel delta, greedy last-kept deduplication, three named modes, global cap, and a 5–10 fps focus request capped after deduplication.

```python
def test_focus_mode_uses_requested_density_but_obeys_cap():
    plan = plan_extraction(4.0, mode="detailed", focus=True, requested_fps=8, max_frames=20)
    assert plan.fps == 8
    assert plan.target_frames == 20
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_video_quality_frames.py`
Expected: import failure.

- [ ] **Step 3: Adapt upstream algorithms**

Port scene detection, I-frame extraction, fixed timestamp extraction, scaling, even sampling and 16×16 grayscale deduplication from upstream `frames.py`. Replace `SystemExit`, hard-coded package-manager messages and unbounded subprocess calls. Use caps: efficient 50, balanced 100, detailed 100; service defaults to 40 globally and 40 across focus windows.

- [ ] **Step 4: Run and verify GREEN**

Run: `pytest -q tests/test_video_quality_frames.py`
Expected: all unit tests pass.

### Task 4: Transcript priority and clipping

**Files:**
- Create: `video_quality/transcript_service.py`
- Create: `tests/test_video_quality_transcript.py`

- [ ] **Step 1: Write failing transcript tests**

Cover YouTube rolling VTT cue deduplication, overlap clipping, VTT output, storyboard-to-timestamp conversion, VTT priority over Whisper, and graceful `unavailable` status when no local model exists.

```python
def test_known_storyboard_skips_whisper(tmp_path):
    result = build_transcript(
        video_path=tmp_path / "video.mp4",
        output_path=tmp_path / "transcript.vtt",
        storyboard={"scenes": [{"duration": 4, "voiceover": "第一句"}]},
        whisper_model_path=None,
    )
    assert result.status == "storyboard"
    assert result.segments[0]["start"] == 0
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_video_quality_transcript.py`
Expected: import failure.

- [ ] **Step 3: Adapt VTT parser and optional local ASR**

Port `parse_vtt`, rolling-cue deduplication and range filtering from upstream. Use known scene duration and voiceover first, resolved VTT second, and optional `faster_whisper.WhisperModel` last. Any ASR import/model failure becomes a warning and an empty valid VTT instead of aborting visual QA.

- [ ] **Step 4: Run and verify GREEN**

Run: `pytest -q tests/test_video_quality_transcript.py`
Expected: all tests pass.

### Task 5: MiMo multimodal JSON call and evidence validation

**Files:**
- Modify: `model_router.py`
- Modify: `static/config.html`
- Create: `video_quality/video_evaluator.py`
- Create: `tests/test_video_quality_evaluator.py`
- Modify: `tests/test_model_router.py`

- [ ] **Step 1: Write failing router and evaluator tests**

Verify a `video_evaluator` route exists with text+vision capability, the request interleaves timestamp labels and Base64 `image_url` items, sets JSON response mode, records actual usage, parses fenced JSON, rejects unknown evidence-frame IDs, and retries invalid JSON at most once.

```python
assert body["response_format"] == {"type": "json_object"}
assert any(item.get("type") == "image_url" for item in body["messages"][1]["content"])
assert "FRAME_0001@1.50s" in str(body["messages"])
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_model_router.py tests/test_video_quality_evaluator.py`
Expected: failures for missing route and evaluator.

- [ ] **Step 3: Implement bounded multimodal JSON evaluation**

Add `call_multimodal_json()` to the existing router using the configured OpenAI-compatible endpoint, `api-key` for MiMo, Base64 images, `max_completion_tokens`, `response_format={"type":"json_object"}`, existing cache and budget tables, and one retry for 429/5xx/timeout. The evaluator defines the requested schema in the system prompt and treats evidence-frame IDs as an allow-list.

- [ ] **Step 4: Run and verify GREEN**

Run: `pytest -q tests/test_model_router.py tests/test_video_quality_evaluator.py`
Expected: all tests pass.

### Task 6: Two-stage service, prompt optimizer and regeneration stop rules

**Files:**
- Create: `video_quality/video_preprocessor.py`
- Create: `video_quality/prompt_optimizer.py`
- Create: `video_quality/regeneration_controller.py`
- Create: `video_quality/service.py`
- Create: `tests/test_video_quality_service.py`

- [ ] **Step 1: Write failing orchestration tests**

Cover artifact manifest creation, no second call for a passing report, merged/expanded risk windows for high issues, maximum 40 focus frames, fail conditions, optimized prompt output, automatic regeneration default-off, max two attempts, score decline and less-than-three-point stop.

```python
def test_passing_scan_does_not_run_focus_review(fake_evaluator, sample_video):
    result = run_quality_mvp(..., evaluator=fake_evaluator.returning(score=88, passed=True))
    assert fake_evaluator.call_count == 1
    assert result["regeneration_decision"]["action"] == "none"
```

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_video_quality_service.py`
Expected: import failure.

- [ ] **Step 3: Implement preprocessing and two-stage orchestration**

Persist `metadata.json`, `technical-report.json`, `frames/index.json`, `transcript.vtt`, `evaluation.json`, `problem-segments.json`, `optimized-generation.json`, and `manifest.json`. First pass submits at most 40 frames. Only high issues trigger merged focus windows and a second call. `prompt_optimizer` consumes existing regeneration fields without a third model call.

- [ ] **Step 4: Run and verify GREEN**

Run: `pytest -q tests/test_video_quality_service.py`
Expected: all tests pass.

### Task 7: Existing pipeline and admin API integration

**Files:**
- Modify: `models.py`
- Modify: `app.py`
- Modify: `video_generation.py`
- Create: `tests/test_video_quality_api.py`
- Modify: `tests/test_video_generation_state.py`

- [ ] **Step 1: Write failing integration tests**

Test admin-only standalone evaluation, API local-path allow-list, generated-preview invocation after technical pass, low score/high issue routing to `needs_review`, preserved preview path, and skipped model call when technical validation is fatally invalid.

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_video_quality_api.py tests/test_video_generation_state.py`
Expected: endpoint 404 and missing integration behavior.

- [ ] **Step 3: Add the narrow integration**

Add `VideoQualityRequest`; expose `POST /api/video-quality/evaluate` for admins. In `preview_quality`, retain current renderer checks, then run semantic QA when `VIDEO_QUALITY_ENABLED` is not false and MiMo is configured. Save artifacts under `static/uploads/video-quality/<job-id>/`. On quality failure or evaluator unavailability, return `needs_review`; do not create another task automatically. Add `VIDEO_QUALITY_AUTO_REGENERATE=0` documentation only—no hidden automatic API call.

- [ ] **Step 4: Run and verify GREEN**

Run: `pytest -q tests/test_video_quality_api.py tests/test_video_generation_state.py`
Expected: all tests pass.

### Task 8: CLI, dependency notes, full local run and documentation

**Files:**
- Create: `scripts/run_video_quality_mvp.py`
- Modify: `requirements-media-ai.txt`
- Modify: `.env.example`
- Modify: `docs/AI视频一键生成与质量门禁操作说明.md`
- Create: `tests/test_video_quality_cli.py`

- [ ] **Step 1: Write failing CLI argument test**

Verify required `--video-source`, optional JSON/storyboard/reference image/platform/output arguments, default auto-regeneration false, and nonzero exit for invalid sources without exposing keys.

- [ ] **Step 2: Run and verify RED**

Run: `pytest -q tests/test_video_quality_cli.py`
Expected: CLI module is absent.

- [ ] **Step 3: Implement CLI and operating documentation**

The CLI loads project `.env`, accepts `--input-json` or explicit flags, invokes the same service, prints only run directory, score, pass status, issue count and regeneration action. Document optional installs using the existing Tsinghua PyPI mirror, environment flags, API request example, output files, timeout behavior and YouTube URL limitations.

- [ ] **Step 4: Run focused tests and syntax checks**

Run:

```bash
pytest -q tests/test_video_quality_*.py tests/test_model_router.py tests/test_video_generation_state.py
python3 -m py_compile model_router.py video_generation.py video_quality/*.py scripts/run_video_quality_mvp.py
```

Expected: zero failures and exit 0.

- [ ] **Step 5: Run a real local video**

Use:

```bash
python3 scripts/run_video_quality_mvp.py \
  --video-source static/uploads/video/sample-24edc50dd82d49ab9d69b4f357344bcd.mp4 \
  --input-json data/samples/24edc50dd82d49ab9d69b4f357344bcd/manifest.json \
  --output-dir data/video-quality-runs/local-mvp
```

Expected: all eight requested artifacts exist; if MiMo is configured, `evaluation.json` has `evaluation_status=completed`, a validated score and evidence-backed issues. If MiMo is not configured, the command exits with an explicit configuration error rather than a fake report.

- [ ] **Step 6: Run regression and browser-safe API smoke tests**

Run: `pytest -q --ignore=tests/test_twitter_adapter.py`
Expected: no new failures. Use HTTP/API checks only; do not open or focus the user's Chrome window.

- [ ] **Step 7: Synchronize docs and Obsidian before any Git operation**

Copy modified product Markdown to the Obsidian Distribution Manager directory and append a `YYYY-MM-DD HH:mm:ss CST｜MiMo 视频自动质检预处理层 MVP` record containing the goal, prior state, exact changes, main files, tests, real-run output, Git status and remaining manual actions. Do not copy secrets or raw API responses containing credentials.
