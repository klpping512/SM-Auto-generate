import pytest

import ai_engine
from models import Platform


def test_normalize_hashtags_accepts_model_string_or_list():
    assert ai_engine._normalize_hashtags("#南非物流, #德班港") == ["南非物流", "德班港"]
    assert ai_engine._normalize_hashtags(["#Logistics", "SupplyChain"]) == ["Logistics", "SupplyChain"]


def test_twitter_truncation_keeps_complete_sentence():
    body = "Durban congestion may cause delays. " + "Take action now. " * 30
    shortened = ai_engine._truncate_twitter_body(body)
    assert len(shortened) <= 280
    assert shortened.endswith(".")
    assert not shortened.endswith("…")


def test_unsupported_claim_detection_catches_vague_metrics_and_fake_attribution():
    body = "官方数据显示，延误可能持续数周。"
    warnings = ai_engine._unsupported_claim_warnings(body, "请写港口拥堵提醒")
    assert "输入中未提供的具体时间或数据" in warnings
    assert "输入中未提供来源的报告或官方数据归因" in warnings


def test_platform_format_detection_flags_script_markers_in_douyin_caption():
    # 抖音 body 现在是发布文案：正常种草文案不该报警，含脚本标记才报警
    assert ai_engine._platform_format_warnings("douyin", "普通种草文案，关注我们获取物流干货") == []
    assert ai_engine._platform_format_warnings("douyin", "【画面】港口\n【口播】注意拥堵")


def test_douyin_scene_normalization_falls_back_to_publishable_timeline():
    scenes = ai_engine._normalize_douyin_scenes([], "德班港提醒")
    assert len(scenes) == 5
    assert 25 <= sum(scene["duration"] for scene in scenes) <= 35
    assert all(scene["asset_id"] is None for scene in scenes)


@pytest.mark.asyncio
async def test_content_generation_uses_mimo_json_api(monkeypatch):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": '{"title":"T","body":"B","hashtags":[]}'}}]}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, headers, json): captured.update({"url":url,"headers":headers,"json":json}); return Response()
    monkeypatch.setattr(ai_engine.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ai_engine, "MIMO_API_KEY", "test-key")
    result = await ai_engine.generate_content("主题", "custom", [Platform.FACEBOOK])
    assert result[0].title == "T"
    assert captured["json"]["model"] == "mimo-v2.5"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["api-key"] == "test-key"


@pytest.mark.asyncio
async def test_chat_platforms_return_distinct_platform_native_outputs(monkeypatch):
    monkeypatch.setattr(ai_engine, "MIMO_API_KEY", "")

    outputs = await ai_engine.chat_platforms(
        messages=[{"role": "user", "content": "生成德班港拥堵预警"}],
        platforms=["xiaohongshu", "douyin", "twitter", "facebook"],
        topic="德班港拥堵",
    )
    by_platform = {item["platform"]: item for item in outputs}

    assert list(by_platform) == ["xiaohongshu", "douyin", "twitter", "facebook"]
    assert len({item["body"] for item in outputs}) == 4
    # 抖音 body 是发布文案，不含脚本标记；分镜脚本在 scenes 里
    assert "【画面】" not in by_platform["douyin"]["body"]
    assert "【口播】" not in by_platform["douyin"]["body"]
    assert by_platform["douyin"]["scenes"]
    assert "Breaking" in by_platform["twitter"]["body"]
    assert "Attention" in by_platform["facebook"]["body"]
    assert "最近很多" in by_platform["xiaohongshu"]["body"]
