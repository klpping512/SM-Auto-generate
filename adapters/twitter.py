"""X(Twitter) 发推：API v2 POST /2/tweets，OAuth 2.0 支持两种模式。"""
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Event

import httpx

from adapters.api_base import ApiAdapter
from adapters.base import PublishResult

logger = logging.getLogger(__name__)

TWEETS_URL = "https://api.twitter.com/2/tweets"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
AUTH_URL = "https://twitter.com/i/oauth2/authorize"
MAX_LEN = 280
DEFAULT_PORT = 18923  # 本地回调端口（需与 X Developer Console 设置一致）

# token 过期提前刷新的缓冲时间（秒）
REFRESH_BUFFER = 300  # 提前 5 分钟刷新


def _pkce_challenge() -> tuple[str, str]:
    """生成 PKCE code_verifier + code_challenge (S256)。"""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """捕获 OAuth2 回调的临时 HTTP handler。"""
    code: str | None = None
    state_expected: str = ""
    done = Event()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if params.get("state", [None])[0] == self.state_expected:
            _CallbackHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("✅ 授权成功！可以关闭此页面。".encode("utf-8"))
        _CallbackHandler.done.set()

    def log_message(self, *args):
        pass  # 静默


async def _exchange_code(code: str, code_verifier: str, client_id: str,
                         client_secret: str, redirect_uri: str) -> dict | None:
    """用授权码换 access_token（Web App 用 Basic Auth + client_secret）。"""
    auth_str = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {auth_str}",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if r.status_code == 200:
            return r.json()
        logger.warning("Twitter token exchange failed: %s %s", r.status_code, r.text[:300])
        return None


async def _refresh_token(refresh_token: str, client_id: str, client_secret: str | None = None) -> dict | None:
    """刷新 access_token。支持有/无 client_secret 两种模式。"""
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }

    # 有 client_secret 则用 Basic Auth（Web App / Confidential Client）
    if client_secret:
        auth_str = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {auth_str}"
        data["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, headers=headers, data=data)
        if r.status_code == 200:
            return r.json()
        logger.warning("Twitter token refresh failed: %s %s", r.status_code, r.text[:300])
        return None


async def twitter_auth(client_id: str, client_secret: str, port: int = DEFAULT_PORT) -> dict | None:
    """交互式 OAuth 2.0 授权：打开浏览器让用户授权，返回 token dict。"""
    verifier, challenge = _pkce_challenge()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://localhost:{port}/callback"

    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "tweet.write tweet.read users.read offline.access",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTH_URL}?{params}"

    # 启动本地服务器等回调
    _CallbackHandler.state_expected = state
    _CallbackHandler.code = None
    _CallbackHandler.done.clear()

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 120  # 最多等 2 分钟

    print(f"正在打开浏览器授权...如果没自动打开，请手动访问：\n{auth_url}\n")
    webbrowser.open(auth_url)

    # 等回调
    while not _CallbackHandler.done.is_set():
        server.handle_request()
    server.server_close()

    if not _CallbackHandler.code:
        print("❌ 未收到授权码")
        return None

    # 换 token
    token_data = await _exchange_code(_CallbackHandler.code, verifier, client_id, client_secret, redirect_uri)
    if token_data and token_data.get("access_token"):
        print(f"✅ 授权成功！access_token: {token_data['access_token'][:20]}...")
        return token_data
    print("❌ Token 交换失败")
    return None


def _is_token_expired(creds: dict) -> bool:
    """检查 access_token 是否过期（基于 token_obtained_at + expires_in）。"""
    obtained = creds.get("token_obtained_at", 0)
    expires_in = creds.get("expires_in", 7200)  # 默认 2 小时
    if not obtained:
        return True
    return time.time() > (obtained + expires_in - REFRESH_BUFFER)


class TwitterAdapter(ApiAdapter):
    name = "twitter"
    # 只需要 client_id，client_secret 可选
    REQUIRED_CREDENTIALS = ["client_id"]

    async def _get_user_token(self, creds: dict) -> str | None:
        """获取可用的 user access_token，过期则自动刷新。"""
        access_token = creds.get("access_token")
        refresh_tok = creds.get("refresh_token")
        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")  # 可选

        # 检查 token 是否过期
        if access_token and not _is_token_expired(creds):
            return access_token

        # token 过期或不存在，尝试刷新
        if refresh_tok and client_id:
            token_data = await _refresh_token(refresh_tok, client_id, client_secret)
            if token_data and token_data.get("access_token"):
                new_access = token_data["access_token"]
                new_refresh = token_data.get("refresh_token", refresh_tok)
                new_expires = token_data.get("expires_in", 7200)

                # 更新 creds（写回存储层）
                creds["access_token"] = new_access
                creds["refresh_token"] = new_refresh
                creds["expires_in"] = new_expires
                creds["token_obtained_at"] = time.time()
                self._update_creds(creds)

                logger.info("Twitter token 刷新成功")
                return new_access
            else:
                logger.warning("Twitter token 刷新失败，可能需要重新授权")

        # 有 access_token 但过期且刷新失败，仍然尝试用（可能还没真正过期）
        if access_token:
            return access_token

        return None

    async def publish(
        self, *, platform, title, content,
        tags=None, images=None, video=None, account=None,
    ) -> PublishResult:
        creds = self._creds(account)
        missing = self._missing(creds, self.REQUIRED_CREDENTIALS)
        if missing:
            return PublishResult(success=False, platform=self.name,
                                 error=f"缺少凭据: {', '.join(missing)}")

        token = await self._get_user_token(creds)
        if not token:
            return PublishResult(
                success=False, platform=self.name,
                error="未授权。请在凭据中填入 access_token + refresh_token + client_id，"
                      "或运行 twitter_auth(client_id) 完成 OAuth2 授权。")

        text = f"{title} {content}".strip() if title else content
        if tags:
            text += " " + " ".join(f"#{t}" for t in tags)
        text = text[:MAX_LEN]

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                TWEETS_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"text": text},
            )
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text}

        if r.status_code in (200, 201) and body.get("data", {}).get("id"):
            return PublishResult(success=True, platform=self.name, output=body["data"]["id"])
        err = body.get("detail") or body.get("title") or str(body)
        return PublishResult(success=False, platform=self.name, error=err)
