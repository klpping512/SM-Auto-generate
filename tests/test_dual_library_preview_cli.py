from pathlib import Path


def test_dual_library_preview_cli_is_portrait_only():
    script = (Path(__file__).parents[1] / "scripts" / "run_dual_library_preview.py").read_text()

    assert 'parser.add_argument("--orientation"' not in script
    assert "output_size = (540, 960)" in script
    assert "1280, 720" not in script
