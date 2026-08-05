# 热点信源调整 — 方案 + Cursor 执行指令（v2 · 第一性原理 + 对抗审查）

> 总指挥出品 · 2026-08-04；v2 修订 2026-08-05（对抗审查后）
> 目标：提高**可下载、可策展的热点母片**中「物流可用」占比，并清掉名存实亡的死源。
> 一句话背景：缺的不是「更多信源」，是「对口、能产出、不占坑」的信源；且母片几乎只来自 YouTube，不是 RSS。

---

## 0. 第一性原理（先对齐优化什么）

### 0.1 系统真实因果链

```
RSS 文字线索 ──FEED_FILTER──► hotspot + signal ──► topic package / 选题侧
                                                    （几乎不产生视频母片）

YouTube 频道 ──无关键词过滤──► video_link 热点媒体
        │
        ├─ HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED=0 → 永远停在 metadata，Hook 库空
        └─ =1 → 下载母片 → Hook 策展 → hotspot_pool 可用片段
```

**成片侧「热点母片 / hook」的供给口是 YouTube，不是 RSS。**
RSS 的价值是：选题包、`logistics_relevance`、文字信号、（可选）Commons 配图。把它和「母片命中率」混谈，会把力气使错层。

### 0.2 本轮真正要优化的指标（必须可测）

| 指标 | 定义 | 数据从哪来 |
|---|---|---|
| **Y-hit** | 本轮新入库 YouTube 热点中，标题/摘要命中 `hotspot_lexicon` 物流词（或 `logistics_relevance≥阈值`）的占比 | 抓取结果 / signals |
| **H-hit** | 本轮新策展成功的 Hook 片段中，primary/节点可归到 warehouse\|delivery\|customs\|port\|border 的占比 | Hook / segment 表 |
| **死源率** | `source_health` 中 `blocked`+`error` 且 `items=0` 的启用源数 / 启用源总数 | 抓取 `source_health` |
| **坑位利用率** | 启用且本轮 `items>0` 的 RSS 数 / `MAX_ENABLED_SOURCES` | 同上 |

验收只认这四类数字的前后对比，不认「感觉物流多了」。

### 0.3 杠杆排序（先贵后便宜会白干）

1. **通授权开关**：`HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED=1`（否则换频道只多 metadata）
2. **通策展墙**：Hook JSON bug（多数路径已修，见 §0.4）
3. **换 YouTube 频道组合**（直接改母片分子分母）← 本文档主战场
4. **砍 RSS 死源、腾坑位给垂直 RSS**（改线索质量与坑位，间接帮选题）
5. **标题/入池过滤（Step 3）**：对仍保留的泛新闻台止血 ← 单独立项，避免与换源混因

### 0.4 前置依赖（状态已更新）

| 依赖 | 状态 | 说明 |
|---|---|---|
| Hook 策展 JSON（MiMo thinking 污染） | ✅ 主体已修（2026-08-04，`hotspot_hook_curator` + `model_router` + requeue） | 仍有少量拒答/坏 JSON 残余；**换源前**用 `reprocess_hotspot_hook_source.py --requeue-uncurated` 确认主路径不再整批卡死即可 |
| 同目录 `Hook策展JSON修复-Cursor执行指令` | ❌ 文件不存在 | 勿再引用失踪文档；以改进日志该条 + 代码为准 |
| `HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED` | ⚠️ `.env.example` 默认 `0` | **换源验收前必须确认生产 `.env` 已为 `1`**，否则 Y-hit/H-hit 测不到母片 |

**顺序不变**：先确认策展墙可过 + 授权开关打开 → 再换 YouTube → 再动 RSS → 最后才做相关性过滤。

---

## 1. 对抗审查结论（原 v1 的坑，必须避开）

