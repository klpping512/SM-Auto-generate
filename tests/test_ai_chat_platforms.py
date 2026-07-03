import pytest

import ai_engine


def test_normalize_hashtags_accepts_model_string_or_list():
    assert ai_engine._normalize_hashtags("#南非物流, #德班港") == ["南非物流", "德班港"]
    assert ai_engine._normalize_hashtags(["#Logistics", "SupplyChain"]) == ["Logistics", "SupplyChain"]


def test_twitter_truncation_keeps_complete_sentence():
    body = "Durban congestion may cause delays. " + "Take action now. " * 30
    shortened = ai_engine._truncate_twitter_body(body)
    assert len(shortened) <= 280
    assert shortened.endswith(".")
    assert not shortened.endswith("…")


@pytest.mark.asyncio
async def test_chat_platforms_return_distinct_platform_native_outputs(monkeypatch):
    monkeypatch.setattr(ai_engine, "DEEPSEEK_API_KEY", "")

    outputs = await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "生成德班港拥堵预警"}],
        platforms=["xiaohongshu", "douyin", "twitter", "facebook"],
        topic="德班港拥堵",
    )
    by_platform = {item["platform"]: item for item in outputs}

    assert list(by_platform) == ["xiaohongshu", "douyin", "twitter", "facebook"]
    assert len({item["body"] for item in outputs}) == 4
    assert "【画面】" in by_platform["douyin"]["body"]
    assert "【口播】" in by_platform["douyin"]["body"]
    assert "Breaking" in by_platform["twitter"]["body"]
    assert "Attention" in by_platform["facebook"]["body"]
    assert "最近很多" in by_platform["xiaohongshu"]["body"]
