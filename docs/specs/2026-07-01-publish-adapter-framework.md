# 自动发布适配器框架（第一期·5 平台）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把发布路径从「huimei CLI 单一实现」改造成「可插拔适配器框架」，按方案文档 §二 为 5 个目标平台各建适配器——Facebook/X/Reddit 走官方 API，小红书/抖音走 Playwright RPA——全部含单元测试，且**不依赖任何外部凭据即可全绿交付**（API 三家写出真实请求构造、用 mock 单测，暂不真发）。

**Architecture:** 所有发布调用收敛到唯一接缝 `publisher.dispatch(platform, ...)`，它查适配器注册表并分发。两条基类：`ApiAdapter`（httpx 请求，凭据从 `accounts.credentials` 读）派生 Facebook/Twitter/Reddit；`RpaAdapter`（Playwright + cookie）派生 小红书/抖音。第一期只做这 5 个平台，注册表仅含这 5 个适配器，不再使用 huimei。频控（每日上限/最小间隔/抖动）在定时调度侧生效，登录/Token 失效置账号 `expired` 并走已有三渠道通知。

**Tech Stack:** Python 3.12 · FastAPI · SQLite · APScheduler · httpx(已有) · Playwright(Chromium) · pytest + pytest-asyncio

---

## 范围与诚实边界（务必先读）

- **第一期交付**：适配器框架 + 5 个适配器 + 全量单测，`pytest` 全绿。**不碰任何外部账号、不绑卡、不过审。**
- **API 三家（FB/X/Reddit）**：写出**真实且正确的 HTTP 请求构造**（端点/头/体/凭据字段都对），用 monkeypatch mock `_post_json` 做单测。真发被**刻意推迟**到你提供开发者凭据之后（见「后续」）。不会假装能真发。
- **RPA 两家（小红书/抖音）**：写出真实的 cookie 登录 + 页面驱动代码，对**纯逻辑**（cookie 存取、必须配图/视频的守卫、未登录短路、文案拼装）做单测。真发需你**扫码 + 提供图片/视频素材**，放在可选的人工验证附录（Task 14），不纳入全绿标准。
- **外部现实（2026-07 核实，决定"真发"何时可行）**：
  - X：2026-02 起**无免费额度**，按量付费 ~$0.015/条、带链接 $0.20/条，需绑卡。
  - Reddit：所有 App 需**预审 2-4 周**，公司主体=商用可能被拒或要付费协议；发帖需完整用户 OAuth。
  - Facebook：开发模式可测（帖仅管理员可见），真·公开发需企业验证(2-5天)+应用审核(~5天)。
- **huimei 彻底不用**：第一期只做这 5 个平台，注册表只注册这 5 个适配器。`publisher.publish_via_huimei` 作为死代码保留（不删历史），仅不再被调用。其余平台（bilibili/知乎/微博等）本期不支持，dispatch 会返回明确「无适配器」错误。
- **队列暂无媒体字段**：`queue` 表无图片/视频列，故经调度/接口发到小红书/抖音会命中「必须配图/视频」守卫并返回明确错误；真发素材经 Task 14 脚本直接传入。媒体入库属后续。
- **频控口径 UTC**：今日数/间隔用 SQLite `date('now')`/`julianday('now')` 库内计算，与 `publish_log.published_at`（UTC）一致。

## File Structure

**新建：**
- `adapters/__init__.py` — 注册表：`ADAPTERS` + `get_adapter(platform)`
- `adapters/base.py` — `PublishResult` + `PublishAdapter` 抽象基类
- `adapters/api_base.py` — `ApiAdapter`：httpx `_post_json` + 凭据解析
- `adapters/facebook.py` — `FacebookAdapter`（Graph API `/{page-id}/feed`）
- `adapters/twitter.py` — `TwitterAdapter`（X API v2 `/2/tweets`，OAuth2 用户 token）
- `adapters/reddit.py` — `RedditAdapter`（OAuth2 取 token + `/api/submit`）
- `adapters/rpa_base.py` — `RpaAdapter`：Playwright 浏览器/cookie + 登录骨架
- `adapters/xiaohongshu.py` — `XiaohongshuAdapter`（图文，必须配图）
- `adapters/douyin.py` — `DouyinAdapter`（必须视频/图文）
- `ratelimit.py` — 频控配置 + `can_publish_now` / `next_run_time`
- `tests/conftest.py` + 各任务测试文件
- `pytest.ini`

**修改：**
- `database.py` — 新增 `update_account_status`、`update_account_credentials`、`count_published_today`、`minutes_since_last_publish`
- `publisher.py` — 新增 `dispatch(...)`；`publish_via_huimei` 保留供 huimei 适配器复用
- `scheduler.py` — `check_scheduled_publish` 改调 `publisher.dispatch` + 频控顺延 + 失效置 expired
- `app.py` — 3 处发布调用改 `dispatch`；`/api/publish/status` 报告适配器注册表
- `requirements.txt` — 新增 `playwright`、`pytest`、`pytest-asyncio`（httpx 已在）

**接缝证据（当前直接调用 `publish_via_huimei` 的 3 处，全部收敛到 dispatch）：** `scheduler.py:93` · `app.py:362`（单发）· `app.py:397`（批量）

---

## Task 0: 开发依赖与测试脚手架

**Files:** Modify `requirements.txt`；Create `pytest.ini`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: 追加依赖**

在 `requirements.txt` 末尾追加（httpx 已存在，勿重复）：
```
playwright==1.48.0
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 2: 安装（清华镜像；Playwright 浏览器走 Clash 代理）**

Run:
```bash
cd ~/Desktop/distribution-manager
pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple playwright==1.48.0 pytest==8.3.3 pytest-asyncio==0.24.0
HTTPS_PROXY=http://127.0.0.1:7890 python3 -m playwright install chromium
```
Expected: 安装成功；chromium 下载完成。

- [ ] **Step 3: pytest.ini**

`pytest.ini`：
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: 测试夹具（临时 DB 隔离真实库）**

`tests/__init__.py`：空文件。

`tests/conftest.py`：
```python
from pathlib import Path
import pytest
import database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """每个测试用独立临时 SQLite，避免污染 data/logiflow.db。"""
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path / "test.db"))
    db.init_db()
    return db
```

- [ ] **Step 5: 验证脚手架**

Run: `cd ~/Desktop/distribution-manager && python3 -m pytest -q`
Expected: `no tests ran`（0 collected，无错误）。

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini tests/__init__.py tests/conftest.py
git commit -m "chore: add pytest + playwright deps and test scaffold"
```

---

## Task 1: 数据库辅助函数（账号状态/凭据 + 频控统计）

**Files:** Modify `database.py`；Test `tests/test_db_helpers.py`

- [ ] **Step 1: 写失败测试**

