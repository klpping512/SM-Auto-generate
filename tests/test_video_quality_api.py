from fastapi.testclient import TestClient
import pytest


def _login(client, username, password):
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_video_quality_endpoint_is_admin_only(tmp_db, monkeypatch, tmp_path):
    import app, auth

    tmp_db.create_user("quality-editor", auth.hash_password("pw123456"), "editor", "Editor")
    client = TestClient(app.app)
    headers = _login(client, "quality-editor", "pw123456")

    response = client.post(
        "/api/video-quality/evaluate",
        headers=headers,
        json={"video_source": str(tmp_path / "video.mp4")},
    )

    assert response.status_code == 403


def test_admin_video_quality_endpoint_uses_static_allowlist(tmp_db, monkeypatch, tmp_path):
    import app, auth

    tmp_db.create_user("quality-admin", auth.hash_password("pw123456"), "admin", "Admin")
    static_dir = tmp_path / "static"
    source = static_dir / "uploads" / "video" / "sample.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    monkeypatch.setattr(app, "STATIC_DIR", static_dir)
    captured = {}

    async def fake_run(request, output_dir, **kwargs):
        captured.update({"request": request, "output_dir": output_dir, **kwargs})
        return {
            "run_dir": str(output_dir),
            "report": {"overall_score": 88, "passed": True, "issues": []},
            "problem_segments": [],
            "optimized_generation": {"required": False},
            "regeneration_decision": {"action": "none"},
            "manifest": {},
        }

    monkeypatch.setattr(app.video_quality_service, "run_quality_mvp", fake_run)
    client = TestClient(app.app)
    headers = _login(client, "quality-admin", "pw123456")
    response = client.post(
        "/api/video-quality/evaluate",
        headers=headers,
        json={
            "video_source": "/static/uploads/video/sample.mp4",
            "original_prompt": "南非仓库履约",
            "storyboard": {"scenes": []},
            "reference_images": [],
            "target_platform": "抖音",
        },
    )

    assert response.status_code == 200
    assert captured["request"].video_source == str(source)
    assert captured["allowed_roots"] == [static_dir]
    assert captured["request"].auto_regenerate is False


def test_preprocessor_rejects_api_local_path_outside_allowlist(tmp_path):
    from video_quality.schemas import VideoQualityInput
    from video_quality.video_preprocessor import VideoPreprocessingError, preprocess_video

    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    allowed = tmp_path / "static"
    allowed.mkdir()

    with pytest.raises(VideoPreprocessingError, match="static"):
        preprocess_video(
            VideoQualityInput(video_source=str(outside)),
            tmp_path / "run",
            allowed_roots=[allowed],
        )
