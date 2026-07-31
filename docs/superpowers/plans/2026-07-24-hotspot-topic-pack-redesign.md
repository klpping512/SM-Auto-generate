# 南非热点专题包工作台重设计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 在现有 SQLite、FastAPI 和热点素材体系上，把热点页改造成事件级“热点专题包”工作台，并保持现有视频跟进、素材库和质量门禁兼容。

**Architecture:** 保留 `hotspots` 作为专题包根记录，新增 `hotspot_signals` 保存来源信号；用纯 Python 聚类/评分模块生成事件状态；通过新增专题包 API 为 B 方案前端提供聚合数据；外部媒体默认只保存链接和元数据，人工确认单个媒体后才调用现有素材下载、抽帧和切片流程。

**Tech Stack:** Python 3.12、FastAPI、SQLite、现有 `hotspot_fetcher.py`/`hotspot_video_sources.py`、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 增加专题包和原始信号的数据层

**Files:**
- Modify: `database.py:259-345, 692-725, 1284-1320`
- Test: `tests/test_hotspot_topic_packages_db.py`

- [ ] **Step 1: 写失败测试，验证信号表和专题包字段存在**

```python
def test_hotspot_topic_package_fields_and_signals(tmp_db):
    hotspot_id, _ = tmp_db.upsert_hotspot({
        "title": "Johannesburg driver strike",
        "summary": "Drivers announce a shutdown.",
        "source_url": "https://sabc.example/strike",
        "publisher": "SABC",
        "published_at": "2026-07-24T07:00:00+00:00",
        "retrieved_at": "2026-07-24T07:05:00+00:00",
        "snapshot_sha256": "a" * 64,
    })
    tmp_db.update_hotspot_package_metrics(
        hotspot_id, heat_score=82, heat_state="rising",
        event_type="strike", logistics_relevance=91,
        locations=["Johannesburg"], entities=["driver"], package_status="new",
    )
    signal_id, created = tmp_db.upsert_hotspot_signal({
        "hotspot_id": hotspot_id, "source_name": "SABC",
        "source_type": "news", "external_id": "sabc-1",
        "title": "Drivers announce a shutdown", "summary": "Johannesburg drivers announce a national shutdown.",
        "source_url": "https://sabc.example/strike",
        "published_at": "2026-07-24T07:00:00+00:00",
        "retrieved_at": "2026-07-24T07:05:00+00:00",
        "metrics": {"cross_platform": 2}, "raw_payload": {"id": "sabc-1"},
    })
    assert created is True
    assert tmp_db.get_hotspot(hotspot_id)["heat_score"] == 82
    assert tmp_db.list_hotspot_signals(hotspot_id)[0]["id"] == signal_id
```

- [ ] **Step 2: 运行测试确认当前实现失败**

Run: `pytest -q tests/test_hotspot_topic_packages_db.py`

Expected: FAIL with missing `update_hotspot_package_metrics` or `hotspot_signals`.

- [ ] **Step 3: 添加 SQLite 表和迁移字段**

在 `hotspots` 的 `CREATE TABLE` 后增加字段迁移：

```python
for name, definition in {
    "heat_score": "REAL NOT NULL DEFAULT 0",
    "heat_state": "TEXT NOT NULL DEFAULT 'unconfirmed'",
    "event_type": "TEXT NOT NULL DEFAULT 'unknown'",
    "locations_json": "TEXT NOT NULL DEFAULT '[]'",
    "entities_json": "TEXT NOT NULL DEFAULT '[]'",
    "signal_count": "INTEGER NOT NULL DEFAULT 0",
    "media_count": "INTEGER NOT NULL DEFAULT 0",
    "logistics_relevance": "REAL NOT NULL DEFAULT 0",
    "package_status": "TEXT NOT NULL DEFAULT 'new'",
}.items():
    _ensure_column(conn, "hotspots", name, definition)
```

新增 `hotspot_signals` 表，唯一键为 `(source_type, external_id)`，并创建 `hotspot_id/published_at` 索引。

- [ ] **Step 4: 实现写入和读取函数**

在 `database.py` 增加以下函数：

