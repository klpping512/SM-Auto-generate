# 小红书图文运营闭环：发布台账 + 周复盘导出 —— Cursor 执行指令（第 3 批）

> 日期：2026-08-06
> 状态：**已执行**。
> 设计真源：《小红书图文种草链路-对抗优化版》4.7 数据监控与复盘、第六节 第 3 批 + `README.md` 第 3 批范围。
> 拍板：总指挥确认——批次 0/1/2 已把出图链做到「生成→门禁→渲染→差异化→可观测发布」，本批接上**人驱动数据回流**：发布即建档、运营补指标、周五可导出复盘。系统只存台账、只做导出，**不承诺自动拉数**。
> 目标：商务矩阵铺量后，阅读/赞藏/评论/涨粉/48h 判定有处可记，周复盘（Top/Bottom 选题、封面类型、关键词）一键导出。不建重系统、不自动扒数、不自动改模板。

## 零、前置依赖（先确认再开工）

| 子块 | 依赖 | 状态 |
|---|---|---|
| 3A 台账表 + 录入 API | 已发布条目（`publish_log`，批次 0 已就绪）、`seo_meta`（批次 2 已就绪） | ✅ 可执行 |
| 3B 发布成功自动预建 | 3A 的 `xhs_ledger` 表 | ✅ 可执行 |
| 3C 周复盘导出 | 台账数据（人工录入积累） | ⛔ 数据靠运营填，**机制先建**，空数据返回空表头 |

> **开批建议**：A→B→C 顺序一次落地。3A/3B 无阻塞；3C 导出机制先行，数据自然积累，首个周五即有内容可导。

## 一、背景与 Why

已核实的事实：

- **发布结果已有可观测基础**（批次 0）：`publish_log` 落成功/重试/失败 + `failure_category`，`queue` 落 `status`、`target_account_id`、`seo_meta`（批次 2）。系统知道"发了什么、谁发的、主词长尾是什么"。
- **数据回流为零**：发完即断。阅读/赞藏/评论/涨粉/48h 判定没有落点，运营拿 Excel 手记，周五复盘靠人翻。
- **复盘所需三样聚合**（设计 4.7）：Top/Bottom 选题、封面类型分布、关键词表现——全部可由台账字段派生，但**没有任何代码产出**。
- **铁律前置**（对抗版击倒 5）：不做自动 7/30 天扒数（无官方 API、RPA 脆且合规风险高），人驱动先闭环。系统只存台账，不承诺自动拉数。

本批真实缺口：① 台账表与录入接口缺失；② 发布成功后无"待填指标"的建档行；③ 周复盘无导出。

## 二、铁律（不做的事）

1. **不自动扒小红书后台数据**——阅读/赞藏/评论/涨粉全部人工录入，任何 RPA/爬虫/OCR 拉数行为即违规。
2. **不自动改模板版本 / 不做封面 A/B 实验框架**——台账数据未积累、未复盘前，不许动 `xhs_cards` 模板或渲染逻辑。
3. **不移植视频 P3 评估器 / `weighted_actionable_score` / 自动重生成**。
4. **不做「策展 + 入库」双诊断表复刻**——台账只用**单表** `xhs_ledger`，不新增任何诊断/分析表。
5. **台账是运营数据，不是系统判定输入**——`xhs_diff_guard`、`xhs_quality_gate`、`truth_guard` 一律**不读** `xhs_ledger`。台账只出人看，不进机器决策。
6. **时区口径：台账日期一律用 UTC**——`published_on` 从 `date(publish_log.published_at)` 派生（与 `count_published_today` 的 `date('now')` 口径一致），**禁止**用本地 `datetime.now()` 当台账日期（批次 2 已识别本地/UTC 时区坑，台账不许重蹈）。
7. **不顺手修批次 2 的两个发现**（`check_scheduled_publish` 未接 diff guard、guard 本地时区）——那属整体修复单，本批只在 `scheduler.py` 成功分支**加台账预建**，**不许**改动 diff guard 接入逻辑。

## 三、改动清单（三块，建议 A→B→C 顺序）

### 改动 A：台账表 + DB 方法（`database.py`）

