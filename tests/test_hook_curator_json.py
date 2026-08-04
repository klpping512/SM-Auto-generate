import json

import pytest


def _segments():
    return [
        {
            "id": 11, "segment_index": 0, "start_ms": 0, "end_ms": 5_000,
            "description": "数十辆卡车在港口入口排队", "transcript": "", "ocr_text": "",
            "tags": [{"dimension": "object", "value": "卡车"}],
        },
        {
            "id": 12, "segment_index": 1, "start_ms": 5_000, "end_ms": 10_000,
            "description": "工作人员在入口检查货车", "transcript": "", "ocr_text": "",
            "tags": [{"dimension": "action", "value": "检查"}],
        },
    ]


def _hooks_payload() -> dict:
    return {
        "hooks": [{
            "event_identity": "港口入口货车排队检查",
            "start_segment_index": 0,
            "end_segment_index": 1,
            "title_zh": "港口入口卡车排队",
            "what_happened": "多辆货车在入口排队，工作人员正在检查。",
            "hook_reason": "连续现场动作和排队画面能快速呈现压力。",
            "logistics_question": "入口检查变慢时，卖家应怎样核对到仓与配送计划？",
            "confidence": 0.88,
        }]
    }


def test_parse_strips_think_blocks_before_json():
    import hotspot_hook_curator

    body = json.dumps(_hooks_payload(), ensure_ascii=False)
    dirty = f"<think>推理过程，包含 {{伪 JSON}}</think>\n{body}"
    hooks = hotspot_hook_curator._parse(dirty, _segments())
    assert len(hooks) == 1
    assert hooks[0]["title_zh"] == "港口入口卡车排队"


def test_parse_accepts_markdown_json_fence():
    import hotspot_hook_curator

    body = json.dumps(_hooks_payload(), ensure_ascii=False)
    dirty = f"```json\n{body}\n```"
    hooks = hotspot_hook_curator._parse(dirty, _segments())
    assert len(hooks) == 1
    assert hooks[0]["evidence"]["event_identity"] == "港口入口货车排队检查"


def test_parse_extracts_balanced_json_after_preamble():
    import hotspot_hook_curator

    body = json.dumps(_hooks_payload(), ensure_ascii=False)
    dirty = f"好的,结果如下:{body}"
    hooks = hotspot_hook_curator._parse(dirty, _segments())
    assert len(hooks) == 1
    assert hooks[0]["start_ms"] == 0
    assert hooks[0]["end_ms"] == 10_000


def test_parse_still_rejects_non_json():
    import hotspot_hook_curator

    with pytest.raises(ValueError, match="Hook 策展模型未返回合法 JSON"):
        hotspot_hook_curator._parse("这不是 JSON，也没有大括号对象", _segments())


def test_extract_json_used_by_audit_path():
    import hotspot_hook_curator

    dirty = '<think>核对中</think>\n{"accepted":[{"candidate_index":1,"reason":"画面一致"}]}'
    payload = hotspot_hook_curator._extract_json(dirty)
    assert payload == {"accepted": [{"candidate_index": 1, "reason": "画面一致"}]}
