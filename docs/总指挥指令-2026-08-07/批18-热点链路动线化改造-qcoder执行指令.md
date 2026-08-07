# 总指挥指令 批18 ｜ 热点链路动线化改造（非固定顺序 + 三源融合 + 时效感知）

> 日期：2026-08-07 ｜ 状态：**已执行（qcoder，2026-08-07 16:02 CST，commit a23cec4）**
> 拍板：总指挥确认设计评审稿四个拍板点全部按建议默认值（跨父中段递进=允许；时效权重=沿用批17档位；超30天=软压后不硬排除；generic防冒充=planner层确定性标记+文案门禁）。
> 执行工具：**qcoder**（2026-08-06 晚起指定）。
> 前置：批16（ba62323）+ 批17（96c8b64）已提交并验收通过。本批不依赖批16/17 的未提交改动。
> 设计评审稿：`docs/总指挥指令-2026-08-07/批18-热点链路动线化改造-设计评审与执行指令.md`（本指令是它的可执行版）。

---

## 〇、背景与 Why

总指挥 2026-08-07 需求："**新的热点链路 看看怎么结合视频生产链路（热点素材+za_stock+buffalo原有素材）。PS：非固定顺序 只要视频动线流畅，什么形式都可以。**"

批16/17 评审结论（总指挥已核码）：三个生产入口（推荐引子 / autopilot / chat 一键）全部消费 `_marketing_hook_candidates`，批17 的时效加分已在这条接缝生效——**"选哪个货"变聪明了**。但"非固定顺序、动线流畅"还没落地，因为它落在 **`plan_followup_scenes`（hotspot_video_planner.py:690-904）**，这一层仍是**固定分槽**：

1. **顺序恒定**：`:807-824` 永远 `hotspot_slots[:2]` 开头 → 自有段中间。40 天前的旧闻和今天的新闻在成片里地位一样。
2. **planner 无时效感知**：`_event_score`（:69-102）纯文本重叠打分，不含新鲜度；`_event_date_seconds` 的时效逻辑只在 app.py 候选层，没进编排层。
3. **同父局限**：三处调用点（app.py:2356/2842/4896）用 `list_hotspot_event_clips(asset_id, hotspot_id)` 只取同一母片 Hook；chat 流明明允许锁 2 个**不同父**事件（`approved_hook_event_ids` / `locked_events` 可跨父），planner 却静默丢弃跨父那条 → 做不出"两件新闻互为印证"的动线。
4. **generic 无防冒充**：14 条常青开场（generic_logistics）也会被当"新闻引子"选中，出片时 `_voiceover` 用的是"**现场正在发生**：{title}"新闻框架——批17 文档 ⚠️1 原样存在。
5. **za_stock 被动补位**：仅缺口类目/总量不足才进（批13），不承担节奏角色。

本批落地后：**成片开场由"新鲜度 + 相关性"决定而非写死；不同父的两条新闻可中段互为印证（escalation）；常青开场文案不再假装新闻；顺带补 `_event_date_seconds` 边界测试。** 这是"非固定顺序、动线流畅"进入编排层的一批。

---

## 一、决策表（总指挥已确认）

| 事项 | 总指挥决策 | 理由 |
|---|---|---|
| 跨父热点中段递进（escalation） | **允许** | "非固定顺序"核心价值；escalation 段文案必须与开场事件分开表述（"除此之外/另一现场"），防混源 |
| 时效权重 | **沿用批17档位**（`<24h +8 / <3d +5 / <7d +2 / ≥30d −3`），不放大 | 时效是排序修饰，不是相关性替代 |
| 超 30 天热点 | **软压后、不硬排除** | 低新闻期避免饿库；但选中作开场时文案走"非新闻"模板（同 generic） |
| generic 防冒充 | **planner 层确定性标记 + 文案门禁**（不改 `_marketing_hook_candidates` 硬门禁） | scene 带 `hook_kind`，`_voiceover` 对 generic / 过期 timely 强制常青模板 |

**已裁掉的选项（本批不做）**：不新建 `_marketing_hook_candidates` 硬门禁；不改 za_stock 准入逻辑（见改动 I2 注）；不加新闻纪录片式"多热点连播"。

---

## 二、铁律（不做的事）

