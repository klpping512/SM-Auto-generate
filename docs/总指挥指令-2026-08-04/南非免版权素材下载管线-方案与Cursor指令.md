# 南非免版权素材下载管线 — 方案 + Cursor 执行指令

> 总指挥出品 · 2026-08-04
> 一句话回答你的问题「哪个适合我、要能下载的」：**Pexels + Pixabay（有官方 API、免费、可商用、能程序化下载），Mixkit 作人工补口。TikTok/YouTube/Facebook 的爆款一律不下载**，只用来"学 Hook 结构"。

---

## 0. 先纠一个会让你返工的坑

你原来的素材树是「城市 / 商业 / 物流 / 人文 / 热点」。**这套分类下载回来，过不了本系统的 owned-matching 闸，等于白下。**

本系统成片是 FFmpeg 拼真实素材，选镜靠 `asset_taxonomy` 的 **8 类固定 primary_category**：
`warehouse / delivery / customs / brand / staff / facility / customer / other`。
话题节点只认这 8 类（`NODE_CATEGORY_RULES`），比如"清关"节点**只吃 `customs`**。你下一堆"Cape Town 城市空镜"标成"城市"，系统里既进不了 customs 也进不了 delivery，永远匹配不到。

**所以铁律：下载归档一律按这 8 类子目录名落地**，其余全归 `other`。城市/生活/人文类空镜价值很低（本系统不是做旅游片，是做物流营销），只在做"品牌开头大景"时少量留用，归 `brand` 或 `other`。

---

## 1. 分层策略（你混在一起的两件事，必须拆开）

| 层 | 干什么 | 能不能下载 | 落在哪 |
|---|---|---|---|
| **A. Hook 学习层** | TikTok/YouTube/FB 南非爆款 → 提炼"为什么爆 / 怎么改成物流营销 / 对应哪个客户痛点" | **不下载**（版权 + 授权门禁不允许） | 产出**文本模板**，不进素材库 |
| **B. 素材供给层** | Pexels/Pixabay/Mixkit 免版权片 → 程序化下载 → 8 类归档 → 入库 | **能下载、可商用** | `LOCAL_ASSET_ROOT` → 现有 ingest → owned 池 |

A 层的价值是"抄结构不抄素材"：看 100 条南非爆款，总结出可复用的开场句式（"很多中国老板误解了南非…""为什么发南非最怕最后一公里…"），这些句式喂给你的脚本生成环节。**爆款视频本身不能剪进成片**——这正是你现有 hotspot 漏斗"线索广抓、下载/使用走授权门禁"的同一条红线，别破它。

本文档只把 **B 层做成自动化管线**（你选的形态）。A 层我在 §8 给你一份"Hook 模板提炼"的清单，人工/AI 都能用。

---

## 2. 源选择（能下载 + 可商用 + 有 API）

| 源 | API | 授权 | 是否署名 | 角色 |
|---|---|---|---|---|
| **Pexels Videos** | ✅ 官方 REST | Pexels License，免费商用 | 免（仍存证） | **主力**，视频质量最好 |
| **Pixabay Videos** | ✅ 官方 REST | Pixabay Content License，免费商用 | 免（仍存证） | **补充**，量大 |
| **Mixkit** | ❌ 无 API | Mixkit Free License | 免 | 人工补口，复用现成 `scripts/ingest_mixkit_asset.py` |
| Coverr / Videvo | ❌/受限 | 各自免费授权 | 视条款 | 备选，人工 |

> 都要免费申请 key：Pexels 在 https://www.pexels.com/api/ ；Pixabay 在 https://pixabay.com/api/docs/ 。
> 速率：Pexels 200 次/小时、20000 次/月；Pixabay 100 次/60 秒。管线要限速，别一次打爆。

---

## 3. 归档结构（直接对齐系统落地契约）

代码真相（已核对）：
- `local_asset_import.configured_root()` = env `LOCAL_ASSET_ROOT`，默认 `~/Desktop/视频&图片素材`。
- `media_assets.guess_category()` **子目录名优先**打标，命中不了才看文件名，再不中归 `other`。
- `media_assets.ingest_file()` 内建 **sha256 去重**（重跑安全）+ 支持 `category="auto"`。
- `db.update_asset_provenance(asset_id, source_url, license_name, attribution)` = 合规存证入口。

