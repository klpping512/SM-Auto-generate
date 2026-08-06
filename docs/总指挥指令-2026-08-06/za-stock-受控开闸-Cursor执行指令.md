# za-stock 受控开闸：素材进匹配池 + 文案门禁兜底 + 扫描上限修复 —— Cursor 执行指令

> 日期：2026-08-06
> 状态：**已执行**（2026-08-06；清关复验通过）
> 拍板：总指挥已确认 **受控开闸**（画面可用作通用背景，口播强制走安全模板，不构成 Buffalo 能力证明）+ **cap 提到 20000**（最小改）。
> 前置：za-stock 61 条已 `ready`、`primary_category=manual`、segments 142 已产出（customs 54/delivery 36/facility 52）。本次只改匹配与文案门禁代码，不动 DB 已落数据。

## 一、背景与 Why

za-stock 素材（Pexels/Pixabay 免版权，attribution 已写"通用背景/非南非现场/非Buffalo能力"）**已经入库、已处理、段已产出**，但成片匹配池看不到它们。两道独立原因：

1. **合规闸**：`hotspot_video_planner.py:249 _is_buffalo_usable_source` 只放行 `_OWNED_ASSET_SOURCES = {upload, directory, local_directory, manual, local}`，`za_stock_license` 不在内。注释明写："Licensed stock or a generic library must not be represented as Buffalo proof"（免版权素材不得当 Buffalo 能力证明）。→ **这是品牌合规闸，不是 bug，需要受控放行**。
2. **扫描 cap（真 bug）**：`database.py:3901 list_asset_segments` 的 `min(limit, 2_000)` 硬上限。生产成片（app.py:1862/2345）和诊断都按 `asset_id` 升序只取前 2000 段——实测只覆盖 asset_id≤314（youtube 18348 段 + za-stock 866+ 全被截掉）。**即使闸开了，成片也选不到 za-stock**。

**受控开闸的合规闭环逻辑**：成片画面用通用港口/清关空镜本身不构成 Buffalo 能力证明——"证明"来自**文案宣称**。系统已有确定性文案门禁 `apply_overclaim_guard`（hotspot_preview_narration.py:349，白名单正向强制 + 黑名单兜底）。只要**所有 za-stock 素材的 scene 一律强制走"备货待清关"安全模板**（无论 primary_category 是不是 customs），画面补洞、文案不撒谎，合规成立。

## 二、铁律（不做的事）

1. **不改 `_is_buffalo_usable_source` 的"排除未知来源"语义**——只把 `za_stock_license` 这一个受控来源加进可用集，其他 license 源（如 mixkit_license）仍排除。
2. **不放宽文案门禁**——反而**收紧**：za-stock 素材无论分类一律强制安全模板。真 customs 素材（自有）才享有正常改写权。
3. **不改 `_functional_categories` / `_eligible_owned_categories`**——清关节点已放行 `{customs, warehouse, delivery}`，za-stock 段 primary_category 命中即可过类别闸。
4. **不为 za-stock 补 buffalo brand 标签**——它们必须保持 `branded=0`，在 rank 里永远排在真 Buffalo 素材后面（只当补缺背景）。
5. **诊断 `category_inventory` 语义不动**——开闸后 za-stock 出现在漏斗 `category_match`（对外键），不会掉进 `category_inventory`；验收看对字段。

## 三、改动清单

### 改动 A：`hotspot_video_planner.py` — 受控放行 + scene 携带来源

**A1.** `_OWNED_ASSET_SOURCES`（约 L250）加一个受控来源，注释说明这是"通用背景免版权素材，受文案门禁强制约束"：

```python
_OWNED_ASSET_SOURCES = frozenset({
    "upload", "directory", "local_directory", "manual", "local",
    # 受控放行：za-stock 免版权通用背景。仅用于补视觉洞，口播由文案门禁
    # (apply_overclaim_guard) 强制走安全模板，不构成 Buffalo 能力证明。
    "za_stock_license",
})
```

**A2.** `_is_buffalo_usable_source` 加注释说明受控来源的合规契约（不改逻辑）：

