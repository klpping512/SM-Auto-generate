import importlib.util
import json
from pathlib import Path

import httpx


def _load_pull():
    path = Path(__file__).resolve().parents[1] / "scripts" / "pull_za_stock.py"
    spec = importlib.util.spec_from_file_location("pull_za_stock", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


def test_pull_writes_sidecar_and_skips_existing(tmp_path, monkeypatch):
    pull = _load_pull()
    monkeypatch.setenv("LOCAL_ASSET_ROOT", str(tmp_path))
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-test")
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.setattr(pull.time, "sleep", lambda *_a, **_k: None)
    # 单测只跑一个 query，避免 6 词×API 放大计数
    monkeypatch.setattr(pull, "QUERY_MAP", {"customs": ["cargo customs inspection"]})

    dest_root = tmp_path / "za-stock"
    existing = dest_root / "customs" / "za_customs_pexels_1.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get(self, url, **kwargs):
            return _FakeResponse({
                "videos": [
                    {
                        "id": 1,
                        "url": "https://www.pexels.com/video/1/",
                        "duration": 10,
                        "user": {"name": "Ada", "url": "https://www.pexels.com/@ada"},
                        "video_files": [
                            {"height": 1080, "link": "https://cdn.example/1.mp4", "file_type": "video/mp4"},
                        ],
                    },
                    {
                        "id": 2,
                        "url": "https://www.pexels.com/video/2/",
                        "duration": 12,
                        "user": {"name": "Bob", "url": "https://www.pexels.com/@bob"},
                        "video_files": [
                            {"height": 720, "link": "https://cdn.example/2.mp4", "file_type": "video/mp4"},
                        ],
                    },
                ]
            })

        def stream(self, method, url, **kwargs):
            class Ctx:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def raise_for_status(self):
                    return None

                def iter_bytes(self):
                    yield b"video-bytes"

            return Ctx()

        def close(self):
            return None

    summary = pull.run_pull(
        ["customs"],
        per_query=2,
        max_seconds=20,
        min_height=720,
        dry_run=False,
        client=FakeClient(),
    )
    assert summary["customs"]["added"] == 1
    assert summary["customs"]["skipped"] >= 1
    new_file = dest_root / "customs" / "za_customs_pexels_2.mp4"
    assert new_file.exists()
    sidecar = json.loads(Path(str(new_file) + ".json").read_text(encoding="utf-8"))
    assert sidecar["license"] == "Pexels License"
    assert sidecar["category"] == "customs"
    assert "备货待清关" in sidecar["note"]
    assert sidecar["source_url"]
    assert sidecar["author"]

    summary2 = pull.run_pull(
        ["customs"],
        per_query=2,
        max_seconds=20,
        min_height=720,
        dry_run=False,
        client=FakeClient(),
    )
    assert summary2["customs"]["added"] == 0
    assert summary2["customs"]["skipped"] >= 2


def test_is_not_available_helper():
    import hotspot_video_sources

    assert hotspot_video_sources.is_not_available_error("ERROR: This video is not available")
    assert not hotspot_video_sources.is_not_available_error("network timeout")
