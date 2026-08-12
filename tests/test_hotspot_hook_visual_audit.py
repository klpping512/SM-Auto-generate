"""Tests for multi-frame hotspot Hook visual critic gates."""
from __future__ import annotations

import json

import pytest


def _port_accept_payload(**overrides):
    payload = {
        "accepted": True,
        "scene_type": "port",
        "visible_objects": ["集装箱", "吊机", "卡车"],
        "visible_actions": ["装卸作业"],
        "is_title_or_logo_card": False,
        "is_anchor_or_studio": False,
        "is_map_or_infographic": False,
        "supports_visible_event": True,
        "reason": "三帧均为港口作业现场",
    }
    payload.update(overrides)
    return payload


def _title_card_payload():
    return {
        "accepted": True,
        "scene_type": "port",
        "visible_objects": ["文字标题", "品牌Logo"],
        "visible_actions": [],
        "is_title_or_logo_card": True,
        "is_anchor_or_studio": False,
        "is_map_or_infographic": False,
        "supports_visible_event": True,
        "reason": "标题卡",
    }


def test_compute_frame_offsets_keeps_three_distinct_points_for_short_clips():
    import hotspot_hook_visual_audit as visual

    offsets = visual.compute_frame_offsets_ms(0, 4_000)
    assert len(offsets) == 3
    assert len(set(offsets)) == 3
    assert offsets[0] < offsets[1] < offsets[2]


def test_title_card_visual_payload_is_rejected_even_if_accepted_flag_true():
    import hotspot_hook_visual_audit as visual

    ok, flags = visual._decision_from_payload(_title_card_payload())
    assert ok is False
    assert flags["is_title_or_logo_card"] is True


def test_port_operation_payload_is_accepted():
    import hotspot_hook_visual_audit as visual

    ok, flags = visual._decision_from_payload(_port_accept_payload())
    assert ok is True
    assert flags["scene_type"] == "port"


def test_missing_source_video_rejects_without_confirming(tmp_db, monkeypatch):
    import hotspot_hook_visual_audit as visual

    monkeypatch.setattr(visual.model_router, "key_is_available", lambda _role: True)
    hooks = [{
        "event_index": 1,
        "start_ms": 0,
        "end_ms": 8_000,
        "review_status": "review_required",
        "evidence": {"what_happened": "港口作业"},
    }]
    accepted, meta = visual.audit_hooks(305, hooks, static_root=None, source_video_path=None)
    assert accepted == []
    assert meta["status"] == "rejected_all"
    assert hooks[0]["evidence"]["visual_audit"]["status"] == "rejected"
    assert hooks[0]["review_status"] == "review_required"


def test_corrupt_or_missing_frames_reject(tmp_path, tmp_db, monkeypatch):
    import hotspot_hook_visual_audit as visual

    video = tmp_path / "mother.mp4"
    video.write_bytes(b"not-a-real-video")
    monkeypatch.setattr(visual.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(
        visual,
        "_extract_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cannot decode")),
    )
    hooks = [{
        "event_index": 1,
        "start_ms": 0,
        "end_ms": 8_000,
        "review_status": "review_required",
        "evidence": {},
    }]
    accepted, meta = visual.audit_hooks(1, hooks, source_video_path=video)
    assert accepted == []
    assert meta["accepted_count"] == 0
    assert hooks[0]["evidence"]["visual_audit"]["status"] == "rejected"
    assert "帧提取失败" in hooks[0]["evidence"]["visual_audit"]["reason"]