所以下载目录长这样（**子目录名必须用系统认得的英文/中文关键词**，我已按 `CATEGORY_KEYWORDS` 选了能命中的名字）：

```
$LOCAL_ASSET_ROOT/
└── za-stock/                     # 本管线专属根，跟你手动素材分开
    ├── warehouse/                # 命中 warehouse 闸
    ├── delivery/                 # 命中 delivery 闸（含港口/卡车/集装箱）
    ├── customs/                  # 命中 customs 闸 ← 最缺，重点填
    ├── facility/                 # 命中 facility 闸（叉车/传送带/流水线）
    ├── staff/                    # 命中 staff 闸（工人/办公/团队）
    ├── brand/                    # 品牌开头大景（少量城市空镜可放这）
    ├── customer/                 # 客户/消费场景
    └── other/                    # 兜底
```

每个视频旁边写一个**同名 `.json` sidecar**（存 provenance），入库脚本读它写进 `update_asset_provenance`。例：`customs/za_customs_pexels_12345.mp4` + `za_customs_pexels_12345.mp4.json`。

---

## 4. 填洞优先级（基于 P0 诊断，别平均用力）

诊断结论（memory / commit 8be5c70）：265 可用片里 **customs = 0**，库存 warehouse 256 / delivery 83 / facility 10。所以：

| 优先级 | 分类 | 现状 | 目标下载量 | 说明 |
|---|---|---|---|---|
| **P0** | `customs` | **0，最严闸** | 15–25 | 免版权库几乎没有"南非海关现场"，用**港口报关/集装箱查验/单据特写/机场货运/edge 关口**作*通用清关上下文*，provenance 必须写"非南非现场、口播只说'备货待清关'不宣称已清关" |
| **P1** | `delivery` | 83 但热点侧饿 | 20–30 | 港口、集装箱船、卡车、最后一公里派送 |
| **P1** | `facility` | 仅 10 | 15–20 | 叉车、传送带、分拣流水线、扫描设备 |
| P2 | `warehouse` | 256 已够 | ≤10 | 只补南非/非洲感强的，别再堆 |
| P2 | `staff` | 中 | 10 | 仓库工人、物流团队办公 |
| P3 | `brand`/`customer` | 低 | 各 5–10 | 品牌大景 + 消费/开箱场景 |

> ⚠️ customs 的坑：**匹配不到不存在的素材，语义向量也救不了**（P1 结论已证），唯一解就是把这 15–25 条 customs 上下文素材真正下回来入库。这是本管线**第一优先、最高杠杆**的产出。

---

## 5. 合规门禁（每条素材必写，别偷懒）

继承现有 `ingest_mixkit_asset.py` 的口径。每个 sidecar `.json` 固定字段：

```json
{
  "source_url": "素材页 URL（Pexels/Pixabay 的 video 页，不是直链）",
  "license": "Pexels License | Pixabay Content License | Mixkit Free License",
  "author": "作者名 + 作者主页",
  "category": "customs",
  "note": "通用港口/清关背景，非南非现场、非 Buffalo 能力；口播仅可表述为『备货待清关』"
}
```

`note` 三条铁律（全部素材统一）：
1. 免版权片 = **通用背景**，不得宣称是南非现场；
2. 不得暗示为 Buffalo 自有能力/自有仓/自有车队；
3. customs/港口类只能作"清关前备货/在途"语境，**不宣称已完成清关**（抄 `NODE_CATEGORY_RULES` 末端的 preparation 模式）。

---

## 6. 接入 hotspot 链路的边界（不碰什么）

✅ **要做**：下载落 `za-stock/<category>/` → 入库脚本 `ingest_file(category="auto")` + `update_asset_provenance` → 素材自动进 owned-matching 候选池 → 被 `/api/diagnostics/owned-matching` 认出 customs 不再是 0。

🚫 **不碰**（硬边界）：
- 不动 `hotspot_lexicon`（那是话题/事件词表，跟画面分类是两回事）；
- 不动 `asset_taxonomy` 的 8 类集合，不新增类目；
- 不改授权门禁 / 匹配阈值 / 渲染参数；
- 不把 A 层爆款视频塞进库。
本管线是**纯供给侧**，只往库里加合规素材，不改任何算法。

---

## 7. 给 Cursor 的执行指令（可直接粘贴）

> 分两个脚本：`pull` 只下载+写 sidecar，`ingest` 只入库+写 provenance。拆开是为了下载失败可重试、入库可幂等复跑。

