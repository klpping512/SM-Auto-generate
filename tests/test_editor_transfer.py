import json
import subprocess


def _run_transfer(expression):
    script = (
        "const t=require('./static/editor-transfer.js');"
        f"const result=({expression});"
        "process.stdout.write(JSON.stringify(result));"
    )
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_build_draft_preserves_all_platforms_and_active_selection():
    result = _run_transfer("t.buildDraft({outputs:["
        "{platform:'xiaohongshu',title:'小红书标题',body:'小红书正文',hashtags:['#物流']},"
        "{platform:'facebook',title:'Facebook title',body:'Facebook body',hashtags:['Logistics']}"
        "]},1,['xiaohongshu','facebook'])")
    assert result["version"] == 2
    assert result["activePlatform"] == "facebook"
    assert [item["platform"] for item in result["contents"]] == ["xiaohongshu", "facebook"]
    assert result["contents"][0]["hashtags"] == ["物流"]


def test_build_draft_preserves_generated_media_assets():
    result = _run_transfer("t.buildDraft({outputs:[{platform:'xiaohongshu',title:'X',body:'B',"
        "image_pages:[{type:'cover',headline:'H'}],attachments:[{type:'image',path:'uploads/image/a.png'}]}]"
        "},0,['xiaohongshu'])")
    content = result["contents"][0]
    assert content["image_pages"][0]["headline"] == "H"
    assert content["attachments"][0]["path"] == "uploads/image/a.png"


def test_build_draft_keeps_clicked_output_when_an_earlier_output_is_invalid():
    result = _run_transfer("t.buildDraft({outputs:["
        "{platform:'xiaohongshu',title:'',body:''},"
        "{platform:'facebook',title:'F',body:'Facebook body'}"
        "]},1,['xiaohongshu','facebook'])")
    assert result["activePlatform"] == "facebook"
    assert [item["platform"] for item in result["contents"]] == ["facebook"]


def test_normalize_draft_filters_unknown_and_keeps_distinct_platform_content():
    result = _run_transfer("t.normalizeDraft({source:'chat',activePlatform:'facebook',contents:["
        "{platform:'xiaohongshu',title:'X',body:'小红书正文'},"
        "{platform:'facebook',title:'F',body:'Facebook body'},"
        "{platform:'unknown',title:'bad',body:'bad'}"
        "]},['xiaohongshu','facebook'],'xiaohongshu')")
    assert result["valid"] is True
    assert result["importedFromChat"] is True
    assert result["activePlatform"] == "facebook"
    assert [item["body"] for item in result["contents"]] == ["小红书正文", "Facebook body"]


def test_normalize_draft_falls_back_to_legacy_when_v2_contents_are_invalid():
    result = _run_transfer("t.normalizeDraft({source:'chat',activePlatform:'unknown',contents:["
        "{platform:'unknown',title:'bad',body:'bad'}],title:'旧标题',body:'旧正文',platforms:['xiaohongshu']"
        "},['xiaohongshu','facebook'],'xiaohongshu')")
    assert result["valid"] is True
    assert result["activePlatform"] == "xiaohongshu"
    assert result["contents"][0]["body"] == "旧正文"


def test_normalize_draft_rejects_empty_payload_without_consuming_it():
    result = _run_transfer("t.normalizeDraft({source:'chat',contents:[]},['xiaohongshu'],'xiaohongshu')")
    assert result["valid"] is False
    assert result["contents"] == []
