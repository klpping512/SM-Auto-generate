# 前端适配新发布机制 Implementation Plan（第二期·前端接线）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端接上第一期的新发布机制——让用户能在界面里录入各平台真实凭据、给小红书/抖音扫码登录、看到每个账号"是否就绪"、对失效账号一键重登，并顺手堵上 `GET /api/accounts` 泄露凭据的安全洞。

**Architecture:** 后端补 3 类接口（写凭据 / 账号就绪度 / RPA 扫码登录），全部 pytest TDD；前端改 `accounts.html` 一个页面（vanilla JS + `common.js`），按平台动态渲染凭据表单、就绪徽章、扫码/重登按钮，改动走人工验证（项目无 JS 测试框架，遵循现状）。凭据的"必填字段"由各适配器声明、单一来源，避免前后端漂移。

**Tech Stack:** FastAPI · SQLite · Playwright · 原生 HTML/JS（Tailwind CDN + design-system.css + iconify）· pytest

---

## 背景与现状（务必先读）

第一期是纯后端：`publisher.dispatch()` + 5 个适配器 + 频控，均已合并 main。前端**一行未改**，因此存在这些缺口：

| 缺口 | 现状 | 本计划是否解决 |
|------|------|--------------|
| 录入真实凭据 | 绑定账号只填 平台/名称/ID/配置摘要，`credentials` 恒为 `{}` | ✅ Task 2+4 |
| RPA 扫码登录 | 无入口，小红书/抖音拿不到 cookie | ✅ Task 3+6 |
| 账号就绪度 | 前端只显示 active/expired，不知道"缺哪个凭据" | ✅ Task 1+2+5 |
| 失效重登 | 后端会置 expired，前端无"重新登录"按钮 | ✅ Task 6 |
| 发布能力总览 | 没人调 `/api/publish/status` | ✅ Task 7 |
| **凭据泄露** | `GET /api/accounts` 用 `SELECT *` 把 credentials 明文返回前端 | ✅ Task 2（列表脱敏） |
| RPA 发布带素材 | 队列无媒体字段、发布按钮只发文 | ❌ 另立计划（见文末） |
| 频控参数可视化设置 | 只能改环境变量 | ❌ 另立计划（见文末） |

**前端约定（照抄现有模式）**：页面 = 单 HTML + 内联 `<script>`，`render()` 重建 `innerHTML`；用 `apiFetch(url, opts)`（自动带 token、401 跳登录、非 2xx 抛错）、`showToast(msg,type)`、`escapeHtml()`、`renderSidebar(PAGE_ID)`。Modal = 隐藏 div 靠 `display` 切换。`accounts.html` 顶部已有 `platformNames/platformIcons/platformColors` 五平台常量。

**后端事实（已核实）**：`/api/auth/register` 需 admin 权限（鸡生蛋），测试里用 `auth.hash_password()` + `db.create_user()` 直接种 admin 再登录。默认 admin 由 app 启动时建（admin/admin123）。`require_role(UserRole.ADMIN)` 已有。`db.update_account_credentials` / `update_account_status` 已存在（第一期 Task 1）。

**凭据形状（各适配器要求，单一来源在 Task 1 落到适配器类属性）**：
- facebook: `page_id`, `page_access_token`
- twitter: `access_token`
- reddit: `client_id`, `client_secret`, `refresh_token`, `user_agent`, `subreddit`
- 小红书 / 抖音: 无手填字段，靠**扫码**写入 `cookies`

## File Structure

**新建：**
- `publish_readiness.py` — `readiness(platform, credentials_json) -> dict`（就绪度判定，读适配器声明的必填字段）
- `tests/test_publish_readiness.py`、`tests/test_accounts_api.py`、`tests/test_scan_login_api.py`

