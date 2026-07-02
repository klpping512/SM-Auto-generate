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
