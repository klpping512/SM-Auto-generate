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
