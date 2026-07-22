# 样本优先证据 Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不编造事实、不自动使用版权不明媒体的前提下，从南非可信热点和 Buffalo 自有素材生成一条内部预览视频、一组图文和一篇公众号软文，并记录来源、质量问题和成本。

**Architecture:** 复用现有 `hotspot_fetcher.py`、素材语义层和视频项目，将新增逻辑拆为信源预置、证据包、模型路由和样本编排四个边界清晰的模块。事实与品牌能力分别入库；模型只能消费已确认的证据，预算不足或模型不可用时保留确定性模板结果；视频低分时只允许生成带水印的内部预览。

**Tech Stack:** FastAPI、SQLite、httpx、FFmpeg、现有 MiMo/OpenAI-compatible API、pytest、原生 HTML/JavaScript。

---

### Task 1: 预置并隔离验证 5 个官方信源

**Files:**
- Modify: `database.py`
- Modify: `hotspot_fetcher.py`
- Modify: `app.py`
- Test: `tests/test_hotspot_fetcher.py`

- [ ] **Step 1: 写失败测试**

```python
def test_default_sources_seed_exactly_five_without_overwriting_admin_changes(tmp_db):
    import hotspot_fetcher
    assert hotspot_fetcher.seed_default_sources() == 5
    assert len(tmp_db.list_hotspot_sources(enabled_only=True)) == 5
    assert hotspot_fetcher.seed_default_sources() == 0

@pytest.mark.asyncio
async def test_fetch_result_reports_health_per_source(tmp_db, tmp_path):
    feed_xml = """<rss><channel><item><title>Durban port update</title>
      <link>https://gov.za/durban</link><description>South Africa freight</description>
    </item></channel></rss>"""
    def handler(request):
        if str(request.url) == "https://gov.za/feed.xml":
            return httpx.Response(200, text=feed_xml, request=request)
        if str(request.url) == "https://gov.za/durban":
            return httpx.Response(200, text="Official update", request=request)
        return httpx.Response(200, json={"query": {"pages": {}}}, request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await hotspot_fetcher.fetch_hotspots(
        tmp_path,
        feeds=[{"name": "Official", "url": "https://gov.za/feed.xml", "allowed_domains": ["gov.za"]}],
        client=client,
    )
    await client.aclose()
    assert result["source_health"] == [{"name": "Official", "status": "ok", "items": 1, "error": ""}]
```

- [ ] **Step 2: 验证测试按预期失败**

Run: `pytest -q tests/test_hotspot_fetcher.py -k 'default_sources or source_health'`

Expected: FAIL，缺少 `seed_default_sources` 或 `source_health`。

- [ ] **Step 3: 最小实现**

在 `hotspot_fetcher.py` 定义固定的 `DEFAULT_OFFICIAL_SOURCES`，字段仅包含 `name`、`url`、`allowed_domains`、`purpose`。`seed_default_sources()` 只在 Feed URL 不存在时插入，已有管理员配置不覆盖；总启用数达到 5 时其余预置为停用。`fetch_hotspots()` 为每个来源记录独立健康状态，单源失败不终止其他来源。

- [ ] **Step 4: 运行测试**

Run: `pytest -q tests/test_hotspot_fetcher.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add hotspot_fetcher.py database.py app.py tests/test_hotspot_fetcher.py
git commit -m "feat: seed and isolate South Africa hotspot sources"
```

### Task 2: 建立事实证据包和 Buffalo 品牌证据包

**Files:**
- Create: `evidence_harness.py`
- Modify: `database.py`
- Modify: `app.py`
- Modify: `models.py`
- Test: `tests/test_evidence_harness.py`

- [ ] **Step 1: 写失败测试**

```python
def test_build_evidence_package_keeps_external_and_brand_claims_separate(tmp_db):
    package = evidence_harness.build_package(hotspot_id, created_by=user_id)
    assert package["fact_claims"][0]["source_url"] == "https://gov.za/item"
    assert package["brand_claims"] == []
    assert package["status"] == "needs_brand_evidence"

def test_unconfirmed_brand_claim_cannot_enter_publishable_package(tmp_db):
    claim_id = tmp_db.create_brand_evidence({"claim": "48小时送达", "status": "draft"})
    package = evidence_harness.build_package(hotspot_id, brand_evidence_ids=[claim_id])
    assert package["brand_claims"] == []
```

- [ ] **Step 2: 验证失败**

Run: `pytest -q tests/test_evidence_harness.py`

