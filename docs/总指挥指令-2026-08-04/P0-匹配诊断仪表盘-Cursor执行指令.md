# P0 · 素材匹配诊断仪表盘 —— Cursor 执行指令

> 目标：当"物流节点 → Buffalo 自有素材"匹配不足时，**暴露它死在哪道闸门**，让运营知道该①补素材②改标签③调阈值，而不是盲目人工打标。
> 本次是**纯观测**：只加诊断输出，**绝不改变任何选片/排序/阈值行为**。

## ⛔ 硬性边界

1. 诊断函数必须**零副作用**：不改 `_owned_candidates` / `_rank` / `plan_followup_scenes` 的返回值与选片结果，不改任何阈值（owned<4、match 35、quality 80 都不动）。
2. **复用**现有 `_functional_categories`、`_eligible_owned_categories`、`hotspot_lexicon.extract_terms`——诊断必须走和真实匹配**同一套判定**，否则诊断会撒谎。不要另写一套并行逻辑。
3. 不新增任何模型调用。
4. 诊断计算可能遍历较多素材，务必只在"被显式请求诊断"时执行（见下方开关），不拖慢正常成片主流程。

---

## 步骤 1 · 在 `hotspot_video_planner.py` 加"漏斗诊断"纯函数

新增 `def diagnose_owned_matching(segments, brief) -> dict`，**照抄 `_owned_candidates`(L241 起) 的每一道闸门顺序**，但不是筛选，而是**逐闸计数并记录被刷掉的样本**。返回结构：

```python
{
  "eligible_categories": [...],        # _eligible_owned_categories(brief) 的结果；None 表示 legacy 不限分类
  "logistics_nodes": [...],            # brief.get("logistics_nodes")
  "total_segments": N,                 # 传入 segments 总数
  "funnel": {                          # 每道闸门"通过后剩余"计数，顺序与 _owned_candidates 一致
    "is_video": n1,                    # 通过"必须是 video"
    "not_licensed_stock": n2,          # 通过"非第三方素材源"
    "category_match": n3,              # 通过"functional_categories & eligible_categories"
    "after_dedup": n4,                 # 同一 asset 去重后（最终候选数）
  },
  "dropped_by_category_mismatch": [    # 因分类不匹配被刷掉的 top 10（最可能是"标错了"）
    {"asset_id":.., "segment_id":.., "primary_category":"..",
     "functional_categories":[..], "brand_visible":bool, "description":"前40字"},
    ...
  ],
  "category_inventory": {              # 被分类闸门刷掉的片，按其"实际拥有的功能分类"聚合计数
    "warehouse": 12, "delivery": 3, ...  # 让人一眼看出"库里有一堆 warehouse，但这条 brief 要 customs"
  },
  "verdict": "empty_pool" | "category_mismatch" | "thin_but_matched" | "healthy",
}
```

`verdict` 判定建议：
- `funnel.is_video == 0` → `empty_pool`（库里就没自有视频，别打标了，去补片）
- `eligible_categories` 非空且 `category_match == 0` 但 `not_licensed_stock > 0` → `category_mismatch`（有片但分类对不上——**大概率标签问题，这才是人工打标该发力处**）
- `after_dedup` 在 1–3 之间 → `thin_but_matched`（能匹配但不够 4，属库存薄，走降级或补片）
- `after_dedup >= 4` → `healthy`

## 步骤 2 · 加事件级关键词诊断（次要路径）

在 `hotspot_event_matching.py` 加 `def diagnose_event_matching(event, segments) -> dict`，复用 `match_event`(L29)/`_rank`(L13) 的 `extract_terms` 逻辑，返回：

```python
{
  "wanted_terms": sorted(list(_terms(event))),   # 事件侧抽出的词
  "owned_pool": len(owned_segments),
  "near_misses": [   # owned 里"词面差一点"的 top 5：有文本但与 wanted 交集为空/极少
    {"segment_id":.., "primary_category":"..",
     "text_terms_sample":[..], "overlap":[..]},  # overlap 为空最说明问题
    ...
  ],
  "verdict": "no_wanted_terms" | "pool_empty" | "no_overlap" | "matched",
}
```
`no_overlap`（候选池非空、wanted 非空、但交集全空）= **词面匹配的结构性短板**证据，为后续 P1（语义向量）留下量化依据。

## 步骤 3 · 让运营能看到——两个出口