### 指令块 A — 新建下载脚本 `scripts/pull_za_stock.py`

```
在 distribution-manager 项目新建 scripts/pull_za_stock.py，实现南非免版权 b-roll 批量下载器。要求：

【输入】
- 读 env：PEXELS_API_KEY、PIXABAY_API_KEY（缺哪个就跳过对应源，不报错）。
- 读 env：LOCAL_ASSET_ROOT（缺省 ~/Desktop/视频&图片素材），下载根 = <LOCAL_ASSET_ROOT>/za-stock。
- 复用项目已有的 SA_HOTSPOT_PROXY（缺省回退 SA_YOUTUBE_PROXY）做 httpx 代理，格式同 hotspot_fetcher。
- 一个内置 QUERY_MAP：{category: [英文搜索词...]}，8 类的词见本文档 §8，直接抄进去。
- CLI 参数：--category（可多选，缺省全部）、--per-query（每个搜索词取几条，缺省 3）、--max-seconds（只保留时长 ≤ 该值的片，缺省 20）、--min-height（缺省 720）、--dry-run。

【Pexels 视频 API 契约】
- GET https://api.pexels.com/videos/search ，Header: Authorization: <PEXELS_API_KEY>
- 参数：query、per_page（=per-query，封顶 15）、orientation=portrait、size=medium
- 响应 videos[]：取 video_files[] 中 height>=min-height 且最接近 1080 宽的 mp4；页面 url=videos[].url，作者=videos[].user.name / user.url，时长=videos[].duration（秒），过滤 >max-seconds 的。
- 速率：200/小时——每次请求间 sleep 至少 0.4s；命中 429 时退避重试一次。

【Pixabay 视频 API 契约】
- GET https://pixabay.com/api/videos/ ，参数 key=<PIXABAY_API_KEY>、q、per_page（>=3）、safesearch=true、video_type=all
- 响应 hits[]：取 videos.large.url（无则 medium.url），页面=hits[].pageURL，作者=hits[].user，时长=hits[].duration。
- 速率：100/60s——每次请求间 sleep 0.7s。

【下载与命名】
- 保存到 <root>/za-stock/<category>/za_<category>_<source>_<id>.mp4
- 若目标文件已存在则跳过（幂等），dry-run 只打印不下载。
- 同时写同名 sidecar：<file>.json，字段严格为 {source_url, license, author, category, note}：
    - license：Pexels 用 "Pexels License"，Pixabay 用 "Pixabay Content License"
    - note：统一取本文档 §5 的三条铁律拼成的一句话；customs 类额外前缀"通用港口/清关背景，非南非现场，口播仅可表述为备货待清关"。
- 每类下载完打印：类目、新增数、跳过数、失败数。

【健壮性】
- 任一源/任一 query 失败只记 warning 继续，不中断整批（照 hotspot_fetcher 的优雅降级风格）。
- 全程用 httpx（项目已依赖），超时 30s。
- 不要引入新的第三方依赖。

写完给我：①脚本；②一段 README 说明怎么设 key 和跑；③一个 pytest（mock httpx，验证 sidecar 字段完整 + 幂等跳过逻辑），放 tests/test_pull_za_stock.py。
```

### 指令块 B — 新建入库脚本 `scripts/ingest_za_stock.py`

```
在 distribution-manager 项目新建 scripts/ingest_za_stock.py，把 <LOCAL_ASSET_ROOT>/za-stock 下已下载的素材批量入库。要求：

- 复用 media_assets.ingest_file 与 db.update_asset_provenance（照 scripts/ingest_mixkit_asset.py 的模式）。
- 遍历 za-stock/**/*.{mp4,mov,webm,jpg,jpeg,png,webp}，跳过 .json。
- 对每个文件：
    1) 读同名 .json sidecar（没有则 warning 跳过，不硬入库——provenance 是硬门禁）。
    2) ingest_file(source, PROJECT_ROOT/"static", category="auto", origin="za_stock_license", created_by=admin.id, import_root=<za-stock 根>, storage_mode="hardlink")
       —— 用 category="auto" 让 guess_category 靠子目录名(warehouse/delivery/customs/...)自动打对 primary_category；传 import_root 避免绝对路径误命中。
    3) 若返回 _dedup=True 则计入"已存在"跳过，不重复写 provenance。
    4) 否则调 update_asset_provenance(asset_id, sidecar.source_url, sidecar.license, sidecar.note)。
- admin 用户取 db.get_user_by_username("admin")，缺则报错退出。
- 先 db.init_db()。
- CLI：--dry-run（只打印将入库的文件与推定分类，不写库）、--category 过滤。
- 结束打印汇总：每类 新增/去重/缺 sidecar 跳过 三个计数。
- 加 pytest tests/test_ingest_za_stock.py：临时目录造 customs/xxx.mp4 + sidecar，mock ingest_file/update_asset_provenance，断言 category=auto 且 provenance 被正确调用；再造一个无 sidecar 的验证被跳过。

不要改动 media_assets.py / asset_taxonomy.py / 任何门禁或匹配逻辑。
```

