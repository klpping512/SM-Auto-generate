"""批18：planner 时效感知 + 动线化编排的行为锁定测试。

批17 验收遗留“_event_date_seconds 边界无提交测试”——本文件把 planner 镜像
helper（_event_ts / _event_urgency）的边界与四套动线模板一起锁死。
"""
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import pytest

from hotspot_video_planner import _event_ts, _event_urgency, plan_followup_scenes
from video_composition_policy import source_usage_report


# ---------------------------------------------------------------------------
# J1. 时效边界单测
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2026-07-30T03:51:10+00:00", 1785_383_470),  # ISO 带 UTC 偏移 → 真实 epoch
    ("Tue, 21 Jul 2026 13:00:00 +0200", "rfc2822"),  # RFC2822 带时区（RSS 源常见）
    ("", 0),
    ("  ", 0),
    (None, 0),
    ("1970-01-01T00:00:00", 0),  # generic 常青 1970 哨兵 → 0
    ("not-a-date", 0),
])
def test_event_ts_boundaries(value, expected):
    if expected == "rfc2822":
        assert _event_ts(value) == int(parsedate_to_datetime(value).timestamp())
    else:
        assert _event_ts(value) == expected


# ---------------------------------------------------------------------------
# J2. urgency 分档（镜像批17：<24h +8 / <3d +5 / <7d +2 / ≥30d −3）
# ---------------------------------------------------------------------------

def test_urgency_bands_and_generic_exemption():
    now = datetime.now()

    def ev(days, kind="timely_event"):
        ts = now - timedelta(days=days)
        return {"hook_kind": kind, "parent_published_at": ts.isoformat()}

    assert _event_urgency(ev(0.1)) == 8
    assert _event_urgency(ev(2)) == 5
    assert _event_urgency(ev(5)) == 2
    assert _event_urgency(ev(40)) == -3
    assert _event_urgency(ev(40, "generic_logistics")) == 0  # 常青豁免，不衰减
    assert _event_urgency({}) == 0                            # 缺失无据
    assert _event_urgency(None) == 0                          # None 安全


# ---------------------------------------------------------------------------
# J3. 四套动线模板（fixture 内联，风格同 test_hotspot_logistics_planner.py）
# ---------------------------------------------------------------------------