1. **不动门禁与模型链路**：`_is_confirmed_renderable_hotspot_hook` 词表、MiMo 策展、`allow_broad_match`/`use_generic` 分支、`_marketing_hook_candidates` 签名——一字不改。
2. **不动既有硬门禁**：`hotspot_count≥1`（app.py:2398）、真实视频每段 ≥3s、`source_usage_report` 去重、overclaim 门禁、za_stock 安全模板——全部保留，一个不松。
3. **不改 za_stock 准入**（批13 的 `ZA_STOCK_MAX_SCENES=2` / 缺口补位逻辑保持原样）。设计稿曾提"za_stock 作不同类目 proof 间节奏过渡"，总指挥评审后裁掉：buffalo 健康时强插通用空镜会稀释品牌证明，反损动线；且 `_diversify_owned_candidates` 已保证相邻 proof 视觉族不重复，节奏问题已被现有机制覆盖。
4. **不改 generic 数据本身**：1970 哨兵、hook_kind、published_at 均不动，只做编排层标记 + 文案分支。
5. **只改四个文件 + 一个新测试文件**：`database.py`（H1）、`app.py`（H2 两处）、`hotspot_video_planner.py`（I1/I2/I3）、`tests/test_hotspot_freshness.py`（J 新文件）。
6. **改完必须重启 app**（旧进程持旧代码）。
7. **定位用函数名/代码锚点，不用绝对行号**（批11/批16/批17 会平移行号）。
8. **新增字段全部 additive**：scene 新增 `hook_kind` / `flow_role`，event 新增 `parent_published_at`——消费方忽略未知字段，零波及。

---

## 三、改动清单

### 块 H — 数据接入：让编排层"看得见"时效与跨父事件

#### H1. `database.list_hotspot_event_clips()`（database.py，锚点 `def list_hotspot_event_clips`）

给每条事件并入父热点真实发布时间（原始字符串，**不在数据层做解析**——epoch 换算放 planner，避免 database↔app 循环导入）。当前结构：

```python
    with get_conn() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            row["virtual_asset_id"] = row.get("virtual_asset_id") or f"hotspot-event-{row['id']}"
            ...
            row["logistics_scenes"] = json.loads(row.pop("logistics_scenes_json") or "[]")
        return rows
```

在 `rows = [dict(row) ...]` 之后、`for row in rows:` 之前插入**一次批量查询**：

```python
        parent_ids = sorted({int(row["hotspot_id"]) for row in rows if row.get("hotspot_id")})
        published_by_parent: dict[int, str | None] = {}
        if parent_ids:
            marks = ",".join("?" for _ in parent_ids)
            for prow in conn.execute(
                f"SELECT id, published_at FROM hotspots WHERE id IN ({marks})", parent_ids
            ).fetchall():
                published_by_parent[int(prow["id"])] = prow["published_at"]
```

在 per-row 循环内（任意一行后，如 `row["hook_kind"]` 行后）加：

```python
            row["parent_published_at"] = published_by_parent.get(int(row.get("hotspot_id") or 0))
```

> 效果：`get_hotspot_event_clip`（database.py:4645 委托 `list_hotspot_event_clips`）与 `list_hotspot_event_clips` 返回的每条事件都带 `parent_published_at`（可空）。跨父合并后的事件天然带该字段。additive，对现有消费方零影响（`_marketing_hook_candidates` 读的是 `hotspot_rows` 的 `published_at`，不受影响）。

#### H2. app.py 两处调用点：并入跨父已确认事件

**H2a. 生成接口（app.py，锚点 `related_events = db.list_hotspot_event_clips(asset_id=event.get("asset_id"), hotspot_id=event.get("hotspot_id"))`，约 :2356）**

该行之后插入：

```python
    # 批18：并入跨父已确认事件——chat 流允许锁不同父的 Hook，planner 之前静默丢弃。
    if approved_hook_event_ids:
        known_ids = {int(e.get("id") or 0) for e in related_events}
        for clip_id in approved_hook_event_ids:
            clip = db.get_hotspot_event_clip(int(clip_id))
            if clip and int(clip.get("id") or 0) not in known_ids and _is_confirmed_renderable_hotspot_hook(clip):
                related_events.append(clip)
```

**H2b. chat 一键生成（app.py，锚点 `related_events = db.list_hotspot_event_clips(\n        asset_id=primary["asset_id"], hotspot_id=primary["hotspot_id"],\n    )`，约 :4896）**

该调用之后插入：

```python
    # 批18：并入跨父 locked 事件（locked_events 可含不同父热点，之前被静默丢弃）。
    known_ids = {int(e.get("id") or 0) for e in related_events}
    for locked in locked_events:
        if int(locked.get("id") or 0) not in known_ids and _is_confirmed_renderable_hotspot_hook(locked):
            related_events.append(locked)
```