`tests/test_db_helpers.py`：
```python
def test_update_account_status(tmp_db):
    tmp_db.create_account("facebook", "主页", "fb_001")
    acc = tmp_db.get_accounts("facebook")[0]
    tmp_db.update_account_status(acc["id"], "expired")
    assert tmp_db.get_accounts("facebook")[0]["status"] == "expired"


def test_update_account_credentials(tmp_db):
    tmp_db.create_account("facebook", "主页", "fb_001")
    tmp_db.update_account_credentials("fb_001", '{"page_id": "123"}')
    assert tmp_db.get_accounts("facebook")[0]["credentials"] == '{"page_id": "123"}'


def test_count_published_today_and_interval(tmp_db):
    assert tmp_db.count_published_today("reddit") == 0
    assert tmp_db.minutes_since_last_publish("reddit") is None
    tmp_db.add_publish_log(1, "reddit", "标题", "published")
    assert tmp_db.count_published_today("reddit") == 1
    mins = tmp_db.minutes_since_last_publish("reddit")
    assert mins is not None and mins < 5
    tmp_db.add_publish_log(2, "reddit", "标题2", "failed")  # 失败不计
    assert tmp_db.count_published_today("reddit") == 1
    assert tmp_db.count_published_today("twitter") == 0  # 平台隔离
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_db_helpers.py -q`
Expected: FAIL，`AttributeError: ... 'update_account_status'`。

- [ ] **Step 3: 实现四个函数**

`database.py` Accounts 区块（`delete_account` 之后）追加：
```python
def update_account_status(account_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET status=? WHERE id=?", (status, account_id))


def update_account_credentials(account_id: str, credentials: str):
    """按业务 account_id（非自增主键）更新凭据 JSON，并刷新 last_sync。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET credentials=?, last_sync=? WHERE account_id=?",
            (credentials, datetime.now().strftime("%Y-%m-%d %H:%M"), account_id),
        )
```

`database.py` Publish Log 区块（`get_publish_logs` 之后）追加：
```python
def count_published_today(platform: str) -> int:
    """今日（UTC 自然日）某平台成功发布条数。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM publish_log "
            "WHERE platform=? AND status='published' AND date(published_at)=date('now')",
            (platform,),
        ).fetchone()[0]


def minutes_since_last_publish(platform: str) -> float | None:
    """距该平台上次成功发布的分钟数；从未发布返回 None。库内 UTC 计算。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(MAX(published_at))) * 1440 AS mins "
            "FROM publish_log WHERE platform=? AND status='published'",
            (platform,),
        ).fetchone()
    return row["mins"] if row and row["mins"] is not None else None
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_db_helpers.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_db_helpers.py
git commit -m "feat(db): account status/credentials updaters + rate-limit queries"
```

---

## Task 2: 适配器接口契约

**Files:** Create `adapters/__init__.py`（先空包）、`adapters/base.py`；Test `tests/test_adapter_base.py`

- [ ] **Step 1: 写失败测试**

`tests/test_adapter_base.py`：
```python
import pytest
from adapters.base import PublishResult, PublishAdapter


def test_publish_result_to_dict_minimal():
    assert PublishResult(success=True, platform="reddit").to_dict() == {
        "success": True, "platform": "reddit"}


def test_publish_result_to_dict_full():
    r = PublishResult(success=False, platform="reddit", error="boom", output="log")
    assert r.to_dict() == {"success": False, "platform": "reddit", "error": "boom", "output": "log"}


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        PublishAdapter()


async def test_default_check_login_true():
    class Dummy(PublishAdapter):
        name = "dummy"
        async def publish(self, **kwargs):
            return PublishResult(success=True, platform="dummy")
    assert await Dummy().check_login() is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_adapter_base.py -q` → Expected: FAIL，`ModuleNotFoundError: No module named 'adapters'`。

- [ ] **Step 3: 建包与实现**

`adapters/__init__.py`：留空（Task 3 填注册表）。

`adapters/base.py`：
```python
"""发布适配器接口契约。所有平台适配器实现同一协议。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PublishResult:
    success: bool
    platform: str
    error: str | None = None
    output: str | None = None

    def to_dict(self) -> dict:
        d = {"success": self.success, "platform": self.platform}
        if self.error is not None:
            d["error"] = self.error
        if self.output is not None:
            d["output"] = self.output
        return d


class PublishAdapter(ABC):
    """一个平台（或一组平台）的发布实现。"""

    name: str = ""

    @abstractmethod
    async def publish(
        self, *, platform: str, title: str, content: str,
        tags: list[str] | None = None, images: list[str] | None = None,
        video: str | None = None, account: dict | None = None,
    ) -> PublishResult:
        ...

    async def check_login(self, account: dict | None = None) -> bool:
        """默认恒为已登录（无状态/Token 型）。RPA 子类覆写。"""
        return True
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_adapter_base.py -q` → Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/__init__.py adapters/base.py tests/test_adapter_base.py
git commit -m "feat(adapters): PublishAdapter contract + PublishResult"
```

---

## Task 3: 适配器注册表 + dispatch 接缝（不含 huimei）

**Files:** Modify `adapters/__init__.py`、`publisher.py`、`scheduler.py`、`app.py`；Test `tests/test_registry_dispatch.py`

> 注册表本任务先建成空表；5 个平台适配器在 Task 13 统一注册。huimei 不再注册。

- [ ] **Step 1: 写失败测试**

`tests/test_registry_dispatch.py`：
```python
import publisher
import adapters
from adapters.base import PublishAdapter, PublishResult


class _Dummy(PublishAdapter):
    name = "dummy"
    async def publish(self, *, platform, title, content,
                      tags=None, images=None, video=None, account=None):
        return PublishResult(success=True, platform=platform, output=f"{title}:{content}")


def test_get_adapter_unknown_returns_none():
    assert adapters.get_adapter("bilibili") is None  # 本期不支持的平台


async def test_dispatch_unknown_platform():
    result = await publisher.dispatch(platform="bilibili", title="T", content="B")
    assert result["success"] is False and "bilibili" in result["error"]


async def test_dispatch_routes_to_registered_adapter(monkeypatch):
    monkeypatch.setitem(adapters.ADAPTERS, "dummy", _Dummy())
    result = await publisher.dispatch(platform="dummy", title="T", content="B")
    assert result == {"success": True, "platform": "dummy", "output": "T:B"}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_registry_dispatch.py -q` → Expected: FAIL，`AttributeError: ... 'dispatch'`。

- [ ] **Step 3: 注册表（空表，Task 13 填 5 个适配器）**

`adapters/__init__.py`（覆盖空文件）：
```python
"""适配器注册表：platform -> adapter 实例。新增平台 = 加文件 + 在 _register_all 注册。"""
from adapters.base import PublishAdapter, PublishResult

ADAPTERS: dict[str, PublishAdapter] = {}


def _register_all():
    # Task 13 在此注册 5 个平台适配器：facebook/twitter/reddit/xiaohongshu/douyin
    pass