Expected: FAIL，模块和数据库表尚不存在。

- [ ] **Step 3: 最小实现**

新增 `evidence_packages`、`evidence_claims`、`brand_evidence` 三张表。事实 claim 必须包含来源 URL、发布者、摘录、发布时间和抓取时间；品牌 claim 必须包含内部证据说明、可公开状态和人工确认人。`build_package()` 只合并 `confirmed` 品牌证据，并返回 `ready`、`needs_brand_evidence` 或 `needs_fact_review`。

- [ ] **Step 4: 增加 API 并验证**

新增：

```text
GET  /api/evidence-packages/{id}
POST /api/hotspots/{id}/evidence-package
GET  /api/brand-evidence
POST /api/brand-evidence
PUT  /api/brand-evidence/{id}/confirm
```

Run: `pytest -q tests/test_evidence_harness.py tests/test_truth_guard.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add evidence_harness.py database.py app.py models.py tests/test_evidence_harness.py
git commit -m "feat: add fact and Buffalo evidence packages"
```

### Task 3: 增加可替换模型角色、缓存和预算熔断

**Files:**
- Create: `model_router.py`
- Modify: `database.py`
- Modify: `app.py`
- Test: `tests/test_model_router.py`

- [ ] **Step 1: 写失败测试**

```python
def test_router_reads_key_from_environment_and_never_returns_secret(tmp_db, monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "secret")
    route = model_router.get_route("planner_text")
    assert route["api_key_env"] == "MIMO_API_KEY"
    assert "secret" not in str(route)

def test_cached_call_does_not_consume_budget_twice(tmp_db):
    first = model_router.record_call(job_id, "planner_text", cache_key="same", input_tokens=100, output_tokens=20)
    second = model_router.record_call(job_id, "planner_text", cache_key="same", input_tokens=100, output_tokens=20)
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert tmp_db.get_model_budget(job_id)["calls_used"] == 1

def test_budget_limit_stops_remote_call(tmp_db):
    with pytest.raises(model_router.BudgetExceeded):
        model_router.reserve_call(job_id, estimated_tokens=2000)
```

- [ ] **Step 2: 验证失败**

Run: `pytest -q tests/test_model_router.py`

Expected: FAIL，缺少路由与预算模块。

- [ ] **Step 3: 最小实现**

数据库新增 `model_role_configs`、`model_budgets`、`model_call_cache`、`model_call_usage`。固定角色为 `planner_text`、`vision_tagger`、`critic`、`tts`；配置只保存环境变量名，不保存 Key。缓存键为输入哈希、角色、模型和提示词版本。每个样本任务默认最多 4 次远程调用、20,000 输入 Token、6,000 输出 Token；超限抛出 `BudgetExceeded`，不自动重试质量问题。

- [ ] **Step 4: 增加管理员配置 API 并验证**

新增 `GET/PUT /api/model-routes/{role}` 与 `GET /api/model-budgets/{job_id}`。

Run: `pytest -q tests/test_model_router.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add model_router.py database.py app.py tests/test_model_router.py
git commit -m "feat: add bounded replaceable model routing"
```

### Task 4: 从一个证据包生成三种一致但不重复的样本

**Files:**
- Create: `sample_harness.py`
- Modify: `database.py`
- Modify: `app.py`
- Modify: `models.py`
- Test: `tests/test_sample_harness.py`

- [ ] **Step 1: 写失败测试**

```python
def test_three_samples_share_claim_ids_but_use_distinct_structures(tmp_db):
    bundle = sample_harness.generate_bundle(package_id, created_by=user_id)
    assert set(bundle["video"]["claim_ids"]) == set(bundle["carousel"]["claim_ids"]) == set(bundle["wechat"]["claim_ids"])
    assert len(bundle["video"]["scenes"]) >= 5
    assert 5 <= len(bundle["carousel"]["pages"]) <= 7
    assert 800 <= len(bundle["wechat"]["body"]) <= 1200

def test_missing_brand_evidence_removes_performance_promises(tmp_db):
    bundle = sample_harness.generate_bundle(package_without_brand)
    serialized = json.dumps(bundle, ensure_ascii=False)
    assert "保证" not in serialized
    assert "48小时" not in serialized
```

- [ ] **Step 2: 验证失败**

Run: `pytest -q tests/test_sample_harness.py`

Expected: FAIL，样本模块尚不存在。

- [ ] **Step 3: 最小实现**

