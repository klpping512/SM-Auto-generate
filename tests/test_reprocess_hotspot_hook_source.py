import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "reprocess_hotspot_hook_source.py"
    spec = importlib.util.spec_from_file_location("reprocess_hotspot_hook_source", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_recuration_accepts_only_object_shaped_legacy_intake_metadata():
    script = _module()

    assert script._normalized_intake_decision('[]') == {}
    assert script._normalized_intake_decision('{"expected_hook":"边境卡车排队"}') == {
        "expected_hook": "边境卡车排队",
    }


def test_legacy_batch_selects_only_confirmed_hooks_without_event_identity(monkeypatch):
    script = _module()

    monkeypatch.setattr(script.db, "list_hotspot_event_clips", lambda **_kwargs: [
        {"review_status": "confirmed", "evidence": {}},
    ])
    assert script._needs_legacy_event_identity({"asset_id": 306})

    monkeypatch.setattr(script.db, "list_hotspot_event_clips", lambda **_kwargs: [
        {"review_status": "confirmed", "evidence": {"event_identity": "Beitbridge 边境卡车拥堵"}},
    ])
    assert not script._needs_legacy_event_identity({"asset_id": 306})
    assert not script._needs_legacy_event_identity({"asset_id": None})
