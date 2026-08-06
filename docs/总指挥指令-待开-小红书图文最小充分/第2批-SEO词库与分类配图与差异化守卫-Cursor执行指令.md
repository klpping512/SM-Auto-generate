# 小红书图文运营接入：SEO 词库 + 分类配图 + 差异化守卫 —— Cursor 执行指令（第 2 批）

> 日期：2026-08-06
> 状态：**已执行**——2B/2C 已落地；2A 机制先行（种子词库 + seo_meta），运营四层词矩阵到位后校准。
> 设计真源：《小红书图文种草链路-对抗优化版》5.2 ①④⑥、第六节 第 2 批 + `README.md` 第 2 批范围。
> 拍板：总指挥确认——第 0/1 批后出图链已「生成→门禁→渲染→自检→可观测发布」，本批把**运营资产接入系统**：词库进选题、配图跟分类、矩阵差异化可拦。技术最小充分：SEO 用独立词库表而非 `hotspot_lexicon`；配图复用 `asset_taxonomy` 确定性映射；差异化守卫**零表结构新增**（publish_log JOIN queue 派生账号与素材维度）。
> 目标：商务铺量时"词有得选、图跟得上主题、矩阵不撞车"。不建重系统、不自动排程、不做封面 A/B。

## 零、前置依赖（先确认再开工）

| 子块 | 依赖 | 状态 |
|---|---|---|
| 2A SEO 词库 | 运营「关键词四层」矩阵（主词/长尾/选题词/场景词） | ⛔ **待运营提供**，机制可先建 |
| 2B 分类配图 | 素材库已分类图片（139 张，primary_category 已落库） | ✅ 可执行 |
| 2C 差异化守卫 | 运营硬规则数值已确认：单号≤2/日、同素材全矩阵≤3 号/日 | ✅ 可执行 |

> **开批建议**：2B、2C 先行（无运营阻塞），2A 待运营交词库后补跑。若必须一次落地，2A 用「种子词库 + 管理接口」交付，运营在台账期逐词校准，不阻塞机制。

## 一、背景与 Why

已核实的事实：

- **配图现状**：`xhs_cards._photo_sources`（L120-127）全量扫描 `static/assets/thumbnails` + `static/assets/library/image`，`render_carousel` 按 `index % len(photos)` **循环取图，与选题主题无关**。商务发"清关攻略"，配图可能抽到配送车。
- **分类素材已有真源**：`asset_taxonomy.py` 的 `CATEGORY_KEYWORDS`（分类→关键词）、`NODE_CATEGORY_RULES`（中文物流节点→允许分类）、`db.list_assets(file_type="image", category=...)` 已可按分类取图，但图文链路完全没用上。
- **矩阵差异化无任何守卫**：同一篇文案/同一批图可被同账号或矩阵多号同日重复发布；运营硬规则（单号≤2/日、同素材全矩阵≤3 号/日）靠人记。
- **SEO 无词库**：`hotspot_lexicon` 是热点事件词表，与「搜索 SEO 埋词」语义不同（对抗版击倒 2），不能复用；小红书标题/正文埋主词全靠模型自由发挥。

本批真实缺口：① 词库与 `seo_meta` 缺失；② 配图不跟分类；③ 无差异化守卫。

## 二、铁律（不做的事）

1. **不复用 `hotspot_lexicon` 当 SEO 词库**——新建独立 `xhs_seo_lexicon`（或配置表），禁止 `from hotspot_lexicon import ...`。
2. **不改 `asset_taxonomy` 本体**（`NODE_CATEGORY_RULES` / `CATEGORY_KEYWORDS` / `CATEGORIES` 是视频链路共享真源，改动须单独评审）。只在其上**新增读取**逻辑。
3. **差异化守卫只拦不排程**——不做自动错峰调度、不做自动改文案，拦截时返回 409 + 人话原因，由运营改。
4. **不做封面 A/B**、不自动扒小红书后台数据（同第 0/1 批红线）。
5. **不新增诊断双表**；`seo_meta` 只落 queue 一列，素材使用记录只随 `attachments` 携带 `asset_id`。
6. 不触碰第 1 批门禁语义（truth_guard 仍为软警告 + 发布硬拦）。

## 三、改动清单（三块，建议按 B→C→A 顺序）

### 改动 A：SEO 词库 + `seo_meta` 注入（依赖运营矩阵，机制先行）

**A1 词库表** `database.py`：

