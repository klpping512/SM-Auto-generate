"""小红书轮播配图：按选题分类匹配素材（只读 asset_taxonomy，禁止改其本体）。"""
from __future__ import annotations

from pathlib import Path

import asset_taxonomy

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def topic_categories(topic: str, category: str = "") -> list[str]:
    """选题 → 有序候选素材分类。

    规则：NODE_CATEGORY_RULES 节点命中优先 → CATEGORY_KEYWORDS 子串命中
    → 请求分类 → CATEGORY_PRIORITY 全量兜底。去重且保持优先级顺序。
    """
    blob = f"{topic or ''} {category or ''}"
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(cat: str) -> None:
        if cat in asset_taxonomy.CATEGORIES and cat != "other" and cat not in seen:
            ordered.append(cat)
            seen.add(cat)

    # 1) 节点规则：任意节点词命中则按 CATEGORY_PRIORITY 展开其允许分类
    node_cats: set[str] = set()
    for node, cats in asset_taxonomy.NODE_CATEGORY_RULES.items():
        if node and node in blob:
            node_cats |= set(cats)
    for cat in asset_taxonomy.CATEGORY_PRIORITY:
        if cat in node_cats:
            _add(cat)

    # 2) 关键词子串命中
    for cat in asset_taxonomy.CATEGORY_PRIORITY:
        keywords = asset_taxonomy.CATEGORY_KEYWORDS.get(cat) or ()
        if any(kw and kw in blob for kw in keywords):
            _add(cat)

    # 3) 请求分类（若本身就是画面分类）
    req = (category or "").strip()
    if req in asset_taxonomy.CATEGORY_PRIORITY:
        _add(req)

    # 4) 全量兜底
    for cat in asset_taxonomy.CATEGORY_PRIORITY:
        _add(cat)

    return ordered


def _asset_rel_path(asset: dict) -> str:
    return str(asset.get("filepath") or "").strip()


def _file_exists(static_dir: Path, rel: str) -> bool:
    if not rel:
        return False
    path = Path(rel)
    if not path.is_absolute():
        path = Path(static_dir) / path
    return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES


def _fallback_scan(static_dir: Path, count: int) -> list[dict]:
    """与 xhs_cards._photo_sources 一致的全量扫描兜底（无 asset_id）。"""
    roots = [
        Path(static_dir) / "assets" / "thumbnails",
        Path(static_dir) / "assets" / "library" / "image",
    ]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(
                sorted(
                    p for p in root.iterdir()
                    if p.suffix.lower() in _IMAGE_SUFFIXES
                )
            )
    out: list[dict] = []
    for path in candidates:
        try:
            rel = path.resolve().relative_to(Path(static_dir).resolve()).as_posix()
        except ValueError:
            continue
        out.append({"path": rel, "asset_id": None})
        if len(out) >= count:
            break
    return out


def pick_photos(db, static_dir: Path, topic: str, category: str, count: int) -> list[dict]:
    """按分类取图；不足 count 时全量扫描兜底。

    返回 [{'path': 相对 static 路径, 'asset_id': id|None}, ...]
    """
    if count <= 0:
        return []

    picked: list[dict] = []
    seen_ids: set[int] = set()
    seen_paths: set[str] = set()

    for cat in topic_categories(topic, category):
        assets = db.list_assets(file_type="image", category=cat, status="active") or []
        for asset in assets:
            rel = _asset_rel_path(asset)
            if not _file_exists(static_dir, rel):
                continue
            aid = asset.get("id")
            if aid is not None and int(aid) in seen_ids:
                continue
            if rel in seen_paths:
                continue
            if aid is not None:
                seen_ids.add(int(aid))
            seen_paths.add(rel)
            picked.append({"path": rel, "asset_id": int(aid) if aid is not None else None})
            if len(picked) >= count:
                return picked

    if len(picked) < count:
        for item in _fallback_scan(static_dir, count):
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            picked.append(item)
            if len(picked) >= count:
                break

    return picked