| # | 原方案问题 | 为什么致命 | v2 改法 |
|---|---|---|---|
| A | 目标写「提高热点**母片**物流命中率」，主体却在调 RSS | RSS 不产母片；调完仍可能母片全是选举/娱乐 | **YouTube 为 P0，RSS 为 P1**；指标拆 Y-hit / H-hit |
| B | 保留 SABC「靠以后过滤压」，同时把过滤推到 Step 3 | 换源当轮仍被最大噪音台主导（约 4/7 频道仍是泛新闻） | 明确：**本轮期望是边际改善，不是翻盘**；SABC 降权方案写进 Step 3 前置假设 |
| C | The Citizen 标 ★★★ 保留 | 真抓取已 **Cloudflare `blocked`、0 产出**（与 BusinessTech 同类，见 2026-08-03 抓取记录） | **停用**，腾坑位；勿当有效源 |
| D | BusinessTech 决策表写「砍或修」，Step 2 又「保留占坑」 | `enabled=1` 仍占 `MAX_ENABLED_SOURCES=12`，零产出 | **DB 停用**；DEFAULT 可留注释种子但不默认启用 |
| E | `seed_default_sources` 只插入、**从不停用**已删默认源 | 只改 `DEFAULT_OFFICIAL_SOURCES`，DB 里 gov.za/SARB 永远还在 | 必须提供 **显式 disable + insert** 脚本，禁止只改常量 |
| F | Step 0 要求复用 `_is_bot_challenge(exc)` 判 Cloudflare | 该函数签名吃的是 **Exception**，探测成功/失败响应不能直接塞进 | 探测脚本按 **status+headers** 自判；或构造 `HTTPStatusError` 再调 |
| G | Step 0 用 feedparser，生产 `parse_feed` 用 ElementTree + `FEED_FILTER` | 探测「能解析」≠「生产会入库」 | 探测末段应用 **同一 `KEYWORDS`/`FEED_FILTER_PATTERN`** 统计会入库条数 |
| H | 只改 `.env` 覆盖频道，不改 `DEFAULT_YOUTUBE_CHANNELS` | 新环境/漏配 env 仍回落到含 SA Today 的默认 5 台 | **代码默认清单与 env 同步改**；env 作运行覆盖 |
| I | 验收「source_health 显示 7 频道」 | 未核对授权开关与母片下载 | 验收强制查 `authorization_status` 分布 + Y-hit |
| J | South Africa Now「聚合、一般」却保留，只砍 SA Today | 标准不一 | SAN 降为 **观察席**：Step 0 后若近 N 条物流命中≈0，本轮一并砍 |
| K | 附录 RSS 列了 10 活源 + BusinessTech，看似 ≤12，但未扣死源 | Citizen+BusinessTech 占 2 坑零产出时，垂直源加不满或加了仍超有效产能 | **先停用全部 blocked 死源，再按优先级加垂直源** |
| L | 与「仓储 hotspot_pool=0」「customs 内容缺口」未对齐 | 换商业台不保证出 warehouse/customs 画面；Transnet 主要补 **port/delivery 视觉** | 预期写清：本轮优先补 **港口/货运/干线**；customs/warehouse 仍靠免版权管线 / 放闸，不承诺本轮填平 |

---

## 2. 决策总表（v2）

### 2.1 RSS 文字线索层

| 信源 | 物流价值 | 真机状态（已知） | 判决 | 依据 |
|---|---|---|:--:|---|
| SARS | ★★★ | ok | **保留** | 海关/清关不可替代 |
| Moneyweb | ★★★ | ok | **保留** | 财经/港口/燃油/供应链 |
| Daily Maverick | ★★★ | ok | **保留** | 港口/铁路/边境 |
| Department of Transport | ★★ | ok（低产） | 保留 | 相关但更新慢 |
| SAnews | ★★ | ok | 保留 | 权威、信号稀 |
| The South African | ★★ | ok | 保留 | 量大、物流密度低，趋势探测 |
| The Citizen | ★★★内容 | **blocked** | **停用** | 与 BusinessTech 同病：启用=占坑零产出 |
| BusinessTech | ★★★内容 | **blocked** | **停用**（DEFAULT 可留注释备日后浏览器抓取） | 不占启用坑 |
| South African Reserve Bank | ★ | 曾 ok | **停用** | 汇率利率几乎不成 hook |
| South African Government | ★ | — | **停用** | 政策声明，物流信号极低 |

### 2.2 YouTube 视频母片层（P0）

| 频道 | 判决 | 依据 |
|---|:--:|---|
| CNBC Africa | **新增** | 非洲商业财经，对口率远高于泛新闻 |
| BusinessDayTV | **新增** | 南非本土商业/市场 |
| Transnet NPA | **新增** | 港口/铁路官方画面，补 delivery/port 视觉（非 customs 万能药） |
| Moneyweb YT | Step 0 通过则加 | 与 RSS 互补；失败则不加 |
| Newzroom Afrika | 保留 | 偏商业，已修 handle |
| eNCA | 保留 | 国家级，含商业板块 |
| SABC Digital News | **暂留观察** | 量最大噪音也最大；本轮靠垂直台稀释，不承诺翻盘 |
| South Africa Now | **观察席** | 聚合、一般；Step 0 抽样物流命中低则本轮砍 |
| SA Today | **砍** | 量小、聚合、价值最低 |

### 2.3 新增 RSS 候选（仅 Step 0 通过且停用死源后）

优先级：**Freight News > Logistics News SA > Supply Chain News Africa**。
只加探测通过的；宁缺毋滥。Feed 路径以 Step 0 命中为准。

**坑位算术（强制）**：

