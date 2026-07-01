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