**修改：**
- `adapters/base.py` — `PublishAdapter` 加类属性 `REQUIRED_CREDENTIALS: list[str] = []` 与 `CREDENTIAL_KIND = "token"`
- `adapters/facebook.py`/`twitter.py`/`reddit.py` — 声明 `REQUIRED_CREDENTIALS`
- `adapters/xiaohongshu.py`/`douyin.py` — 声明 `CREDENTIAL_KIND = "cookie"`
- `models.py` — 新增 `AccountCredentialsRequest`
- `app.py` — 新增 `PUT /api/accounts/{id}/credentials`、`POST /api/accounts/{id}/scan-login`；改造 `GET /api/accounts`（脱敏 + 附就绪度）
- `static/accounts.html` — 凭据 Modal、就绪徽章、扫码/重登按钮、轮询

---

## Task 1: 适配器声明必填凭据 + 就绪度判定

**Files:** Modify `adapters/base.py`、`adapters/{facebook,twitter,reddit,xiaohongshu,douyin}.py`；Create `publish_readiness.py`；Test `tests/test_publish_readiness.py`

- [ ] **Step 1: 写失败测试**

`tests/test_publish_readiness.py`：
```python
import publish_readiness as pr


def test_api_platform_missing_fields():
    r = pr.readiness("facebook", "{}")
    assert r == {"ready": False, "kind": "token",
                 "missing": ["page_id", "page_access_token"]}


def test_api_platform_ready():
    r = pr.readiness("twitter", '{"access_token": "tok"}')
    assert r == {"ready": True, "kind": "token", "missing": []}


def test_reddit_partial():
    r = pr.readiness("reddit", '{"client_id":"a","client_secret":"b"}')
    assert r["ready"] is False
    assert set(r["missing"]) == {"refresh_token", "user_agent", "subreddit"}


def test_rpa_platform_needs_cookies():
    assert pr.readiness("xiaohongshu", "{}") == {
        "ready": False, "kind": "cookie", "missing": ["cookies"]}
    ready = pr.readiness("xiaohongshu", '{"cookies": [{"name": "a"}]}')
    assert ready == {"ready": True, "kind": "cookie", "missing": []}


def test_unknown_platform():
    assert pr.readiness("bilibili", "{}") == {
        "ready": False, "kind": "unknown", "missing": ["<无适配器>"]}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_publish_readiness.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'publish_readiness'`。

- [ ] **Step 3: 适配器声明字段**

`adapters/base.py` 在 `class PublishAdapter(ABC):` 内、`name: str = ""` 下方加两个类属性：
```python
    # 该适配器发布所需的凭据字段（供前端表单 + 就绪度判定，单一来源）
    REQUIRED_CREDENTIALS: list[str] = []
    # "token"=手填字段；"cookie"=靠扫码登录写入 cookies
    CREDENTIAL_KIND: str = "token"
```

`adapters/facebook.py` 在 `class FacebookAdapter(ApiAdapter):` 内 `name = "facebook"` 下方加：
```python
    REQUIRED_CREDENTIALS = ["page_id", "page_access_token"]
```

`adapters/twitter.py` 在 `class TwitterAdapter(ApiAdapter):` 内 `name = "twitter"` 下方加：
```python
    REQUIRED_CREDENTIALS = ["access_token"]
```

`adapters/reddit.py` 在 `class RedditAdapter(ApiAdapter):` 内 `name = "reddit"` 下方加：
```python
    REQUIRED_CREDENTIALS = ["client_id", "client_secret", "refresh_token", "user_agent", "subreddit"]
```

`adapters/xiaohongshu.py` 在 `class XiaohongshuAdapter(RpaAdapter):` 内 `name = "xiaohongshu"` 下方加：
```python
    CREDENTIAL_KIND = "cookie"
```

`adapters/douyin.py` 在 `class DouyinAdapter(RpaAdapter):` 内 `name = "douyin"` 下方加：
```python
    CREDENTIAL_KIND = "cookie"
```

- [ ] **Step 4: 就绪度模块**

