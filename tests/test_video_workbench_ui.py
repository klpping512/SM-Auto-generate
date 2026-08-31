from pathlib import Path
from html.parser import HTMLParser
import subprocess


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and not dict(attrs).get("src"):
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def test_video_workbench_has_three_column_matching_contract():
    workbench = (Path(__file__).parents[1] / "static" / "video-workbench.html").read_text()
    project = (Path(__file__).parents[1] / "static" / "video-project.html").read_text()
    assert "视频精确调整已合并到视频工作台" in workbench or 'id="scriptColumn"' in workbench
    assert "/api/semantic-match" in project or "/api/semantic-match" in workbench


def test_video_workbench_locks_matching_to_mobile_portrait():
    workbench = (Path(__file__).parents[1] / "static" / "video-workbench.html").read_text()
    project = (Path(__file__).parents[1] / "static" / "video-project.html").read_text()
    combined = workbench + project
    assert "竖屏" in combined or "portrait" in combined
    assert 'value="landscape"' not in workbench


def test_editor_links_to_precise_matching_workbench():
    page = (Path(__file__).parents[1] / "static" / "editor.html").read_text()
    assert "/video-project.html" in page or "/video-workbench.html" in page


def test_changed_asset_pages_have_valid_javascript():
    root = Path(__file__).parents[1] / "static"
    for name in ("assets.html", "video-workbench.html"):
        parser = _Scripts()
        parser.feed((root / name).read_text())
        result = subprocess.run(["node", "--check"], input="\n".join(parser.parts), text=True, capture_output=True)
        assert result.returncode == 0, f"{name}: {result.stderr}"