def test_empty_or_illegal_json_rejects_without_text_critic(tmp_path, tmp_db, monkeypatch):
    import hotspot_hook_visual_audit as visual

    video = tmp_path / "mother.mp4"
    video.write_bytes(b"fake")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpeg-bytes-for-hash-long-enough")

    monkeypatch.setattr(visual.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(visual.model_router, "create_budget", lambda *_a, **_k: None)
    monkeypatch.setattr(visual.model_router, "route_scoped_job_id", lambda job, _role: job)
    monkeypatch.setattr(visual.model_router, "required_output_budget", lambda *_a, **_k: 900)
    monkeypatch.setattr(visual.model_router, "get_route", lambda _role: {"model": "mimo-v2.5"})
    monkeypatch.setattr(
        visual,
        "_extract_frames",
        lambda *_a, **_k: [
            {"offset_ms": 400, "path": frame, "sha256": "a" * 64},
            {"offset_ms": 4000, "path": frame, "sha256": "b" * 64},
            {"offset_ms": 7600, "path": frame, "sha256": "c" * 64},
        ],
    )

    async def empty_call(*_a, **_k):
        return {"content": "", "cache_hit": False}

    monkeypatch.setattr(visual.model_router, "call_multimodal_json", empty_call)
    ok, evidence = visual.audit_single_hook(1, {"event_index": 1, "start_ms": 0, "end_ms": 8000}, video)
    assert ok is False
    assert evidence["status"] == "rejected"

    async def illegal_call(*_a, **_k):
        return {"content": "not-json", "cache_hit": False}

    monkeypatch.setattr(visual.model_router, "call_multimodal_json", illegal_call)
    ok, evidence = visual.audit_single_hook(1, {"event_index": 1, "start_ms": 0, "end_ms": 8000}, video)
    assert ok is False
    assert evidence["status"] == "rejected"


def test_accept_writes_three_frame_hashes_into_evidence(tmp_path, tmp_db, monkeypatch):
    import hotspot_hook_visual_audit as visual

    video = tmp_path / "mother.mp4"
    video.write_bytes(b"fake")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpeg-bytes-for-hash-long-enough")
    monkeypatch.setattr(visual.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(visual.model_router, "create_budget", lambda *_a, **_k: None)
    monkeypatch.setattr(visual.model_router, "route_scoped_job_id", lambda job, _role: job)
    monkeypatch.setattr(visual.model_router, "required_output_budget", lambda *_a, **_k: 900)
    monkeypatch.setattr(visual.model_router, "get_route", lambda _role: {"model": "mimo-v2.5"})
    monkeypatch.setattr(
        visual,
        "_extract_frames",
        lambda *_a, **_k: [
            {"offset_ms": 400, "path": frame, "sha256": "a" * 64},
            {"offset_ms": 4000, "path": frame, "sha256": "b" * 64},
            {"offset_ms": 7600, "path": frame, "sha256": "c" * 64},
        ],
    )

    async def ok_call(*_a, **_k):
        return {"content": json.dumps(_port_accept_payload(), ensure_ascii=False), "cache_hit": False}

    monkeypatch.setattr(visual.model_router, "call_multimodal_json", ok_call)
    hooks = [{"event_index": 1, "start_ms": 0, "end_ms": 8000, "evidence": {}}]
    accepted, meta = visual.audit_hooks(7, hooks, source_video_path=video)
    assert len(accepted) == 1
    assert meta["status"] == "verified"
    evidence = accepted[0]["evidence"]["visual_audit"]
    assert evidence["status"] == "accepted"
    assert evidence["prompt_version"] == visual.VISUAL_AUDIT_PROMPT_VERSION
    assert evidence["frame_sha256"] == ["a" * 64, "b" * 64, "c" * 64]
    assert evidence["frame_offsets_ms"] == [400, 4000, 7600]


def test_curator_requires_visual_before_text_and_never_confirms_on_visual_reject(tmp_db, monkeypatch):
    import hotspot_hook_curator
    import hotspot_hook_visual_audit as visual

    text_calls = []

    async def fake_text(*_args, **kwargs):
        text_calls.append(kwargs.get("prompt_version"))
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": [{"candidate_index": 1, "reason": "ok"}]}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "南非港口标题卡",
            "start_segment_index": 0, "end_segment_index": 1,
            "title_zh": "南非港口现场作业画面",
            "what_happened": "港口吊机正在装卸集装箱。",
            "hook_reason": "作业画面清晰",
            "logistics_question": "港口拥堵时如何改线？",
            "confidence": 0.9,
        }]}), "cache_hit": False}

    def fake_visual(asset_id, hooks, **_kwargs):
        for hook in hooks:
            evidence = dict(hook.get("evidence") or {})
            evidence["visual_audit"] = {
                "status": "rejected",
                "prompt_version": visual.VISUAL_AUDIT_PROMPT_VERSION,
                "scene_type": "non_event",
                "is_title_or_logo_card": True,
                "frame_offsets_ms": [400, 4000, 7200],
                "frame_sha256": ["x", "y", "z"],
                "visible_objects": ["标题文字"],
                "visible_actions": [],
            }
            hook["evidence"] = evidence
            hook["review_status"] = "review_required"
        return [], {"status": "rejected_all", "accepted_count": 0}

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_a, **_k: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_text)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})
    monkeypatch.setattr(hotspot_hook_curator.hotspot_hook_visual_audit, "audit_hooks", fake_visual)

    segments = [
        {"id": 1, "segment_index": 0, "start_ms": 0, "end_ms": 5_000,
         "description": "标题卡", "transcript": "", "ocr_text": "", "tags": []},
        {"id": 2, "segment_index": 1, "start_ms": 5_000, "end_ms": 10_000,
         "description": "标题卡续", "transcript": "", "ocr_text": "", "tags": []},
    ]
    hooks, meta = hotspot_hook_curator.curate_hook_clips(
        305, "南非港口 Transport Month", segments, source_video_path="/tmp/missing.mp4",
    )
    assert hooks == []
    assert meta["status"] == "no_qualified_hooks"
    assert meta["visual_audit"]["status"] == "rejected_all"
    assert hotspot_hook_curator.AUDIT_PROMPT_VERSION not in text_calls