```sql
CREATE TABLE IF NOT EXISTS xhs_seo_lexicon (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,          -- 主词/长尾词（如「南非海外仓」/「南非清关要多久」）
    kind TEXT NOT NULL DEFAULT 'longtail', -- main 主词 / longtail 长尾 / scene 场景词
    topic_hint TEXT DEFAULT '',            -- 关联选题关键词（生成时按 topic 匹配）
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);
```

种子词：从对抗版「标题公式 10 条」与物流场景词各挑 10-15 条入库（`南非清关`/`南非海外仓`/`德班港`/`南非快递时效`/`清关费用` 等）；运营后续经管理接口/DB 校准。**`_ensure_column` 模式照旧（CREATE 表用 `IF NOT EXISTS`）**。

**A2 生成注入**：

- `GeneratedContent` 加 `seo_meta: dict = {}`（主词/长尾/埋点位置，向后兼容）。
- `ai_engine._generate_xhs_with_gate`：生成前按 `topic` 查 `xhs_seo_lexicon`（命中 `topic_hint` 或 `keyword in topic`），取 ≤3 个主词/长尾注入 prompt，要求模型返回 `seo_meta` 字段：`{"main": "主词", "longtail": ["长尾1","长尾2"], "positions": ["title","body","hashtag"]}`。
- 模型未返回 `seo_meta` 时兜底：把命中词写入 `seo_meta.main`（不阻断，可编辑）。

**A3 落库**：queue 增列 `seo_meta TEXT DEFAULT '{}'`；`add_to_queue` 扩参；`/api/generate` 或入队时把 `content.seo_meta` 写入。审批页可见/可改（若前端改动超范围，先只落库+API 返回，UI 后续补）。

**明确不做**：`hotspot_lexicon` 复用；词权重自动调参；埋点位置自动校验（仅提示）。

### 改动 B：分类配图（可执行）

**B1 新模块 `xhs_photo_match.py`**（纯函数，复用 `asset_taxonomy` 读取层）：

```python
def topic_categories(topic: str, category: str) -> list[str]:
    """选题 → 候选素材分类（有序）。规则：NODE_CATEGORY_RULES 精确节点命中优先，
    其次 CATEGORY_KEYWORDS 命中，其次请求分类，兜底全部类别。"""
    # 返回 asset_taxonomy.CATEGORY_PRIORITY 顺序去重后的候选列表

def pick_photos(db, static_dir, topic: str, category: str, count: int) -> list[dict]:
    """按分类取图：list_assets(file_type='image', category=c) 逐类取，文件存在校验；
    取不足 count 时按现有全量扫描兜底。返回 [{'path': 相对static路径, 'asset_id': id}]。"""
```

关键规则（确定性，禁 LLM）：
- `NODE_CATEGORY_RULES` 命中（如 topic 含「清关」→ `{"customs"}`）优先；`CATEGORY_KEYWORDS` 子串命中次之；请求 `category` 兜底。
- **文件必须存在**（用 `static_dir / filepath` 校验，过滤脏记录）；顺序按 `CATEGORY_PRIORITY`。
- 兜底逻辑与现网 `_photo_sources` 一致（全量扫描，避免"没图可配"）。

**B2 接入渲染**：

- `xhs_cards.render_carousel` 增加可选参数 `photo_pool: list[dict] | None = None`（`[{'path','asset_id'}]`）；为空时走现网 `_photo_sources` 行为（向后兼容）。
- 渲染时给每个 attachment 补 `asset_id`：`attachments[i]["asset_id"] = photo_pool[i % len(photo_pool)]["asset_id"]`（轮转复用现逻辑，只是带上来源 id）。
- `/api/generate` 与 `/api/xhs/render`：xhs 分支调 `xhs_photo_match.pick_photos(db, STATIC_DIR, topic, category, count=len(pages))` 传入 render。
- **不做**：素材去重换新（同一卡片内不重复即可，跨卡允许轮转）；缩略图 vs 原图选择逻辑改动。

### 改动 C：差异化守卫（可执行，零表结构新增）

**C1 新模块 `xhs_diff_guard.py`**（纯函数，注入 `db`）：

