from pathlib import Path
import time

import pytest
from PIL import Image


def test_ingest_file_hardlinks_without_copy(tmp_path, tmp_db):
    import media_assets

    source = tmp_path / "source" / "仓库照片.jpg"
    source.parent.mkdir()
    Image.new("RGB", (32, 24), "white").save(source)
    static_dir = tmp_path / "static"

    asset = media_assets.ingest_file(
        source,
        static_dir,
        category="warehouse",
        origin="local_directory",
        created_by=None,
        storage_mode="hardlink",
    )

    stored = static_dir / asset["filepath"]
    assert stored.stat().st_ino == source.stat().st_ino
    assert source.exists()


def test_resolve_local_root_rejects_path_outside_configured_root(tmp_path):
    from local_asset_import import resolve_source_path

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.touch()

    with pytest.raises(ValueError, match="受信任素材目录"):
        resolve_source_path(outside, allowed)


def test_discover_counts_supported_and_unsupported_files(tmp_path):
    from local_asset_import import discover

    (tmp_path / "a.JPG").touch()
    (tmp_path / "b.MOV").touch()
    (tmp_path / "c.HEIC").touch()
    (tmp_path / ".DS_Store").touch()

    supported, unsupported = discover(tmp_path)

    assert [path.name for path in supported] == ["a.JPG", "b.MOV"]
    assert [path.name for path in unsupported] == ["c.HEIC"]


def test_local_import_job_is_idempotent_and_cancelable(tmp_db, tmp_path):
    user_id = tmp_db.create_user("local-importer", "hash", "admin", "Local Importer")
    root = str(tmp_path.resolve())

    first, created = tmp_db.create_or_get_local_asset_import_job(root, user_id)
    second, created_again = tmp_db.create_or_get_local_asset_import_job(root, user_id)

    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]
    canceled = tmp_db.request_local_asset_import_cancel(first["id"], user_id)
    assert canceled["status"] == "cancel_requested"
    assert canceled["cancel_requested"] == 1


def test_recover_interrupted_local_import_jobs_marks_them_interrupted(tmp_db, tmp_path):
    user_id = tmp_db.create_user("recover-importer", "hash", "admin", "Recover Importer")
    job, _ = tmp_db.create_or_get_local_asset_import_job(str(tmp_path.resolve()), user_id)
    tmp_db.update_local_asset_import_job(job["id"], status="importing", stage="importing")

    assert tmp_db.recover_interrupted_local_asset_import_jobs() == 1
    recovered = tmp_db.get_local_asset_import_job(job["id"], user_id)
    assert recovered["status"] == "interrupted"
    assert recovered["finished_at"]


def _local_import_headers(tmp_db, client):
    import auth

    tmp_db.create_user(
        "local-api-admin", auth.hash_password("pw12345"), "admin", "Local API Admin"
    )
    token = client.post(
        "/api/auth/login", json={"username": "local-api-admin", "password": "pw12345"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_local_import_api_returns_immediately_and_counts_every_file(
    tmp_db, tmp_path, monkeypatch
):
    import app
    from fastapi.testclient import TestClient

    root = tmp_path / "local-assets"
    root.mkdir()
    Image.new("RGB", (32, 24), "white").save(root / "仓库.JPG")
    (root / "暂不支持.HEIC").touch()
    static_dir = tmp_path / "static"
    monkeypatch.setenv("LOCAL_ASSET_ROOT", str(root))
    monkeypatch.setattr(app, "STATIC_DIR", static_dir)
    with TestClient(app.app) as client:
        headers = _local_import_headers(tmp_db, client)
        response = client.post("/api/assets/local-imports", headers=headers)

        assert response.status_code == 202
        job_id = response.json()["job"]["id"]
        duplicate = client.post("/api/assets/local-imports", headers=headers)
        assert duplicate.status_code == 200
        assert duplicate.json()["job"]["id"] == job_id

        detail = None
        for _ in range(100):
            detail = client.get(f"/api/assets/local-imports/{job_id}", headers=headers).json()
            if detail["status"] in {"succeeded", "failed", "canceled"}:
                break
            time.sleep(0.02)

    assert detail["status"] == "succeeded"
    assert detail["total"] == 2
    assert detail["imported"] == 1
    assert detail["skipped"] == 1
    assert detail["scanned"] == 2
    assert detail["imported"] + detail["duplicated"] + detail["skipped"] + detail["failed"] == detail["total"]


def test_local_import_cancel_endpoint_is_user_scoped(tmp_db, tmp_path, monkeypatch):
    import app
    from fastapi.testclient import TestClient

    root = tmp_path / "local-assets"
    root.mkdir()
    monkeypatch.setenv("LOCAL_ASSET_ROOT", str(root))
    monkeypatch.setattr(app, "STATIC_DIR", tmp_path / "static")
    with TestClient(app.app) as client:
        headers = _local_import_headers(tmp_db, client)
        user_id = tmp_db.get_user_by_username("local-api-admin")["id"]
        job, _ = tmp_db.create_or_get_local_asset_import_job(str(root), user_id)

        response = client.post(f"/api/assets/local-imports/{job['id']}/cancel", headers=headers)

        assert response.status_code == 200
        assert response.json()["status"] == "cancel_requested"