**A. 挂进现有 readiness 返回**（低成本、复用现有链路）
在 `app.py` `_chat_video_delivery_readiness`（L4318，owned_count 在 L4380）里，当 `owned_count < 4` **或** `delivery_ready == False` 时，调用 `diagnose_owned_matching(owned_segments, planning_brief)`，把结果塞进返回 dict 的新键 `"diagnostics"`。达标（healthy）时不塞，省开销。

**B. 加一个只读调试端点**（供随时抽查，不依赖走完聊天）
`GET /api/diagnostics/owned-matching?topic=<物流话题>&hotspot_event_id=<可选>`：
- 复用 `_chat_video_logistics_nodes` + `build_brief` 拼一个 brief，跑 `diagnose_owned_matching`，直接返回诊断 JSON。
- 管理员权限（照 `Depends(get_current_user)` + admin 校验既有写法）。
- 若给了 `hotspot_event_id`，附带 `diagnose_event_matching` 结果。

（前端可选：`static/assets.html` 或 `hotspots.html` 加个"匹配诊断"按钮调 B 端点并展示。**本轮先不做 UI，端点返回 JSON 即可**，除非你确认要顺手加。）

---

## 步骤 3.5 · 关键：区分"饿在哪一侧"（热点侧 vs 自有侧）

> 背景：**热点素材侧**每 3 天抓一批、切成几秒 hook 入库，是**时效性/批次刷新**的；**Buffalo 自有侧**相对静态、靠人工打标。两侧"匹配失败"根因相反：自有侧失败可靠打标/补片修，**热点侧失败是信源覆盖/时效问题，打标无效**。诊断必须点明是哪一侧在饿。

在诊断返回里加一层 `starving_side` 判定：
- 统计当前**热点侧**可用池：对给定物流话题/节点，`db.list_hotspot_event_clips` 里 `review_status=confirmed` 且 `clip_status in {ready,pending}`、并能命中该节点词表（复用 `hotspot_lexicon.category_profile` / `extract_terms`）的 hook 数量 = `hotspot_pool`。
- 结合步骤 1 的自有侧 `after_dedup` = `owned_pool`。
- 判定 `starving_side`：
  - `hotspot_pool == 0` → `"hotspot"`（**这一批没抓到对得上的 hook —— 去看信源/等下一批/放宽入库，别打标**）
  - `hotspot_pool > 0` 且 `owned_pool < 4` → `"owned"`（热点有、自有不足 —— 打标/补 Buffalo 片有效）
  - 两者都够 → `"none"`
- 附 `hotspot_batch_age`：最近一次热点抓取批次时间（若 `hotspots`/事件表有 `created_at`/`fetched_at` 可取），让运营知道"这批多久前抓的、是不是该等下一批"。

把 `starving_side` 一并放进 `diagnostics` 与调试端点返回的**顶层**——这是运营最先要看的一句话结论。

## 步骤 4 · 测试

新增 `tests/test_matching_diagnostics.py`，覆盖四种 verdict：
- 空库 → `empty_pool`
- 有 3 条 warehouse 片、brief 要 customs → `category_mismatch`，且 `category_inventory` 含 `warehouse`
- 有 2 条命中分类 → `thin_but_matched`，`after_dedup == 2`
- 有 5 条命中 → `healthy`
- 一条：诊断**不改变** `_owned_candidates` 原返回（同 segments 跑诊断前后，`_owned_candidates` 结果逐一相等）——守住"零副作用"边界。
- 端点测试：管理员 200 且返回含 `funnel`/`verdict`；非管理员被拒。

```
pytest -q tests/test_matching_diagnostics.py
pytest -q   # 全量，确认无回归
```

---

## 交付验收清单（回给总指挥）

- [ ] `diagnose_owned_matching` 逐闸计数，闸门顺序与 `_owned_candidates` **完全一致**（复用同函数，非另写）
- [ ] `verdict` 四态齐全；`category_inventory` 能显示"库里都有什么分类"
- [ ] `diagnose_event_matching` 的 `no_overlap` 能抓出词面匹配失败样本
- [ ] readiness 仅在不达标时附 `diagnostics`；调试端点管理员可用
- [ ] `starving_side` 能区分热点侧饿 / 自有侧饿 / 都够，位于返回顶层
- [ ] "零副作用"测试通过：诊断前后 `_owned_candidates` 结果不变
- [ ] `pytest -q` 全绿（既有 8 个无关失败除外）
- [ ] 跑一次真实 `GET /api/diagnostics/owned-matching?topic=清关`，把 verdict 与 category_inventory 截图/贴回来——我们据此定 P1 该先补素材还是先改匹配算法
