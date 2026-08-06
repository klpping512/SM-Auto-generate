"""第 0 批：小红书发布可观测性——结构化 category + 缺失附件显式化。"""
from pathlib import Path

import pytest

import adapters
import publish_readiness
import publisher
from adapters.base import PublishResult
from adapters.xiaohongshu import XiaohongshuAdapter, _exception_category


def test_publish_result_category_defaults_none_backward_compatible():
    result = PublishResult(success=True, platform="reddit")
    assert result.category is None
    assert result.to_dict() == {"success": True, "platform": "reddit"}


def test_publish_result_category_included_when_set():
    result = PublishResult(
        success=False, platform="xiaohongshu",
        error="小红书必须配图", category="no_images",
    )
    assert result.to_dict()["category"] == "no_images"


def test_resolve_uploaded_media_missing_under_uploads(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(publisher, "UPLOAD_ROOT", upload_root.resolve())
    # resolve() joins relative paths under publisher package static/; use absolute under upload_root
    missing = upload_root / "image" / "gone.png"
    missing.parent.mkdir(parents=True, exist_ok=True)
    # file intentionally not created
    resolved, missing_paths = publisher._resolve_uploaded_media([str(missing)])
    assert resolved == []
    assert missing_paths == [str(missing)]


def test_resolve_uploaded_media_existing_file(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(publisher, "UPLOAD_ROOT", upload_root.resolve())
    present = upload_root / "image" / "ok.png"
    present.parent.mkdir(parents=True, exist_ok=True)
    present.write_bytes(b"png")
    resolved, missing_paths = publisher._resolve_uploaded_media([str(present)])
    assert resolved == [str(present.resolve())]
    assert missing_paths == []


async def test_dispatch_attachment_missing_does_not_call_adapter(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(publisher, "UPLOAD_ROOT", upload_root.resolve())
    missing = upload_root / "image" / "gone.png"
    missing.parent.mkdir(parents=True, exist_ok=True)

    called = {"n": 0}

    class _Guard(adapters.base.PublishAdapter):
        name = "guard"
        async def publish(self, **kwargs):
            called["n"] += 1
            return PublishResult(success=True, platform="guard")

    monkeypatch.setitem(adapters.ADAPTERS, "guard", _Guard())
    result = await publisher.dispatch(
        platform="guard", title="T", content="B",
        images=[str(missing)],
    )
    assert result["success"] is False
    assert result["category"] == "attachment_missing"
    assert "gone.png" in result["error"]
    assert called["n"] == 0


async def test_dispatch_empty_images_reaches_adapter_no_images(monkeypatch):
    """images 为空/None 时仍交给适配器，保留 no_images 语义。"""
    seen = {}

    class _XhsStub(adapters.base.PublishAdapter):
        name = "xiaohongshu"
        CREDENTIAL_KIND = "cookie"

        async def publish(self, *, platform, title, content,
                          tags=None, images=None, video=None, account=None):
            seen["images"] = images
            return PublishResult(
                success=False, platform="xiaohongshu",
                category="no_images", error="小红书必须配图，images 不能为空",
            )

        async def check_login(self, account=None):
            return True

    monkeypatch.setitem(adapters.ADAPTERS, "xiaohongshu", _XhsStub())
    monkeypatch.setattr(publisher.db, "get_accounts", lambda platform, owner_id=None: [])
    result = await publisher.dispatch(
        platform="xiaohongshu", title="T", content="B", images=None, account={"id": 1},
    )
    assert result["category"] == "no_images"
    assert seen["images"] is None


def test_xiaohongshu_readiness_uses_cookie_not_huimei():
    """改动 E：小红书就绪度走 cookie，不依赖 huimei 二进制。"""
    adapter = adapters.get_adapter("xiaohongshu")
    assert isinstance(adapter, XiaohongshuAdapter)
    assert adapter.CREDENTIAL_KIND == "cookie"
    empty = publish_readiness.readiness("xiaohongshu", "{}")
    assert empty["ready"] is False
    assert empty["kind"] == "cookie"
    assert empty["missing"] == ["cookies"]
    # 就绪判定源码路径不含 huimei
    import inspect
    src = inspect.getsource(publish_readiness.readiness)
    assert "huimei" not in src.lower()


def test_exception_category_timeout_vs_unknown():
    assert _exception_category(TimeoutError("wait timeout")) == "timeout"

    class PlaywrightTimeout(Exception):
        pass

    assert _exception_category(PlaywrightTimeout("Timeout 30000ms exceeded")) == "timeout"
    assert _exception_category(RuntimeError("boom")) == "unknown"


def test_debug_screenshot_from_error():
    assert publisher.debug_screenshot_from_error(
        "发布页未就绪。已截图: /static/debug/xhs-no-upload.png"
    ) == "/static/debug/xhs-no-upload.png"
    assert publisher.debug_screenshot_from_error("无截图") is None


def test_add_publish_log_stores_failure_category(tmp_db):
    tmp_db.add_publish_log(
        1, "xiaohongshu", "t", "failed", "已截图: /static/debug/xhs-submit-fail.png",
        failure_category="selector_failed",
        debug_screenshot="/static/debug/xhs-submit-fail.png",
    )
    logs = tmp_db.get_publish_logs(limit=5)
    row = next(r for r in logs if r.get("failure_category") == "selector_failed")
    assert row["debug_screenshot"] == "/static/debug/xhs-submit-fail.png"