`publish_readiness.py`：
```python
"""账号就绪度判定：给定平台 + 凭据 JSON，返回是否可发布 + 缺哪些字段。
必填字段由各适配器 REQUIRED_CREDENTIALS / CREDENTIAL_KIND 声明，单一来源。"""
import json

from adapters import get_adapter
from adapters.rpa_base import parse_cookies


def readiness(platform: str, credentials: str | None) -> dict:
    adapter = get_adapter(platform)
    if adapter is None:
        return {"ready": False, "kind": "unknown", "missing": ["<无适配器>"]}

    kind = adapter.CREDENTIAL_KIND
    try:
        creds = json.loads(credentials or "{}")
    except (json.JSONDecodeError, TypeError):
        creds = {}

    if kind == "cookie":
        has = bool(parse_cookies(credentials))
        return {"ready": has, "kind": "cookie", "missing": [] if has else ["cookies"]}

    missing = [k for k in adapter.REQUIRED_CREDENTIALS if not creds.get(k)]
    return {"ready": not missing, "kind": "token", "missing": missing}
```

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_publish_readiness.py -q`
Expected: PASS（5 passed）。

- [ ] **Step 6: 全量回归 + 提交**

Run: `python3 -m pytest -q`（应仍全绿）
```bash
git add adapters/base.py adapters/facebook.py adapters/twitter.py adapters/reddit.py adapters/xiaohongshu.py adapters/douyin.py publish_readiness.py tests/test_publish_readiness.py
git commit -m "feat(readiness): adapters declare required credentials + readiness helper"
```
提交信息结尾加：`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Task 2: 写凭据接口 + 账号列表脱敏并附就绪度

**Files:** Modify `models.py`、`app.py`；Test `tests/test_accounts_api.py`

> 安全：`GET /api/accounts` 现在返回 `credentials` 明文。改为**剔除 credentials，改附 `ready/missing/credential_kind`**。写凭据用新的 `PUT /api/accounts/{id}/credentials`。

- [ ] **Step 1: 写失败测试**

`tests/test_accounts_api.py`：
```python
import json
from fastapi.testclient import TestClient


def _client(tmp_db, monkeypatch):
    import app
    monkeypatch.setattr(app.db, "DB_PATH", tmp_db.DB_PATH)
    return TestClient(app.app)


def _admin_token(tmp_db, client):
    # 注意: /api/auth/register 需要 admin 权限（鸡生蛋），故直接种 admin 再登录
    import auth
    tmp_db.create_user("adm", auth.hash_password("pw12345"), "admin", "A")
    r = client.post("/api/auth/login", json={"username": "adm", "password": "pw12345"})
    return r.json()["access_token"]


def test_list_accounts_redacts_credentials_and_adds_readiness(tmp_db, monkeypatch):
    tmp_db.create_account("facebook", "主页", "fb_1")
    tmp_db.update_account_credentials("fb_1", '{"page_id": "123"}')  # 缺 token
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.get("/api/accounts", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    acc = r.json()[0]
    assert "credentials" not in acc  # 脱敏
    assert acc["ready"] is False
    assert acc["missing"] == ["page_access_token"]
    assert acc["credential_kind"] == "token"


def test_put_credentials(tmp_db, monkeypatch):
    tmp_db.create_account("facebook", "主页", "fb_1")
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.put("/api/accounts/1/credentials",
                   headers={"Authorization": f"Bearer {tok}"},
                   json={"credentials": {"page_id": "123", "page_access_token": "tok"}})
    assert r.status_code == 200 and r.json()["ready"] is True
    got = tmp_db.get_accounts("facebook")[0]
    assert json.loads(got["credentials"]) == {"page_id": "123", "page_access_token": "tok"}
    assert got["status"] == "active"


def test_put_credentials_missing_account(tmp_db, monkeypatch):
    client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.put("/api/accounts/999/credentials",
                   headers={"Authorization": f"Bearer {tok}"},
                   json={"credentials": {"access_token": "x"}})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_accounts_api.py -q`
Expected: FAIL（`PUT /api/accounts/{id}/credentials` 不存在 → 405/404；列表仍含 credentials）。

