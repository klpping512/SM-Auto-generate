from pathlib import Path


EDITOR_HTML = Path(__file__).parents[1] / "static" / "editor.html"


def test_video_project_url_is_authoritative_over_local_drafts():
    source = EDITOR_HTML.read_text(encoding="utf-8")

    project_loader = source.index("async function loadVideoProjectDraft")
    init_project_branch = source.index("const videoProjectId = getVideoProjectId()")
    local_draft_branch = source.index("const autoSaved = JSON.parse(localStorage.getItem('editorAutoSave')")

    assert "video_project_id" in source
    assert "cache: 'no-store'" in source
    assert "generatedContents = { [platform]: content }" in source
    assert "绝不回退到旧草稿" in source
    assert project_loader < init_project_branch < local_draft_branch


def test_video_project_loader_restores_rendered_output_and_revision_scenes():
    source = EDITOR_HTML.read_text(encoding="utf-8")

    loader = source[source.index("async function loadVideoProjectDraft"):source.index("async function init()")]

    assert "const scenes = Array.isArray(payload.scenes) ? payload.scenes : [];" in loader
    assert "rendered_video: videoCandidate" in loader
    assert "selected_asset_ids: selectedAssetIds" in loader
    assert "loadPlatformToEditor(currentPreview);" in loader
    assert "renderPreview(currentPreview);" in loader