### 指令块 C — 验收（下载入库后跑一遍）

```
入库后，用现有诊断端点验证 customs 缺口是否被填：
GET /api/diagnostics/owned-matching?topic=清关
预期：candidate 池里 customs 类从 0 变为 >0（等于本次 customs 入库数）。
再跑 topic=末端、topic=运输 确认没把别的闸搞乱。
把三次结果贴回给我，我判断是否达标、要不要补量。
```

---

## 8. 附录

### 8.1 QUERY_MAP（8 类英文搜索词，直接抄进脚本）

```python
QUERY_MAP = {
    "customs": [   # P0 最缺——用港口/集装箱查验/单据/机场货运作通用清关上下文
        "cargo customs inspection", "container port inspection", "shipping documents desk",
        "airport cargo terminal", "container yard crane", "freight paperwork",
    ],
    "delivery": [  # P1
        "cargo ship containers", "container truck highway", "last mile delivery courier",
        "delivery van city", "port container loading", "logistics truck fleet",
    ],
    "facility": [  # P1 仅 10
        "forklift warehouse", "conveyor belt parcels", "package sorting machine",
        "barcode scanner warehouse", "automated sorting line",
    ],
    "warehouse": [ # P2 已够，少补
        "warehouse shelves aerial", "workers picking warehouse", "pallet stacking warehouse",
    ],
    "staff": [
        "warehouse workers team", "logistics office meeting", "dispatch team working",
    ],
    "brand": [     # 少量品牌大景/城市开场
        "cape town aerial drone", "johannesburg skyline", "south africa highway aerial",
    ],
    "customer": [
        "online shopping unboxing", "ecommerce customer parcel", "person receiving package",
    ],
    "other": [],
}
```
> 注：Pexels/Pixabay 里"南非本地"物流现场极少，多是通用国际素材——这正是为什么它们只能当"通用背景"、口播绝不能宣称南非现场。南非本地感靠 §8.2 的 A 层爆款学结构 + 你自有素材补。

### 8.2 A 层 Hook 学习清单（不下载，只产模板）

看 TikTok/YouTube/FB 南非爆款时，每条只填三行，攒成一张"Hook 模板表"喂给脚本生成：

```
爆款链接 | 为什么爆（钩子类型：反差/悬念/数字/痛点）| 改成物流营销的开场句 | 对应客户痛点(customs/delivery/成本…)
```

四个已验证好用的钩子方向（来自你原始思路，保留）：
- **反差**："很多中国老板误解了南非"（配 Cape Town 商场/豪车大景 → brand）
- **机会**："为什么越来越多中国卖家盯上南非"（配电商/开箱 → customer）
- **痛点**："发南非最怕最后一公里"（配港口/卡车/仓库 → delivery/customs）★这条直接打 P0 缺口
- **接地气**："100 人民币在南非能买什么"（配超市/街头 → customer）

---

## 验收清单（总指挥留给你打勾）

- [ ] 申请到 PEXELS_API_KEY、PIXABAY_API_KEY 并写进 .env
- [ ] Cursor 按指令块 A/B 建好两脚本 + 两 pytest，测试绿
- [ ] `python scripts/pull_za_stock.py --category customs delivery facility`（先跑三类 P0/P1）
- [ ] 人工快扫 customs 下载结果，剔掉明显不像"清关/港口"的
- [ ] `python scripts/ingest_za_stock.py`
- [ ] 指令块 C 验收：`/api/diagnostics/owned-matching?topic=清关` 的 customs 候选 > 0
- [ ] 达标后再补 warehouse/staff/brand/customer

---
*本方案只加素材、不改算法；A 层红线（爆款不下载）与你现有 hotspot 授权门禁一致。*