**A1 新表 `xhs_ledger`**（单表，最小充分，`IF NOT EXISTS` 照旧）：

```sql
CREATE TABLE IF NOT EXISTS xhs_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL UNIQUE,      -- 关联已发布条目，同一条只一行台账
    publish_log_id INTEGER,                -- 具体 published 日志行（可空）
    title TEXT DEFAULT '',                 -- 从 queue 快照
    account_name TEXT DEFAULT '',          -- 从 accounts 快照（防改名/删除）
    published_on TEXT DEFAULT '',          -- UTC 日期 date(publish_log.published_at)
    topic_level TEXT DEFAULT '',           -- S / A / B（运营选）
    cover_type TEXT DEFAULT '',            -- 大字报/对比图/清单体/实拍+标注/问答体（运营选）
    seo_meta TEXT DEFAULT '{}',            -- 主词/长尾快照（从 queue.seo_meta 预填）
    reads INTEGER DEFAULT 0,               -- 阅读
    likes_saves INTEGER DEFAULT 0,         -- 赞藏
    comments INTEGER DEFAULT 0,            -- 评论
    followers_gained INTEGER DEFAULT 0,    -- 涨粉
    verdict_48h TEXT DEFAULT '',           -- 待判定/达标/未达标（运营选，备注记原因）
    notes TEXT DEFAULT '',
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (queue_id) REFERENCES queue(id)
);
```

取值字典（从设计真源 4.3/4.4 抄，写入注释）：
- `topic_level`：`S` 已验证爆文复制 / `A` 搜索词占位 / `B` 人设日常互动
- `cover_type`：`大字报` `对比图` `清单体` `实拍+标注` `问答体`
- `verdict_48h`：`待判定` / `达标` / `未达标`（未达标原因进 `notes`）

**A2 DB 方法**：

```python
def ensure_xhs_ledger(queue_id: int) -> int | None:
    """发布成功时幂等预建台账行。queue_id 已建档则返回现有 id（不重复插）。
    预填：title / account_name（accounts.name，经 queue.target_account_id）/
    published_on（date(publish_log.published_at)，取该 queue 最新一条 status='published' 的日志）/
    seo_meta（queue.seo_meta 快照）/
    created_by（= queue.created_by，权限口径「非 admin 只看自己建的行」，不填则编辑打开台账
    看不到自己刚发布成功的行）。指标字段全 0，topic_level/cover_type/verdict_48h 留空待人工。
    返回 ledger id；queue 非 xiaohongshu 或非 published 时返回 None。"""

def list_xhs_ledger(from_date: str | None = None, to_date: str | None = None) -> list[dict]:
    """台账列表，按 published_on DESC。可传日期区间过滤（含边界）。每行解析 seo_meta JSON。"""

def update_xhs_ledger(ledger_id: int, fields: dict) -> None:
    """人工录入/更新：只更新显式传入的字段（None 跳过），刷新 updated_at。
    允许更新的字段白名单：topic_level / cover_type / reads / likes_saves / comments /
    followers_gained / verdict_48h / notes。白名单外字段拒绝。"""

def list_xhs_ledger_candidates() -> list[dict]:
    """已发布但未建档的小红书条目（供录入页下拉）：
    publish_log status='published' AND platform='xiaohongshu'，排除已存在 xhs_ledger 的 queue_id。
    返回 [{queue_id, title, account_name, published_on, seo_meta}]，按 published_on DESC。"""

def weekly_xhs_ledger_summary(from_date: str, to_date: str) -> dict:
    """周复盘聚合（全部基于 xhs_ledger 中 published_on 落在区间内的行）：
    - overview: 条数 / 达标数（verdict_48h='达标'）/ 平均阅读 / 平均互动率（(赞藏+评论)/阅读，
      **reads=0 时互动率记 0**，空表/新预建行不除零）
    - top_bottom: 按 reads DESC 的 Top3 与 Bottom3（字段：title/topic_level/cover_type/主词/
      reads/likes_saves/comments/followers_gained/verdict_48h）
    - cover_dist: {cover_type: 条数, 平均阅读, 平均赞藏}（按条数 DESC）
    - keyword_perf: {主词: 条数, 平均阅读, 平均互动率}（按条数 DESC；主词取 seo_meta.main，无主词归 '—'）
    """
```

