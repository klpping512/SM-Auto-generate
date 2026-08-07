# 总指挥指令 批16 ｜ 内容资产库面修复：Hook 分区 + 媒体格去噪 + 停 RSS 图片灌入

> 日期：2026-08-07 ｜ 状态：**已产出，待 qcoder 执行**
> 拍板：总指挥拍板 A+B+C 一起做合成一批（三个修复轨互补，一起落地才消除"恶性"库面问题）。
> 执行工具：**qcoder**（2026-08-06 晚起指定；批11 为 opencode，批12 起为 qcoder）。
> 前置：批11 未提交改动（app.py / hotspot_media.py / assert_hotspot_channel_set.py）先确认提交状态，避免混交。

---

## 〇、背景与 Why

总指挥 2026-08-07 报内容资产三件事（"几乎全部无 Hook" / "混进 buffalo 原有素材" / "恶性影响生产链"）。诊断闭环（已用生产库 + 代码核验，非猜测）：

1. **"无 Hook"墙 = 媒体格被两类噪音卡灌满。** 热点素材库媒体格约 1188 卡，91% 无用：
   - **794 张 `media_kind=image` 的 RSS 新闻配图**（The South African 335 / Daily Maverick 189 / SAnews 120 / Freight News 82 / 交通部 50），`not_started` / "仅链接"，永不会被下载成片；**8 月新增 687 张 = 抓取管线还在持续灌**（批11 说"图片噪音另立项"没立）。
   - **288 张"已分析无 Hook"**（216 已下载视频 + 72 视频链接）——模型正常拒收演播室/无 b-roll 内容，判定没错。
   - 真正能用的 106 张"有 Hook"卡被淹没。
2. **"混进 buffalo 素材" = 批12 #33 常青开场池已执行。** `generic_logistics` 池 14 条（clip 273-286，warehouse 6 / last_mile 4 / border 4），**母片 = buffalo 自有 8（directory 6 + local_directory 2）+ za-stock 6**，父热点 727-729（publisher="Buffalo 内部素材库"）。全部过门禁 → 以"热点 Hook · 可匹配"卡混在新闻 Hook 区，**前端不区分 `hook_kind`**。
3. **生产链：链路没坏，坏在库面。** 数据层三池干净（assets 表 youtube 母片全部带 hotspot_id 被正确排除、hotspot_media 无 buffalo/za-stock 行）；匹配链功能隔离（`use_generic = not anchor.has_event_anchor`，`allow_broad_match` 只在 use_generic 分支传 True，新闻路径零放宽）。**真正的恶性面**：库面无法区分"新闻锚点 Hook"与"常青开场 Hook"，且 794 图片噪音 + 288 无 Hook 卡拖垮母片检索 → 验收/选材不可信。

本批 = 纯展示层 + 自动灌入停源，**不碰匹配链、不动 MiMo/策展/门禁词表、不物理删数据**。

---

## 二、铁律（不做的事）

1. **不动匹配链与模型判定**：app.py:2003/2020 的 `allow_broad_match`、`use_generic` 分支、`_is_confirmed_renderable_hotspot_hook` 门禁词表、MiMo 策展链路——一字不改。
2. **不物理删除数据**：图片行在热点审核台（hotspots.html）被当证据图计数/展示，删除会影响审核台；本批只用展示过滤 + 停源头。若要彻底清理存量图片另立项。
3. **generic_logistics 池数据本身不动**：14 条是批12 已验收产物、功能正确，只改它们的**展示归属**（从新闻 Hook 区拆出去）。
4. **只改三处**：`static/assets.html`（改动 A/B）、`hotspot_fetcher.py`（改动 C）。
5. **改完必须重启 app**（运行进程持旧代码，已吃过多次亏）。
6. **定位用函数名/代码锚点，不用绝对行号**（批11 在跑会平移行号）。

---

## 三、改动清单

### 改动 A — 热点 Hook 区按 `hook_kind` 分区（治"混进"）

**A1. `renderHotspotLibrary()`（assets.html，锚点 `const eventBody=`）** 把 `visibleEvents` 拆两路：