def test_curator_confirms_only_after_visual_and_text_accept(tmp_db, monkeypatch, tmp_path):
    import hotspot_hook_curator
    import hotspot_hook_visual_audit as visual

    async def fake_text(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": [{"candidate_index": 1, "reason": "画面与来源一致"}]}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "港口吊机装卸集装箱",
            "start_segment_index": 0, "end_segment_index": 1,
            "title_zh": "港口吊机作业",
            "what_happened": "吊机正在装卸集装箱，货车排队等待。",
            "hook_reason": "连续作业动作清晰",
            "logistics_question": "港口作业变慢时如何调整到仓计划？",
            "confidence": 0.91,
        }]}), "cache_hit": False}

    def fake_visual(asset_id, hooks, **_kwargs):
        for hook in hooks:
            evidence = dict(hook.get("evidence") or {})
            evidence["visual_audit"] = {
                "status": "accepted",
                "prompt_version": visual.VISUAL_AUDIT_PROMPT_VERSION,
                "scene_type": "port",
                "frame_offsets_ms": [400, 5000, 9600],
                "frame_sha256": ["a" * 64, "b" * 64, "c" * 64],
                "visible_objects": ["吊机", "集装箱"],
                "visible_actions": ["装卸"],
                "model": "mimo-v2.5",
                "cache_hit": False,
            }
            hook["evidence"] = evidence
        return hooks, {"status": "verified", "accepted_count": len(hooks)}

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_a, **_k: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_text)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})
    monkeypatch.setattr(hotspot_hook_curator.hotspot_hook_visual_audit, "audit_hooks", fake_visual)

    segments = [
        {"id": 1, "segment_index": 0, "start_ms": 0, "end_ms": 5_000,
         "description": "吊机装卸集装箱", "transcript": "", "ocr_text": "",
         "tags": [{"dimension": "object", "value": "吊机"}]},
        {"id": 2, "segment_index": 1, "start_ms": 5_000, "end_ms": 10_000,
         "description": "货车排队", "transcript": "", "ocr_text": "",
         "tags": [{"dimension": "object", "value": "卡车"}]},
    ]
    video = tmp_path / "port.mp4"
    video.write_bytes(b"fake")
    hooks, meta = hotspot_hook_curator.curate_hook_clips(
        7, "南非港口作业", segments, source_video_path=video,
    )
    assert len(hooks) == 1
    assert hooks[0]["review_status"] == "confirmed"
    assert hooks[0]["evidence"]["visual_audit"]["status"] == "accepted"
    assert hooks[0]["evidence"]["text_audit"]["status"] == "accepted"
    assert hooks[0]["evidence"]["text_audit"]["prompt_version"] == "hotspot-hook-grounding-audit-v6"
    assert meta["status"] == "curated"


