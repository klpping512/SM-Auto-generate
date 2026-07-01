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
