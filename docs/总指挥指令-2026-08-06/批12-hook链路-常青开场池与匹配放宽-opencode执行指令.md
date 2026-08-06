# 总指挥指令 批12 ｜ hook 链路：常青开场池（#33）+ 空 profile 匹配放宽（#34）

> 日期：2026-08-06 ｜ 状态：**已产出，待 opencode 执行**
> 拍板：**hook 链路与生产视频链路分开解决**。本批=hook 链路，是批7 v2（生产链路）的**跨链前置**。
> 目标：让常青/科普/降级话题（无新闻事件锚）有**可自动锁定的 generic_logistics 开场 Hook**，且「空物流 profile」话题不再被匹配过滤踢掉 → `打字 → 出片` 对非新闻话题成立。
> 执行工具：**opencode**。
> 依赖核验：本批所有机制（schema、落库接口、匹配链、event_identity 要求、provenance 纪律、scene 词汇）总指挥已用生产库/代码逐条验证。

---

## 〇、背景与 Why

chat 一键生产对新闻话题（Beitbridge/R60/德班港）已能自动出片，因为池里有 146 条 `timely_event` 确认 Hook。但常青/科普/降级话题（「海外仓是什么」「南非本地快递怎么选？关键维度科普」）不行，断点有两层：

1. **无候选可自动锁定**：`generic_logistics`（常青开场型）Hook = **0**。`_retrieve_confirmed_chat_hooks` 的 use_generic 分支找不到任何候选 → 无法 `matched` → 按钮不出现。（#33 治）
2. **有候选也可能被踢掉**：`_marketing_hook_candidates`（app.py:1860）有**两个过滤器**会拒绝「空物流 profile」话题（对比降级输出正是这类）：
   - `if not topic_profile and not specific_terms: continue`（:1969，记入 scene_mismatch）
   - `if not direct and not profile_overlap and not intent_bridge: continue`（:1986，记入 relevance_low）
   
   「南非本地快递怎么选？关键维度科普」→ `_topic_keywords` 只得广义词「南非」→ specific_terms=[] → topic_profile=∅ → 两个过滤器全踢 → 池建了也匹配不到。（#34 治）

本批 #33+#34 一起落地，常青生产链才真正闭合。**timely_event（新闻）路径绝不放宽**——只放宽 generic_logistics 的中性场景开场。

---

## 二、铁律（不做的事）

1. **不动新闻型 Hook 的门禁**：`timely_event` 匹配链保持严格；#34 的 `allow_broad_match` 只在 use_generic 分支传 True。
2. **za_stock 来源沿用归属纪律**：标题/what_happened **不写「南非」、不写「Buffalo 能力」**（通用背景素材，`source_label` 仍是「免版权素材」）；SA 自有素材（local_directory/upload/directory）可写「南非」。
3. **开场画面是中性场景表述，不构成服务能力声明**：evidence 三字段按模板写，`hook_reason` 固定注明。
4. **幂等**：脚本可重复跑；已建片段不重复建；`upsert_hotspot` 按 `source_url` 查重。
5. **改完必须重启 app**（运行进程持旧代码，已吃过一次亏）。
6. **批12 与批7 v2 同改 app.py 不同函数**：建议同一 opencode 会话**连续执行 批12 → 批7 v2**（或反向），不并行开两会话；定位用函数名/代码锚点，不用绝对行号（批11 在跑会平移行号）。

---

## 三、改动清单

### 指令 #33：新脚本 `scripts/build_generic_logistics_pool.py`

从自有 active 视频资产（za_stock + SA 自有混选）按场景建 `hook_kind='generic_logistics'` 的确认 Hook 池。**总指挥已验证的机制**：父行 `hotspots` + 事件片段行 `hotspot_event_clips`（过 `_is_confirmed_renderable_hotspot_hook` 门禁）+ `hotspot_event_segment_links` 关联段。

#### #33.1 数据源与预算

```python
SCENE_BUDGETS = {"warehouse": 6, "last_mile": 4, "border": 4}   # 合计 14，位于 12-20 拍板区间；可用 --scene-budget 覆盖
SCENE_CATEGORIES = {
    "warehouse": ("warehouse", "facility"),
    "last_mile": ("delivery",),
    "border": ("customs",),
}
ALLOWED_SOURCES = ("local_directory", "upload", "za_stock", "mixkit", "directory")   # za_stock 与 SA 自有混选
```