- [ ] **Step 3: 加请求模型**

`models.py` 的 Accounts 区块（`AccountCreateRequest` 下方）追加：
```python
class AccountCredentialsRequest(BaseModel):
    credentials: dict
```

- [ ] **Step 4: 改造 GET /api/accounts（脱敏 + 就绪度）**

`app.py` 顶部 import 区加：
```python
import json as _json
import publish_readiness
```
把 `AccountCredentialsRequest` 加进 `from models import (...)` 那个导入列表。

把 `list_accounts` 端点（约 261-263 行）替换为：
```python
@app.get("/api/accounts")
async def list_accounts(platform: str = None, user=Depends(get_current_user)):
    result = []
    for a in db.get_accounts(platform):
        r = publish_readiness.readiness(a["platform"], a.get("credentials"))
        a.pop("credentials", None)  # 脱敏：不把凭据明文返回前端
        a["ready"] = r["ready"]
        a["missing"] = r["missing"]
        a["credential_kind"] = r["kind"]
        result.append(a)
    return result
```

- [ ] **Step 5: 加写凭据端点**

`app.py` 在 `delete_account`（约 276-279 行）之后追加：
```python
@app.put("/api/accounts/{account_id}/credentials")
async def set_account_credentials(
    account_id: int, req: AccountCredentialsRequest,
    user=Depends(require_role(UserRole.ADMIN)),
):
    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        raise HTTPException(404, "Account not found")
    acc = accounts[0]
    db.update_account_credentials(acc["account_id"], _json.dumps(req.credentials, ensure_ascii=False))
    db.update_account_status(account_id, "active")  # 填了凭据即恢复可用
    db.add_audit_log(user["id"], user["username"], "set_credentials", target=f"{acc['platform']}:{acc['account_id']}")
    r = publish_readiness.readiness(acc["platform"], _json.dumps(req.credentials))
    return {"ok": True, "ready": r["ready"], "missing": r["missing"]}
```

- [ ] **Step 6: 运行确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_accounts_api.py -q` → Expected: 3 passed。
Run: `python3 -m pytest -q` → Expected: 全绿。
> FastAPI 的 `TestClient` 随 fastapi/starlette 提供，无需额外装依赖。

- [ ] **Step 7: Commit**

```bash
git add models.py app.py tests/test_accounts_api.py
git commit -m "feat(accounts): PUT credentials endpoint + redact credentials & add readiness to list"
```
结尾加 Co-Authored-By 行。

---

## Task 3: RPA 扫码登录接口

**Files:** Modify `app.py`；Test `tests/test_scan_login_api.py`

> 小红书/抖音靠扫码。`POST /api/accounts/{id}/scan-login` 启动**有头浏览器**（在运行 FastAPI 的本机弹出，本地单机场景可用）+ 后台轮询登录态 → 存 cookie → 置 active。接口立即返回；前端轮询账号 status。真实浏览器行为人工验证；单测只覆盖守卫与调度（mock 掉后台任务）。

- [ ] **Step 1: 写失败测试**

`tests/test_scan_login_api.py`：
```python
from fastapi.testclient import TestClient


def _client(tmp_db, monkeypatch):
    import app
    monkeypatch.setattr(app.db, "DB_PATH", tmp_db.DB_PATH)
    return app, TestClient(app.app)


def _admin_token(tmp_db, client):
    import auth
    tmp_db.create_user("adm", auth.hash_password("pw12345"), "admin", "A")
    return client.post("/api/auth/login", json={"username": "adm", "password": "pw12345"}).json()["access_token"]


