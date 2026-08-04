# 画面素材分类词表统一 —— Cursor 执行指令

> 目标：把散落在 5 个文件里的「画面素材分类（asset category）」词表/规则收敛到单一模块 `asset_taxonomy.py`，
> 消除 `media_assets.py` 与 `video_renderer.py` 两份同名 `CATEGORY_KEYWORDS` 的漂移。
> **物流话题/事件词表已经统一在 `hotspot_lexicon.py`，本次不动它。**

## ⛔ 硬性边界（务必遵守，不许越界）

1. **不要**把 `semantic_matching.py` 的 `MOOD_TERMS`、`BUSINESS_TERMS`、`SCENE_ROLE_ALIASES`、`HOTSPOT_SCENE_ROLES`、`OWNED_SCENE_ROLES` 并入本模块——它们是"情绪/口播职责/场景角色"，与"素材分类"是不同职责。
2. **不要**改 `hotspot_lexicon.py`。
3. **不要**改任何函数的对外签名/返回结构；本次是"抽取 + 别名"，不是重构调用方逻辑。
4. 所有旧的模块级常量名（`CATEGORY_KEYWORDS`、`CATEGORY_PRIORITY`、`NODE_CATEGORY_RULES`、`_DELIVERY_TAG_VALUES` 等）**保留为向后兼容别名**，指向新模块（照抄现有 `hotspot_hook_selector.HOOK_TERMS = hotspot_lexicon.HOOK_SCORE_TERMS` 的做法）。
5. 8 类官方集合 `CATEGORIES = {warehouse, delivery, customs, brand, staff, facility, customer, other}` 是唯一真源，不得增删类目。

---

## 阶段 A —— 零行为变化的搬运（先做，先跑测试）

### A1. 新建 `asset_taxonomy.py`

模块 docstring 写明：本模块是"自有素材画面分类（primary_category）"的唯一真源，区别于 `hotspot_lexicon`（热点话题/事件匹配）。

把以下**单一来源、无歧义**的定义原样迁入（值一个字都不改）：

- `CATEGORIES`（从 `media_assets.py:22` 迁入）
- `CATEGORY_PRIORITY`（从 `video_renderer.py:270` 迁入）
- `NODE_CATEGORY_RULES`（从 `hotspot_video_planner.py:57` 迁入）
- 把 `_DELIVERY_TAG_VALUES` / `_WAREHOUSE_TAG_VALUES` / `_CUSTOMS_TAG_VALUES`（`hotspot_video_planner.py:190-206`）迁入，并额外导出一个聚合：
  ```
  CAPABILITY_TAG_VALUES = {
      "delivery": DELIVERY_TAG_VALUES,
      "warehouse": WAREHOUSE_TAG_VALUES,
      "customs": CUSTOMS_TAG_VALUES,
  }
  ```
  （去掉前导下划线改为公开名，原文件保留 `_DELIVERY_TAG_VALUES = asset_taxonomy.DELIVERY_TAG_VALUES` 别名。）
- `ROLE_CATEGORY_FIT`（从 `semantic_matching.py:37`）

### A2. 各文件改为引用别名（值不变 → 行为不变）

- `hotspot_video_planner.py`：`import asset_taxonomy`；`NODE_CATEGORY_RULES = asset_taxonomy.NODE_CATEGORY_RULES`；`_DELIVERY_TAG_VALUES = asset_taxonomy.DELIVERY_TAG_VALUES`（三个同理）。
- `semantic_matching.py`：`ROLE_CATEGORY_FIT = asset_taxonomy.ROLE_CATEGORY_FIT`。
- `video_renderer.py`：`CATEGORY_PRIORITY = asset_taxonomy.CATEGORY_PRIORITY`。

### A3. 跑测试确认零回归

```
pytest -q
```
特别关注：`test_hotspot_topic_pack_video_flow.py`、`test_dual_library_matching.py`、`test_video_generation_rendering.py`、`test_hotspot_lexicon.py`。
**阶段 A 结束时所有测试必须与改动前一致全绿，才能进入阶段 B。**

---

## 阶段 B —— 合并两份漂移的 `CATEGORY_KEYWORDS`（带评审，会改变行为）

⚠️ `media_assets.py:26` 与 `video_renderer.py:255` 是两份**已经漂移**的同名词表。合并成一份会改变分类结果，必须逐条决策，**不许静默丢词**。

### B1. 采用「并集」为基线，下列分歧项**逐条保留并加注释**

| 类目 | 仅 media_assets 有 | 仅 video_renderer 有 | 处理建议 |
|---|---|---|---|
| warehouse | — | `货物` | 并入（保留 `货物`）|
| delivery | `货车,拖车,厢式车,truck,van,trailer` | `车辆,路线` | 全部并入 |
| customs | — | `文件,单据` | ⚠️`文件/单据`太泛，易误伤——**默认并入但打注释**，请人工确认是否保留 |
| brand | — | `信息卡,结尾` | 并入 |
| staff | `访谈` | — | 见下方冲突 |
| customer | — | `采访` | 见下方冲突 |

**必须请示的冲突**：`访谈`(staff) vs `采访`(customer) —— 采访/访谈类镜头两份词表归类相反。合并前**停下来问人**：这类镜头应归 `staff` 还是 `customer`？确定后只保留一个归属，避免同一画面被两处判成不同类。

### B2. 落地

- 在 `asset_taxonomy.py` 定义合并后的唯一 `CATEGORY_KEYWORDS`。
- `media_assets.py:26` 与 `video_renderer.py:255` 改为 `CATEGORY_KEYWORDS = asset_taxonomy.CATEGORY_KEYWORDS`。
- 检查 `semantic_matching.py:120` `_tag_map` 里内联的 `category_tags`（warehouse→仓库/仓库作业 等）——这是 category→标签的反向映射，**本阶段先不动**，只在代码里加一条 `# TODO: 与 asset_taxonomy 对齐` 注释，留作阶段 C。

### B3. 回归 + 人工核对

```
pytest -q
```
- 合并后 `guess_category`（media_assets）与 `_match_asset_by_scene`（video_renderer）命中率都会上升，属预期。
- 手工抽查：拿 3~5 个真实素材文件名/分镜 visual 跑 `guess_category`，确认没有把 staff 误判成 delivery 之类的明显错误。
- 若某测试因"现在能匹配上了"而失败，判断是词表变好还是断言过旧，再改断言。

---

## 交付验收清单（回给总指挥核对）

- [ ] 新增 `asset_taxonomy.py`，docstring 声明其与 `hotspot_lexicon` 的分工
- [ ] 5 处旧常量全部改为指向新模块的别名，无重复定义残留
- [ ] `MOOD_TERMS/BUSINESS_TERMS/SCENE_ROLE_ALIASES` 未被并入（边界遵守）
- [ ] 两份 `CATEGORY_KEYWORDS` 已合并为一份，分歧项逐条留痕
- [ ] `访谈/采访` 归类冲突已按人工决定统一
- [ ] `pytest -q` 全绿；列出因合并而调整的测试及原因
- [ ] `semantic_matching._tag_map.category_tags` 留了对齐 TODO