> `_is_confirmed_renderable_hotspot_hook` 定义于 app.py:1396，作用域内可用；`locked_events` / `approved_hook_event_ids` 在各自函数作用域内已存在。预览接口（app.py:2842）是单事件预览，无跨父需求，**不改**。

---

### 块 I — planner 动线编排（hotspot_video_planner.py）

#### I1. 新增两个 helper（放在 `_event_score` 之后，锚点 `def _event_display_title` 之前）

顶部 import 区加（现有 `from datetime import datetime, timezone` 那行附近）：

```python
from email.utils import parsedate_to_datetime
```

新 helper：

```python
def _event_ts(value) -> int:
    """批18：兼容 ISO（含 UTC 偏移）与 RSS RFC2822 日期 → epoch 秒；无法解析/1970 哨兵返回 0。

    镜像批17 app._event_date_seconds 的实现，供 planner 编排层读时效，避免跨层导入。
    """
    if not value:
        return 0
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except Exception:
            return 0
    ts = dt.timestamp()
    return int(ts) if ts > 0 else 0


def _event_urgency(event: dict) -> int:
    """批18：新鲜度→开场紧迫度。仅 timely_event 有紧迫度；generic 常青不衰减恒 0；缺失无据 0。

    档位镜像批17：<24h +8 / <3d +5 / <7d +2 / ≥30d −3。
    """
    if str(event.get("hook_kind") or "timely_event") == "generic_logistics":
        return 0
    ts = _event_ts(event.get("parent_published_at"))
    if not ts:
        return 0
    age_days = (datetime.now().timestamp() - ts) / 86400.0
    if age_days < 1:
        return 8
    if age_days < 3:
        return 5
    if age_days < 7:
        return 2
    if age_days >= 30:
        return -3
    return 0
```

#### I2. 动线化槽位组装（`plan_followup_scenes` 内，替换现有 `slots.extend(hotspot_slots[:2]) ... image_index += 1` 整段，锚点 `# 开头先给热点事实` 注释到 `image_index += 1`）

**替换前**（现 ~:807-824）：

```python
    slots.extend(hotspot_slots[:2])
    if len(hotspot_slots) > 2:
        slots.append(hotspot_slots[2])
    image_index = 0
    for position, item in enumerate(owned_slots):
        slots.append(item)
        if image_index < len(context_images) and position in {0, 2, 4}:
            slots.append(("image", context_images[image_index]))
            image_index += 1
    # Adaptive: if owned footage is sparse, append remaining stills as bridges.
    while allow_adaptation and image_index < len(context_images):
        slots.append(("image", context_images[image_index]))
        image_index += 1
```

**替换后**：

```python
    def _slot_event(slot: tuple[str, object]) -> dict:
        kind, payload = slot
        return payload[1] if kind == "hotspot" else payload

    def _slot_asset(slot: tuple[str, object]) -> int:
        return int(_slot_event(slot).get("asset_id") or 0)

    # 批18 动线化：不再"热点全堆开头"。
    #   1) 开场 = 新鲜度最高 Hook（timely 优先，generic 常青兜底）——由 I1 的
    #      urgency 已并入 _event_score 排序，slots 里第一个就是它。
    #   2) 同源补充现场紧跟开场（最多 1 段，原行为保留：同一事件的更多实拍）。
    #   3) 异源事件 = mid-roll：放到首个证明段之后再出现，作局势再确认。
    #   4) 其余热点不再强制进片头（避免新闻纪录片感）；用户锁定的跨父事件保底进片。
    mid_roll_clip_id: int | None = None
    slots.extend(hotspot_slots[:1])
    if hotspot_slots:
        primary_asset = _slot_asset(hotspot_slots[0])
        slots.extend([item for item in hotspot_slots[1:] if _slot_asset(item) == primary_asset][:1])
        cross_parent = next((item for item in hotspot_slots[1:] if _slot_asset(item) != primary_asset), None)
        if cross_parent is not None:
            mid_roll_clip_id = int(_slot_event(cross_parent).get("id") or 0)
    image_index = 0
    for position, item in enumerate(owned_slots):
        slots.append(item)
        # mid-roll：首个证明段之后插入异源事件（escalation / supplementary）
        if mid_roll_clip_id is not None and position == 0:
            slot = next(
                (s for s in hotspot_slots if int(_slot_event(s).get("id") or 0) == mid_roll_clip_id),
                None,
            )
            if slot is not None:
                slots.append(slot)
                mid_roll_clip_id = None
        if image_index < len(context_images) and position in {0, 2, 4}:
            slots.append(("image", context_images[image_index]))
            image_index += 1
    # Adaptive: if owned footage is sparse, append remaining stills as bridges.
    while allow_adaptation and image_index < len(context_images):
        slots.append(("image", context_images[image_index]))
        image_index += 1
```