```js
const newsEvents   = visibleEvents.filter(e => e.hook_kind !== 'generic_logistics');
const genericEvents= visibleEvents.filter(e => e.hook_kind === 'generic_logistics');
```

- `newsEvents` 渲染原"热点 Hook 素材"区块（`<h2>热点 Hook 素材</h2>`，说明补一句"新闻事件锚点画面"语义）。
- `genericEvents` 新增独立区块（放在 news 区块之后）：

```js
const genericEventBody = genericEvents.length
  ? `<section class="virtual-event-library"><div class="virtual-event-library-head"><div><h2>常青开场池（内部素材 / 免版权素材）</h2><p>科普、常青、降级话题的通用开场画面，由 Buffalo 自有素材与 za-stock 免版权素材构建；非新闻事件锚点，仅用于无事件锚话题的开场，不构成任何服务能力声明。</p></div><span>${genericEvents.length} 条开场片段</span></div><div class="asset-grid">${genericEvents.map(virtualEventCard).join('')}</div></section>`
  : '';
```

- `eventBody` 改为 `${newsBody}${genericEventBody}`（news 空态保留原文案；generic 空则不渲染该区块）。
- `allBody` 继续引用 `eventBody`，无需再改。

**A2. `virtualEventCard()`（锚点 `function virtualEventCard`）** 区分类型标 + 素材来源：

- 函数体首行加 `const isGeneric = event.hook_kind === 'generic_logistics';`
- 类型标 `<span class="asset-type">热点 Hook</span>` → `isGeneric ? '常青开场' : '热点 Hook'`。
- 卡片在"所属热点" meta 区下方加一行来源（isGeneric 时）：

```js
${isGeneric ? `<div class="rights-line">素材池：${escapeHtml((va.source_label) || '内部素材')}</div>` : ''}
```

> `va = event.virtual_asset || {}`（函数内已有）；`source_label` 由 `media_assets.public_asset` 给出（za_stock_license → "免版权素材"，directory/local_directory → "Buffalo 原有素材"），已核验可用。
> "可匹配"状态保留（generic 池过门禁 = 可匹配，语义正确）。

---

### 改动 B — 媒体格去噪（治"墙"）

**B1. `filteredHotspotMedia()`（锚点 `function filteredHotspotMedia`）** 默认排除图片噪音卡：

在 `return withinHotspotFreshness(...)` 之前插入：

```js
if (hotspotMediaKind !== 'image' && item.media_kind === 'image') return false;
```

> 语义：除非用户在"媒体类型"下拉显式选了"图片"，否则 `media_kind=image` 的 RSS 配图卡一律不显示（794 张墙瞬间消失）。

**B2. 工具栏 media_kind 下拉文案（锚点 `const toolbar=`）**：第一项"全部媒体"改文案为"全部视频"（value 仍为 `""`），保留"图片/视频链接/已下载视频"选项。让用户知道默认已排除图片。

**B3. 默认浏览模式改"全部素材"（锚点 `let selectedHotspotId='' ... hotspotBrowseMode='latest'`）**：

`hotspotBrowseMode='latest'` → `'all'`。

> 目的：打开热点素材库第一屏 = Hook 区（新闻 + 常青开场）在前，媒体格在后，直接看到可用内容；"最新入库"tab 保留可切换。

**B4.（连带）** `visibleMedia.length` 计数器随 B1 自然回落，无需额外改。

---

### 改动 C — 停 RSS 图片自动灌入（治根）

**C1. hotspot_fetcher.py（锚点 `for candidate in hotspot_media.discover_media_candidates`）** 自动灌入路径跳过 image：

```python
for candidate in hotspot_media.discover_media_candidates(article_response.text, final_url):
    if candidate.get("media_kind") == "image":
        continue          # 批16：RSS 自动灌入不再落图片行（新闻配图噪音，永不用于成片）。
                         # og:image 已单独存 hotspots.image_candidate_url 供卡片缩略图，此处跳过无副作用。
    db.upsert_hotspot_media({**candidate, "hotspot_id": hotspot_id, ...})  # 保持原有 upsert 不变
```