```python
update_hotspot_package_metrics(
    hotspot_id: int, *, heat_score: float, heat_state: str,
    event_type: str, logistics_relevance: float,
    locations: list[str], entities: list[str], package_status: str,
) -> dict

upsert_hotspot_signal(data: dict) -> tuple[int, bool]
list_hotspot_signals(hotspot_id: int | None = None, limit: int = 200) -> list[dict]
get_hotspot_package(hotspot_id: int) -> dict | None
```

`upsert_hotspot_signal` 按 `source_type + external_id` 幂等写入；`list_hotspot_signals` 按抓取时间倒序返回并解码 `metrics/raw_payload`；`get_hotspot_package` 返回已解码的 `locations/entities` 和媒体计数。

JSON 字段必须在读取时解码成列表/字典；任何外部 JSON 无法解析时返回空结构，不让页面崩溃。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest -q tests/test_hotspot_topic_packages_db.py`

Expected: `1 passed`。

- [ ] **Step 6: 仅暂存本任务文件并检查范围**

Run: `git diff --check -- database.py tests/test_hotspot_topic_packages_db.py && git diff --name-only -- database.py tests/test_hotspot_topic_packages_db.py`

Expected: 只出现本任务两个文件；当前工作区其他未提交改动不得被加入提交。

### Task 2: 实现信号规范化、事件聚类和热点评分

**Files:**
- Create: `hotspot_topic_packages.py`
- Modify: `hotspot_fetcher.py:1-260`
- Modify: `hotspot_video_sources.py:1-180`
- Test: `tests/test_hotspot_topic_packages.py`

- [ ] **Step 1: 写纯函数失败测试**

```python
def test_cluster_signals_into_one_event_and_score_logistics_relevance():
    signals = [
        {"source_type": "news", "source_name": "SABC", "title": "E-hailing drivers begin national shutdown", "summary": "Johannesburg drivers protest", "source_url": "https://a.example/1", "published_at": "2026-07-24T08:00:00+00:00"},
        {"source_type": "news", "source_name": "News24", "title": "Uber drivers protest in Johannesburg", "summary": "Major disruption expected", "source_url": "https://b.example/2", "published_at": "2026-07-24T08:20:00+00:00"},
    ]
    packages = hotspot_topic_packages.cluster_signals(signals)
    package = packages[0]
    assert len(package["signals"]) == 2
    assert package["event_type"] == "strike"
    assert package["logistics_relevance"] >= 70
    assert package["heat_score"] > 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_hotspot_topic_packages.py`

Expected: FAIL with missing module/functions.

- [ ] **Step 3: 实现确定性规范化和聚类**

`hotspot_topic_packages.py` 提供：

```python
LOGISTICS_TERMS = {
    "strike": ("strike", "protest", "shutdown", "罢工", "抗议"),
    "risk": ("crime", "hijacking", "security", "危险", "劫车"),
    "infrastructure": ("port", "customs", "road", "warehouse", "港口", "清关"),
    "ecommerce_growth": ("e-commerce", "takealot", "amazon", "temu", "电商"),
}

classify_event(text: str) -> tuple[str, float]
normalize_signal(raw: dict) -> dict
cluster_signals(signals: list[dict]) -> list[dict]
calculate_heat_score(signals: list[dict], *, now=None) -> float
```

`classify_event` 返回事件类型和 0–100 物流相关度；`normalize_signal` 统一标题、时间、来源、URL 和 metrics；`cluster_signals` 返回带 `signals/event_type/heat_score/breakdown` 的专题包；`calculate_heat_score` 使用设计文档权重返回 0–100 分。

聚类要求：共享城市/实体且标题相似度不低于 0.82，发布时间差不超过 48 小时；不满足条件的信号单独成包并标记 `unconfirmed`。评分返回 0–100，所有权重写成常量并在结果中返回 breakdown。

- [ ] **Step 4: 将新闻 Feed 和 YouTube 元数据写入信号表**

在 `hotspot_fetcher.fetch_hotspots` 和 `hotspot_video_sources.fetch_youtube_channel_hotspots` 写入/更新 `hotspot_signals`，并保留现有 `upsert_hotspot` 和 `upsert_hotspot_media` 兼容路径。信号写入失败只追加来源错误，不阻止原有热点写入。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest -q tests/test_hotspot_topic_packages.py tests/test_hotspot_video_sources.py tests/test_hotspot_fetcher.py`