> ⚠️ `mid_roll_clip_id` 会在下面的 scene 构建循环里被读（判定 flow_role），**变量名必须保留**。
> ⚠️ 若 owned_slots 为空（无 Buffalo 实拍），mid-roll 事件自然不进片——只留 opener+同源，是最小可渲染方案，上游时长门禁兜底。
> ⚠️ 现有测试 `test_video_plan_has_sixty_seconds_and_mixed_evidence`（3 个同父事件 → 期望 hotspot_evidence==2）在新逻辑下：opener+同源[:1]=2 段，行为不变 ✅。

#### I3. scene 构建 + `_voiceover` 常青/escalation 文案（`plan_followup_scenes` scene 循环 + `_voiceover`）

**I3a. hotspot scene 构建（锚点 `scenes.append({` 内 `"visual": title, "voiceover": _voiceover(brief, "hotspot_evidence", position, title),` 那段）**

在 `title = _event_display_title(event, brief)` 之后、`scenes.append({` 之前插入：

```python
            flow_role = (
                "opener" if position == 1
                else "escalation" if int(event.get("id") or 0) == (mid_roll_clip_id or -1)
                else "supplementary"
            )
            hook_kind = str(event.get("hook_kind") or "timely_event")
```

`scenes.append({` 里改两行、加两键：

```python
                "visual": title,
                "voiceover": _voiceover(brief, "hotspot_evidence", position, title, event=event, flow_role=flow_role),
                "text_overlay": title[:24], "asset_id": event.get("asset_id"), "event_clip_id": event.get("id"),
                "hook_kind": hook_kind, "flow_role": flow_role,
```

**I3b. `_voiceover` 函数（锚点 `def _voiceover(brief: dict, role: str, index: int, title: str, category: str = "") -> str:`）**

签名加两个 keyword-only 参数：

```python
def _voiceover(brief: dict, role: str, index: int, title: str, category: str = "", *, event: dict | None = None, flow_role: str = "") -> str:
```

`hotspot_evidence` 分支改为（保留原有 timely 文案，在前面插入两个确定性分支）：

```python
    if role == "hotspot_evidence":
        kind = str((event or {}).get("hook_kind") or "timely_event")
        # 批18：常青开场不随事件衰减，不得用"现场正在发生"新闻框架
        if kind == "generic_logistics":
            if index == 1:
                return f"以{title}为背景，看一个跨境订单从仓到门，要核对哪些环节。"
            return "场景只是入口；真正要核对的是订单进入南非后，每一步履约动作。"
        # 批18：过期 timely（≥30天）作开场时同样走"非新闻"模板，不假装今日事件
        if index == 1 and _event_urgency(event) < 0:
            return f"以{title}为背景，看一个跨境订单从仓到门，要核对哪些环节。"
        # 批18：异源中段递进——与开场事件分开表述，不混源
        if flow_role == "escalation":
            return f"除此之外，{title}，也在改变今天的履约预期。"
        if index == 1:
            if "musina" in title.casefold() and "拥堵" in title:
                return "Musina 现场，筛查让卡车排起长队。你的订单，还能按原计划走吗？"
            return f"现场正在发生：{title}。你的订单，还能按原计划走吗？"
        if index == 2:
            return f"堵的不只是一条路，{topic}的交付预期也要重新核对。"
        return "热点不是 Buffalo 的服务证明；真正该问的是，异常出现时，谁在提前调整路线和沟通？"
```

> `owned_proof` 分支与函数尾部不变。现有测试 `test_overclaim_guard.py:190/196` 以 `(brief, "owned_proof", 2, "", category)` 位置参数调用，不受 keyword-only 新增参数影响。
> `_event_urgency(event)` 对 `event=None` 安全（`(event or {})` 路径）。

---

### 块 J — 测试补课 + 动线回归（新文件 `tests/test_hotspot_freshness.py`）

> 批17 验收遗留："`_event_date_seconds` 边界无提交测试"。本批把 planner 镜像 `_event_ts`/`_event_urgency` 的边界 + 四套动线模板一起锁死。

