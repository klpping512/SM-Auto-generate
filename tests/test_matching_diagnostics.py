"""Matching diagnostics: observation-only funnel + admin endpoint."""
from __future__ import annotations

import copy

import auth
import hotspot_event_matching
import hotspot_video_planner


def _seg(
    segment_id: int,
    asset_id: int,
    category: str,
    *,
    file_type: str = "video",
    source: str | None = "upload",
    description: str = "",
    quality: float = 0.8,
    tags: list | None = None,
) -> dict:
    return {
        "id": segment_id,
        "asset_id": asset_id,
        "primary_category": category,
        "asset_file_type": file_type,
        "asset_source": source,
        "description": description or f"{category} scene",
        "quality_score": quality,
        "tags": tags or [],
    }


def _customs_brief() -> dict:
    return {
        "topic_brief_id": "diag-test",
        "logistics_topic": "清关风险",
        "logistics_nodes": ["清关"],
    }


def test_diagnose_empty_pool():
    brief = _customs_brief()
    diag = hotspot_video_planner.diagnose_owned_matching(
        [_seg(1, 1, "warehouse", file_type="image")],
        brief,
    )
    assert diag["verdict"] == "empty_pool"
    assert diag["funnel"]["is_video"] == 0
    assert diag["funnel"]["after_dedup"] == 0


def test_diagnose_category_mismatch_inventory():
    # 放闸(preparation 模式)后：customs 节点 eligible 集扩到
    # {customs,warehouse,delivery}，warehouse 库存不再是 mismatch；
    # 仍未放闸的 staff/facility 保持 mismatch 判定。
    brief = _customs_brief()
    segments = [
        _seg(1, 10, "staff", description="分拣人员"),
        _seg(2, 11, "staff", description="检查作业"),
        _seg(3, 12, "facility", description="园区设施"),
    ]
    diag = hotspot_video_planner.diagnose_owned_matching(segments, brief)
    assert diag["verdict"] == "category_mismatch"
    assert diag["funnel"]["not_licensed_stock"] == 3
    assert diag["funnel"]["category_match"] == 0
    assert diag["category_inventory"] == {"staff": 2, "facility": 1}
    assert len(diag["dropped_by_category_mismatch"]) == 3
    assert set(diag["eligible_categories"] or []) == {"customs", "warehouse", "delivery"}


def test_diagnose_gate_open_admits_warehouse_inventory_for_customs_brief():
    # 同一条清关 brief，warehouse 库存在放闸后直接过漏斗，verdict 升级。
    brief = _customs_brief()
    segments = [
        _seg(1, 10, "warehouse", description="仓内分拣"),
        _seg(2, 11, "warehouse", description="货架通道"),
        _seg(3, 12, "warehouse", description="入库作业"),
    ]
    diag = hotspot_video_planner.diagnose_owned_matching(segments, brief)
    assert diag["verdict"] == "thin_but_matched"
    assert diag["funnel"]["category_match"] == 3
    assert diag["funnel"]["after_dedup"] == 3
    assert not diag.get("dropped_by_category_mismatch")


def test_diagnose_thin_but_matched():
    brief = _customs_brief()
    segments = [
        _seg(1, 21, "customs", description="报关现场"),
        _seg(2, 22, "customs", description="海关查验"),
    ]
    diag = hotspot_video_planner.diagnose_owned_matching(segments, brief)
    assert diag["verdict"] == "thin_but_matched"
    assert diag["funnel"]["after_dedup"] == 2


def test_diagnose_healthy():
    brief = _customs_brief()
    segments = [
        _seg(i, 100 + i, "customs", description=f"清关{i}")
        for i in range(1, 6)
    ]
    diag = hotspot_video_planner.diagnose_owned_matching(segments, brief)
    assert diag["verdict"] == "healthy"
    assert diag["funnel"]["after_dedup"] == 5


def test_diagnose_zero_side_effect_on_owned_candidates():
    brief = _customs_brief()
    segments = [
        _seg(1, 1, "customs"),
        _seg(2, 2, "warehouse"),
        _seg(3, 3, "customs", source="stock_library"),
        _seg(4, 4, "customs", file_type="image"),
        _seg(5, 1, "customs"),  # same asset, dedup
    ]
    before = copy.deepcopy(segments)
    candidates_before = hotspot_video_planner._owned_candidates(segments, brief)
    diag = hotspot_video_planner.diagnose_owned_matching(segments, brief)
    candidates_after = hotspot_video_planner._owned_candidates(segments, brief)

    assert segments == before
    assert candidates_before == candidates_after
    assert diag["funnel"]["after_dedup"] == len(candidates_before)
    assert [item["id"] for item in candidates_before] == [item["id"] for item in candidates_after]