- 资产选择：`db.list_assets(status="active")`，过滤 `file_type=="video"`、`hotspot_id is None`、`deprecated != 1`、`category` 命中场景映射、`source` 在 ALLOWED_SOURCES。
- 段选择：`db.list_asset_segments(asset_id=a["id"], status="active", limit=20000)`，取 `thumbnail_path` 非空 且 `duration_ms>=5000 且 <=12000` 的段；若该资产不足，放宽 `duration_ms>=3000`。每资产至多 2 段，按 `segment_index` 升序取、时间段不重叠。
- 幂等跳过：已存在「clip_status='ready' AND hook_kind='generic_logistics'」片段的资产，不再重复建。
- 每场景按 `asset_id` 升序取满预算；全局 `--dry-run`（默认）只打印计划，`--apply` 才落库。

#### #33.2 父热点行（每场景 1 条，用 `db.upsert_hotspot`）

```python
import hashlib
parent = {
    "title": SCENE_TITLES[scene]["zh"],           # 见下表
    "summary": SCENE_TITLES[scene]["zh"],
    "source_url": f"buffalo://generic-logistics/{scene}",
    "publisher": "Buffalo 内部素材库",
    "published_at": "1970-01-01T00:00:00",
    "retrieved_at": "1970-01-01T00:00:00",
    "snapshot_sha256": hashlib.sha256(f"generic-logistics-{scene}".encode()).hexdigest(),
    "image_candidate_url": "",
}
hotspot_id, created = db.upsert_hotspot(parent)   # 幂等：source_url 查重
```

| scene | 父标题（title_zh / title_en） |
|---|---|
| warehouse | `仓库仓储作业场景` / `Warehouse storage and sorting scenes` |
| last_mile | `末端配送作业场景` / `Last-mile delivery operation scenes` |
| border | `跨境清关作业场景` / `Cross-border customs clearance scenes` |

（za_stock 与 SA 自有同池时父标题用中性「作业场景」，不点名南非，遵守归属纪律；事件片段标题同。）

#### #33.3 事件片段行（每资产 1-2 条，用 `db.replace_hotspot_event_clips`）

```python
SCENE_EVIDENCE = {
    "warehouse": {
        "what_happened": "展示了仓库内仓储、分拣与货架作业的典型画面。",
        "logistics_question": "海外仓与本地仓的仓储、分拣环节如何运作？",
    },
    "last_mile": {
        "what_happened": "展示了末端配送与派送环节的典型作业画面。",
        "logistics_question": "末端配送如何高效完成最后三公里履约？",
    },
    "border": {
        "what_happened": "展示了跨境物流清关环节的典型作业画面。",
        "logistics_question": "跨境物流的清关环节通常涉及哪些流程？",
    },
}
HOOK_REASON = "作为常青物流话题的通用开场画面，用真实作业场景引入该环节，不构成任何服务能力声明。"
```

对每个选中资产 `asset` 及选定段 `segments`：

```python
events = []
for i, seg in enumerate(segments):
    evidence = {
        "what_happened": SCENE_EVIDENCE[scene]["what_happened"],
        "hook_reason": HOOK_REASON,
        "logistics_question": SCENE_EVIDENCE[scene]["logistics_question"],
        "event_identity": f"generic-{scene}-{asset['id']}",
    }
    events.append({
        "event_index": i,
        "start_ms": seg["start_ms"],
        "end_ms": seg["end_ms"],
        "title_zh": SCENE_TITLES[scene]["zh"],
        "title_en": SCENE_TITLES[scene]["en"],
        "location": "",
        "entities": [],
        "keywords": [],
        "evidence": evidence,
        "confidence": 0.95,
        "review_status": "confirmed",
        "hook_kind": "generic_logistics",
        "logistics_scenes": [scene],
        "segments": [seg],                       # seg 必须含 id + thumbnail_path
    })
created = db.replace_hotspot_event_clips(asset["id"], hotspot_id, events)
```