**J1. 时效边界单测**（直接 import planner helper，fixture 内联 dict，参考 `tests/test_hotspot_logistics_planner.py` 风格）：

```python
import pytest
from datetime import datetime, timedelta

from hotspot_video_planner import _event_ts, _event_urgency, plan_followup_scenes


@pytest.mark.parametrize("value,expected", [
    ("2026-07-30T03:51:10+00:00", 1785_353_470),   # ISO 带偏移（换算为真实 epoch，你按 datetime 算值）
    ("Tue, 21 Jul 2026 13:00:00 +0200", None),     # RFC2822 带时区 → 断言 == parsedate_to_datetime(value).timestamp()
    ("", 0), ("  ", 0), (None, 0),                 # 空 → 0
    ("1970-01-01T00:00:00", 0),                    # 1970 哨兵 → 0
    ("not-a-date", 0),                             # 乱串 → 0
])
def test_event_ts_boundaries(value, expected):
    ...
```

**J2. urgency 分档**：

```python
def test_urgency_bands_and_generic_exemption():
    now = datetime.now()
    def ev(days, kind="timely_event"):
        ts = now - timedelta(days=days)
        return {"hook_kind": kind, "parent_published_at": ts.isoformat()}
    assert _event_urgency(ev(0.1)) == 8
    assert _event_urgency(ev(2)) == 5
    assert _event_urgency(ev(5)) == 2
    assert _event_urgency(ev(40)) == -3
    assert _event_urgency(ev(40, "generic_logistics")) == 0   # 常青豁免
    assert _event_urgency({}) == 0                             # 缺失无据
```

**J3. 四套动线模板行为**（构造与 `test_hotspot_logistics_planner.py` 同款内联 fixture）：

- **模板 A 新闻动线**：两个 timely 事件同父 + 充足 Buffalo → opener 是新鲜 timely（`flow_role=="opener"` 且 `hook_kind=="timely_event"`），热点段数 ≤2，顺序为 hotspot → owned。
- **模板 B 常青动线**：只有 generic 事件 → opener `hook_kind=="generic_logistics"`，voiceover 不含"现场正在发生"、含"以…为背景"。
- **模板 C 异源中段递进**：opener（父A、fresh）+ 同父补充 + 第二事件父B fresh + Buffalo → 父B 事件 `flow_role=="escalation"` 且**出现在 owned 段之后**（scene 序号 > 首个 owned 序号），voiceover 含"除此之外"。
- **模板 D 薄库存**：1 个热点 + 1 段 Buffalo + `allow_adaptation=True` → 图片桥插入，`evidence_type=="image"` 存在。
- **guardrail 回归**：模板 C 输出过 `source_usage_report`（无重复时段）；所有真实视频段 `duration_ms ≥ 3000`。

> J3 的 fixture 里事件必须带 `hook_kind`、`asset_id`、`parent_published_at`、`start_ms/end_ms`、`clip_status:"ready"`、`review_status:"confirmed"`（`_limit_distinct_hotspot_hooks` 会过滤）。owned fixture 带 `primary_category`、`asset_file_type:"video"`、`asset_source:"buffalo"`（非 za_stock 以避开补充层）。

---

## 四、验收清单（改完必验，逐条打勾）

1. **重启 app**（必做；`/api/health` 正常）。
2. **pytest 全量**：相对批17 基线（885 passed / 8 存量失败）**不新增失败**；新 `tests/test_hotspot_freshness.py` 全绿；`tests/test_hotspot_logistics_planner.py` 与 `tests/test_topic_briefs.py`（plan_followup_scenes 相关）不回归。
3. **代码层**：`_event_urgency` 仅 timely 生效且档位与批17 一致；`:807-824` 固定分槽已被 I2 动线逻辑替换；跨父 approved/locked 事件不再被静默丢弃（H2 两处）；hotspot scene 带 `hook_kind` + `flow_role`。
4. **行为层（单测可控）**：
   - 同一 brief，opener 在"新鲜 timely" vs "过期 timely" vs "仅 generic"三种输入下，输出 opener 不同，且过期/generic 的 voiceover 无新闻框架。
   - 两个不同父 fresh 事件 → 第二条 `flow_role=="escalation"` 且位置在 owned 之后。
   - generic 开场 scene 标记正确、voiceover 为常青模板。