def test_diagnose_starving_side_hotspot_vs_owned():
    assert hotspot_video_planner.diagnose_starving_side(
        owned_pool=5, hotspot_pool=0,
    )["starving_side"] == "hotspot"
    assert hotspot_video_planner.diagnose_starving_side(
        owned_pool=2, hotspot_pool=3,
    )["starving_side"] == "owned"
    assert hotspot_video_planner.diagnose_starving_side(
        owned_pool=4, hotspot_pool=2,
    )["starving_side"] == "none"


def test_diagnose_event_matching_no_overlap():
    event = {
        "id": 9,
        "hotspot_id": 1,
        "title_zh": "Musina 边境卡车排队清关",
        "title_en": "Musina border truck queue customs",
        "keywords": ["清关", "边境", "卡车"],
    }
    segments = [
        {
            "id": 1, "asset_hotspot_id": None, "primary_category": "warehouse",
            "description": "约翰内斯堡仓内货架整理", "transcript": "", "ocr_text": "",
        },
        {
            "id": 2, "asset_hotspot_id": None, "primary_category": "staff",
            "description": "团队开会培训", "transcript": "", "ocr_text": "",
        },
    ]
    diag = hotspot_event_matching.diagnose_event_matching(event, segments)
    assert diag["verdict"] == "no_overlap"
    assert diag["owned_pool"] == 2
    assert diag["wanted_terms"]
    assert diag["near_misses"]
    assert all(row["overlap"] == [] for row in diag["near_misses"])


def test_count_matching_hotspot_hooks_uses_lexicon():
    brief = _customs_brief()
    events = [
        {
            "id": 1, "review_status": "confirmed", "clip_status": "ready",
            "title_zh": "口岸清关排队", "title_en": "", "keywords": ["清关"],
        },
        {
            "id": 2, "review_status": "confirmed", "clip_status": "ready",
            "title_zh": "仓内分拣", "title_en": "", "keywords": ["仓库"],
        },
        {
            "id": 3, "review_status": "rejected", "clip_status": "ready",
            "title_zh": "清关现场", "title_en": "", "keywords": ["清关"],
        },
    ]
    assert hotspot_video_planner.count_matching_hotspot_hooks(brief, events) >= 1


def _login(tmp_db, username: str, role: str):
    from fastapi.testclient import TestClient
    import app

    tmp_db.create_user(username, auth.hash_password("pw12345"), role, username)
    client = TestClient(app.app)
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pw12345"}
    ).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_diagnostics_endpoint_admin_ok_editor_denied(tmp_db):
    admin, admin_headers = _login(tmp_db, "diag-admin", "admin")
    editor, editor_headers = _login(tmp_db, "diag-editor", "editor")

    denied = editor.get(
        "/api/diagnostics/owned-matching",
        params={"topic": "清关"},
        headers=editor_headers,
    )
    assert denied.status_code == 403

    ok = admin.get(
        "/api/diagnostics/owned-matching",
        params={"topic": "清关"},
        headers=admin_headers,
    )
    assert ok.status_code == 200
    body = ok.json()
    assert "funnel" in body["diagnostics"]
    assert "verdict" in body["diagnostics"]
    assert body["starving_side"] in {"hotspot", "owned", "none"}
    assert "hotspot_pool" in body
    assert "owned_pool" in body


def test_deprecated_assets_are_excluded_from_owned_candidates():
    # 已砍旧频道的降权素材（deprecated=1）不得进匹配池，但文件保留可回滚
    brief = _customs_brief()
    segments = [
        _seg(1, 1, "customs", description="清关正常素材"),
        {**_seg(2, 2, "customs", description="SABC 旧频道垃圾素材"), "asset_deprecated": 1},
    ]
    candidates = hotspot_video_planner._owned_candidates(segments, brief)
    assert [item["id"] for item in candidates] == [1]


def test_non_deprecated_assets_stay_in_owned_candidates():
    # deprecated=0 与未标注（现役频道）不受影响
    brief = _customs_brief()
    segments = [
        {**_seg(1, 1, "customs", description="eNCA 现役素材"), "asset_deprecated": 0},
        _seg(2, 2, "customs", description="未标注降权列的素材"),
    ]
    candidates = hotspot_video_planner._owned_candidates(segments, brief)
    assert [item["id"] for item in candidates] == [1, 2]