```python
def _is_buffalo_usable_source(item: dict) -> bool:
    # Licensed stock or a generic library must not be represented as Buffalo
    # proof.  Existing legacy rows omit asset_source and remain usable.
    # za_stock_license 是受控例外：通用背景素材，仅补视觉洞；scene 带
    # asset_source 标记，文案门禁据此强制走安全模板（不宣称南非现场/自有能力）。
    source = item.get("asset_source")
    return not source or str(source) in _OWNED_ASSET_SOURCES
```

**A3.** scene 构造（约 L790-800，`owned_proof` 分支）补 `asset_source` 字段，让文案门禁能识别来源。在现有 `"primary_category": category,` 行旁加：

```python
                # 文案门禁需要按镜头主分类精准拦截过度宣称。
                "primary_category": category,
                # 受控开闸：za-stock 通用背景段必须带来源标记，门禁据此强制安全模板。
                "asset_source": segment.get("asset_source") or "",
```

（`segment` 来自 `list_asset_segments`，JOIN 已带 `a.source AS asset_source`，无需额外查询。）

### 改动 B：`hotspot_preview_narration.py` — za-stock 一律强制安全模板

**B1.** 在 `requires_safe_customs_copy`（约 L318）前新增一个来源判定纯函数：

```python
# 受控开闸：za-stock 免版权通用背景，即使 primary_category=='customs' 也必须走
# 安全准备模板——画面是通用空镜，口播不得宣称南非现场或 Buffalo 自有能力。
_ZASTOCK_SOURCES = frozenset({"za_stock_license"})


def is_zastock_context(source: str) -> bool:
    return str(source or "").casefold() in _ZASTOCK_SOURCES
```

**B2.** `apply_overclaim_guard`（约 L349）的白名单分支条件从只看分类，扩展为"分类命中 **或** 来源是 za-stock"：

```python
        category = str(scene.get("primary_category") or "")
        source = str(scene.get("asset_source") or "")
        try:
            max_chars = scene_voiceover_char_limit(scene)
        except (TypeError, ValueError):
            max_chars = None
        # 受控开闸：za-stock 素材无论分类一律强制安全模板（画面是通用背景）。
        # 真 customs 自有素材仍可正常改写；黑名单兜底保留。
        if is_zastock_context(source) or requires_safe_customs_copy(category, logistics_nodes):
            safe_copy = hotspot_video_planner.safe_customs_preparation_copy(
                category, max_chars=max_chars, min_chars=5,
            )
            records.append({
                "scene": index + 1,
                "primary_category": category,
                "asset_source": source,
                "mode": "whitelist_forced",
                "issues": [],
                "original_voiceover": voiceover,
                "replaced_voiceover": safe_copy,
            })
            item["voiceover"] = safe_copy
            item["text_overlay"] = safe_copy.rstrip("。")[:24]
            continue
```

> 说明：`record` 增加 `asset_source` 便于审计区分"za-stock 强制"与"借来上下文强制"。若 `_voiceover` 里的 `safe_customs_preparation_copy` 已按分类产出安全文案，za-stock customs 段会得到 customs 安全模板——正确。

### 改动 C：`database.py` — 扫描上限提到 20000

**C1.** `list_asset_segments`（约 L3914）的 cap 常量：

```python
        # 全库段约 1.9 万（youtube 1.8 万 + 自有 + za-stock），cap 2000 会把
        # 后半库资产（asset_id>314）全部截掉——生产成片与诊断都选不到。
        # 提到 20000 覆盖当前全库，仍留 10 倍余量防失控。
        sql += " ORDER BY s.asset_id,s.segment_index LIMIT ?"
        params.append(max(1, min(int(limit), 20_000)))
```

**C2.** 生产/诊断调用点 `limit=2_000` 全部改 `limit=20_000`（**不改则 cap 涨了但调用方仍只要 2000 行，等于没改**）。全库实查共 **11 处**：
- `app.py`：L1020、L1055、L1097、L1235、L1862、**L2346**、L2469、**L4482**、**L4590**、**L4636** 的 `db.list_asset_segments(limit=2_000)`
- `scripts/run_dual_library_preview.py`：L58