> 关键点（总指挥已核验 replace_hotspot_event_clips 实现）：
> - `evidence["event_identity"]` 同资产同值 → `_select_chat_video_hook_pair` 按 (asset_id, event_identity) 分组，1-2 段同资产可成对返回。
> - `replace_hotspot_event_clips` 会校验 `end_ms <= asset.duration`，且把 `clip_status` 硬编码为 `pending`——**必须在创建后 UPDATE 为 ready + 补 clip_path**（见 #33.4）。
> - `logistics_scenes=[scene]` → `is_generic_logistics_eligible`（producible_topics.py:50，要求 scenes ∩ {warehouse,last_mile,port,border,linehaul,disruption}）通过。

#### #33.4 落库后置（clip_status → ready + clip_path）

```python
# 用 database 层连接（database.get_conn），对每 event_id：
with db.get_conn() as conn:
    conn.execute(
        "UPDATE hotspot_event_clips SET clip_status='ready', clip_path=? WHERE id=?",
        (asset["filepath"], event_id),
    )
```

#### #33.5 校验（--check 模式）

逐条断言（失败的打印并退出码非 0）：
1. `is_generic_logistics_eligible(event)` == True（producible_topics.py:50）。
2. `app._is_confirmed_renderable_hotspot_hook(event)` == True（app.py:1376 门禁：review_status=confirmed、clip_status=ready、clip_path 存在、evidence 三字段齐全、无 out_of_scope 词、无 unsupported_cost_leap）。
3. 片段文件存在：`static/{clip_path}` 可读。
4. 池总量在 **12-20** 之间；每场景数量 ≥ 预算下限（--scene-budget 未覆盖时）。
5. 幂等：`--apply` 后再跑一次 `--dry-run`，增量应为 0。

> 注：脚本 import app 函数的方式与现有 `scripts/audit_eligible_hotspot_hook_pairs.py` 一致（仓库脚本可直接 import app）。若 import 副作用过重，把 `_is_confirmed_renderable_hotspot_hook` 的门禁条件**内联复刻**到脚本（判定逻辑是纯函数、无网络/DB 副作用），并在脚本注释注明「与 app.py:1376 同步」。

#### #33.6 CLI

```bash
python3 scripts/build_generic_logistics_pool.py --dry-run        # 默认：打印计划（资产/段/场景预算/将建片段）
python3 scripts/build_generic_logistics_pool.py --apply          # 落库 + 置 ready + 打印校验
python3 scripts/build_generic_logistics_pool.py --check          # 只校验现有池，不建
python3 scripts/build_generic_logistics_pool.py --scene-budget warehouse:6,last_mile:4,border:4
```

---

### 指令 #34：app.py `_marketing_hook_candidates` 加 `allow_broad_match`（仅 use_generic 放宽）

#### #34.1 签名（app.py:1860，锚点=函数名）

```python
def _marketing_hook_candidates(
    brief: dict,
    limit: int = 8,
    *,
    hook_kind: str | None = None,
    require_scene_overlap: bool = False,
    allow_broad_match: bool = False,   # 新增
) -> tuple[list[dict], str, list[dict], dict]:
```

#### #34.2 放宽两个过滤器（用函数内代码锚点）

过滤器 ①（锚点 `if not topic_profile and not specific_terms:`）改为：

```python
        if not allow_broad_match and not topic_profile and not specific_terms:
            funnel["scene_mismatch"] += 1
            continue
```

过滤器 ②（锚点 `if not direct and not profile_overlap and not intent_bridge:`）改为：

```python
        if not allow_broad_match and not direct and not profile_overlap and not intent_bridge:
            funnel["relevance_low"] += 1
            continue
```

> 其余过滤**一律不动**：`strict_terms and not specific_direct`（:1977）保留——用户明确点名事件/道路/事故（如「R60 事故」）时，仍要求事实文本精确命中，generic 开场绝不顶替新闻锚。`require_scene_overlap` 场景过滤（:1963）保留——有 profile 的话题仍优先同场景。

#### #34.3 调用侧：`_retrieve_confirmed_chat_hooks`（app.py:4499，锚点=函数名）

