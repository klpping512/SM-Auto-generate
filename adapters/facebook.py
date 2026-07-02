"""Facebook 主页发文：Graph API /{page_id}/feed。"""
import logging

from adapters.api_base import ApiAdapter
from adapters.base import PublishResult

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"


class FacebookAdapter(ApiAdapter):
    name = "facebook"
    REQUIRED_CREDENTIALS = ["page_id", "page_access_token"]

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