> **不动** app.py:3314 的人工"检查原文媒体"路径——人工在审核台显式发现时仍可拿到图片证据。
> `discover_media_candidates` 函数本体**不改**（tests/test_hotspot_media_discovery.py 直测它）。

---

## 四、验收清单（改完必验）

> 2026-08-07 执行结果（opencode 落地）：1✅ 2✅ 3✅(代码级验证，浏览器目检待总指挥复核) 4✅ 5✅ 6✅ 7✅

1. **重启 app**（必做，旧进程持旧代码；重启后确认 /api/health 正常）。✅ 已重启（kill 81140 → `bash start.sh` 新进程 PID 85567），8080 监听正常、`/static/assets.html` 200、API 鉴权正常。
2. **API 口径**：`GET /api/hotspot-events`（eligible_only 默认 true）返回 **98** 条且每条含 `hook_kind`：**84 `timely_event` + 14 `generic_logistics`**。✅ TestClient + 生产库实测：total=98，kinds={'generic_logistics': 14, 'timely_event': 84}，全部含 hook_kind。
3. **UI 目检（热点素材库）**：
   - 默认打开 = "全部素材"视图，第一屏依次是"热点 Hook 素材"（新闻）区 → "常青开场池"区（14 张卡，类型标"常青开场"，带"素材池：免版权素材/Buffalo 原有素材"标签）→ 媒体格。✅ 代码级验证（hotspotBrowseMode='all' 默认、news/generic 两区渲染、isGeneric 打标 + source_label 行），浏览器目检待总指挥复核。
   - 媒体格无 `media_kind=image` 噪音卡；工具栏计数"媒体"数不含 image。✅ `filteredHotspotMedia` 默认排除 image（除非显式选"图片"），计数随 visibleMedia 回落。
   - "最新入库"tab 切换后仍可用，且媒体格同样无 image 卡。✅ 同一 filter 路径。
4. **停灌验证**：手动跑一轮 RSS 抓取（或等 6h 调度）→ 新 `hotspot_media` 行中 `media_kind='image'` 增量 = **0**；`video_link` 仍正常落库。✅ 源头组合验证：`discover_media_candidates` 对含 og:image+og:video 的 HTML 产出 [image, video_link]，批16 循环跳过 image、保留 video_link（模拟 upsert 计数 1=video）；Wikimedia Commons 授权配图（独立下载通道，`image,commons`）不受影响。真实 6h 调度验证待自然周期。
5. **审核台回归**：hotspots.html 打开任意单热点 → 图片证据卡仍在（存量 794 未删不受影响）。✅ 生产库 image 行 794 未删；hotspots.html 未动。
6. **回归**：全量 pytest 相对基线（875 passed / 8 存量失败）**不新增失败**；`tests/test_hotspot_media_discovery.py` 全过。✅ 全量 885 passed / 8 存量基线失败不变；test_hotspot_media_discovery 全过。3 条断言随拍板行为变更更新（test_hotspot_fetcher 停灌断言、assets.html 两处 UI 断言）。
7. **匹配链零回归**：新闻话题（如"Beitbridge 边境拥堵"）matched 不变；常青话题（"海外仓是什么"）仍 matched + generic 开场（批12 既有行为）。✅ 匹配链代码一字未动（app.py allow_broad_match/use_generic 分支未改），test_matching_diagnostics 全过。

---

## 五、回滚

- 改动 A/B：`git revert <批16 提交>`（纯前端 assets.html，零数据风险）。
- 改动 C：恢复 hotspot_fetcher.py 循环体（`git revert` 或手删 continue 行）。

---

## 六、交付口径

- 完成标记：A/B/C 落地 + 重启 + 验收 1-7 逐条打勾。
- 提交信息：`批16 内容资产库面修复：Hook按hook_kind分区 + 媒体格去噪 + 停RSS图片自动灌入`。
- 回写：README 登记批16 行 + 本文件验收清单打勾。