def test_visual_accept_but_text_reject_yields_zero_hooks(tmp_db, monkeypatch, tmp_path):
    import hotspot_hook_curator
    import hotspot_hook_visual_audit as visual

    async def fake_text(*_args, **kwargs):
        if kwargs["prompt_version"] == hotspot_hook_curator.AUDIT_PROMPT_VERSION:
            return {"content": json.dumps({"accepted": []}), "cache_hit": False}
        return {"content": json.dumps({"hooks": [{
            "event_identity": "道路中断",
            "start_segment_index": 0, "end_segment_index": 0,
            "title_zh": "干线拥堵",
            "what_happened": "卡车在道路上排队。",
            "hook_reason": "拥堵画面",
            "logistics_question": "如何改线？",
            "confidence": 0.8,
        }]}), "cache_hit": False}

    def fake_visual(asset_id, hooks, **_kwargs):
        for hook in hooks:
            evidence = dict(hook.get("evidence") or {})
            evidence["visual_audit"] = {
                "status": "accepted",
                "prompt_version": visual.VISUAL_AUDIT_PROMPT_VERSION,
                "scene_type": "road",
                "frame_offsets_ms": [400, 2500, 4600],
                "frame_sha256": ["a", "b", "c"],
                "visible_objects": ["卡车"],
                "visible_actions": ["排队"],
            }
            hook["evidence"] = evidence
        return hooks, {"status": "verified", "accepted_count": len(hooks)}

    monkeypatch.setattr(hotspot_hook_curator.model_router, "key_is_available", lambda _role: True)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "create_budget", lambda *_a, **_k: None)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "call_text", fake_text)
    monkeypatch.setattr(hotspot_hook_curator.model_router, "get_route", lambda _role: {"model": "mimo-test"})
    monkeypatch.setattr(hotspot_hook_curator.hotspot_hook_visual_audit, "audit_hooks", fake_visual)

    segments = [{
        "id": 1, "segment_index": 0, "start_ms": 0, "end_ms": 5_000,
        "description": "卡车排队", "transcript": "", "ocr_text": "", "tags": [],
    }]
    (tmp_path / "x.mp4").write_bytes(b"x")
    hooks, meta = hotspot_hook_curator.curate_hook_clips(
        9, "道路中断", segments, source_video_path=tmp_path / "x.mp4",
    )
    assert hooks == []
    assert meta["grounding_audit"]["status"] == "rejected_all"


def test_audit_prompt_no_longer_claims_source_title_is_verified_fact():
    import hotspot_hook_curator

    prompt = hotspot_hook_curator._audit_prompt(
        "南非港口 Transport Month",
        "港口作业",
        [{
            "event_index": 1,
            "title_zh": "港口作业",
            "evidence": {
                "what_happened": "吊机作业",
                "hook_reason": "清晰",
                "visual_audit": {
                    "scene_type": "port",
                    "visible_objects": ["吊机"],
                    "visible_actions": ["装卸"],
                },
            },
            "segments": [{
                "segment_index": 0, "start_ms": 0, "end_ms": 5000,
                "description": "吊机", "transcript": "", "ocr_text": "", "tags": [],
            }],
        }],
    )
    assert "母片标题是已验证事件事实" not in prompt
    assert "只是待核对的来源线索" in prompt
    assert "visual_scene_type" in prompt
    assert hotspot_hook_curator.AUDIT_PROMPT_VERSION == "hotspot-hook-grounding-audit-v6"


def test_title_contradicts_visual_hard_gate():
    import hotspot_hook_curator as curator
    assert curator._title_contradicts_visual(
        "南非海滨休闲日常",
        {"visible_objects": ["急救车", "燃烧的车辆"], "visible_actions": ["车辆燃烧"], "reason": "公路事故"},
    )
    assert not curator._title_contradicts_visual(
        "雪天N3高速持续行车",
        {"visible_objects": ["车辆", "公路"], "visible_actions": ["行驶"], "reason": "雪天公路"},
    )
