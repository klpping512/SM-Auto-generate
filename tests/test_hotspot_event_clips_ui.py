from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_assets_page_defaults_to_ready_hook_library():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    assert "audited_only=true" not in page
    assert "eligible_only=true" in page
    assert "library_status=audit_only" in page
    assert "可直接成片" in page
    assert "审计归档" in page
    assert "不可成片原因" in page


def test_assets_page_renders_shell_before_blocking_on_hotspot_events():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    assert "function renderShell(" in page
    assert "async function loadHotspotEvents()" in page
    assert "async function openHotspotLibrary()" in page
    assert "正在加载内容资产" in page
    assert "MATERIALS_PAGE_SIZE=48" in page
    assert "iconify-icon.min.js\" defer" in page
    assert "apiFetch('/api/assets?'+p),apiFetch('/api/hotspot-events?'" not in page


def test_assets_page_exposes_event_clips_and_source_labels():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    assert "事件片段" in page
    assert "title_zh" in page
    assert "title_en" in page
    assert "热点素材" in page


def test_hotspot_media_task_requires_hook_choice_before_video_use():
    page = (ROOT / "static/assets.html").read_text(encoding="utf-8")
    assert 'onclick="viewHotspotEvents(${item.asset_id})">选择 Hook 后成片</button>' in page
    assert 'onclick="useAsset(${item.asset_id})">应用到成片</button>' not in page


def test_video_workbench_redirects_precision_adjust_into_matching_stage():
    page = (ROOT / "static/video-workbench.html").read_text(encoding="utf-8")
    assert "stage=match" in page
    assert "素材匹配" in page
