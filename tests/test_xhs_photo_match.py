"""第 2 批 2B：分类配图（复用 asset_taxonomy，不改其本体）。"""
from pathlib import Path

from PIL import Image

import asset_taxonomy
from xhs_cards import render_carousel
from xhs_photo_match import pick_photos, topic_categories


def _make_image(path: Path, color="#336699"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 480), color).save(path)


def test_topic_categories_customs_first():
    cats = topic_categories("南非清关攻略", "")
    assert cats[0] == "customs"
    assert "customs" in cats
    # 兜底含全量 CATEGORY_PRIORITY
    for cat in asset_taxonomy.CATEGORY_PRIORITY:
        assert cat in cats


def test_topic_categories_delivery_node():
    cats = topic_categories("末端配送怎么做", "")
    # 节点命中按 CATEGORY_PRIORITY 展开：warehouse 优先于 delivery
    assert set(cats[:4]) >= {"delivery", "warehouse", "staff", "facility"}
    assert cats.index("warehouse") < cats.index("delivery") or cats[0] in {
        "warehouse", "delivery", "staff", "facility",
    }


def test_topic_categories_request_category_and_fallback():
    cats = topic_categories("随便聊聊", "warehouse")
    assert cats[0] == "warehouse"
    assert cats == asset_taxonomy.CATEGORY_PRIORITY


def test_pick_photos_prefers_category_and_stamps_asset_id(tmp_db, tmp_path):
    static = tmp_path / "static"
    customs = static / "assets" / "library" / "image" / "customs1.jpg"
    delivery = static / "assets" / "library" / "image" / "delivery1.jpg"
    _make_image(customs, "#aa0000")
    _make_image(delivery, "#00aa00")

    dirty = static / "assets" / "library" / "image" / "missing.jpg"
    # 脏记录：库里有 filepath，文件不存在
    tmp_db.create_asset({
        "name": "missing", "filepath": "assets/library/image/missing.jpg",
        "file_type": "image", "category": "customs", "duration": None,
        "width": 1, "height": 1, "size": 10, "thumbnail": None,
        "sha256": "deadbeef1", "source": "test", "status": "active", "created_by": None,
    })
    aid_customs = tmp_db.create_asset({
        "name": "customs1", "filepath": "assets/library/image/customs1.jpg",
        "file_type": "image", "category": "customs", "duration": None,
        "width": 320, "height": 480, "size": 100, "thumbnail": None,
        "sha256": "deadbeef2", "source": "test", "status": "active", "created_by": None,
    })
    tmp_db.create_asset({
        "name": "delivery1", "filepath": "assets/library/image/delivery1.jpg",
        "file_type": "image", "category": "delivery", "duration": None,
        "width": 320, "height": 480, "size": 100, "thumbnail": None,
        "sha256": "deadbeef3", "source": "test", "status": "active", "created_by": None,
    })

    picked = pick_photos(tmp_db, static, "清关要注意什么", "", count=1)
    assert len(picked) == 1
    assert picked[0]["asset_id"] == aid_customs
    assert picked[0]["path"] == "assets/library/image/customs1.jpg"
    assert not dirty.exists()


def test_pick_photos_falls_back_to_scan(tmp_db, tmp_path):
    static = tmp_path / "static"
    orphan = static / "assets" / "thumbnails" / "orphan.jpg"
    _make_image(orphan, "#112233")
    picked = pick_photos(tmp_db, static, "无分类命中素材", "", count=2)
    assert len(picked) >= 1
    assert picked[0]["path"].endswith("orphan.jpg")
    assert picked[0]["asset_id"] is None


def test_render_carousel_photo_pool_stamps_asset_id(tmp_path):
    photo = tmp_path / "pool.jpg"
    _make_image(photo, "#445566")
    pages, attachments = render_carousel(
        "清关攻略",
        [
            {"headline": "清关攻略", "subheadline": "收藏"},
            {"headline": "要点", "points": ["核对单据"]},
        ],
        tmp_path,
        photo_pool=[{"path": str(photo), "asset_id": 42}],
    )
    assert len(attachments) == len(pages) == 5
    assert all(a.get("asset_id") == 42 for a in attachments)
    assert (tmp_path / attachments[0]["path"]).exists()


def test_render_carousel_without_pool_backward_compatible(tmp_path):
    thumb = tmp_path / "assets" / "thumbnails"
    thumb.mkdir(parents=True)
    _make_image(thumb / "a.jpg")
    _, attachments = render_carousel(
        "仓储",
        [{"headline": "仓储", "subheadline": "x"}, {"headline": "要点", "points": ["a"]}],
        tmp_path,
    )
    assert "asset_id" not in attachments[0]