_register_all()


def get_adapter(platform: str) -> PublishAdapter | None:
    return ADAPTERS.get(platform)
```

- [ ] **Step 4: dispatch 接缝**

`publisher.py` 末尾追加：
```python
async def dispatch(
    platform: str, title: str, content: str,
    tags: list[str] = None, images: list[str] = None,
    video: str = None, account: dict = None,
) -> dict:
    """统一发布入口：查适配器注册表并分发。返回与旧接口兼容的 dict。"""
    from adapters import get_adapter  # 延迟导入避免循环
    adapter = get_adapter(platform)
    if adapter is None:
        return {"success": False, "platform": platform, "error": f"无适配器: '{platform}'"}
    result = await adapter.publish(
        platform=platform, title=title, content=content,
        tags=tags, images=images, video=video, account=account,
    )
    return result.to_dict()
```

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_registry_dispatch.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 6: 迁移 3 处调用点到 dispatch**

`scheduler.py:93`：将 `result = await publisher.publish_via_huimei(` 改为 `result = await publisher.dispatch(`（参数 `platform/title/content/tags` 不变）。

`app.py:362`（`publish_item`）：将 `await publisher.publish_via_huimei(` 改为 `await publisher.dispatch(`。

`app.py:397`（`publish_batch`）：同样将 `await publisher.publish_via_huimei(` 改为 `await publisher.dispatch(`。

> 注意：此后到 Task 13 之间，dispatch 因注册表为空会对所有平台返回「无适配器」；这是预期的临时状态，Task 13 注册 5 个适配器后恢复。各适配器在 Task 7-12 直接调 `adapter.publish()` 单测，不受影响。

- [ ] **Step 7: 冒烟测试**

Run: `cd ~/Desktop/distribution-manager && python3 -c "import app, scheduler, adapters; print('ok', len(adapters.ADAPTERS))"`
Expected: 打印 `ok 0`，无 ImportError / 循环导入。

- [ ] **Step 8: Commit**

```bash
git add adapters/__init__.py publisher.py scheduler.py app.py tests/test_registry_dispatch.py
git commit -m "feat(publish): adapter registry + dispatch seam, migrate call sites"
```

---

## Task 4: 频控逻辑（每日上限 / 最小间隔 / 随机抖动）

**Files:** Create `ratelimit.py`；Test `tests/test_ratelimit.py`

- [ ] **Step 1: 写失败测试**

`tests/test_ratelimit.py`：
```python
from datetime import datetime
import ratelimit


def test_defaults():
    assert ratelimit.DAILY_LIMIT == 10
    assert ratelimit.MIN_INTERVAL_MIN == 30
    assert ratelimit.JITTER_MIN == 5


def test_ok_when_empty(tmp_db):
    ok, reason = ratelimit.can_publish_now("reddit")
    assert ok is True and reason == "ok"


def test_blocked_by_daily_limit(tmp_db, monkeypatch):
    monkeypatch.setattr(ratelimit, "DAILY_LIMIT", 2)
    for i in range(2):
        tmp_db.add_publish_log(i, "reddit", "t", "published")
    ok, reason = ratelimit.can_publish_now("reddit")
    assert ok is False and "上限" in reason


def test_blocked_by_min_interval(tmp_db):
    tmp_db.add_publish_log(1, "reddit", "t", "published")
    ok, reason = ratelimit.can_publish_now("reddit")
    assert ok is False and "分钟" in reason


def test_next_run_time_interval_and_jitter():
    now = datetime(2026, 7, 1, 12, 0)
    got = ratelimit.next_run_time(now, jitter_fn=lambda lo, hi: 3)
    assert got == "2026-07-01 12:33"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_ratelimit.py -q` → Expected: FAIL，`ModuleNotFoundError: No module named 'ratelimit'`。

- [ ] **Step 3: 实现频控**

`ratelimit.py`：
```python
"""发布频控：每日上限 / 最小间隔 / 随机抖动。第一期用常量 + 环境变量覆盖。"""
import os
import random
from datetime import datetime, timedelta

import database as db

DAILY_LIMIT = int(os.environ.get("PUBLISH_DAILY_LIMIT", "10"))
MIN_INTERVAL_MIN = int(os.environ.get("PUBLISH_MIN_INTERVAL_MIN", "30"))
JITTER_MIN = int(os.environ.get("PUBLISH_JITTER_MIN", "5"))


def can_publish_now(platform: str) -> tuple[bool, str]:
    count = db.count_published_today(platform)
    if count >= DAILY_LIMIT:
        return False, f"今日已达上限 {count}/{DAILY_LIMIT}"
    mins = db.minutes_since_last_publish(platform)
    if mins is not None and mins < MIN_INTERVAL_MIN:
        return False, f"距上次发布仅 {mins:.0f} 分钟（需 ≥{MIN_INTERVAL_MIN}）"
    return True, "ok"


def next_run_time(now: datetime, jitter_fn=random.randint) -> str:
    """顺延时间 = now + 最小间隔 + [-抖动,+抖动]，格式对齐 queue.scheduled_at。"""
    delay = MIN_INTERVAL_MIN + jitter_fn(-JITTER_MIN, JITTER_MIN)
    return (now + timedelta(minutes=delay)).strftime("%Y-%m-%d %H:%M")
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_ratelimit.py -q` → Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add ratelimit.py tests/test_ratelimit.py
git commit -m "feat(ratelimit): daily cap + min interval + jitter"
```

---

## Task 5: 频控接入定时调度 + 失效账号置 expired

**Files:** Modify `scheduler.py`；Test `tests/test_scheduler_ratelimit.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduler_ratelimit.py`：
```python
import scheduler
import ratelimit
import publisher


async def _noop(*a, **k):
    return None


async def test_defers_when_rate_limited(tmp_db, monkeypatch):
    tmp_db.add_to_queue("标题", "正文", "reddit", scheduled_at="2020-01-01 00:00", status="queued")
    item_id = tmp_db.get_queue("queued", "reddit")[0]["id"]
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (False, "今日已达上限 10/10"))
    monkeypatch.setattr(ratelimit, "next_run_time", lambda now, **k: "2099-01-01 00:00")

    async def boom(**kwargs):
        raise AssertionError("频控命中时不应发布")
    monkeypatch.setattr(publisher, "dispatch", boom)

    await scheduler.check_scheduled_publish()
    row = tmp_db.get_queue_item_by_id(item_id)
    assert row["status"] == "queued"
    assert row["scheduled_at"] == "2099-01-01 00:00"
    assert "顺延" in (row["error_msg"] or "")


async def test_marks_account_expired_on_login_error(tmp_db, monkeypatch):
    tmp_db.create_account("reddit", "主号", "rd_001")
    tmp_db.add_to_queue("标题", "正文", "reddit", scheduled_at="2020-01-01 00:00", status="queued")
    item_id = tmp_db.get_queue("queued", "reddit")[0]["id"]
    for _ in range(3):
        tmp_db.increment_retry_count(item_id)
    monkeypatch.setattr(ratelimit, "can_publish_now", lambda p: (True, "ok"))

    async def token_fail(**kwargs):
        return {"success": False, "platform": "reddit", "error": "token 过期，请重新登录"}
    monkeypatch.setattr(publisher, "dispatch", token_fail)
    monkeypatch.setattr(scheduler, "send_alert", _noop)

    await scheduler.check_scheduled_publish()
    assert tmp_db.get_accounts("reddit")[0]["status"] == "expired"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_scheduler_ratelimit.py -q` → Expected: FAIL（当前无频控顺延、不置 expired）。

- [ ] **Step 3: 改造 check_scheduled_publish**

`scheduler.py` 顶部 `import publisher` 下一行加：
```python
import ratelimit
```

循环体内、`result = await publisher.dispatch(...)` **之前**插入频控闸门：
```python
        ok, reason = ratelimit.can_publish_now(platform)
        if not ok:
            next_at = ratelimit.next_run_time(datetime.now())
            db.update_queue_status(item_id, "queued", f"频控顺延: {reason}", scheduled_at=next_at)
            logger.info("频控顺延: id=%d, %s -> %s", item_id, reason, next_at)
            continue
```

最终失败分支里、`await send_alert(...)` **之前**插入：
```python
                _maybe_mark_expired(platform, result.get("error", ""))
```

`_suggest_for_error` 函数下方新增：
```python
def _maybe_mark_expired(platform: str, error: str):
    """登录/cookie/token 类错误：把该平台账号置 expired，供前端提示重新登录。"""
    e = (error or "").lower()
    if "cookie" in e or "登录" in error or "login" in e or "token" in e:
        for acc in db.get_accounts(platform):
            db.update_account_status(acc["id"], "expired")
            logger.warning("账号置为 expired: platform=%s, id=%d", platform, acc["id"])
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_scheduler_ratelimit.py -q` → Expected: PASS（2 passed）。

- [ ] **Step 5: 全量回归**

Run: `python3 -m pytest -q` → Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add scheduler.py tests/test_scheduler_ratelimit.py
git commit -m "feat(scheduler): rate-limit deferral + mark account expired on auth error"
```

---

## Task 6: API 适配器基类（httpx + 凭据解析）

**Files:** Create `adapters/api_base.py`；Test `tests/test_api_base.py`

- [ ] **Step 1: 写失败测试**

`tests/test_api_base.py`：
```python
from adapters.api_base import ApiAdapter


def test_creds_parses_json():
    assert ApiAdapter._creds({"credentials": '{"a": 1}'}) == {"a": 1}


def test_creds_empty_and_bad():
    assert ApiAdapter._creds(None) == {}
    assert ApiAdapter._creds({"credentials": ""}) == {}
    assert ApiAdapter._creds({"credentials": "not-json"}) == {}


def test_require_returns_missing_keys():
    creds = {"page_id": "1"}
    missing = ApiAdapter._missing(creds, ["page_id", "page_access_token"])
    assert missing == ["page_access_token"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_api_base.py -q` → Expected: FAIL，`ModuleNotFoundError: No module named 'adapters.api_base'`。

- [ ] **Step 3: 实现 API 基类**

`adapters/api_base.py`：
```python
"""官方 API 适配器基类：httpx 请求 + 凭据解析。凭据从 accounts.credentials(JSON) 读。"""
import json
import logging

from adapters.base import PublishAdapter

logger = logging.getLogger(__name__)


class ApiAdapter(PublishAdapter):
    name = ""

    @staticmethod
    def _creds(account: dict | None) -> dict:
        if not account:
            return {}
        try:
            return json.loads(account.get("credentials") or "{}")
        except (json.JSONDecodeError, AttributeError):
            return {}

    @staticmethod
    def _missing(creds: dict, required: list[str]) -> list[str]:
        return [k for k in required if not creds.get(k)]

    async def _post_json(self, url, *, headers=None, json=None, data=None) -> tuple[int, dict]:
        """执行 POST，返回 (status_code, body_dict)。单测里被 monkeypatch。"""
        import httpx
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, headers=headers, json=json, data=data)
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text}
            return r.status_code, body
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_api_base.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/api_base.py tests/test_api_base.py
git commit -m "feat(api): ApiAdapter base with httpx post + credential parsing"
```

---

## Task 7: Facebook 适配器（Graph API）

**Files:** Create `adapters/facebook.py`；Test `tests/test_facebook_adapter.py`

> 端点 `POST https://graph.facebook.com/v21.0/{page_id}/feed`，body `message` + `access_token`，成功返回 `{"id": "<post_id>"}`。凭据形状：`{"page_id": "...", "page_access_token": "..."}`。真发需企业验证+审核（推迟）。

- [ ] **Step 1: 写失败测试**

`tests/test_facebook_adapter.py`：
```python
from adapters.facebook import FacebookAdapter
from adapters.base import PublishResult


async def test_success(monkeypatch):
    a = FacebookAdapter()
    async def fake(url, **kw):
        assert "/123/feed" in url
        assert kw["data"]["message"] == "hello"
        assert kw["data"]["access_token"] == "tok"
        return 200, {"id": "123_456"}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"page_id":"123","page_access_token":"tok"}'}
    r = await a.publish(platform="facebook", title="", content="hello", account=acc)
    assert isinstance(r, PublishResult)
    assert r.success and r.output == "123_456"


async def test_missing_credentials():
    a = FacebookAdapter()
    r = await a.publish(platform="facebook", title="", content="hi", account={"credentials": "{}"})
    assert r.success is False and "page_id" in r.error


async def test_api_error(monkeypatch):
    a = FacebookAdapter()
    async def fake(url, **kw):
        return 400, {"error": {"message": "Invalid OAuth token"}}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"page_id":"123","page_access_token":"bad"}'}
    r = await a.publish(platform="facebook", title="", content="hi", account=acc)
    assert r.success is False and "Invalid OAuth token" in r.error
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_facebook_adapter.py -q` → Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

`adapters/facebook.py`：
```python
"""Facebook 主页发文：Graph API /{page_id}/feed。"""
import logging

from adapters.api_base import ApiAdapter
from adapters.base import PublishResult

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"


class FacebookAdapter(ApiAdapter):
    name = "facebook"

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        creds = self._creds(account)
        missing = self._missing(creds, ["page_id", "page_access_token"])
        if missing:
            return PublishResult(success=False, platform=self.name,
                                 error=f"缺少凭据: {', '.join(missing)}")

        message = f"{title}\n{content}" if title else content
        if tags:
            message += " " + " ".join(f"#{t}" for t in tags)

        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{creds['page_id']}/feed"
        status, body = await self._post_json(
            url, data={"message": message, "access_token": creds["page_access_token"]})

        if status == 200 and body.get("id"):
            return PublishResult(success=True, platform=self.name, output=body["id"])
        err = (body.get("error") or {}).get("message") or str(body)
        return PublishResult(success=False, platform=self.name, error=err)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_facebook_adapter.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/facebook.py tests/test_facebook_adapter.py
git commit -m "feat(facebook): Graph API page-feed publish adapter"
```

---

## Task 8: Twitter/X 适配器（API v2）

**Files:** Create `adapters/twitter.py`；Test `tests/test_twitter_adapter.py`

> 端点 `POST https://api.x.com/2/tweets`，头 `Authorization: Bearer <OAuth2 用户 token>`（`tweet.write` scope 才能发），body JSON `{"text": ...}`，成功返回 `{"data": {"id": ...}}`。凭据形状：`{"access_token": "<oauth2 用户 token>"}`。X 已无免费额度、按量付费，真发推迟。

- [ ] **Step 1: 写失败测试**

`tests/test_twitter_adapter.py`：
```python
from adapters.twitter import TwitterAdapter


async def test_success(monkeypatch):
    a = TwitterAdapter()
    async def fake(url, **kw):
        assert url.endswith("/2/tweets")
        assert kw["headers"]["Authorization"] == "Bearer tok"
        assert kw["json"]["text"].startswith("hello")
        return 201, {"data": {"id": "999", "text": "hello"}}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"access_token":"tok"}'}
    r = await a.publish(platform="twitter", title="", content="hello", account=acc)
    assert r.success and r.output == "999"


async def test_missing_token():
    a = TwitterAdapter()
    r = await a.publish(platform="twitter", title="", content="hi", account={"credentials": "{}"})
    assert r.success is False and "access_token" in r.error


async def test_api_error(monkeypatch):
    a = TwitterAdapter()
    async def fake(url, **kw):
        return 403, {"detail": "Unsupported Authentication"}
    monkeypatch.setattr(a, "_post_json", fake)
    acc = {"credentials": '{"access_token":"bad"}'}
    r = await a.publish(platform="twitter", title="", content="hi", account=acc)
    assert r.success is False and "Unsupported Authentication" in r.error
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_twitter_adapter.py -q` → Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

`adapters/twitter.py`：
```python
"""X(Twitter) 发推：API v2 POST /2/tweets，OAuth2 用户 token（tweet.write）。"""
import logging

from adapters.api_base import ApiAdapter
from adapters.base import PublishResult

logger = logging.getLogger(__name__)

TWEETS_URL = "https://api.x.com/2/tweets"
MAX_LEN = 280


class TwitterAdapter(ApiAdapter):
    name = "twitter"

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        creds = self._creds(account)
        missing = self._missing(creds, ["access_token"])
        if missing:
            return PublishResult(success=False, platform=self.name,
                                 error=f"缺少凭据: {', '.join(missing)}")

        text = f"{title} {content}".strip() if title else content
        if tags:
            text += " " + " ".join(f"#{t}" for t in tags)
        text = text[:MAX_LEN]  # X 单条上限

        status, body = await self._post_json(
            TWEETS_URL,
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            json={"text": text},
        )

        if status in (200, 201) and body.get("data", {}).get("id"):
            return PublishResult(success=True, platform=self.name, output=body["data"]["id"])
        err = body.get("detail") or body.get("title") or str(body)
        return PublishResult(success=False, platform=self.name, error=err)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_twitter_adapter.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/twitter.py tests/test_twitter_adapter.py
git commit -m "feat(twitter): X API v2 tweet publish adapter"
```

---

## Task 9: Reddit 适配器（OAuth2 取 token + submit）

**Files:** Create `adapters/reddit.py`；Test `tests/test_reddit_adapter.py`

> 两步：① `POST https://www.reddit.com/api/v1/access_token`（Basic auth=client_id:client_secret，`grant_type=refresh_token`）取 token；② `POST https://oauth.reddit.com/api/submit`（Bearer + User-Agent，`sr/kind=self/title/text`）。凭据形状：`{"client_id","client_secret","refresh_token","user_agent","subreddit"}`。需预审 2-4 周，真发推迟。

- [ ] **Step 1: 写失败测试**

`tests/test_reddit_adapter.py`：
```python
from adapters.reddit import RedditAdapter

CREDS = ('{"client_id":"ci","client_secret":"cs","refresh_token":"rt",'
         '"user_agent":"linux:com.sa.logiflow:v1 (by /u/bot)","subreddit":"test"}')


async def test_success(monkeypatch):
    a = RedditAdapter()
    async def fake_token(self, creds):
        return "tok"
    monkeypatch.setattr(RedditAdapter, "_get_access_token", fake_token)
    async def fake_post(url, **kw):
        assert url.endswith("/api/submit")
        assert kw["headers"]["Authorization"] == "Bearer tok"
        assert kw["data"]["sr"] == "test" and kw["data"]["title"] == "标题"
        return 200, {"json": {"errors": [], "data": {"url": "https://redd.it/x"}}}
    monkeypatch.setattr(a, "_post_json", fake_post)
    r = await a.publish(platform="reddit", title="标题", content="正文",
                        account={"credentials": CREDS})
    assert r.success and "redd.it" in r.output


async def test_missing_credentials():
    a = RedditAdapter()
    r = await a.publish(platform="reddit", title="t", content="b", account={"credentials": "{}"})
    assert r.success is False and "client_id" in r.error


async def test_submit_returns_errors(monkeypatch):
    a = RedditAdapter()
    monkeypatch.setattr(RedditAdapter, "_get_access_token", lambda self, creds: _ret("tok"))
    async def fake_post(url, **kw):
        return 200, {"json": {"errors": [["RATELIMIT", "too fast", "ratelimit"]]}}
    monkeypatch.setattr(a, "_post_json", fake_post)
    r = await a.publish(platform="reddit", title="t", content="b", account={"credentials": CREDS})
    assert r.success is False and "RATELIMIT" in r.error


async def _ret(v):
    return v
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_reddit_adapter.py -q` → Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

`adapters/reddit.py`：
```python
"""Reddit 发帖：OAuth2 refresh_token 取 access_token，再 POST /api/submit（self post）。"""
import logging

from adapters.api_base import ApiAdapter
from adapters.base import PublishResult

logger = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SUBMIT_URL = "https://oauth.reddit.com/api/submit"
REQUIRED = ["client_id", "client_secret", "refresh_token", "user_agent", "subreddit"]


class RedditAdapter(ApiAdapter):
    name = "reddit"

    async def _get_access_token(self, creds: dict) -> str | None:
        import base64
        basic = base64.b64encode(
            f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
        status, body = await self._post_json(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}", "User-Agent": creds["user_agent"]},
            data={"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]},
        )
        if status == 200:
            return body.get("access_token")
        logger.warning("Reddit 取 token 失败: %s %s", status, body)
        return None

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        creds = self._creds(account)
        missing = self._missing(creds, REQUIRED)
        if missing:
            return PublishResult(success=False, platform=self.name,
                                 error=f"缺少凭据: {', '.join(missing)}")

        token = await self._get_access_token(creds)
        if not token:
            return PublishResult(success=False, platform=self.name,
                                 error="token 获取失败，请重新登录/刷新授权")

        status, body = await self._post_json(
            SUBMIT_URL,
            headers={"Authorization": f"Bearer {token}", "User-Agent": creds["user_agent"]},
            data={"sr": creds["subreddit"], "kind": "self",
                  "title": title, "text": content, "api_type": "json"},
        )
        errors = (body.get("json") or {}).get("errors") or []
        if status == 200 and not errors:
            url = ((body.get("json") or {}).get("data") or {}).get("url", "submitted")
            return PublishResult(success=True, platform=self.name, output=url)
        return PublishResult(success=False, platform=self.name, error=str(errors or body))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_reddit_adapter.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/reddit.py tests/test_reddit_adapter.py
git commit -m "feat(reddit): OAuth2 token + self-post submit adapter"
```

---

## Task 10: RPA 适配器基类（Playwright + cookie）

**Files:** Create `adapters/rpa_base.py`；Test `tests/test_rpa_cookies.py`

> Playwright 浏览器驱动无法稳定单测；本任务只对 cookie 序列化/反序列化做 TDD。`playwright` 方法内延迟导入，未装也不影响本任务。

- [ ] **Step 1: 写失败测试**

`tests/test_rpa_cookies.py`：
```python
import json
from adapters.rpa_base import parse_cookies, build_credentials


def test_parse_cookies_empty():
    assert parse_cookies("{}") == []
    assert parse_cookies("") == []
    assert parse_cookies(None) == []


def test_parse_cookies_roundtrip():
    cookies = [{"name": "sid", "value": "abc", "domain": ".xiaohongshu.com"}]
    cred = build_credentials(cookies)
    assert json.loads(cred)["cookies"] == cookies
    assert parse_cookies(cred) == cookies


def test_parse_cookies_bad_json_is_safe():
    assert parse_cookies("not-json") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_rpa_cookies.py -q` → Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 RPA 基类**

`adapters/rpa_base.py`：
```python
"""Playwright RPA 适配器基类：cookie 登录态存取 + 登录骨架。"""
import json
import logging

import database as db
from adapters.base import PublishAdapter

logger = logging.getLogger(__name__)


def parse_cookies(credentials: str | None) -> list[dict]:
    if not credentials:
        return []
    try:
        return json.loads(credentials).get("cookies", []) or []
    except (json.JSONDecodeError, AttributeError):
        return []


def build_credentials(cookies: list[dict]) -> str:
    return json.dumps({"cookies": cookies}, ensure_ascii=False)


class RpaAdapter(PublishAdapter):
    name = ""
    login_url = ""
    headless = True

    def _logged_in_selector(self) -> str:
        raise NotImplementedError

    async def _new_context(self, playwright, account: dict | None):
        browser = await playwright.chromium.launch(headless=self.headless)
        context = await browser.new_context()
        cookies = parse_cookies((account or {}).get("credentials"))
        if cookies:
            await context.add_cookies(cookies)
        return browser, context

    async def check_login(self, account: dict | None = None) -> bool:
        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                try:
                    page = await context.new_page()
                    await page.goto(self.login_url, timeout=30000)
                    return await page.query_selector(self._logged_in_selector()) is not None
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("check_login 异常: platform=%s, err=%s", self.name, e)
            return False

    async def save_login(self, account: dict, context) -> None:
        cookies = await context.cookies()
        db.update_account_credentials(account["account_id"], build_credentials(cookies))
        logger.info("已保存登录 cookie: platform=%s, account=%s", self.name, account["account_id"])
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_rpa_cookies.py -q` → Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/rpa_base.py tests/test_rpa_cookies.py
git commit -m "feat(rpa): Playwright adapter base with cookie load/save + login skeleton"
```

---

## Task 11: 小红书适配器（图文，必须配图）

**Files:** Create `adapters/xiaohongshu.py`；Test `tests/test_xiaohongshu_adapter.py`

> 小红书创作平台强制配图（纯文字发不了），故 `images` 为空直接返回明确错误。选择器集中在类常量便于随改版维护。真发需扫码 + 图片素材（Task 14）。

- [ ] **Step 1: 写失败测试**

`tests/test_xiaohongshu_adapter.py`：
```python
from adapters.xiaohongshu import XiaohongshuAdapter
from adapters.base import PublishResult


def test_identity():
    a = XiaohongshuAdapter()
    assert a.name == "xiaohongshu"
    assert "xiaohongshu" in a.login_url and a._logged_in_selector()


async def test_requires_images():
    a = XiaohongshuAdapter()
    r = await a.publish(platform="xiaohongshu", title="T", content="正文",
                        images=None, account={"account_id": "x1", "credentials": "{}"})
    assert isinstance(r, PublishResult)
    assert r.success is False and "配图" in r.error


async def test_requires_account():
    a = XiaohongshuAdapter()
    r = await a.publish(platform="xiaohongshu", title="T", content="正文",
                        images=["/tmp/a.jpg"], account=None)
    assert r.success is False and "账号" in r.error


async def test_short_circuit_when_not_logged_in(monkeypatch):
    a = XiaohongshuAdapter()
    async def not_logged_in(account=None):
        return False
    monkeypatch.setattr(a, "check_login", not_logged_in)
    r = await a.publish(platform="xiaohongshu", title="T", content="正文",
                        images=["/tmp/a.jpg"], account={"account_id": "x1", "credentials": "{}"})
    assert r.success is False and ("登录" in r.error or "cookie" in r.error.lower())
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_xiaohongshu_adapter.py -q` → Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

`adapters/xiaohongshu.py`：
```python
"""小红书图文发布（RPA）：cookie 登录 + 创作平台上传图片 + 填标题正文 + 发布。"""
import logging

from adapters.base import PublishResult
from adapters.rpa_base import RpaAdapter

logger = logging.getLogger(__name__)


class XiaohongshuAdapter(RpaAdapter):
    name = "xiaohongshu"
    login_url = "https://creator.xiaohongshu.com/login"
    publish_url = "https://creator.xiaohongshu.com/publish/publish?target=image"

    LOGGED_IN_MARK = "text=发布笔记"
    UPLOAD_INPUT = "input[type='file']"
    TITLE_INPUT = "input[placeholder*='标题']"
    CONTENT_INPUT = "div[contenteditable='true']"
    SUBMIT_BUTTON = "button:has-text('发布')"

    def _logged_in_selector(self) -> str:
        return self.LOGGED_IN_MARK

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        if not account:
            return PublishResult(success=False, platform=self.name, error="缺少账号（无 cookie 可用）")
        if not images:
            return PublishResult(success=False, platform=self.name,
                                 error="小红书必须配图，images 不能为空")
        if not await self.check_login(account):
            return PublishResult(success=False, platform=self.name,
                                 error="cookie/登录失效，请重新登录小红书账号")

        body = content
        if tags:
            body += " " + " ".join(f"#{t}" for t in tags)

        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                try:
                    page = await context.new_page()
                    await page.goto(self.publish_url, timeout=30000)
                    await page.set_input_files(self.UPLOAD_INPUT, images, timeout=30000)
                    await page.fill(self.TITLE_INPUT, title or content[:20], timeout=15000)
                    await page.fill(self.CONTENT_INPUT, body, timeout=15000)
                    await page.click(self.SUBMIT_BUTTON, timeout=15000)
                    await page.wait_for_timeout(3000)
                    logger.info("小红书发布已提交: %s", title)
                    return PublishResult(success=True, platform=self.name, output="submitted")
                finally:
                    await browser.close()
        except Exception as e:
            logger.exception("小红书发布异常")
            return PublishResult(success=False, platform=self.name, error=str(e))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_xiaohongshu_adapter.py -q` → Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/xiaohongshu.py tests/test_xiaohongshu_adapter.py
git commit -m "feat(xiaohongshu): RPA image-note publish adapter"
```

---

## Task 12: 抖音适配器（必须视频/图文）

**Files:** Create `adapters/douyin.py`；Test `tests/test_douyin_adapter.py`

> 抖音创作平台需上传视频（或图文），故 `video` 与 `images` 均空时返回明确错误。真发需扫码 + 素材（Task 14）。

- [ ] **Step 1: 写失败测试**

`tests/test_douyin_adapter.py`：
```python
from adapters.douyin import DouyinAdapter
from adapters.base import PublishResult


def test_identity():
    a = DouyinAdapter()
    assert a.name == "douyin"
    assert "douyin" in a.login_url and a._logged_in_selector()


async def test_requires_media():
    a = DouyinAdapter()
    r = await a.publish(platform="douyin", title="T", content="正文",
                        video=None, images=None, account={"account_id": "d1", "credentials": "{}"})
    assert isinstance(r, PublishResult)
    assert r.success is False and ("视频" in r.error or "图文" in r.error)


async def test_requires_account():
    a = DouyinAdapter()
    r = await a.publish(platform="douyin", title="T", content="正文",
                        video="/tmp/v.mp4", account=None)
    assert r.success is False and "账号" in r.error


async def test_short_circuit_when_not_logged_in(monkeypatch):
    a = DouyinAdapter()
    async def not_logged_in(account=None):
        return False
    monkeypatch.setattr(a, "check_login", not_logged_in)
    r = await a.publish(platform="douyin", title="T", content="正文",
                        video="/tmp/v.mp4", account={"account_id": "d1", "credentials": "{}"})
    assert r.success is False and ("登录" in r.error or "cookie" in r.error.lower())
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_douyin_adapter.py -q` → Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

`adapters/douyin.py`：
```python
"""抖音发布（RPA）：cookie 登录 + 创作平台上传视频 + 填文案 + 发布。"""
import logging

from adapters.base import PublishResult
from adapters.rpa_base import RpaAdapter

logger = logging.getLogger(__name__)


class DouyinAdapter(RpaAdapter):
    name = "douyin"
    login_url = "https://creator.douyin.com/"
    publish_url = "https://creator.douyin.com/creator-micro/content/upload"

    LOGGED_IN_MARK = "text=发布视频"
    UPLOAD_INPUT = "input[type='file']"
    CONTENT_INPUT = "div[contenteditable='true'], textarea"
    SUBMIT_BUTTON = "button:has-text('发布')"

    def _logged_in_selector(self) -> str:
        return self.LOGGED_IN_MARK

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        if not account:
            return PublishResult(success=False, platform=self.name, error="缺少账号（无 cookie 可用）")
        media = [video] if video else (images or [])
        if not media:
            return PublishResult(success=False, platform=self.name,
                                 error="抖音必须上传视频或图文素材")
        if not await self.check_login(account):
            return PublishResult(success=False, platform=self.name,
                                 error="cookie/登录失效，请重新登录抖音账号")

        caption = f"{title} {content}".strip() if title else content
        if tags:
            caption += " " + " ".join(f"#{t}" for t in tags)

        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser, context = await self._new_context(p, account)
                try:
                    page = await context.new_page()
                    await page.goto(self.publish_url, timeout=30000)
                    await page.set_input_files(self.UPLOAD_INPUT, media, timeout=60000)
                    await page.fill(self.CONTENT_INPUT, caption, timeout=15000)
                    await page.click(self.SUBMIT_BUTTON, timeout=15000)
                    await page.wait_for_timeout(3000)
                    logger.info("抖音发布已提交: %s", title)
                    return PublishResult(success=True, platform=self.name, output="submitted")
                finally:
                    await browser.close()
        except Exception as e:
            logger.exception("抖音发布异常")
            return PublishResult(success=False, platform=self.name, error=str(e))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_douyin_adapter.py -q` → Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add adapters/douyin.py tests/test_douyin_adapter.py
git commit -m "feat(douyin): RPA video/image publish adapter"
```

---

## Task 13: 注册 5 个适配器 + status 接口报告

**Files:** Modify `adapters/__init__.py`、`app.py`；Test `tests/test_registry_dispatch.py`（追加断言）

- [ ] **Step 1: 写失败测试**

`tests/test_registry_dispatch.py` 末尾追加：
```python
def test_five_adapters_registered():
    from adapters.facebook import FacebookAdapter
    from adapters.twitter import TwitterAdapter
    from adapters.reddit import RedditAdapter
    from adapters.xiaohongshu import XiaohongshuAdapter
    from adapters.douyin import DouyinAdapter
    assert isinstance(adapters.get_adapter("facebook"), FacebookAdapter)
    assert isinstance(adapters.get_adapter("twitter"), TwitterAdapter)
    assert isinstance(adapters.get_adapter("reddit"), RedditAdapter)
    assert isinstance(adapters.get_adapter("xiaohongshu"), XiaohongshuAdapter)
    assert isinstance(adapters.get_adapter("douyin"), DouyinAdapter)
    # 注册表只含这 5 个；其余平台本期不支持
    assert set(adapters.ADAPTERS) == {"facebook", "twitter", "reddit", "xiaohongshu", "douyin"}
    assert adapters.get_adapter("bilibili") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_registry_dispatch.py::test_five_adapters_registered -q` → Expected: FAIL（当前注册表为空，5 个平台全部为 None）。

- [ ] **Step 3: 注册 5 个适配器**

`adapters/__init__.py` 的 `_register_all()` 改为：
```python
def _register_all():
    from adapters.facebook import FacebookAdapter
    from adapters.twitter import TwitterAdapter
    from adapters.reddit import RedditAdapter
    from adapters.xiaohongshu import XiaohongshuAdapter
    from adapters.douyin import DouyinAdapter

    ADAPTERS["facebook"] = FacebookAdapter()
    ADAPTERS["twitter"] = TwitterAdapter()
    ADAPTERS["reddit"] = RedditAdapter()
    ADAPTERS["xiaohongshu"] = XiaohongshuAdapter()
    ADAPTERS["douyin"] = DouyinAdapter()
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_registry_dispatch.py -q` → Expected: PASS（含新断言）。

- [ ] **Step 5: 更新 /api/publish/status**

`app.py` 中 `publish_status`（约 420-427 行）替换为（去掉 huimei 相关）：
```python
@app.get("/api/publish/status")
async def publish_status():
    from adapters import ADAPTERS
    return {
        "adapters": {p: type(a).__name__ for p, a in ADAPTERS.items()},
        "supported_platforms": list(ADAPTERS.keys()),
    }
```

- [ ] **Step 6: 冒烟 + 全量回归**

Run:
```bash
cd ~/Desktop/distribution-manager
python3 -c "import adapters; print({p: type(a).__name__ for p,a in adapters.ADAPTERS.items()})"
python3 -m pytest -q
```
Expected: 打印 5 项字典（facebook→FacebookAdapter、xiaohongshu→XiaohongshuAdapter 等，无其它平台）；pytest 全绿。

- [ ] **Step 7: Commit**

```bash
git add adapters/__init__.py app.py tests/test_registry_dispatch.py
git commit -m "feat(publish): register 5 platform adapters + report adapters in status API"
```

---

## Task 14（可选·人工验证）真发脚本，按平台分别 gated

**Files:** Create `scripts/rpa_login.py`、`scripts/verify_publish.py`（不纳入 pytest 全绿标准）

> 真发依赖外部条件，无法自动化，故独立成脚本、按需运行：
> - **RPA（小红书/抖音）**：需你扫码 + 提供图片/视频素材。
> - **API（FB/X/Reddit）**：需你在 `accounts.credentials` 里填入对应凭据（见各适配器顶部注释的凭据形状）；X 需绑卡、Reddit 需过预审、FB 需 App+主页。

- [ ] **Step 1: RPA 扫码登录脚本**

`scripts/rpa_login.py`（先写文件再运行，勿用 `python3 -c`）：
```python
"""一次性：有头浏览器让用户扫码登录指定平台，cookie 存入账号。
用法: python3 scripts/rpa_login.py xiaohongshu x1   （平台 账号account_id）"""
import asyncio
import sys
import database as db
from adapters import get_adapter
from adapters.rpa_base import RpaAdapter, build_credentials


async def main(platform: str, account_id: str):
    db.init_db()
    adapter = get_adapter(platform)
    assert isinstance(adapter, RpaAdapter), f"{platform} 不是 RPA 适配器"
    accs = [a for a in db.get_accounts(platform) if a["account_id"] == account_id]
    if not accs:
        db.create_account(platform, f"{platform}主号", account_id, "RPA 登录")

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(adapter.login_url)
        print(f">>> 在浏览器里扫码登录 {platform}，完成后回车继续...")
        input()
        cookies = await context.cookies()
        db.update_account_credentials(account_id, build_credentials(cookies))
        print(f">>> 已保存 {len(cookies)} 条 cookie 到账号 {account_id}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
```

- [ ] **Step 2: 通用真发验证脚本**

`scripts/verify_publish.py`：
```python
"""按平台真发一条测试内容。RPA 需先跑 rpa_login.py；API 需账号 credentials 已填凭据。
用法: python3 scripts/verify_publish.py facebook fb_001
      python3 scripts/verify_publish.py xiaohongshu x1 /path/a.jpg"""
import asyncio
import sys
import database as db
from adapters import get_adapter
from adapters.rpa_base import RpaAdapter


async def main(platform: str, account_id: str, media: list[str]):
    db.init_db()
    accs = [a for a in db.get_accounts(platform) if a["account_id"] == account_id]
    assert accs, f"账号 {account_id} 不存在，先创建/登录"
    account = accs[0]
    adapter = get_adapter(platform)
    if isinstance(adapter, RpaAdapter):
        adapter.headless = False
    r = await adapter.publish(
        platform=platform, title="", content="【测试】自动发布联调，请忽略。",
        images=media or None, video=(media[0] if media and media[0].endswith(".mp4") else None),
        account=account,
    )
    print(r.to_dict())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
```

- [ ] **Step 3: 按需真发（示例，需你满足各平台前置条件）**

```bash
# 小红书：先扫码，再带图真发
python3 scripts/rpa_login.py xiaohongshu x1
python3 scripts/verify_publish.py xiaohongshu x1 ~/Desktop/test.jpg
# Facebook：先在账号 credentials 填 {"page_id","page_access_token"}，再：
python3 scripts/verify_publish.py facebook fb_001
```
Expected: 打印 `{'success': True, ...}` 或明确错误（据此调选择器/凭据）。

- [ ] **Step 4: Commit**

```bash
git add scripts/rpa_login.py scripts/verify_publish.py
git commit -m "chore(scripts): manual login + publish verification helpers"
```

---

## 完成标准（Definition of Done，第一期）

- [ ] `python3 -m pytest -q` **全绿**（Task 0-13，不含 Task 14 人工验证）
- [ ] `/api/publish/status` 的 `adapters` 字段只含这 5 个：facebook/twitter/reddit → 各自 API 适配器；xiaohongshu/douyin → RPA 适配器
- [ ] 未支持平台（bilibili/知乎等）dispatch 返回明确「无适配器」错误，不静默
- [ ] 频控：同平台第二条到期任务在 30 分钟内会被顺延
- [ ] 认证失效（cookie/token）时账号置 `expired` 并触发三渠道通知
- [ ] 5 个适配器均能对「缺凭据/缺素材/未登录」返回明确错误，绝不静默假成功

## 后续（第二期，不在本计划内）

- **接真发**：你提供凭据后逐个开通——FB(企业验证+审核)、X(绑卡按量付费)、Reddit(预审 2-4 周)；RPA 两家扫码 + 素材。
- **OAuth 落地**：FB/X/Reddit 的授权取 token 流程（含 X 的 OAuth2 PKCE、Reddit refresh_token 获取）做成后台任务 + 前端「重新登录」按钮。
- **媒体入库**：`queue` 加图片/视频字段，让小红书/抖音经审核流真发。
- **内容差异化**（方案 §四）：小红书种草 / 抖音短文案 / FB 正式英文 / X 精简 / Reddit 讨论——属 `ai_engine` 生成侧，另立计划。
- **频控参数迁 settings 表** + 配置页可视化。
