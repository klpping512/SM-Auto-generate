# za-stock 素材库展示层修复：免版权素材不再冒充"Buffalo 原有素材" —— Cursor 执行指令

> 日期：2026-08-06
> 状态：**待执行**
> 拍板：总指挥确认这是**展示层 bug**（数据与合规口径都对，UI 硬编码标签让免版权素材视觉上等于 Buffalo 自营资产，与"不构成 Buffalo 能力证明"相悖）。只修展示，不动数据、不动匹配。
> 与 za-stock 受控开闸指令（`za-stock-受控开闸-Cursor执行指令.md`）**无文件冲突**，可并行执行。本指令涉及 `media_assets.py` / `static/assets.html` / `tests/test_hotspot_event_clips.py`，不碰 planner / narration / database / app.py。

## 一、背景与 Why

61 条 za-stock 免版权素材（Pexels/Pixabay，source=`za_stock_license`）按设计进了"原本素材库"（该页定义=「Buffalo 自有**或已明确授权、可长期复用**」），但 UI 上全部显示成"Buffalo 原有素材"：

- `media_assets.py:207` 的 `source_label` 是**硬编码二选一**：`"热点素材" if library_origin=="hotspot" else "Buffalo 原有素材"`。za-stock 无 hotspot_id → library_origin="owned" → 一律标"Buffalo 原有素材"。
- `static/assets.html` 把全部过滤后素材塞进**一个**标题为"Buffalo 原有素材"的区块。

效果：库存空镜和 Buffalo 自营实拍长得一模一样——恰好违背我们给 za-stock 写的合规口径（"通用背景/非南非现场/非Buffalo能力"）。**数据没错，错的是展示。**

## 二、铁律（不做的事）

1. **不改 za-stock 的 `source` / `category` / `primary_category` / `attribution` 等已落库数据**——只改 `public_asset` 的展示字段与前端分组。
2. **不把 za-stock 移出原本素材库**——它们按授权定义就该在这（热点素材库 tab 是 Hook 层，与 B-roll 层完全独立）。修复是"标明身份"，不是"换个池子"。
3. **不改匹配逻辑**——`source_label` 是纯前端展示字段，不参与 `_owned_candidates` 判定（那是 `_is_buffalo_usable_source` 管的事，走受控开闸指令）。
4. **不新增 DB 列、不做迁移**——展示信息由 `public_asset` 按 `source` 现场推导，零迁移。

## 三、改动清单

### 改动 A：`media_assets.py:207` — source_label 三元

把 L206-207 的 `library_origin` / `source_label` 两行改为：

```python
    item["library_origin"] = "hotspot" if item.get("hotspot_id") else "owned"
    item["source_label"] = (
        "热点素材" if item["library_origin"] == "hotspot"
        else "免版权素材" if str(item.get("source") or "").casefold() == "za_stock_license"
        else "Buffalo 原有素材"
    )
```

说明：`db.get_asset` 返回行自带 `source` 列；`casefold()` 防大小写漂移。非 hotspot 且非 za-stock（upload/local/directory/youtube 等）仍得"Buffalo 原有素材"，与现有行为一致。

### 改动 B：`static/assets.html` render() — 原本素材库区块拆成两节

在 `render()`（约 L90-103）的模板前，把过滤后的 `visibleAssets` 按来源分两组，再渲染两个区块：

在 `const visibleAssets=assets.filter(...)` 之后插入：

```js
  // 受控开闸配套展示：免版权库存空镜与 Buffalo 自营实拍分节，避免库存素材
  // 视觉上冒充自营资产（合规口径：不构成 Buffalo 能力证明）。
  const stockAssets = visibleAssets.filter(a => String(a.source || '') === 'za_stock_license');
  const ownedAssets = visibleAssets.filter(a => String(a.source || '') !== 'za_stock_license');
  const materialSections = [
    ownedAssets.length ? `<section><h2 style="font-size:15px;margin:18px 0 10px">Buffalo 原有素材</h2><div class="asset-grid">${ownedAssets.map(assetCard).join('')}</div></section>` : '',
    stockAssets.length ? `<section><h2 style="font-size:15px;margin:18px 0 10px">免版权素材（Pexels/Pixabay）</h2><p style="font-size:12px;color:var(--text-muted);margin:-4px 0 10px">开放许可库存空镜，仅作通用背景；口播走安全模板，不构成 Buffalo 能力证明。</p><div class="asset-grid">${stockAssets.map(assetCard).join('')}</div></section>` : '',
  ].join('');
```

把原 L102 的单区块模板替换为：

