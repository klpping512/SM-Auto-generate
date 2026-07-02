"""X(Twitter) 发推：API v2 POST /2/tweets，OAuth2 用户 token（tweet.write）。"""
import logging

from adapters.api_base import ApiAdapter
from adapters.base import PublishResult

logger = logging.getLogger(__name__)

TWEETS_URL = "https://api.x.com/2/tweets"
MAX_LEN = 280


class TwitterAdapter(ApiAdapter):
    name = "twitter"
    REQUIRED_CREDENTIALS = ["access_token"]

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
