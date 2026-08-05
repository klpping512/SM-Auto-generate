import importlib.util
import json
from pathlib import Path


def _load_ingest():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_za_stock.py"
    spec = importlib.util.spec_from_file_location("ingest_za_stock", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ingest_requires_sidecar_and_writes_provenance(tmp_path, monkeypatch):
    ingest = _load_ingest()
    root = tmp_path / "za-stock"
    customs = root / "customs"
    customs.mkdir(parents=True)
    good = customs / "za_customs_pexels_9.mp4"
    good.write_bytes(b"\x00\x00fake")
    (Path(str(good) + ".json")).write_text(json.dumps({
        "source_url": "https://www.pexels.com/video/9/",
        "license": "Pexels License",
        "author": "Ada",
        "category": "customs",
        "note": "通用港口/清关背景，非南非现场，口播仅可表述为备货待清关",
    }), encoding="utf-8")
    bad = customs / "no_sidecar.mp4"
    bad.write_bytes(b"x")

    calls = []

    def fake_ingest(source, static_dir, **kwargs):
        assert kwargs.get("category") == "auto"
        assert kwargs.get("import_root") == root
        return {"id": 99, "_dedup": False}

    def fake_prov(asset_id, source_url, license_name, attribution):
        calls.append((asset_id, source_url, license_name, attribution))

    monkeypatch.setattr(ingest.db, "get_user_by_username", lambda _n: {"id": 1})

    summary = ingest.ingest_tree(
        root,
        dry_run=False,
        ingest_file=fake_ingest,
        update_provenance=fake_prov,
    )
    assert summary["customs"]["added"] == 1
    assert summary["customs"]["skip_sidecar"] == 1
    assert calls == [(
        99,
        "https://www.pexels.com/video/9/",
        "Pexels License",
        "通用港口/清关背景，非南非现场，口播仅可表述为备货待清关",
    )]