- `MAX_ENABLED_SOURCES = 12`
- 先停用：gov.za、SARB、Citizen、BusinessTech（至少腾 4 坑）
- 保留启用约：SARS、Moneyweb、DM、DoT、SAnews、TSA = 6
- 可新增垂直 ≤ 6，但按优先级最多加 3 个候选足够；**启用总数 ≤ 12，且 blocked 源不得 `enabled=1`**

---

## 3. 执行步骤

### Step 0 — 连通性预验证（先验后加）

垂直站极易 Cloudflare；频道也可能拉不到列表。

**给 Cursor 的指令（可直接粘贴）：**

```
在 distribution-manager 新建 scripts/check_source_candidates.py，纯探测、不写库、不改配置。

【代理】httpx / yt-dlp 均复用 SA_HOTSPOT_PROXY，缺则回退 SA_YOUTUBE_PROXY。

【RSS 候选】对每个站按序尝试常见 feed 路径，命中即停：
  - Freight News: https://www.freightnews.co.za/rss 、/feed 、/rss.xml
  - Logistics News SA: https://logisticsnews.co.za/feed 、/feed/ 、/rss
  - Supply Chain News Africa: https://scnafrica.com/feed 、/feed/
  - 对照基线 Moneyweb: https://www.moneyweb.co.za/feed/

对每个 URL 打印：
  URL | HTTP状态 | Cloudflare?(看 response：status∈{403,429,503} 且 headers 含 cf-mitigated 或 server~cloudflare)
  | 原始条目数 | 经 hotspot_lexicon.FEED_FILTER_PATTERN 过滤后条目数 | 过滤后前3条标题

注意：
  - 不要调用 hotspot_fetcher._is_bot_challenge(response)；它只接受 httpx.HTTPStatusError。
  - 解析优先复用 hotspot_fetcher.parse_feed（与生产一致）；不要只依赖 feedparser 就判「可用」。
  - 「可用」定义：非 Cloudflare、HTTP 2xx、且 FEED_FILTER 后条目数 ≥ 1。

【YouTube 候选】用与 hotspot_video_sources._command 相同参数
（python -m yt_dlp --flat-playlist --playlist-end 5 --dump-single-json）拉 /videos：
  - CNBC Africa: https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ
  - BusinessDayTV: https://www.youtube.com/@BusinessDayTelevision
  - Transnet NPA: https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw
  - South Africa Now: https://www.youtube.com/@SouthAfricaNow1 （观察席抽样）
  - Moneyweb: 先确认官方频道 URL 再验

打印：频道 | 可拉条数 | 前5条标题 | 标题命中 FEED_FILTER_PATTERN 的条数（作粗 Y-hit 代理）

任一失败只 warning 继续。最后输出「可用/不可用」汇总表 + 建议启用清单（按本文档优先级）。
把汇总贴回总指挥定夺后再进 Step 1/2。
```

> ⚠️ Step 1/2 **只加** Step 0 判定可用的源。Cloudflare RSS 禁止硬塞。

### Step 1 — YouTube 调整（代码默认 + env 覆盖，P0）

1. 改 `hotspot_video_sources.DEFAULT_YOUTUBE_CHANNELS`：去掉 SA Today；按总指挥圈定加入垂直台；SAN 按 Step 0 决定去留。
2. 生产 `.env` 用 `SA_HOTSPOT_VIDEO_CHANNELS_JSON` **整体覆盖**为同一清单（非法 JSON 会静默回落默认——改完应用脚本 `json.loads` 自检）。
3. 确认 `HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED=1`。
4. `.env.example` 已有注释示例；补一句：「整体替换不是追加；须含全部要保留频道」。

参考清单（最终以 Step 0 + 总指挥圈定为准）：

```
SA_HOTSPOT_VIDEO_CHANNELS_JSON=[{"name":"SABC Digital News","url":"https://www.youtube.com/@sabcdigitalnews"},{"name":"eNCA","url":"https://www.youtube.com/channel/UCI3RT5PGmdi1KVp9FG_CneA"},{"name":"Newzroom Afrika","url":"https://www.youtube.com/@NewzroomAfrikaTV"},{"name":"CNBC Africa","url":"https://www.youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ"},{"name":"BusinessDayTV","url":"https://www.youtube.com/@BusinessDayTelevision"},{"name":"Transnet NPA","url":"https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw"}]
```

（若 Step 0 证明 SAN / Moneyweb YT 有物流命中，再追加进数组。）

每频道上限仍由 `HOTSPOT_YOUTUBE_CHANNEL_VIDEO_LIMIT`（默认 8）与 `MAX_CHANNEL_VIDEO_LIMIT`（24）控制。

**单测**：更新 `tests/test_hotspot_video_sources.py` 中对默认频道名单的断言（无 SA Today，含新垂直台）。

### Step 2 — RSS 增删 + 强制 DB 同步（P1）

