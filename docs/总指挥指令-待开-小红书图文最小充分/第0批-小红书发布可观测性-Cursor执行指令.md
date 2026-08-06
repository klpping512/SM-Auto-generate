# 小红书发布可观测性：结构化失败分类 + 缺失附件显式化 —— Cursor 执行指令（第 0 批）

> 日期：2026-08-06
> 状态：**已执行**（2026-08-06 执行完毕，待总指挥验收）
> 设计真源：《小红书图文种草链路-对抗优化版》第五节 5.2-⑥⑦ / 第六节 第 0 批 + `README.md` 第 0 批范围草稿。
> 拍板：总指挥对抗审阅确认——发布链已走 adapter/RPA，huimei 不是小红书主路径；`publish_readiness` 已用 cookie 判就绪、与 huimei 解耦；`adapters/xiaohongshu.py` 已返回显式自由文本 reason。**本批真实缺口只有三个：①失败原因无结构化分类；②磁盘缺失附件被静默丢弃导致"无图"误报；③publish_log 无失败现场字段。**
> 目标：小红书发布失败**看得见、说得清、能聚合**；不修 huimei、不动 RPA 发布流程本身。

## 一、背景与 Why

发布链现状（已核实）：

- `/api/publish` → `publisher.dispatch` → `adapters.get_adapter("xiaohongshu")` = `XiaohongshuAdapter`（Playwright RPA），`PublishResult(success, platform, error, output)`。
- `adapters/xiaohongshu.py` 已区分错误文案：缺账号 / 无图 / 登录失效 / 发布页未就绪（带截图）/ 找不到发布按钮（带截图）。
- `publish_readiness.readiness("xiaohongshu", credentials)` = `parse_cookies` 判定，**不依赖 huimei**。
- 缺口：
  1. `PublishResult` 无 `category` 字段——`error` 是自由文本，queue 状态与 `publish_log` 无法按「未登录/无图/超时/选择器失败」聚合与筛诊。
  2. `publisher._resolve_uploaded_media`（L15-32）对 `uploads` 目录外或**磁盘已不存在**的附件只 `logger.warning` 后丢弃 → `resolved_images` 空 → `images=None` → 适配器误报「小红书必须配图」。**文件缺失被伪装成"没配图"。**
  3. `publish_log` 只有 `error_msg` 文本列，无失败分类、无 debug 截图现场。

## 二、铁律（不做的事）

1. **不修/不装 huimei**，不把 `publish_via_huimei` 接回主路径，**不**以 `which huimei` 作为小红书健康检查信号。
2. **不改 RPA 发布流程本身**：`_click_submit` 选择器、上传等待、登录态判断逻辑一律不动。
3. **不新增诊断双表**（禁止复刻 `hook_curation_diagnostics` + `hook_intake_diagnostics` 模式）——失败现场只扩 `publish_log` 现有表。
4. **不碰** `ai_engine` / `xhs_cards` / 门禁（属第 1 批）、不碰分类配图（属第 2 批）。
5. **不破坏** Facebook/Twitter/Reddit/Douyin/Huimei 各适配器现有返回——`PublishResult` 新增 `category` 带默认值，向后兼容。

## 三、改动清单

### 改动 A：`adapters/base.py` — PublishResult 增加结构化 `category`

在 `@dataclass` 增加字段（默认 None，向后兼容）：

```python
@dataclass
class PublishResult:
    success: bool
    platform: str
    error: str | None = None
    output: str | None = None
    category: str | None = None   # 结构化失败分类：login_expired/no_images/timeout/selector_failed/page_not_ready/attachment_missing/unknown
```

`to_dict()` 里 `category` 非空时一并带出。

### 改动 B：`adapters/xiaohongshu.py` — 各失败返回补 `category`

对每个 `PublishResult(success=False, ...)` 返回补 `category=`：

| 场景 | 现有 error | category |
|---|---|---|
| `if not account` | 缺少账号（无 cookie 可用） | `no_account` |
| `if not images` | 小红书必须配图 | `no_images` |
| `check_login` 失败 / 发布页重定向到 login | cookie/登录失效… | `login_expired` |
| 未找到上传入口（带截图） | 发布页未就绪… | `page_not_ready` |
| `_click_submit` False（带截图） | 未找到可点击的「发布」按钮… | `selector_failed` |
| 外层 `except Exception` | 原样 str(e) | 超时→`timeout`，其他→`unknown`（可判 `asyncio.TimeoutError`/`TimeoutError`） |