**明确不做**：自动拉数逻辑；台账行 `status` 字段（无需 draft/confirmed 状态机）；多表/诊断表。

### 改动 B：API（`app.py`）

五个接口，全部走 `get_current_user`：

1. `GET /api/xhs/ledger?from=&to=` → `list_xhs_ledger`。返回台账行（含解析后的 `seo_meta`）。
2. `POST /api/xhs/ledger` → body `{queue_id, topic_level?, cover_type?, reads?, likes_saves?, comments?, followers_gained?, verdict_48h?, notes?}`。
   - **语义：只服务历史补建**。正常路径是「发布成功自动预建 → 台账列表 `PUT` 填指标」；`POST` 是给批次 3 上线前已发布、未建档的存量条目补建（配合候选下拉）。
   - 校验：queue 存在；`platform == 'xiaohongshu'`；该 queue 存在 `status='published'` 的 publish_log（未发布不许建档）；已建档则 **409**（人话：「该条目已建档」）。
   - 通过后 `ensure_xhs_ledger(queue_id)` 建行，再 `update_xhs_ledger` 写入人工字段。返回 `{id, ...}`。
3. `PUT /api/xhs/ledger/{ledger_id}` → body 同 POST（不含 queue_id）→ `update_xhs_ledger` 白名单字段。返回 `{"status":"ok"}`。
4. `GET /api/xhs/ledger/candidates` → `list_xhs_ledger_candidates`。
5. `GET /api/xhs/ledger/export?from=&to=` → **CSV 导出**（`StreamingResponse`，`text/csv; charset=utf-8-sig`，文件名 `xhs-ledger-week-{from}-{to}.csv`）。
   - 区块（用 `# 区块名` 行分隔，Excel 中文直接打开不乱码）：
     - `# 概览`：周区间 / 发布条数 / 达标数 / 达标率 / 平均阅读 / 平均互动率
     - `# Top 选题（按阅读）`：rank/标题/选题级别/封面类型/主词/阅读/赞藏/评论/涨粉/48h 判定
     - `# Bottom 选题（按阅读）`：同上
     - `# 封面类型分布`：封面类型/条数/平均阅读/平均赞藏
     - `# 关键词表现`：主词/条数/平均阅读/平均互动率
   - 无数据：各区块保留表头，数据行为空（**不报错**）。
   - `from`/`to` 缺省：**UTC 本周一至 UTC 今天**（`date('now','weekday 0','-6 days')` 与 `date('now')` 口径），**不许用本地时区算周界**——`published_on` 一律 UTC，周界若用本地会重蹈批次 2 时区坑。

**明确不做**：台账删除接口（删 queue 时联删可留后续）；分页（台账量级小，全量返回）；鉴权细分（编辑/审核/管理员均可录入；admin 可见全部，非 admin 只可见自己 `created_by` 的行——与 `get_publish_logs` 的权限口径一致）。

### 改动 C：发布成功自动预建（三处触点）

在**三处**发布成功分支（`add_publish_log(..., "published")` 之后）各加一行 `db.ensure_xhs_ledger(item_id)`：

1. `app.py` `publish_item` 的 `result["success"]` 分支（约 L694-696）
2. `app.py` `publish_batch` 的 `result["success"]` 分支（约 L757-759）
3. `scheduler.py` `check_scheduled_publish` 的 `result["success"]` 分支（约 L560-564）

`ensure_xhs_ledger` 幂等（queue_id UNIQUE），重复发布/重试转成功不会重复建档。**不许**改动这三个分支里的 diff guard / retry / 其他逻辑。

### 改动 D：前端 `static/ledger.html`（最小）

- 页面三块：
  1. **候选建档**：`/api/xhs/ledger/candidates` 下拉（显示「标题 · 账号 · 日期」），选中后可填 选题级别/封面类型 → 建行。
  2. **台账列表**：`/api/xhs/ledger` 表格（日期/账号/标题/选题级别/封面类型/主词/阅读/赞藏/评论/涨粉/48h/备注），每行可编辑指标并保存（`PUT`）。
  3. **周导出**：起止日期选择 + 「导出 CSV」按钮（`/api/xhs/ledger/export?from=&to=`）。
