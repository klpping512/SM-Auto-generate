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
    page = (Path(__file__).parents[1] / "static" / "video-workbench.html").read_text()

    assert 'id="scriptColumn"' in page
    assert 'id="atomColumn"' in page
    assert 'id="candidateColumn"' in page
    assert "/api/semantic-match" in page
    assert "匹配依据" in page
    assert "Top 3" in page


def test_video_workbench_locks_matching_to_mobile_portrait():
    page = (Path(__file__).parents[1] / "static" / "video-workbench.html").read_text()

    assert "9:16 竖屏（固定）" in page
    assert 'id="orientation"' not in page
    assert "const orientation='portrait'" in page
    assert 'value="landscape"' not in page


def test_editor_links_to_precise_matching_workbench():
    page = (Path(__file__).parents[1] / "static" / "editor.html").read_text()

    assert "/video-workbench.html" in page


def test_changed_asset_pages_have_valid_javascript():
    root = Path(__file__).parents[1] / "static"
    for name in ("assets.html", "video-workbench.html"):
        parser = _Scripts()
        parser.feed((root / name).read_text())
        result = subprocess.run(["node", "--check"], input="\n".join(parser.parts), text=True, capture_output=True)
        assert result.returncode == 0, f"{name}: {result.stderr}"
