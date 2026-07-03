import json
from adapters.rpa_base import parse_cookies, build_credentials, playwright_proxy_from_env


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


def test_playwright_proxy_from_env_supports_auth_and_priority():
    proxy = playwright_proxy_from_env({
        "HTTP_PROXY": "http://fallback:7890",
        "RPA_PROXY": "socks5://user:p%40ss@127.0.0.1:7891",
    })
    assert proxy == {
        "server": "socks5://127.0.0.1:7891",
        "username": "user",
        "password": "p@ss",
    }


def test_playwright_proxy_from_env_empty():
    assert playwright_proxy_from_env({}) is None