Expected: 所有相关测试 PASS。

### Task 3: 增加专题包查询、确认、合并和单媒体准备 API

**Files:**
- Create: `hotspot_package_service.py`
- Modify: `app.py:1548-1908, 2043-2070`
- Modify: `hotspot_media.py:1-120`
- Test: `tests/test_hotspot_topic_packages_api.py`

- [ ] **Step 1: 写 API 失败测试**

```python
def test_topic_package_detail_contains_signals_media_and_actions(tmp_db):
    hotspot_id = _create_hotspot_with_signal_and_media(tmp_db)
    client, headers = _login(tmp_db, "topic-editor", "editor")
    response = client.get(f"/api/hotspot-packages/{hotspot_id}", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["signals"]
    assert "media_groups" in payload
    assert payload["actions"]["can_confirm"] is True
```

- [ ] **Step 2: 实现读取接口**

新增：

```python
@app.get("/api/hotspot-packages")
async def list_hotspot_packages(
    query: str = "", source: str = "", event_type: str = "",
    heat_state: str = "", media_form: str = "", since: str = "",
    limit: int = 100, user=Depends(get_current_user),
):
    """返回带计数和评分 breakdown 的专题包卡片数据。"""
    return await hotspot_package_service.list_packages(
        query=query, source=source, event_type=event_type,
        heat_state=heat_state, media_form=media_form,
        since=since, limit=max(1, min(limit, 200)),
    )

@app.get("/api/hotspot-packages/{hotspot_id}")
async def get_hotspot_package_detail(
    hotspot_id: int, user=Depends(get_current_user),
):
    """返回专题包、信号、三类媒体、权利汇总和出库权限。"""
    package = hotspot_package_service.get_package_detail(hotspot_id)
    if package is None:
        raise HTTPException(404, "热点专题包不存在")
    return package
```

列表接口支持 `query/source/event_type/heat_state/media_form/since/limit`，返回 `video_count/image_count/text_count/signal_count`。详情接口返回事件、信号时间线、按 `video/image/text` 分组的媒体、权利汇总、物流候选角度和动作权限。

在新文件 `hotspot_package_service.py` 实现 `list_packages`、`get_package_detail`、`confirm_package`、`merge_signals` 和 `prepare_media`；这些函数只负责业务组合，数据库读写继续通过 `database.py` 完成。

- [ ] **Step 3: 实现确认和合并接口**

```python
@app.post("/api/hotspot-packages/{hotspot_id}/confirm")
async def confirm_hotspot_package(
    hotspot_id: int, user=Depends(require_role(UserRole.EDITOR)),
):
    """只更新 package_status 并写审计日志，不下载媒体。"""
    package = hotspot_package_service.confirm_package(hotspot_id, user)
    if package is None:
        raise HTTPException(404, "热点专题包不存在")
    return package

@app.post("/api/hotspot-packages/{hotspot_id}/merge")
async def merge_hotspot_signals(
    hotspot_id: int, signal_ids: list[int],
    user=Depends(require_role(UserRole.EDITOR)),
):
    """将待合并信号归入专题包并重新计算评分与计数。"""
    if not signal_ids:
        raise HTTPException(422, "至少选择一条待合并信号")
    return hotspot_package_service.merge_signals(hotspot_id, signal_ids, user)
```

确认只更新 `package_status='confirmed'` 并写审计日志；不下载任何外部媒体。合并接口必须接收 `signal_ids`，合并后重新计算标题、实体、热度和计数。

- [ ] **Step 4: 实现单媒体准备接口**

```python
@app.post("/api/hotspot-media/{media_id}/prepare", status_code=202)
async def prepare_hotspot_media(
    media_id: int, user=Depends(require_role(UserRole.ADMIN)),
):
    """确认专题包和媒体权利后，启动单媒体分析任务。"""
    return await hotspot_package_service.prepare_media(media_id, user)
```