5. **数据层**：`list_hotspot_event_clips` 返回每条带 `parent_published_at`（可空）；98 条可出片 Hook 中 timely 父热点有日期者该字段非空。
6. **运行态（真机，可留宿主侧）**：连渲两单，scene 顺序/素材有可观察差异（非恒定"热点开头"）；质检链路不受影响（video_evaluator 未动）；既有发布护栏行为不变。
7. **回归**：旧项目重渲染不炸；发布链路 succeeded + publish_allowed。
8. **提交口径**：`git show --name-only` 交叉核对，提交只含批18 四个文件 + 新测试，不夹带批16/17 或无关改动。

---

## 五、回滚

- `git revert <批18 提交>` 即可。新增字段（`parent_published_at`/`hook_kind`/`flow_role`）additive，回滚后无残留副作用；不涉及数据写入/回填，零数据风险。
- 若只想临时关掉动线化：I2 替换段还原为原 `slots.extend(hotspot_slots[:2])` 逻辑即可，I1/I3 可保留（urgency 只影响排序、场景字段 additive）。

---

## 六、交付口径

- 完成标记：H1 + H2a + H2b + I1 + I2 + I3a + I3b + J 全落地 + 重启 + 验收 1-8 逐条打勾。
- 提交信息：`批18 热点链路动线化：planner 时效感知开场 + 跨父中段递进 + 常青开场防冒充文案 + 时效边界测试`。
- 回写：README 登记批18 行 + 本文件验收清单打勾；把 pytest 数字写进验收 2 的勾注里。

---

## 七、执行结果（qcoder 2026-08-07 回写）

全部改动已落地，验收清单逐条结果：

1. ✅ 重启 app（旧 PID 67647 已停，新 PID 64869）。项目无 `/api/health` 路由，以 `GET / 200` + `Application startup complete` 启动日志 + 鉴权接口正常响应验证。
2. ✅ pytest 全量：**898 passed / 8 failed**（885 基线 + 13 新用例）；8 个失败用 `git worktree` 在批17 HEAD（96c8b64）复验，逐条一致，均为存量 UI 基线失败；`tests/test_hotspot_freshness.py` 13 用例全绿；`tests/test_hotspot_logistics_planner.py` / `tests/test_topic_briefs.py` 零回归。
3. ✅ 代码层：`_event_urgency` 仅 timely 生效、档位与批17 一致，并已并入 `_event_score`（基础分 >0 才叠加，保底 1 分——"软压后不硬排除"）；原 :807-824 固定分槽已替换为动线组装；H2a/H2b 两处跨父并入已落地；hotspot scene 带 `hook_kind` + `flow_role`。
4. ✅ 行为层：模板 A/B/C/D + 三态 opener 差异测试全绿（过期 timely 与 generic 共用常青模板、靠 `hook_kind` 标记区分，符合定稿文案）。
5. ✅ 数据层：168 条事件全带 `parent_published_at`；timely 151/151 父热点有日期；generic 14 条常青保持 1970 哨兵。
6. ✅（部分留宿主侧）真库 planner 冒烟：开场 fresh timely（"现场正在发生：约翰内斯堡…"）→ 证明段 → 异源 escalation 中段（"除此之外，N2公路抗议现场…"），9 段计划无异常；连渲两单真机观察留宿主侧。
7. ✅（部分留宿主侧）旧项目重渲染/发布链路未触代码（门禁与模型链路零改动），真机复验留宿主侧。
8. ✅ 提交口径：`git show --name-only` 交叉核对，只含批18 四个文件 + 新测试 + 本批回写文档。

**执行中两处对定稿代码的最小修正（均为定稿内部自洽性问题，非语义变更）：**

- I2 定稿在 mid-roll 插入后置 `mid_roll_clip_id = None`，与 I3a/⚠️注（scene 循环读该变量标 flow_role）矛盾——不清空则 escalation 永不触发。实际保留变量不清空，模板 C 测试据此锁定。
- `_event_urgency` 签名扩为 `dict | None` 并做 `event = event or {}`，兑现定稿 "对 event=None 安全" 的声明。
- J fixture 的 owned 素材 `asset_source` 用 `"upload"`（在 `_OWNED_ASSET_SOURCES` 白名单内且非 za_stock）：字面值 `"buffalo"` 不在白名单会被 `_is_buffalo_usable_source` 过滤。
- `_event_score` 并入时效项按设计评审稿第三节与设计拍板（"时效是排序修饰"）实现：仅相关性基础分 >0 时叠加，过期保底 1 分不硬排除。
