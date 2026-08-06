"""公众号图文 /api/articles/* 端点自动化测试。

覆盖：list / create 校验(422) / create 成功(201) / 详情解码 / 归属权限(403) / 列表过滤 /
generate(sync def 在 TestClient event loop 下不抛 asyncio.run 错误) / 选图落盘 /
render 冲突(--force)路径 / publish 状态机(409) / 未登录 401 / /article-assets 静态服务。
generate 的模型调用在 model_router 层 mock，不花真实预算。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth  # noqa: E402
import database as db  # noqa: E402

GOOD_PACKAGE = {
    "slug": "api-test-article",
    "title": "API 测试文章",
    "topic_brief": "测试",
    "reference_style": "",
    "materials": [
        {"excerpt": "PoE 办理周期通常为 4–8 周，费用约 0.5%–1.5% 货值。", "source_note": "SARS 2026-07", "source_url": ""},
        {"excerpt": "2025 年 12 月起加严查验。", "source_note": "ITAC 2026-01"},
    ],
}

FAKE_GENERATED = {
    "intro": "南非市场 PoE 登记办理周期通常 4–8 周，费用约 0.5%–1.5% 货值。",
    "sections": [{"heading": "周期与费用", "body": "周期通常 4–8 周，但材料不齐可能拖到 12 周。"}],
    "conclusion": "建议提前规划。",
}


def _create_user(db_module, username: str, role: str = "editor") -> int:
    with db_module.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?,?,?,?)",
            (username, auth.hash_password("pw12345"), role, username),
        )
        return cur.lastrowid


def _login(client, username: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": "pw12345"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as app_module

    # render/选图产出目录改到临时目录，避免污染真实 data/articles/
    import scripts.render_article_package as render_mod
    import scripts.select_article_images as select_mod
    monkeypatch.setattr(render_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(select_mod, "PROJECT_ROOT", tmp_path)
    # /article-assets 静态挂载改指临时目录（新版 StaticFiles 用 init 时算出的 all_directories）
    for route in app_module.app.routes:
        if getattr(route, "name", "") == "article-assets":
            static_app = getattr(route, "app", None)
            target_dir = str(tmp_path / "data" / "articles")
            if static_app is not None:
                static_app.directory = target_dir
                if hasattr(static_app, "all_directories"):
                    static_app.all_directories = [target_dir]
            route.directory = target_dir
    return TestClient(app_module.app), app_module


@pytest.fixture
def api_ctx(tmp_db, tmp_path, monkeypatch):
    """返回 (client, headers, article_id 创建函数)。"""
    client, _ = _make_client(tmp_path, monkeypatch)
    user_id = _create_user(db, "article-tester")
    headers = _login(client, "article-tester")

    def make_article(package=None):
        resp = client.post("/api/articles", headers=headers,
                           json=package or GOOD_PACKAGE)
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    return {"client": client, "headers": headers, "user_id": user_id, "make": make_article}


def test_list_empty_and_create_validation_422(api_ctx):
    client, headers = api_ctx["client"], api_ctx["headers"]
    resp = client.get("/api/articles", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []

    bad = {
        "slug": "bad-!slug", "title": "", "materials": [{"excerpt": "x"}],
    }
    resp = client.post("/api/articles", headers=headers, json=bad)
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert any("slug" in e for e in errors)
    assert any("title" in e for e in errors)
    assert any("source_note" in e for e in errors)


def test_create_ok_and_detail_decodes_json(api_ctx):
    client, headers = api_ctx["client"], api_ctx["headers"]
    article_id = api_ctx["make"]()

    resp = client.get(f"/api/articles/{article_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "api-test-article"
    assert isinstance(body["materials_json"], list) and len(body["materials_json"]) == 2
    assert body["generated_content_json"] == {}
    assert body["image_selections_json"] == {}
    assert body["status"] == "draft"


def test_ownership_403_and_list_filtered(api_ctx):
    client, headers = api_ctx["client"], api_ctx["headers"]
    article_id = api_ctx["make"]()

    _create_user(db, "other-tester")
    other_headers = _login(client, "other-tester")

    resp = client.get(f"/api/articles/{article_id}", headers=other_headers)
    assert resp.status_code == 403

    resp = client.get("/api/articles", headers=other_headers)
    assert resp.status_code == 200
    assert resp.json() == []

    resp = client.get("/api/articles", headers=headers)
    assert [a["id"] for a in resp.json()] == [article_id]


def test_unauthenticated_401(api_ctx):
    client = api_ctx["client"]
    assert client.get("/api/articles").status_code == 401


def test_generate_sync_endpoint_runs_under_test_client(api_ctx, monkeypatch):
    """sync def 端点在 TestClient 的 event loop 线程池里执行：
    generate_article 内部 asyncio.run 不应抛 'cannot be called from a running event loop'。"""
    import model_router

    async def fake_call_text(job_id, role, messages, prompt_version=None,
                             max_output_tokens=None, use_cache=True, **kwargs):
        return {
            "content": json.dumps({
                "intro": FAKE_GENERATED["intro"],
                "sections": FAKE_GENERATED["sections"],
                "conclusion": FAKE_GENERATED["conclusion"],
                "evidence_footnotes": [
                    {"claim_text": "PoE 办理周期通常为 4–8 周，费用约 0.5%–1.5% 货值。", "material_index": 1},
                ],
            }),
            "cache_hit": False,
        }

    monkeypatch.setattr(model_router, "call_text", fake_call_text)

    client, headers = api_ctx["client"], api_ctx["headers"]
    article_id = api_ctx["make"]()

    resp = client.post(f"/api/articles/{article_id}/generate", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok", body
    assert body["section_count"] == 1
    assert body["footnote_count"] == 1

    detail = client.get(f"/api/articles/{article_id}", headers=headers).json()
    assert detail["generated_content_json"]["sections"][0]["heading"] == "周期与费用"
    assert len(detail["evidence_footnotes_json"]) == 1


def test_images_and_render_force_conflict_path(api_ctx, tmp_path, monkeypatch):
    """选图落盘（真实 asset 文件）+ 渲染 --force 冲突路径 + unresolved 扫描。"""
    import scripts.select_article_images as select_mod

    # 造一个真实的 asset 记录 + 假图片文件（tmp 的 static 下）
    fake_dir = tmp_path / "static" / "test-assets"
    fake_dir.mkdir(parents=True)
    img_file = fake_dir / "img-1.jpg"
    img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    monkeypatch.setattr(select_mod, "STATIC_DIR", tmp_path / "static")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO assets (name, filepath, file_type, category, size, sha256, source, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("测试图", "test-assets/img-1.jpg", "image", "warehouse", 10,
             "sha256-test-images-1", "local_directory", "active"),
        )
        asset_id = cur.lastrowid

    # 先有生成结果（mock 写库，避免走 generate 端点）
    article_id = api_ctx["make"]()
    db.update_article(article_id, generated_content_json=json.dumps(FAKE_GENERATED, ensure_ascii=False))

    client, headers = api_ctx["client"], api_ctx["headers"]

    # 选图：cover 选中、section-01 传 null（保留不动）
    resp = client.post(f"/api/articles/{article_id}/images", headers=headers,
                       json={"selections": {"cover": {"asset_id": asset_id}, "section-01": None}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    assert body["selections"]["cover"]["asset_id"] == asset_id
    assert (tmp_path / "data" / "articles" / "api-test-article" / "cover.jpg").is_file()

    # 选图缺资产：错误不静默
    resp = client.post(f"/api/articles/{article_id}/images", headers=headers,
                       json={"selections": {"cover": {"asset_id": 99999}}})
    assert resp.status_code == 200
    assert any("99999" in e for e in resp.json()["errors"])

    # 渲染：第一次（目录已有 cover 文件）→ --force 冲突
    resp = client.post(f"/api/articles/{article_id}/render", headers=headers, json={"force": False})
    result = resp.json()
    assert result["status"] == "error" and "--force" in result["error"]

    # 渲染：force 后成功，unresolved 扫出无来源「12 周」
    resp = client.post(f"/api/articles/{article_id}/render", headers=headers, json={"force": True})
    result = resp.json()
    assert result["status"] == "ok", result
    assert "12 周" in result["unresolved"]

    # /article-assets 静态服务可访问产物
    resp = client.get("/article-assets/api-test-article/article.md")
    assert resp.status_code == 200
    assert "【待核实：』12 周『】" in resp.text
    assert "【发布前必核】" in resp.text
    resp = client.get("/article-assets/api-test-article/meta.json")
    assert resp.status_code == 200
    assert "fixed_materials_review_notice" in resp.text


def test_publish_requires_ready(api_ctx):
    client, headers = api_ctx["client"], api_ctx["headers"]
    article_id = api_ctx["make"]()

    resp = client.post(f"/api/articles/{article_id}/publish", headers=headers)
    assert resp.status_code == 409

    db.update_article(article_id, status="ready")
    resp = client.post(f"/api/articles/{article_id}/publish", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"

    resp = client.post(f"/api/articles/{article_id}/publish", headers=headers)
    assert resp.status_code == 409


def test_article_not_found_404(api_ctx):
    client, headers = api_ctx["client"], api_ctx["headers"]
    resp = client.get("/api/articles/999999", headers=headers)
    assert resp.status_code == 404