```js
${materialSections || `<div class="card empty-state"><iconify-icon icon="mdi:image-multiple-outline" width="50"></iconify-icon><p>${assets.length?'当前筛选下没有素材。可切换「待复核」或「分类为其他」查看需人工打标的镜头。':'暂无素材。可上传文件，或将素材放入 static/assets/import 后扫描。'}</p></div>`}
```

说明：`assetCard`（L106）的来源标签已读 `a.source_label || 'Buffalo 原有素材'`，改动 A 后 za-stock 卡片自动显示"免版权素材"。

**B3.（用户要求：za-stock tag）** 在 `assetCard`（L106）里加一个卡片级可见 tag——即使搜索/筛选把 za-stock 和 Buffalo 自有素材混在一起，也能一眼区分。仿 `brandBadge` 的模式：

在 `const brandBadge=...` 之后加：

```js
const isStock = String(a.source || '') === 'za_stock_license';
const stockBadge = isStock ? `<div data-stock-badge style="margin:7px 0;font-size:10px;color:#1e40af;background:#dbeafe;border-radius:999px;padding:4px 7px;width:max-content;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">免版权素材 · za-stock</div>` : '';
```

在卡片模板 `${brandBadge}` 处一起渲染：`${brandBadge}${stockBadge}`。

**B4.（可选顺手）** 工具筛选栏（L100）加一个来源筛选 select，方便只看 za-stock：`<select class="select" onchange="stockFilter=this.value;refresh()">`，选项 `全部来源 / 免版权素材(za-stock) / Buffalo自有`，在 `visibleAssets` 过滤里加对应条件。若 qcoder 觉得加筛选超范围，可只做 B3 tag，验收不卡这条。

### 改动 C：测试

在 `tests/test_hotspot_event_clips.py` 末尾新增（仿 `test_owned_asset_is_labeled_as_buffalo_source`，用 `tmp_db` 夹具）：

```python
def test_licensed_stock_asset_is_labeled_as_stock_source(tmp_db):
    import media_assets

    asset_id = tmp_db.create_asset({
        "name": "za_customs_pexels_11801939",
        "filepath": "assets/library/video/stock.mp4",
        "file_type": "video",
        "category": "customs",
        "primary_category": "customs",
        "duration": 10,
        "size": 10,
        "source": "za_stock_license",
        "status": "active",
        "sha256": "z" * 64,
    })

    public = media_assets.public_asset(tmp_db.get_asset(asset_id))

    assert public["library_origin"] == "owned"
    assert public["source_label"] == "免版权素材"
```

跑：`python3 -m pytest tests/test_hotspot_event_clips.py tests/test_hotspot_event_clips_ui.py tests/test_hotspot_media_ui.py -q`，再全量 `python3 -m pytest -q`。既有 `test_owned_asset_is_labeled_as_buffalo_source`（source="local"）必须仍绿——它应继续断言"Buffalo 原有素材"。

## 四、验收清单

1. pytest：新测试绿，`test_owned_asset_is_labeled_as_buffalo_source` 不回归，全量无新增破坏。
2. **重启 app**（media_assets.py 是服务端，必须重启生效；assets.html 改完强刷浏览器清缓存）。
3. GET `/api/assets`：所有 za-stock 条目 `source_label=="免版权素材"`、`library_origin=="owned"`；upload/local 等仍 `"Buffalo 原有素材"`。
4. 素材库 →「原本素材库」tab：
   - 出现两个区块：`Buffalo 原有素材` / `免版权素材（Pexels/Pixabay）`；
   - 免版权区块下每条卡片来源标签显示"免版权素材"，且卡片上有蓝色 `免版权素材 · za-stock` tag；许可证/署名/来源链接正常；
   - 用搜索（如"清关"）把 za-stock 与 Buffalo 自有混在一起时，tag 依然能区分；
   - 过滤器（类型/分类/状态/搜索）对两区块同时生效（分组的时机在过滤之后）。
5. 顺带目检：热点素材库 tab 不受影响（改动只在原本素材库 render 分支）。

## 五、回滚

`git revert` 本次改动（media_assets.py / static/assets.html / tests/test_hotspot_event_clips.py 三处）。回滚后 za-stock 重新显示"Buffalo 原有素材"，合并回单区块——纯展示回退，无数据/匹配副作用。与受控开闸指令可独立回滚，互不牵连。

## 六、备注

- 本修复与受控开闸（指令 #2）是**配套展示**：开闸后 za-stock 进匹配池、能出现在成片里，素材库必须能标明"这是库存空镜"，用户和审片人才不会把库存画面当成 Buffalo 现场实拍。
- 若后续引入其他受控免版权源（如 mixkit_license），在 `public_asset` 的 source_label 里加一个分支即可，前端分组条件同步加。
- `source_label` 只是展示字段；匹配判定仍走 `_is_buffalo_usable_source`，不要为了让标签正确而去动匹配侧。