def _fresh_ts(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def _event(event_id, asset_id, hotspot_id, title, *, days_ago=0.1,
           kind="timely_event", start_ms=0, end_ms=6_000):
    return {
        "id": event_id, "asset_id": asset_id, "hotspot_id": hotspot_id,
        "title_zh": title, "hook_kind": kind,
        "parent_published_at": _fresh_ts(days_ago) if kind == "timely_event" else "1970-01-01T00:00:00",
        "start_ms": start_ms, "end_ms": end_ms,
        "clip_status": "ready", "review_status": "confirmed",
        "keywords": [], "entities": [],
    }


# 注：asset_source 用 "upload"（在 _OWNED_ASSET_SOURCES 白名单内且非 za_stock）。
# "buffalo" 字面值不在白名单，会被 _is_buffalo_usable_source 过滤。
def _owned(segment_id, category, description):
    return {
        "id": segment_id, "asset_id": segment_id, "asset_file_type": "video",
        "asset_source": "upload", "primary_category": category,
        "asset_name": f"{category}-{segment_id}", "description": description,
        "start_ms": 0, "end_ms": 10_000, "quality_score": 0.8, "tags": [],
    }


def _owned_pool(count=6):
    specs = [
        ("warehouse", "仓内货架分拣准备"),
        ("delivery", "车辆进行发运前准备"),
        ("staff", "工作人员检查包裹"),
        ("facility", "仓内设备处理包裹"),
        ("warehouse", "叉车搬运包裹"),
        ("delivery", "拖车等待调度"),
    ]
    return [_owned(index, category, description)
            for index, (category, description) in enumerate(specs[:count], 1)]


def test_template_a_news_flow_fresh_timely_opener():
    """模板 A 新闻动线：新鲜 timely 作开场，热点段 ≤2，顺序 hotspot → owned。"""
    brief = {
        "hotspot_title": "南非边境卡车拥堵", "hotspot_id": 12,
        "logistics_topic": "本地快递时效", "hotspot_type": "infrastructure",
    }
    events = [
        _event(1, 90, 12, "南非边境卡车拥堵，口岸排队", days_ago=40, start_ms=0, end_ms=6_000),
        _event(2, 90, 12, "南非边境卡车拥堵，夜间筛查", days_ago=0.1, start_ms=7_000, end_ms=13_000),
    ]
    scenes = plan_followup_scenes(brief, events, _owned_pool(), target_duration_ms=90_000)

    hotspot_scenes = [scene for scene in scenes if scene["scene_role"] == "hotspot_evidence"]
    assert 1 <= len(hotspot_scenes) <= 2
    opener = scenes[0]
    assert opener["scene_role"] == "hotspot_evidence"
    assert opener["flow_role"] == "opener"
    assert opener["hook_kind"] == "timely_event"
    # 新鲜 timely（event 2）必须压过 40 天旧闻成为开场
    assert int(opener["event_clip_id"]) == 2
    # 动线顺序：热点段在 owned 段之前
    first_owned = next(i for i, s in enumerate(scenes) if s["scene_role"] == "owned_proof")
    assert all(i < first_owned for i, s in enumerate(scenes) if s["scene_role"] == "hotspot_evidence")


def test_template_b_evergreen_flow_generic_opener_no_news_frame():
    """模板 B 常青动线：generic 开场不冒充新闻，文案走常青模板。"""
    brief = {
        "hotspot_title": "跨境订单履约核对", "hotspot_id": 20,
        "logistics_topic": "本地快递时效", "hotspot_type": "ecommerce_growth",
    }
    events = [_event(1, 95, 20, "跨境订单履约核对", kind="generic_logistics")]
    scenes = plan_followup_scenes(brief, events, _owned_pool(), target_duration_ms=90_000)

    opener = scenes[0]
    assert opener["scene_role"] == "hotspot_evidence"
    assert opener["hook_kind"] == "generic_logistics"
    assert opener["flow_role"] == "opener"
    assert "现场正在发生" not in opener["voiceover"]
    assert "为背景" in opener["voiceover"]


def test_template_c_cross_parent_escalation_midroll():
    """模板 C 异源中段递进：父B fresh 事件在首个 owned 段之后出现，文案分开表述。"""
    brief = {
        "hotspot_title": "南非边境卡车拥堵", "hotspot_id": 12,
        "logistics_topic": "本地快递时效", "hotspot_type": "infrastructure",
    }
    events = [
        _event(1, 90, 12, "南非边境卡车拥堵，口岸排队", days_ago=0.1, start_ms=0, end_ms=6_000),
        _event(2, 90, 12, "南非边境卡车拥堵，夜间筛查", days_ago=0.2, start_ms=7_000, end_ms=13_000),
        _event(3, 91, 13, "南非边境卡车拥堵，另一口岸车辆排队", days_ago=0.1),
    ]
    scenes = plan_followup_scenes(brief, events, _owned_pool(), target_duration_ms=90_000)

    escalation = next(
        (scene for scene in scenes
         if scene["scene_role"] == "hotspot_evidence" and scene["flow_role"] == "escalation"),
        None,
    )
    assert escalation is not None
    assert int(escalation["event_clip_id"]) == 3
    # 异源事件必须出现在首个 owned 证明段之后（mid-roll，不混在片头）
    first_owned = next(i for i, s in enumerate(scenes) if s["scene_role"] == "owned_proof")
    escalation_index = next(i for i, s in enumerate(scenes) if s is escalation)
    assert escalation_index > first_owned
    # 文案与开场分开表述，防混源
    assert "除此之外" in escalation["voiceover"]

    # guardrail 回归：素材去重门禁 + 真实视频每段 ≥3s
    usage = source_usage_report(scenes)
    assert usage["passed"]
    assert all(
        int(scene["duration_ms"]) >= 3_000
        for scene in scenes if scene["evidence_type"] in {"hotspot_video", "owned_video"}
    )


def test_template_d_thin_inventory_image_bridges():
    """模板 D 薄库存：1 热点 + 1 Buffalo + allow_adaptation → 图片桥插入。"""
    brief = {
        "hotspot_title": "道路受阻", "hotspot_id": 9,
        "logistics_topic": "路线稳定性", "hotspot_type": "infrastructure",
    }
    events = [_event(1, 80, 9, "道路受阻现场", days_ago=0.5)]
    owned = [_owned(2, "warehouse", "仓内备货准备")]
    images = [
        {"id": 31, "asset_id": 31, "file_type": "image", "asset_file_type": "image",
         "primary_category": "warehouse", "source": "upload", "name": "Buffalo 货架"},
        {"id": 32, "asset_id": 32, "file_type": "image", "asset_file_type": "image",
         "primary_category": "delivery", "source": "upload", "name": "Buffalo 配送车"},
    ]
    scenes = plan_followup_scenes(
        brief, events, owned, target_duration_ms=50_000,
        owned_images=images, allow_adaptation=True,
    )

    assert any(scene["evidence_type"] == "image" for scene in scenes)
    assert sum(scene["scene_role"] == "hotspot_evidence" for scene in scenes) == 1


def test_adaptive_image_bridges_fill_a_video_duration_gap_after_four_segments():
    """视频段达到 4 段但仍不足正式时长时，图片仍可作为受控补位。"""
    brief = {
        "hotspot_title": "港口作业变化", "hotspot_id": 10,
        "logistics_topic": "跨境履约准备", "hotspot_type": "infrastructure",
    }
    events = [_event(1, 81, 10, "港口作业变化", end_ms=6_000)]
    owned = [
        _owned(index, category, f"Buffalo {category}现场动作 {index}")
        for index, category in enumerate(("warehouse", "delivery", "staff", "facility"), 2)
    ]
    images = [
        {"id": 100 + index, "asset_id": 100 + index, "file_type": "image",
         "asset_file_type": "image", "primary_category": "warehouse",
            "source": "local_directory", "name": f"Buffalo 仓内图片 {index}"}
        for index in range(1, 13)
    ]

    scenes = plan_followup_scenes(
        brief, events, owned, target_duration_ms=50_000,
        owned_images=images, allow_adaptation=True,
    )

    image_scenes = [scene for scene in scenes if scene["evidence_type"] == "image"]
    assert image_scenes
    assert len(image_scenes) <= 12
    assert all(int(scene["duration_ms"]) == 2_000 for scene in image_scenes)
    assert sum(int(scene["duration_ms"]) for scene in scenes) >= 50_000
    assert sum(scene["scene_role"] == "hotspot_evidence" for scene in scenes) == 1
    assert all(scene["scene_role"] == "owned_context_image" for scene in image_scenes)


def test_adaptive_image_bridges_do_not_pad_a_video_plan_that_already_reaches_target():
    """视频实际时长已达目标时，不无故插入静态图片。"""
    brief = {
        "hotspot_title": "仓内履约准备", "hotspot_id": 11,
        "logistics_topic": "跨境履约准备", "hotspot_type": "warehouse",
    }
    events = [_event(1, 82, 11, "仓内履约准备", end_ms=60_000)]
    owned = _owned_pool(6) + [_owned(8, "facility", "Buffalo 装车复核动作")]
    for segment in owned:
        segment["end_ms"] = 20_000
    images = [
        {"id": 200, "asset_id": 200, "file_type": "image",
         "asset_file_type": "image", "primary_category": "warehouse",
         "source": "local_directory", "name": "Buffalo 仓内图片"},
    ]

    scenes = plan_followup_scenes(
        brief, events, owned, target_duration_ms=50_000,
        owned_images=images, allow_adaptation=True,
    )

    assert not any(scene["evidence_type"] == "image" for scene in scenes)


def test_opener_differs_across_fresh_stale_generic_inputs():
    """验收 4：同一 brief，fresh / 过期 timely / 仅 generic 三种输入 opener 不同。"""
    brief = {
        "hotspot_title": "南非边境卡车拥堵", "hotspot_id": 12,
        "logistics_topic": "本地快递时效", "hotspot_type": "infrastructure",
    }
    fresh = plan_followup_scenes(
        brief, [_event(1, 90, 12, "南非边境卡车拥堵，口岸排队", days_ago=0.1)],
        _owned_pool(), target_duration_ms=90_000,
    )
    stale = plan_followup_scenes(
        brief, [_event(2, 90, 12, "南非边境卡车拥堵，口岸排队", days_ago=40)],
        _owned_pool(), target_duration_ms=90_000,
    )
    generic = plan_followup_scenes(
        brief, [_event(3, 90, 12, "南非边境卡车拥堵，口岸排队", kind="generic_logistics")],
        _owned_pool(), target_duration_ms=90_000,
    )

    assert fresh[0]["voiceover"].startswith("现场正在发生")
    # 过期 timely / generic 开场不得用新闻框架（两者共用常青模板，靠标记区分）
    assert "现场正在发生" not in stale[0]["voiceover"]
    assert "为背景" in stale[0]["voiceover"]
    assert "现场正在发生" not in generic[0]["voiceover"]
    assert stale[0]["hook_kind"] == "timely_event"
    assert generic[0]["hook_kind"] == "generic_logistics"
    assert fresh[0]["voiceover"] != stale[0]["voiceover"]