只有专题包已确认且媒体权利不为 `red/unknown` 时，才允许进入现有 materialize/analysis 任务；YouTube/TikTok 默认仍保留 `link_only` 或 `internal_preview`。重复点击返回 409，不重复启动后台任务。

- [ ] **Step 5: 运行 API 测试**

Run: `pytest -q tests/test_hotspot_topic_packages_api.py tests/test_hotspot_media_api.py`

Expected: 新增接口和旧媒体接口全部 PASS。

### Task 4: 将热点抓取结果改造成专题包结果

**Files:**
- Modify: `hotspot_fetcher.py`
- Modify: `app.py:2043-2070`
- Test: `tests/test_hotspot_fetch_runs.py`

- [ ] **Step 1: 写抓取结果失败测试**

```python
def test_fetch_result_reports_packages_and_source_health(tmp_db, tmp_path):
    result = asyncio.run(hotspot_fetcher.fetch_hotspots(tmp_path, feeds=[{
        "name": "SABC", "url": "https://sabc.example/feed.xml",
        "allowed_domains": ["sabc.example"],
    }]))
    assert "packages" in result
    assert "source_health" in result
    assert result["packages"] >= 1
```

- [ ] **Step 2: 在抓取结束时聚合并回写专题包**

抓取完成后读取本轮新增/更新信号，调用 `cluster_signals`，将每个聚类结果回写到 `hotspots`，并在结果中返回：`packages`、`signals`、`media_candidates`、`source_health`、`errors`。原有 `new/updated/video_new/video_updated` 字段继续保留。

- [ ] **Step 3: 增加来源级降级行为**

Google Trends、GDELT、YouTube Key/工具不可用时，返回来源状态 `disabled` 或 `error`，不让整体抓取失败；新闻 RSS 仍按现有五个管理员信源执行。

- [ ] **Step 4: 运行抓取回归**

Run: `pytest -q tests/test_hotspot_fetch_runs.py tests/test_hotspot_fetcher.py tests/test_hotspot_video_sources.py`

Expected: 现有字段兼容且新增专题包统计通过。

### Task 5: 重做 B 方案热点页

**Files:**
- Modify: `static/hotspots.html`
- Modify: `static/design-system.css`
- Test: `tests/test_hotspot_topic_pack_ui.py`

- [ ] **Step 1: 写前端契约失败测试**

```python
def test_hotspot_page_uses_topic_pack_structure():
    page = (ROOT / "static" / "hotspots.html").read_text(encoding="utf-8")
    assert "/api/hotspot-packages" in page
    assert "热点专题包" in page
    assert "来源信号" in page
    assert "视频素材" in page and "图片素材" in page and "纯文本" in page
    assert "确认并入热点库" in page
    assert "仅保存链接" in page
    assert "抓取最新热点" in page
    assert "选择热点" not in page
```

- [ ] **Step 2: 替换前端状态模型**

将 `hotspots/sources/hotspotMedia` 状态改成 `packages/selectedPackage/packageFilters`，首屏请求 `/api/hotspot-packages`，选中卡片后请求详情接口；旧 URL `?hotspot_id=` 继续映射到对应专题包。

- [ ] **Step 3: 实现左侧专题包卡片**

卡片必须显示双语标题、日期、热度分、升温状态、事件类型/城市、视频/图片/纯文本数量、来源数量和物流相关度；筛选项至少包含日期、来源、事件类型、升温状态和素材形式。

- [ ] **Step 4: 实现右侧专题包详情**

详情按“摘要 → 信号时间线 → 素材标签页 → 权利汇总 → 物流角度 → 出库动作”排列。外部媒体卡片显示缩略图/嵌入预览、来源、发布时间、权利状态、原文按钮和“准备分析”按钮；不显示笼统的“下载全部”。

- [ ] **Step 5: 保留并改造现有内容生产动作**

将原来的 `buildEvidencePackage/generateSamples` 改为专题包按钮；“生成视频跟进”跳转 `/video-followup.html?hotspot_id={selectedPackage.id}`，仍然使用现有 60 秒证据门禁；“生成图文”继续调用现有 sample bundle API。

