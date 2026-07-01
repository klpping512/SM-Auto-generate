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