### 改动 C：`publisher.py` — 缺失附件显式化，消除"无图"误报

`_resolve_uploaded_media` 保持「忽略 uploads 目录外」的既有安全语义，但**把"文件已不存在"从静默升级为可见**：

- `_resolve_uploaded_media` 改为返回 `(resolved: list[str], missing: list[str])`（`missing` = 配置了但磁盘上找不到的原始路径）。若不想动签名，可新增内部 `_split_missing` 辅助，dispatch 内调用并消费。
- `dispatch()` 中：
  - 若 `images` 参数非空但 `missing` 非空 → 直接返回失败 `{"success": False, "category": "attachment_missing", "error": f"附件缺失: {missing}"}`，**不调用 adapter**。
  - 若 `images` 参数本就为空/None → 照旧让 adapter 返回 `no_images`（保持"没配图"语义）。

> 语义区分：`attachment_missing` = 配了图但文件没了（运营要补文件）；`no_images` = 根本没配图（运营要传图）。两个不能混。

### 改动 D：`database.py` + 发布路由 — publish_log 落失败分类与现场

- `_ensure_column` 给 `publish_log` 增列：`failure_category TEXT`、`debug_screenshot TEXT`（轻量，单表扩展）。
- `db.add_publish_log(...)` 扩展参数 `failure_category=None, detail=None`，把 adapter 返回的 `category` 和截图路径写库。
- `/api/publish`（app.py:597-629）与 `/api/publish/batch`（app.py:632+）失败分支：`result.get("category")`、`error`（截图路径在 error 文本里的 `/static/debug/*.png` 可原样入库，不额外解析）传入 `add_publish_log`；`update_queue_status(..., f"重试中 (n/3): {category}: {error}")` 带上分类前缀。

### 改动 E：健康检查（验证为主，改动最小）

- 复核 `publish_readiness.readiness("xiaohongshu", ...)` 走 cookie 解析、不触碰 huimei（现状已满足）。
- 若执行中确认存在任何以 huimei 二进制/命令作为小红书就绪信号的代码路径，移除之；否则**不改代码**，只在测试里锁一条断言。

### 改动 F：测试

- `tests/test_xiaohongshu_publish_observability.py`（新）：
  - `PublishResult` 默认 `category is None`（向后兼容，旧适配器不受影响）。
  - `_resolve_uploaded_media` 缺失文件 → `missing` 非空；`dispatch` 对「有 images 参数但文件缺失」返回 `category=="attachment_missing"` 且不调用 adapter。
  - `dispatch` 对「images 为空」返回路径仍到 adapter 层（`no_images` 语义保留）。
- 既有适配器/发布测试全量回归（重点 `test_media_api.py`、`test_publish*`）。

## 四、验收清单

1. pytest：新增用例全绿；既有发布/适配器测试无回归；全量 `python3 -m pytest -q` 无新增破坏（基线失败项保持基线）。
2. **重启 app**（`database.py` 增列、`publisher.py`、`adapters/base.py` 是服务端改动，必须重启）。
3. 人为制造「磁盘缺失附件」：给队列一条 images 指向已删文件的 xhs 条目 → 发布 → queue status 应显示 `attachment_missing` + 缺失路径，**不再**误报"必须配图"。
4. 人为制造「未登录」：无有效 cookie 账号 → 发布 → status/日志含 `login_expired` + 可读文案。
5. `publish_log` 新列有值：失败的记录带 `failure_category`；带截图场景 `debug_screenshot` 含 `/static/debug/*.png`。
6. 小红书账号就绪度健康检查：文案/逻辑中无「需安装 huimei」作为小红书阻塞项。

## 五、回滚

`git revert` 本次改动（`adapters/base.py` / `adapters/xiaohongshu.py` / `publisher.py` / `database.py` / 发布路由 / 新增测试）。回滚后 `PublishResult` 恢复无 category、缺失附件回到静默丢弃——纯可观测性回退，无数据/发布语义副作用（`publish_log` 增列回滚即忽略，SQLite 无强制约束问题）。

## 六、备注

- 第 1 批（渲染前/后门禁 + 标题三层）另开指令，本批不触碰。
- `debug_screenshot` 直接取 adapter error 文本中的截图路径即可，**不做**截图入库/压缩（保持最小）。
- 若执行中发现 `publish_error`/审批流对 `category` 有更优落点，可在验收口径内微调，但**不要**扩大为新的失败处理系统。