- 导航：`static/common.js` `NAV_ITEMS` 在「分析」区加 `{ id: 'ledger', label: '发布台账', href: '/ledger.html' }`，`NAV_ICONS` 补 `发布台账` 图标（可复用现有 `审核中心` 风格路径）。
- 样式复用现有 design-system.css，不引第三方库。

### 改动 E：测试 `tests/test_xhs_ledger.py`

- `ensure_xhs_ledger`：xhs published queue → 建档且 title/account/published_on/seo_meta 预填；重复调用 → 同 id 不重复插；非 xhs / 未 published → None。
- `list_xhs_ledger`：日期区间过滤边界包含。
- `update_xhs_ledger`：白名单字段更新生效；白名单外字段被拒；updated_at 刷新。
- `list_xhs_ledger_candidates`：已发布未建档出现、已建档不出现。
- `weekly_xhs_ledger_summary`：概览计数/达标数、Top3/Bottom3 排序、封面分布、关键词分组（无主词归 '—'）。
- 导出：`GET /api/xhs/ledger/export` 有数据返回五区块表头；无数据不报错。
- 回归：`test_xhs_diff_guard` / `test_xhs_quality_gate` / 发布可观测性无回归。

跑：定向 `tests/test_xhs_ledger.py`，再全量 `python3 -m pytest -q`。

## 四、验收清单

1. pytest：新增全绿，批次 0/1/2 相关测试无回归，全量无新增破坏。
2. **重启 app**。
3. **预建**：发布一条小红书成功 → 台账页出现该行，title/账号/日期/seo_meta 已预填、指标为 0；同一 queue 再发/重试成功不重复建档。
4. **scheduler 触点**：给一条 queue 设 `scheduled_at` 过去时间触发 `check_scheduled_publish` → 成功发布后台账自动出现该行（专门验证 scheduler.py 三处触点之一）。
5. **人工录入**：候选下拉能选到已发布未建档条目；建行后填指标保存 → 刷新仍在；改 48h 判定为「达标」→ 覆盖成功。
6. **导出**：有数据周导出 CSV 含概览/Top3/Bottom3/封面分布/关键词表现五区块；无数据周返回空表头不报错；Excel 打开中文不乱码。
7. **权限**：编辑/审核/管理员可录入；非 admin 只看到自己建的台账行；admin 看到全部。
8. **铁律**：`xhs_diff_guard` / `xhs_quality_gate` / `truth_guard` 无任何 `xhs_ledger` 引用；无新增诊断表；`scheduler.py` 改动仅限成功分支加 `ensure_xhs_ledger`（diff guard 接入逻辑未动）。

## 五、回滚

按子块独立回滚：`git revert` 对应提交。
- 3A 回滚：表保留无害（新表不破坏存量）；API 404。
- 3B 回滚：发布路径移除预建调用，台账恢复"纯人工建档"（候选接口仍可用）。
- 3C 回滚：导出接口移除；数据仍在表中。
三块互不牵连；`xhs_ledger` 表可长期保留，删除需单独确认。

## 六、备注

- **台账日期口径**：`published_on` 必须来自 `date(publish_log.published_at)`（UTC）。这是批次 2 时区发现的反向应用——台账不许再引入本地/UTC 混用。
- **`ensure_xhs_ledger` 的 `account_name` 快照**：从 `queue.target_account_id` → `accounts.name` 取；账号删除/改名不影响已建档行。
- **导出用 `StreamingResponse` + `utf-8-sig`**：Excel 直接打开中文不乱码；区块用 `# 名称` 行分隔即可，不需要多 sheet（CSV 无 sheet）。
- **48h 判定是运营动作输入**：达标 → 追评/系列续篇/他号差异化跟进；未达标 → 记原因进淘汰清单。系统只存字段，不自动执行任何跟进。
- 批次 2 的两个发现（scheduler 未接 diff guard、guard 时区）**不在本批范围**，另开整体修复单；本批在 scheduler 加台账预建时**不要**顺手动 diff guard 代码。