> 改完用 `grep -rn "list_asset_segments(limit=2_000)" --include="*.py" .` 确认全库无残留；`limit` 参数仍可被调用方覆盖（按 asset_id 单查的 `limit=500` 等不受影响）。

### 改动 D：测试

**D1.** 新增 `tests/test_zastock_gate.py`（仿 `tests/test_matching_diagnostics.py` 的 `_seg` 夹具；za-stock 段用 `source="za_stock_license"`，清关 brief 用 `logistics_topic='清关风险', logistics_nodes=['清关']`）：
- `test_is_buffalo_usable_source_accepts_zastock`：`asset_source='za_stock_license'` → `_is_buffalo_usable_source` 返回 True；`mixkit_license` 仍 False。
- `test_owned_candidates_admits_zastock_customs`：`_owned_candidates` 传入 za-stock customs 段 + 清关 brief → 出现在候选结果里（仿 `test_hotspot_logistics_planner.py`）。
- `test_zastock_scene_forced_safe_copy`：`apply_overclaim_guard` 对 `primary_category='customs'` + `asset_source='za_stock_license'` 的 scene → 命中记录 mode=`whitelist_forced`、voiceover 被替换；对照真 customs 自有段（`asset_source='upload'` 或不带）→ **不**被强制。
- `test_zastock_diag_passed_category`：`diagnose_owned_matching` 传 2 条 za-stock customs 段 → `funnel.not_licensed_stock==2`、`category_match==2`、`after_dedup==2`、verdict=`thin_but_matched`、`category_inventory` 为空（不进 category_inventory）。

**D2.** 跑相关测试集：
```
python3 -m pytest tests/test_zastock_gate.py tests/test_hotspot_logistics_planner.py tests/test_hotspot_preview_narration.py tests/test_overclaim_guard.py tests/test_matching_diagnostics.py -q
```
再全量 `python3 -m pytest -q`，记录总数与基线（当前 807 passed / 8 存量 UI 失败）。

## 四、验收清单（做完逐条勾）

1. `pytest tests/test_zastock_gate.py` 4 条新测全过，既有 hotspot planner / narration 测试不回归。
2. 全量 pytest 无新增存量断言破坏。
3. **对抗式复核（别只看测试绿）**：
   - 确认 scene 构造 `owned_proof` 分支真的带上 `asset_source`（打开渲染日志或加临时 print 验证一条 za-stock 成片）；
   - 确认 za-stock customs 段的 scene 口播被 `whitelist_forced` 替换（渲染报告 `overclaim_guard` 记录里应出现 `asset_source: za_stock_license` 的条目）；
   - 确认真 customs 自有素材（若有）不被误强制。
4. **验收 topic=清关**：重启 app 后 GET `/api/diagnostics/owned-matching?topic=清关`，看：
   - `funnel.category_match` 从 0 变正（za-stock customs 段应计入：54 段、去重后约 24 资产）；
   - `category_inventory` 仍是**被类别闸踢掉的**（不应含 customs）；
   - `dropped_by_category_mismatch` 不再含 za-stock customs 资产。
5. 重启 app（改动涉及成片路径，必须重启才生效——老规矩）。

## 五、回滚

单点回滚：`git revert` 本次改动（涉及 hotspot_video_planner.py / hotspot_preview_narration.py / database.py / app.py / run_dual_library_preview.py / 新测试）。回滚后恢复：za_stock_license 被排除、cap 回到 2000、za-stock 不强制安全模板（但此时它也进不了匹配池，无副作用）。DB 里已处理的 61 条数据不受影响。

## 六、备注

- **合规口径**：本次放行的是"受控来源"，不是"放开所有 license"。`mixkit_license` 等仍被排除；未来如需放行其他免版权源，须走同一套（来源进白名单 + scene 带标记 + 门禁强制安全模板）。
- **性能**：cap 提到 20000 后，每次成片 select 扫约 1.9 万段 + 每段取 tags。若实测变慢，后续再优化为按 eligible 分类过滤（另立项），本次保持最小改。
- 诊断 `category_inventory` 字段语义（"被类别闸踢掉的库存"）已确认不变；验收别拿它当 customs 候选数。