- [ ] **Step 6: 运行 UI 契约和 JavaScript 语法检查**

Run: `pytest -q tests/test_hotspot_topic_pack_ui.py tests/test_hotspot_workbench_ui.py`

Expected: 新契约 PASS；如果旧测试仍断言四步向导，删除/改写为专题包契约，不保留互相冲突的断言。

### Task 6: 接入现有视频跟进和素材库，并加生命周期清理

**Files:**
- Modify: `hotspot_logistics_planner.py`
- Modify: `hotspot_video_planner.py`
- Modify: `static/video-followup.html`
- Modify: `database.py:1989-2015`
- Test: `tests/test_hotspot_topic_pack_video_flow.py`

- [ ] **Step 1: 写联动失败测试**

```python
def test_confirmed_package_creates_dynamic_video_brief_without_unrelated_media():
    package = build_confirmed_package_with_strike_and_owned_videos()
    brief = hotspot_logistics_planner.build_brief(package, owned_segments=[])
    scenes = hotspot_video_planner.build_scenes(package, owned_segments=[])
    assert brief["hotspot_type"] == "strike"
    assert all(scene["source_type"] in {"hotspot_video", "owned_video"} for scene in scenes)
    assert sum(scene["duration_ms"] for scene in scenes) == 60_000
```

- [ ] **Step 2: 让视频跟进读取专题包详情**

前端创建项目时保存 `package_id`、brief、signals、selected_media 和 `target_duration_ms=60000`；后端继续使用现有 `hotspot_evidence_gate`，不直接读取整条母片。

- [ ] **Step 3: 增加引用保护的热点媒体清理**

清理函数必须先调用现有引用检查；只清理 `internal_preview/link_only` 的本地低清副本、关键帧和临时字幕。存在项目、事件片段或素材库引用时不得删除。

- [ ] **Step 4: 运行联动和生命周期测试**

Run: `pytest -q tests/test_hotspot_topic_pack_video_flow.py tests/test_hotspot_logistics_planner.py tests/test_hotspot_virtual_assets.py`

Expected: 动态主题、60 秒预算、热点/自有素材比例和引用保护全部 PASS。

### Task 7: 端到端验收与本地浏览器验证

**Files:**
- Modify: `docs/功能介绍.md`
- Modify: `docs/产研部署交接说明.md`
- Create: `docs/superpowers/specs/2026-07-24-hotspot-topic-pack-redesign-acceptance.md`
- Test: `tests/test_hotspot_topic_pack_e2e.py`

- [ ] **Step 1: 写端到端测试**

```python
def test_topic_pack_e2e_keeps_external_media_as_links_until_confirmed(tmp_db):
    package = seed_package_with_news_and_youtube(tmp_db)
    assert package["package_status"] == "new"
    confirm_package(package["id"])
    assert get_media(package["youtube_media_id"])["local_path"] is None
    assert create_video_task(package["id"])["target_duration_ms"] == 60000
```

- [ ] **Step 2: 运行完整回归**

Run: `pytest -q --ignore=tests/test_twitter_adapter.py`

Expected: 本次相关测试全部通过；记录既有 Twitter 适配器导入冲突，不把它归因于热点专题包改动。

- [ ] **Step 3: 启动 8080 并强制刷新浏览器**

Run: `./start.sh`（如已有服务则只重启当前 8080 进程）

浏览器：打开 `http://localhost:8080/hotspots.html`，执行 `⌘+Shift+R`。

验收动作：抓取最新热点 → 看到专题包列表 → 打开一个专题包 → 查看来源信号和三类素材 → 点击确认 → 验证未自动下载外部视频 → 点击生成视频跟进 → 验证 60 秒证据计划。

- [ ] **Step 4: 更新知识库和改进日志**

将功能说明、部署说明和本计划同步到知识库；追加精确 CST 时间的改进日志，记录测试结果、浏览器验证和未提交/未推送状态。

- [ ] **Step 5: 只提交本次文件**

Run: `git diff --check && git diff --name-only`，确认没有把既有未提交文件混入；仅在暂存区文件清单准确时创建本地提交，未获授权不推送服务器。