def test_scan_login_rejects_api_platform(tmp_db, monkeypatch):
    tmp_db.create_account("facebook", "主页", "fb_1")
    app, client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.post("/api/accounts/1/scan-login", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400 and "扫码" in r.json()["detail"]


def test_scan_login_starts_for_rpa_platform(tmp_db, monkeypatch):
    tmp_db.create_account("xiaohongshu", "主号", "x_1")
    app, client = _client(tmp_db, monkeypatch)
    called = {}

    async def fake_bg(account):
        called["account_id"] = account["account_id"]
    monkeypatch.setattr(app, "_run_scan_login", fake_bg)

    tok = _admin_token(tmp_db, client)
    r = client.post("/api/accounts/1/scan-login", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["started"] is True


def test_scan_login_missing_account(tmp_db, monkeypatch):
    app, client = _client(tmp_db, monkeypatch)
    tok = _admin_token(tmp_db, client)
    r = client.post("/api/accounts/1/scan-login", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_scan_login_api.py -q` → Expected: FAIL（端点不存在）。

- [ ] **Step 3: 实现端点 + 后台任务**

`app.py` 顶部 import 区加（若未 `import asyncio` 也补上）：
```python
from adapters import get_adapter
from adapters.rpa_base import RpaAdapter, build_credentials
```
在写凭据端点之后追加：
```python
async def _run_scan_login(account: dict):
    """后台：有头浏览器让用户扫码，轮询登录态 → 存 cookie → 置 active。本地单机场景。"""
    adapter = get_adapter(account["platform"])
    if not isinstance(adapter, RpaAdapter):
        return
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(adapter.login_url, timeout=60000)
            logged_in = False
            for _ in range(90):  # 最多 180s
                if await page.query_selector(adapter._logged_in_selector()):
                    logged_in = True
                    break
                await page.wait_for_timeout(2000)
            if logged_in:
                cookies = await context.cookies()
                db.update_account_credentials(account["account_id"], build_credentials(cookies))
                db.update_account_status(account["id"], "active")
                logger.info("扫码登录成功: %s", account["account_id"])
            else:
                db.update_account_status(account["id"], "expired")
                logger.warning("扫码登录超时: %s", account["account_id"])
            await browser.close()
    except Exception:
        logger.exception("扫码登录异常: %s", account["account_id"])


@app.post("/api/accounts/{account_id}/scan-login")
async def scan_login(account_id: int, user=Depends(require_role(UserRole.ADMIN))):
    accounts = [a for a in db.get_accounts() if a["id"] == account_id]
    if not accounts:
        raise HTTPException(404, "Account not found")
    acc = accounts[0]
    adapter = get_adapter(acc["platform"])
    if not isinstance(adapter, RpaAdapter):
        raise HTTPException(400, "该平台不使用扫码登录，请填写凭据")
    asyncio.create_task(_run_scan_login(acc))
    db.add_audit_log(user["id"], user["username"], "scan_login", target=f"{acc['platform']}:{acc['account_id']}")
    return {"started": True, "message": "已启动扫码登录，请在弹出的浏览器完成扫码"}
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_scan_login_api.py -q` → Expected: 3 passed。
Run: `python3 -m pytest -q` → Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_scan_login_api.py
git commit -m "feat(accounts): RPA scan-login endpoint (headed Playwright, background poll)"
```
结尾加 Co-Authored-By 行。

---

## Task 4: 前端 — 凭据录入 Modal（按平台动态字段）

**Files:** Modify `static/accounts.html`（人工验证，无自动化测试）

- [ ] **Step 1: 加平台必填字段表 + 中文标签**

在 `static/accounts.html` 顶部脚本、`platformColors` 常量下方追加：
```javascript
const requiredFields = {
    facebook: [['page_id','主页 ID'],['page_access_token','主页访问令牌 (Page Access Token)']],
    twitter:  [['access_token','用户访问令牌 (OAuth2 access_token)']],
    reddit:   [['client_id','App Client ID'],['client_secret','App Client Secret'],
               ['refresh_token','Refresh Token'],['user_agent','User-Agent'],['subreddit','目标 Subreddit']],
    xiaohongshu: [], douyin: [],
};
```

- [ ] **Step 2: 加凭据 Modal 的 DOM**

在 `render()` 返回模板里、原 `addModal` 那个 `</div>` 之后、模板末尾的 `` \`; `` 之前，插入：
```javascript
    <div id="credModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:100;align-items:center;justify-content:center;">
        <div class="card" style="width:440px;padding:24px;max-height:80vh;overflow:auto;">
            <h3 style="font-size:16px;font-weight:700;margin-bottom:4px;">填写凭据</h3>
            <p id="credHint" style="font-size:12px;color:var(--text-muted);margin-bottom:16px;"></p>
            <div id="credFields" style="display:flex;flex-direction:column;gap:12px;"></div>
            <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:20px;">
                <button class="btn btn-secondary btn-sm" onclick="closeCredModal()">取消</button>
                <button class="btn btn-primary btn-sm" onclick="saveCredentials()">保存</button>
            </div>
        </div>
    </div>
```

- [ ] **Step 3: 加打开/关闭/保存逻辑**

在 `closeAddModal` 定义之后追加：
```javascript
let credAccountId = null;

function openCredModal(id, platform) {
    const fields = requiredFields[platform] || [];
    credAccountId = id;
    document.getElementById('credHint').textContent =
        `平台：${platformNames[platform] || platform}。凭据仅用于发布，保存后不再回显。`;
    document.getElementById('credFields').innerHTML = fields.map(([k, label]) => `
        <div><label style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:4px;display:block;">${escapeHtml(label)}</label>
        <input id="cred_${k}" class="input" placeholder="${escapeHtml(k)}" autocomplete="off"/></div>`).join('');
    document.getElementById('credModal').style.display = 'flex';
}
function closeCredModal() { document.getElementById('credModal').style.display = 'none'; credAccountId = null; }

async function saveCredentials() {
    const acc = accounts.find(a => a.id === credAccountId);
    const fields = requiredFields[acc.platform] || [];
    const credentials = {};
    for (const [k] of fields) {
        const v = document.getElementById(`cred_${k}`).value.trim();
        if (!v) { showToast('请填写全部字段', 'error'); return; }
        credentials[k] = v;
    }
    try {
        const r = await apiFetch(`/api/accounts/${credAccountId}/credentials`,
            { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ credentials }) });
        const d = await r.json();
        closeCredModal();
        showToast(d.ready ? '凭据已保存，账号就绪' : '已保存，但仍缺字段');
        init();
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
}
```

- [ ] **Step 4: 人工验证**

Run: 启动应用（`cd ~/Desktop/distribution-manager && python3 -m uvicorn app:app --reload`），登录后到 `/accounts.html`：
1. 绑定一个 facebook 账号；在浏览器控制台 `openCredModal(<id>,'facebook')` 打开 Modal（Task 5 会加行内按钮）。
2. 填 `page_id` + `page_access_token` → 保存 → Toast "账号就绪"。
3. Network 里确认 `PUT /api/accounts/<id>/credentials` 返回 `{ok:true, ready:true}`。
4. 刷新页面，确认凭据**不回显**（安全）。
Expected: 全部符合。

- [ ] **Step 5: Commit**

```bash
git add static/accounts.html
git commit -m "feat(ui): per-platform credential entry modal on accounts page"
```
结尾加 Co-Authored-By 行。

---

## Task 5: 前端 — 就绪徽章 + 每行"填写凭据"入口

**Files:** Modify `static/accounts.html`（人工验证）

- [ ] **Step 1: 状态列改为就绪徽章**

`render()` 里账号表格的状态单元格（现为"已授权/已过期"那个 `<td>`）替换为：
```javascript
                                    <td>
                                        <span style="display:flex;align-items:center;gap:4px;font-size:12px;">
                                          <span style="width:6px;height:6px;border-radius:50%;background:${a.status==='expired' ? '#EF4444' : (a.ready ? '#10B981' : '#F59E0B')};"></span>
                                          ${a.status==='expired' ? '已过期' : (a.ready ? '就绪' : '缺凭据')}
                                        </span>
                                        ${a.ready ? '' : `<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">缺: ${escapeHtml((a.missing||[]).join(', '))}</div>`}
                                    </td>
```

- [ ] **Step 2: 操作列加"填写凭据"（仅 token 类平台）**

`render()` 里操作单元格（现只有删除按钮），在删除按钮前加：
```javascript
                                        ${a.credential_kind==='token' ? `<button class="btn btn-ghost btn-sm" title="填写凭据" onclick="openCredModal(${a.id},'${a.platform}')"><iconify-icon icon="mdi:key-outline" width="16"></iconify-icon></button>` : ''}
```

- [ ] **Step 3: 人工验证**

Run: 刷新 `/accounts.html`：
1. facebook 未填凭据 → 徽章"缺凭据"(橙) + "缺: page_id, page_access_token"；有"钥匙"按钮。
2. 点钥匙 → 凭据 Modal 打开 → 填好保存 → 徽章变"就绪"(绿)。
3. reddit 只填部分字段 → 徽章仍"缺凭据"，缺失列表正确。
4. 小红书（cookie 类）→ **无**钥匙按钮（Task 6 给它扫码按钮）。
Expected: 符合。

- [ ] **Step 4: Commit**

```bash
git add static/accounts.html
git commit -m "feat(ui): readiness badge + per-row credential entry on accounts page"
```
结尾加 Co-Authored-By 行。

---

## Task 6: 前端 — RPA 扫码登录 + 失效重登

**Files:** Modify `static/accounts.html`（人工验证）

- [ ] **Step 1: 操作列加"扫码登录"（cookie 类）与"重新登录"（expired）**

`render()` 操作单元格里，在删除按钮前追加：
```javascript
                                        ${a.credential_kind==='cookie' && a.status!=='expired' ? `<button class="btn btn-ghost btn-sm" title="扫码登录" onclick="scanLogin(${a.id})"><iconify-icon icon="mdi:qrcode-scan" width="16"></iconify-icon></button>` : ''}
                                        ${a.status==='expired' && a.credential_kind==='cookie' ? `<button class="btn btn-ghost btn-sm" style="color:var(--error);" title="重新登录" onclick="scanLogin(${a.id})"><iconify-icon icon="mdi:login-variant" width="16"></iconify-icon></button>` : ''}
```

- [ ] **Step 2: 扫码逻辑 + 轮询**

在 `saveCredentials` 之后追加：
```javascript
async function scanLogin(id) {
    try {
        await apiFetch(`/api/accounts/${id}/scan-login`, { method: 'POST' });
        showToast('已启动，请在弹出的浏览器完成扫码', 'info');
        pollAccountReady(id, 0);
    } catch (e) { showToast('扫码启动失败: ' + e.message, 'error'); }
}

function pollAccountReady(id, tries) {
    if (tries > 90) { showToast('扫码超时，请重试', 'error'); return; }
    setTimeout(async () => {
        try {
            const resp = await apiFetch('/api/accounts');
            accounts = await resp.json();
            const acc = accounts.find(a => a.id === id);
            render();
            if (acc && acc.ready) { showToast('扫码登录成功，账号就绪'); return; }
        } catch (e) { /* 忽略单次失败，继续轮询 */ }
        pollAccountReady(id, tries + 1);
    }, 2000);
}
```

- [ ] **Step 3: 人工验证（需本机 + Playwright chromium 已装）**

Run: `/accounts.html`：
1. 绑定小红书账号 → 该行有"二维码"按钮。
2. 点它 → 后端弹出 Chromium 打开小红书登录页 → 你扫码登录。
3. 扫码完成后 ~2-4s，前端徽章自动变"就绪"，Toast "扫码登录成功"。
4. 若 180s 内没登录 → 账号置 expired，出现红色"重新登录"按钮。
Expected: 符合。（浏览器在运行后端的本机弹出，本地单机使用。）

- [ ] **Step 4: Commit**

```bash
git add static/accounts.html
git commit -m "feat(ui): RPA scan-login + relogin for expired accounts"
```
结尾加 Co-Authored-By 行。

---

## Task 7: 前端 — 发布能力总览面板

**Files:** Modify `static/accounts.html`（人工验证）

> 用已存在的 `GET /api/publish/status`（返回 `{adapters, supported_platforms}`）+ 账号就绪度，给账号页顶部加"发布能力"小结。

- [ ] **Step 1: 拉状态并渲染小结**

`init()` 改为同时拉 status：
```javascript
let pubStatus = { adapters: {}, supported_platforms: [] };
async function init() {
    const [ra, rs] = await Promise.all([apiFetch('/api/accounts'), apiFetch('/api/publish/status')]);
    accounts = await ra.json();
    pubStatus = await rs.json();
    render();
}
```
在 `render()` 的 `page-body` 顶部（过滤按钮之前）插入小结卡片：
```javascript
            <div class="card" style="margin-bottom:16px;"><div class="card-body" style="display:flex;gap:16px;flex-wrap:wrap;">
                ${Object.keys(platformNames).map(pf => {
                    const hasAdapter = (pf in (pubStatus.adapters||{}));
                    const readyCount = accounts.filter(a => a.platform===pf && a.ready).length;
                    const total = accounts.filter(a => a.platform===pf).length;
                    return `<div style="min-width:120px;"><div style="font-size:12px;color:var(--text-muted);">${platformNames[pf]}</div>
                        <div style="font-size:13px;font-weight:600;color:${hasAdapter ? (readyCount>0?'#10B981':'#F59E0B') : '#EF4444'};">
                        ${hasAdapter ? `就绪 ${readyCount}/${total}` : '无适配器'}</div></div>`;
                }).join('')}
            </div></div>
```

- [ ] **Step 2: 人工验证**

Run: `/accounts.html`：顶部出现 5 平台小结；填好凭据/扫码后对应平台"就绪 N/M"数字增加；`/api/publish/status` 在 Network 里返回 5 个 adapters。
Expected: 符合。

- [ ] **Step 3: Commit**

```bash
git add static/accounts.html
git commit -m "feat(ui): publish-capability summary panel on accounts page"
```
结尾加 Co-Authored-By 行。

---

## 完成标准（Definition of Done）

- [ ] `python3 -m pytest -q` 全绿（新增 readiness / accounts API / scan-login 测试）
- [ ] `GET /api/accounts` **不再返回 credentials 明文**，改附 `ready/missing/credential_kind`
- [ ] API 平台（FB/X/Reddit）能在界面填凭据、就绪徽章正确、缺字段可见
- [ ] RPA 平台（小红书/抖音）有扫码按钮，扫码后自动变就绪；失效账号有"重新登录"
- [ ] 账号页顶部有 5 平台发布能力小结

## 自查记录

- 覆盖了背景表里除"媒体上传/频控设置"外的全部缺口（含凭据泄露安全洞）。
- 类型/字段一致：`readiness()` 返回 `{ready,kind,missing}` → 接口映射为 `ready/missing/credential_kind` → 前端读同名字段；`requiredFields`(前端) 镜像 `REQUIRED_CREDENTIALS`(后端)。
- 已修正：测试不能用 `/api/auth/register`（需 admin），改为 `auth.hash_password()` + `db.create_user()` 直接种 admin。

## 后续（各自另立计划，不在本计划内）

1. **RPA 发布带素材**：`queue` 表加 `images/video` 字段 + 上传接口 + 素材选择 UI，让小红书/抖音经审核流真发。工作量最大，单独成计划。
2. **频控参数可视化**：新建 `settings` 表 + 读写接口，`config.html` 加"每日上限/最小间隔/抖动"设置项。
3. **真发接通**：本计划只解决"能录入凭据/能扫码"；真正发出去仍受第一期外部门槛约束（X 绑卡付费、Reddit 预审 2-4 周、FB 企业验证+审核）。