初次调用（use_generic 分支）加参数：

```python
    candidates, kb_context, brand_evidence, funnel = _marketing_hook_candidates(
        brief,
        limit=8,
        hook_kind=hook_kind,
        require_scene_overlap=use_generic,
        allow_broad_match=use_generic,   # 新增：仅无事件锚的常青路径放宽；新闻路径为 False
    )
```

relaxed 重试调用（`if use_generic and not candidates:` 分支）加参数：

```python
        candidates, kb_context, brand_evidence, funnel = _marketing_hook_candidates(
            brief,
            limit=8,
            hook_kind="generic_logistics",
            require_scene_overlap=False,
            allow_broad_match=True,      # 新增：常青兜底重试必然放宽
        )
```

> 语义：`use_generic=False`（有事件锚，新闻话题）→ `allow_broad_match=False`，**门禁与原来完全一致**，零回归风险。

---

## 四、验收清单（改完必验）

1. **脚本自检**：`--dry-run` 打印 3 场景计划（数量在预算内）；`--apply` 后 `--check` 全绿（门禁/文件存在/总量 12-20/幂等增量 0）。
2. **库内核对**：
   ```bash
   python3 - <<'EOF'
   import sqlite3, json
   db = sqlite3.connect('file:data/logiflow.db?mode=ro', uri=True, timeout=30)
   db.row_factory = sqlite3.Row
   rows = db.execute(
       "SELECT * FROM hotspot_event_clips WHERE hook_kind='generic_logistics'"
   ).fetchall()
   print("generic_logistics clips:", len(rows))
   for r in rows[:20]:
       ev = json.loads(r["evidence_json"])
       print(r["asset_id"], r["start_ms"], r["end_ms"], r["review_status"],
             r["clip_status"], r["clip_path"], ev.get("event_identity"), r["logistics_scenes_json"])
   EOF
   ```
   期望：`review_status=confirmed`、`clip_status=ready`、`clip_path` 非空、`event_identity` = `generic-{scene}-{asset_id}`、`logistics_scenes_json` 为单场景。
3. **门禁口径**：池内每条过 `app._is_confirmed_renderable_hotspot_hook`（脚本 --check 覆盖，此处人工确认 0 失败）。
4. **#34 定向测试**（app 重启后，Python 直调）：构造空 profile 话题 brief（`raw_input="南非本地快递怎么选？关键维度科普"`），`_marketing_hook_candidates(brief, hook_kind="generic_logistics", allow_broad_match=True)` 返回**非空**候选；`allow_broad_match=False` 时返回空（原行为）。`_topic_keywords("南非本地快递怎么选？关键维度科普")` 的 specific_terms 为空（佐证放宽必要性）。
5. **timely_event 零回归**：对「Beitbridge 边境拥堵」「R60 事故」等新闻话题，`_marketing_hook_candidates(brief, hook_kind="timely_event", allow_broad_match=False)` 候选数/命中与改动前一致。
6. **端到端**（依赖批7 v2 已落地）：重启后 chat 输入 `海外仓是什么` → use_generic 分支返回 `status='matched'` + `video.status='ready'`；前端出现「创建60秒视频项目」按钮；渲染首段为选定的 generic_logistics 开场。
7. **全量回归**：`pytest -q` 相对基线（875 passed / 8 存量失败）**不新增失败**。
8. **重启 app**：旧进程持旧代码，必须重启后验 4-6。

---

## 五、回滚

- #33：`DELETE FROM hotspot_event_clips WHERE hook_kind='generic_logistics'` + `DELETE FROM hotspots WHERE source_url LIKE 'buffalo://generic-logistics/%'`（连同 hotspot_event_segment_links 级联清理）。
- #34：`git revert <批12 提交>` 恢复 `allow_broad_match` 参数与两处调用。

---

## 六、交付口径

- 完成标记：#33 脚本 + #34 参数 + 重启 + 验收 1-8 逐条打勾。
- 提交信息：`批12 hook链路：#33常青开场池脚本 #34空profile匹配放宽`。
- 批12 与批7 v2 建议同一 opencode 会话连续执行；完成后同步 Obsidian 改进日志 + README。