```python
def content_fingerprint(title: str, body: str) -> str:
    """canonical 归一化（去空白/全半角/emoji/#）后 sha256。同文案 → 同指纹。"""

def account_daily_count(db, account_id: int) -> int:
    """publish_log JOIN queue：今日 platform='xiaohongshu' 且 status='published'
    且 queue.target_account_id=account_id 的条数。"""

def asset_matrix_count(db, asset_ids: list[int]) -> dict[int, int]:
    """今日已发布（published）条目的 queue.attachments 里各 asset_id 出现账号数
    （同账号多篇只计 1 个账号）。"""

def check(item: dict, db, account_id: int | None) -> tuple[bool, str]:
    """返回 (允许, 原因)。任一规则触发即拦：
    1. account_daily_count >= 2           → 单号今日已达上限 2
    2. 今日已发布同指纹（含本号）          → 同文案今日已在矩阵发布，请差异化
    3. 本次所用 asset_ids 中有任意 asset 的 asset_matrix_count >= 3 → 该素材今日已达 3 号上限
    """
```

规则数值从常量读（`XHS_ACCOUNT_DAILY_MAX=2`、`XHS_ASSET_MATRIX_MAX=3`），运营改数值只动常量，不碰逻辑。

**C2 接入发布**：`app.py` `publish_item`（L658 附近，account 解析后、`dispatch` 前）与 `publish_batch`：xhs 条目先 `xhs_diff_guard.check`，拒绝 → `409` + reason（中文人话），不调 adapter，不消耗重试次数（status 保持 queued/draft，附 `error_msg`）。

**C3 同素材账号去重口径**：`asset_matrix_count` 用「不同账号数」而非「次数」——同账号发两篇用同一素材不算超上限（对齐"全矩阵≤3 号"语义）。`asset_id` 来源于 attachments（B2 后已携带）；旧条目无 asset_id 的跳过（不误拦历史数据）。

**明确不做**：自动排程/错峰；跨天累计；抖音侧接入（仅小红书，抖音复用需另立项）。

### 改动 D：测试

- `tests/test_xhs_photo_match.py`：`topic_categories`（「清关」→ customs 优先；「配送」→ delivery/warehouse/staff/facility；无命中→兜底全量）；`pick_photos` 取不足 count 走全量兜底、脏 filepath 过滤。
- `tests/test_xhs_diff_guard.py`：指纹归一化（空白/全半角差异同指纹）；`account_daily_count` 用 tmp_db 造今日 published 记录验证；`asset_matrix_count` 同账号两篇计 1 号；`check` 三规则各触发一次 + 全过放行。
- 回归：`test_xhs_cards`（render 兼容默认参数）、`test_xhs_quality_gate`、发布可观测性测试。
- `seo_meta` 落库测试：`add_to_queue` 带 seo_meta → 读出一致。

跑：定向 4 个测试文件，再全量 `python3 -m pytest -q`。

## 四、验收清单

1. pytest：新增全绿，`test_xhs_cards`/`test_xhs_quality_gate`/发布可观测性无回归，全量无新增破坏。
2. **重启 app**。
3. **配图**：`/api/xhs/render` 传 topic=「清关」→ attachments 的 `asset_id` 主要来自 customs 分类素材；topic=「仓储」→ warehouse；`asset_id` 齐全且指向真实文件。
4. **差异化**：同一账号今日已发 2 篇 → 第 3 篇发布返回 409「单号今日已达上限」；同文案今日已发 → 409「同文案已在矩阵发布」；同素材被 3 个账号用过 → 第 4 号 409「素材已达 3 号上限」；未触规则 → 正常放行。
5. **SEO**：`/api/generate` 小红书返回的 `seo_meta` 有主词/长尾；入队后 `queue.seo_meta` 有值；词库表有种子词（机制先行，运营词矩阵到位后补跑校准）。
6. 守卫拦截**不计入重试**：被拦条目 status 不进入 retry 循环。

## 五、回滚

按子块独立回滚：`git revert` 对应提交。2B 回滚后 `render_carousel` 恢复全量循环取图（attachments 无 asset_id，2C 自动退化为"无素材维度拦截"，只留指纹+单号规则）；2C 回滚后发布恢复无守卫；2A 回滚后 `seo_meta` 字段忽略、词库表保留无害。三块互不牵连。

## 六、备注

- 2B 的 `asset_id` 是 2C 素材维度守卫的**数据前提**——务必 2B 先行并验收，2C 才有据可依。
- `content_fingerprint` 归一化要覆盖全半角与换行（`unicodedata.normalize('NFKC')` + 去空白），否则"看起来一样"的文案指纹不同，守卫漏拦。
- `seo_meta` 是运营可编辑数据，不是门禁输入——不得拿 seo_meta 字段做过 truth_guard 或门禁判定。
- 若 `NODE_CATEGORY_RULES` 覆盖不到的新选题分类，运营在台账期提出 → 单独立项评审 `asset_taxonomy`，不走本批指令绕过评审。