def test_diagnose_excludes_deprecated_assets():
    # 批13 清洗：deprecated 素材不得出现在诊断输出（与 _owned_candidates 同闸门）
    import json
    brief = _customs_brief()
    segments = [
        _seg(1, 1, "customs", description="清关正常素材"),
        {**_seg(2, 2, "customs", description="已下线垃圾素材"), "asset_deprecated": 1},
    ]
    diag = hotspot_video_planner.diagnose_owned_matching(segments, brief)
    blob = json.dumps(diag, ensure_ascii=False, default=str)
    assert '"asset_id": 2,' not in blob and '"asset_id": 2}' not in blob


def _owned_seg(segment_id, asset_id, category, *, source="upload", usage=0, last_used=None, quality=0.8, description=""):
    item = _seg(segment_id, asset_id, category, source=source, quality=quality, description=description or f"{category} scene")
    item["asset_usage_count"] = usage
    if last_used:
        item["asset_last_used_at"] = last_used
    return item


def test_owned_candidates_penalize_high_usage_assets():
    # 批13 D4：同质量下，使用次数多的素材排名下降（封顶 5）
    brief = _customs_brief()
    segments = [
        _owned_seg(1, 1, "customs", usage=10, description="老素材用很多次"),
        _owned_seg(2, 2, "customs", usage=0, description="新素材没用过"),
    ]
    candidates = hotspot_video_planner._owned_candidates(segments, brief)
    assert [item["asset_id"] for item in candidates] == [2, 1]


def test_owned_candidates_cooldown_penalizes_recently_used():
    # 批13 D4：24h 内刚用过的素材再罚一档，排在同样用过但已久远的之后
    from datetime import datetime, timezone
    brief = _customs_brief()
    recent = datetime.now(timezone.utc).isoformat()
    segments = [
        _owned_seg(1, 1, "customs", usage=1, last_used=recent, description="刚刚用过"),
        _owned_seg(2, 2, "customs", usage=1, description="用过一次但很久前"),
    ]
    candidates = hotspot_video_planner._owned_candidates(segments, brief)
    assert [item["asset_id"] for item in candidates] == [2, 1]


def _plan_owned_seg(segment_id, asset_id, category, source, description):
    return {"id": segment_id, "asset_id": asset_id, "asset_file_type": "video",
            "asset_source": source, "primary_category": category,
            "asset_name": description, "description": description,
            "start_ms": 0, "end_ms": 8000, "quality_score": 0.8, "tags": []}


def test_zastock_supplement_fills_customs_gap_capped_at_two():
    # 批13 B：清关 topic、buffalo 无 customs → za_stock 补缺口，硬上限 2
    from hotspot_video_planner import plan_followup_scenes
    brief = {"topic_brief_id": 1, "logistics_topic": "跨境清关", "logistics_nodes": ["清关"]}
    owned = []
    idx = 1
    for cat, name in [("warehouse", "仓储作业A"), ("delivery", "配送作业A"), ("warehouse", "仓储作业B"),
                      ("delivery", "配送作业B"), ("warehouse", "仓储作业C"), ("delivery", "配送作业C")]:
        owned.append(_plan_owned_seg(idx, idx, cat, "upload", name)); idx += 1
    za_ids = set()
    for j in range(3):
        aid = 900 + j; za_ids.add(aid)
        owned.append(_plan_owned_seg(100 + j, aid, "customs", "za_stock_license", f"清关现场{j}"))
    scenes = plan_followup_scenes(brief, [], owned)
    za_scenes = [s for s in scenes if s.get("asset_id") in za_ids]
    assert 1 <= len(za_scenes) <= 2


def test_zastock_does_not_displace_buffalo_when_coverage_sufficient():
    # 批13 B：buffalo 已覆盖清关类目 → za_stock 不挤占
    from hotspot_video_planner import plan_followup_scenes
    brief = {"topic_brief_id": 1, "logistics_topic": "跨境清关", "logistics_nodes": ["清关"]}
    owned = []
    idx = 1
    for cat, name in [("warehouse", "仓储作业A"), ("delivery", "配送作业A"), ("customs", "自有清关现场A"),
                      ("warehouse", "仓储作业B"), ("delivery", "配送作业B"), ("customs", "自有清关现场B"),
                      ("warehouse", "仓储作业C"), ("delivery", "配送作业C")]:
        owned.append(_plan_owned_seg(idx, idx, cat, "upload", name)); idx += 1
    za_ids = set()
    for j in range(3):
        aid = 900 + j; za_ids.add(aid)
        owned.append(_plan_owned_seg(100 + j, aid, "customs", "za_stock_license", f"清关现场{j}"))
    scenes = plan_followup_scenes(brief, [], owned)
    za_scenes = [s for s in scenes if s.get("asset_id") in za_ids]
    assert len(za_scenes) == 0