新增 `sample_bundles` 表。生成器先用确定性结构形成视频脚本、5–7 页图文和 800–1200 字公众号文章，再可选调用 `planner_text` 润色；模型返回的事实句重新通过 claim ID 校验。三个样本共用 claim ID，但分别采用“冲突—应对—承接”、“事实—影响—清单”和长文解释结构，避免同文复制。

- [ ] **Step 4: 增加 API 与文件导出**

新增：

```text
POST /api/evidence-packages/{id}/sample-bundle
GET  /api/sample-bundles/{id}
```

同时输出到 `data/samples/<bundle_id>/video-script.json`、`carousel.json`、`wechat.md` 和 `manifest.json`。

Run: `pytest -q tests/test_sample_harness.py tests/test_truth_guard.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add sample_harness.py database.py app.py models.py tests/test_sample_harness.py
git commit -m "feat: generate evidence-bound sample bundles"
```

### Task 5: 内部预览与发布级门禁分层

**Files:**
- Modify: `video_generation.py`
- Modify: `video_renderer.py`
- Modify: `truth_guard.py`
- Modify: `static/video-project.html`
- Modify: `static/assets.html`
- Test: `tests/test_sample_preview.py`
- Test: `tests/test_video_generation_state.py`

- [ ] **Step 1: 写失败测试**

```python
def test_low_match_can_render_internal_preview_but_not_publishable():
    decision = video_generation.quality_decision(score=42, requested_tier="internal_preview")
    assert decision["render_allowed"] is True
    assert decision["publish_allowed"] is False
    assert decision["watermark"] == "内部测试｜素材待确认"

def test_publish_tier_still_rejects_low_match():
    decision = video_generation.quality_decision(score=42, requested_tier="publish")
    assert decision["render_allowed"] is False
```

- [ ] **Step 2: 验证失败**

Run: `pytest -q tests/test_sample_preview.py tests/test_video_generation_state.py`

Expected: FAIL，缺少质量层级决策。

- [ ] **Step 3: 最小实现**

内部预览允许低相关镜头继续渲染，但必须烧录固定水印、保留问题清单并设置 `publish_allowed=false`；发布级继续执行现有匹配、事实、品牌、版权和技术门禁。前端明确区分“内部测试预览”和“可发布成片”。

- [ ] **Step 4: 验证**

Run: `pytest -q tests/test_sample_preview.py tests/test_video_generation_state.py tests/test_video_generation_rendering.py tests/test_video_generation_ui.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add video_generation.py video_renderer.py truth_guard.py static/video-project.html static/assets.html tests/test_sample_preview.py tests/test_video_generation_state.py
git commit -m "feat: separate internal previews from publish output"
```

### Task 6: 真实运行、样本验收与文档同步

**Files:**
- Create: `scripts/run_sample_harness.py`
- Modify: `docs/功能介绍.md`
- Modify: `docs/AI视频一键生成与质量门禁操作说明.md`
- Modify: `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

- [ ] **Step 1: 编写只读取公开信源和本地素材的运行脚本**

脚本依次执行：预置信源、抓取、选择最新可用热点、建立证据包、选择已确认品牌证据、生成三样本、请求内部视频预览并打印输出路径。脚本不得写入 API Key，不得自动下载版权不明媒体。

- [ ] **Step 2: 运行真实抓取**

Run: `python3 scripts/run_sample_harness.py --fetch --output data/samples`

Expected: 至少一个来源成功；失败来源单独列出，不阻塞后续；生成一个 bundle 目录。

- [ ] **Step 3: 验收产物**

检查：视频 30–45 秒或明确的内部预览问题清单；图文 5–7 页；公众号 800–1200 字；三者 claim ID 一致；每个外部事实有 URL；无未经确认的 Buffalo 数字承诺；manifest 显示调用次数、Token、成本估算和缓存命中。

- [ ] **Step 4: 完整验证**

Run: `pytest -q`

Expected: 全部测试 PASS。

Run: `node --check static/common.js`

Expected: exit 0。

- [ ] **Step 5: 同步文档与改进日志后提交**

先同步 `docs/` 对应 Markdown 到 Obsidian，并按 `YYYY-MM-DD HH:mm:ss CST｜改动主题` 追加改进日志，再执行：

```bash
git add scripts/run_sample_harness.py docs/功能介绍.md docs/AI视频一键生成与质量门禁操作说明.md
git commit -m "docs: document evidence harness sample workflow"
```

不得推送服务器；继续使用本地 8080。