**给 Cursor 的指令（可直接粘贴）：**

```
改 hotspot_fetcher.py 的 DEFAULT_OFFICIAL_SOURCES：

【从默认启用集移除】（可从列表删除，或保留条目但文档写明「默认不启用」）
  - South African Government (gov.za)
  - South African Reserve Bank (resbank.co.za)
  - The Citizen（真机 Cloudflare blocked）
  - BusinessTech（真机 Cloudflare blocked；若保留在文件中须注释说明「仅备日后浏览器抓取，seed 不得 enabled=True」）

【新增】仅追加 Step 0 验证通过的垂直 feed，格式同现有项（name/url/allowed_domains/purpose）。
  优先级：Freight News > Logistics News SA > SCN Africa。

【上限】启用总数 ≤ MAX_ENABLED_SOURCES(12)。禁止靠「blocked 但仍 enabled」凑数。

【关键】seed_default_sources() 只 insert、不停用。必须新增 scripts/reseed_hotspot_sources.py（或等价）：
  1) 按 feed_url/name 将 gov.za、SARB、Citizen、BusinessTech 在 DB 中 enabled=0
  2) 将 Step 0 通过的新源 insert（若 URL 不存在），在未超上限时 enabled=1
  3) 打印变更前后 list_hotspot_sources 对照表
  4) 若环境设置了非空 SA_HOTSPOT_FEEDS_JSON，脚本必须警告：运行时以 env 为准，改 DEFAULT/DB 不会生效

【测试】更新 test_hotspot_fetcher.py：默认名单/数量断言；新增 reseed 停用+插入的单测。
```

### Step 3 — 物流相关性过滤（记账，单独立项）

即使垂直台就位，SABC 仍会灌选举/娱乐。止血点在：

- YouTube 入池前对 **title** 做 `FEED_FILTER_PATTERN` 或更严的物流权重（今日 **YouTube 路径无关键词过滤**，这是噪音主因）
- 与「仓储 hotspot_pool=0」同源：抓取覆盖而非打标

**建议**：Step 1/2 跑一轮拿到 Y-hit/H-hit 后再立项。本轮禁止与换源同时改过滤，以免无法归因。

---

## 4. 验收清单

- [ ] 前置：策展主路径不再整批「未返回合法 JSON」；生产 `HOTSPOT_CONFIGURED_SOURCES_AUTHORIZED=1`
- [ ] Step 0：汇总表已出；总指挥圈定实际清单；SAN/Moneyweb YT 去留有书面结论
- [ ] Step 1：`DEFAULT_YOUTUBE_CHANNELS` 与 `.env` JSON 一致；无 SA Today；JSON 可 `json.loads`
- [ ] Step 2：死源 DB `enabled=0`；垂直源已启用；启用数 ≤ 12；无「blocked 却 enabled」；相关测试绿
- [ ] 跑一轮 `run_authorized_hotspot_prewarm.py`（或等价授权预热），记录：
  - 调整前基线（若无，先抓一轮只读基线再改配置）
  - 调整后 **Y-hit、H-hit、死源率、坑位利用率**
  - Transnet/CNBC 是否出现港口/货运类母片（抽 5 条人工看）
- [ ] 数据贴回总指挥 → 决定是否立 Step 3（YouTube 标题过滤）及是否砍 SABC/SAN

**本轮成功线（建议）**：Y-hit 相对基线 **+15pct 及以上**，或垂直频道贡献的新母片中物流标题占比 **≥50%**；死源率下降。未达则优先 Step 3，而不是继续加源。

---

## 5. 附录：调整后信源全景（预期，以 Step 0 为准）

**RSS（启用 ≤12，禁止 enabled 死源）**：SARS、Moneyweb、Daily Maverick、Department of Transport、SAnews、The South African、＋ Step 0 通过的垂直源（Freight / Logistics / SCN）。  
**已停用**：gov.za、SARB、The Citizen、BusinessTech。

**YouTube（约 6，观察席另计）**：eNCA、Newzroom Afrika、SABC（观察）、CNBC Africa、BusinessDayTV、Transnet NPA；（SAN/Moneyweb YT 可选）。

**已核验地址**：

- CNBC Africa — `youtube.com/channel/UCsba91UGiQLFOb5DN3Z_AdQ`
- BusinessDayTV — `youtube.com/@BusinessDayTelevision`
- Transnet NPA — `youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw`
- 垂直 RSS feed 路径 — **必须** Step 0 探测确认

**明确不承诺**：本轮单独填平 customs=0 或 warehouse hotspot_pool=0；那两条继续走「免版权管线 / 清关放闸」轨道。

---

*v2：只换对口且能产出的源，并把母片层与线索层拆开度量；相关性过滤(Step 3)留待数据到手后单独立项。*
