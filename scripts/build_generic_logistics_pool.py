"""Build the generic_logistics evergreen-opener Hook pool (批12 #33).

Selects owned active video assets (za-stock + SA-owned mixed) by logistics scene
and creates, per scene, one parent hotspot plus confirmed ``generic_logistics``
event clips that pass the renderable Hook gate.  This lets evergreen / science
/ degraded chat topics (no news event anchor) auto-lock a neutral opening Hook
so one-click production can light up "创建60秒视频项目".

Provenance discipline (批12 铁律): opener titles / ``what_happened`` are neutral
scene descriptions — they never name 南非 and never state Buffalo service
capability.  ``hook_reason`` explicitly records this.

Gate parity: the ``--check`` path calls the real serving gate
``app._is_confirmed_renderable_hotspot_hook`` and
``producible_topics.is_generic_logistics_eligible`` so the pool口径 stays
identical to production matching.

Usage:
    python3 scripts/build_generic_logistics_pool.py --dry-run
    python3 scripts/build_generic_logistics_pool.py --apply
    python3 scripts/build_generic_logistics_pool.py --check
    python3 scripts/build_generic_logistics_pool.py --scene-budget warehouse:6,last_mile:4,border:4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
from app import _is_confirmed_renderable_hotspot_hook
from producible_topics import is_generic_logistics_eligible

STATIC_DIR = PROJECT_ROOT / "static"

# 合计 14，位于拍板的 12-20 区间；可用 --scene-budget 覆盖。
DEFAULT_SCENE_BUDGETS = {"warehouse": 6, "last_mile": 4, "border": 4}
SCENE_CATEGORIES = {
    "warehouse": ("warehouse", "facility"),
    "last_mile": ("delivery",),
    "border": ("customs",),
}
# 母文档写 za_stock/mixkit，但生产库实际 source 值带 _license 后缀；
# 短/长两种写法都接受，避免历史行差异把免版权素材漏掉。
ALLOWED_SOURCES = (
    "local_directory", "upload", "directory",
    "za_stock", "za_stock_license", "mixkit", "mixkit_license",
)
SCENE_TITLES = {
    "warehouse": {"zh": "仓库仓储作业场景", "en": "Warehouse storage and sorting scenes"},
    "last_mile": {"zh": "末端配送作业场景", "en": "Last-mile delivery operation scenes"},
    "border": {"zh": "跨境清关作业场景", "en": "Cross-border customs clearance scenes"},
}
SCENE_EVIDENCE = {
    "warehouse": {
        "what_happened": "展示了仓库内仓储、分拣与货架作业的典型画面。",
        "logistics_question": "海外仓与本地仓的仓储、分拣环节如何运作？",
    },
    "last_mile": {
        "what_happened": "展示了末端配送与派送环节的典型作业画面。",
        "logistics_question": "末端配送如何高效完成最后三公里履约？",
    },
    "border": {
        "what_happened": "展示了跨境物流清关环节的典型作业画面。",
        "logistics_question": "跨境物流的清关环节通常涉及哪些流程？",
    },
}
HOOK_REASON = "作为常青物流话题的通用开场画面，用真实作业场景引入该环节，不构成任何服务能力声明。"
TOTAL_MIN, TOTAL_MAX = 12, 20


def _segment_duration(seg: dict) -> int:
    return int(seg.get("end_ms") or 0) - int(seg.get("start_ms") or 0)


def _pick_segments(segments: list[dict]) -> list[dict]:
    """Pick ≤2 non-overlapping thumbnailed segments, preferring 5-12s then ≥3s."""
    valid = [s for s in segments if str(s.get("thumbnail_path") or "").strip()]
    strict = [s for s in valid if 5000 <= _segment_duration(s) <= 12000]
    pool = strict if strict else [s for s in valid if 3000 <= _segment_duration(s) <= 12000]
    pool.sort(key=lambda s: int(s.get("segment_index") or 0))
    chosen: list[dict] = []
    for seg in pool:
        if len(chosen) >= 2:
            break
        s0, s1 = int(seg.get("start_ms") or 0), int(seg.get("end_ms") or 0)
        if all(s1 <= int(c.get("start_ms") or 0) or int(c.get("end_ms") or 0) <= s0 for c in chosen):
            chosen.append(seg)
    return chosen


def _candidate_assets_by_scene() -> dict[str, list[dict]]:
    by_scene: dict[str, list[dict]] = {scene: [] for scene in SCENE_CATEGORIES}
    for asset in db.list_assets(status="active"):
        if str(asset.get("file_type")) != "video":
            continue
        if asset.get("hotspot_id") is not None:
            continue
        if int(asset.get("deprecated") or 0) == 1:
            continue
        if str(asset.get("source") or "") not in ALLOWED_SOURCES:
            continue
        category = str(asset.get("category") or "")
        for scene, cats in SCENE_CATEGORIES.items():
            if category in cats:
                by_scene[scene].append(asset)
                break
    for scene in by_scene:
        by_scene[scene].sort(key=lambda a: int(a["id"]))
    return by_scene


def _existing_pool_state() -> tuple[dict[str, int], set[int]]:
    """Count already-ready generic clips per scene + assets that own any clip.

    Existing clips count toward the scene budget so a re-run is a true no-op
    (幂等增量 0).  Assets owning ANY hotspot_event_clips are skipped because
    replace_hotspot_event_clips deletes that asset's existing clip rows.
    """
    per_scene = {scene: 0 for scene in SCENE_CATEGORIES}
    asset_ids_with_clips: set[int] = set()
    with db.get_conn() as conn:
        for row in conn.execute(
            "SELECT asset_id, logistics_scenes_json FROM hotspot_event_clips"
        ).fetchall():
            asset_ids_with_clips.add(int(row["asset_id"]))
        for row in conn.execute(
            "SELECT logistics_scenes_json FROM hotspot_event_clips "
            "WHERE hook_kind='generic_logistics' AND clip_status='ready'"
        ).fetchall():
            try:
                scenes = json.loads(row["logistics_scenes_json"] or "[]")
            except (TypeError, ValueError):
                scenes = []
            for scene in scenes:
                if scene in per_scene:
                    per_scene[scene] += 1
    return per_scene, asset_ids_with_clips


def build_plan(scene_budgets: dict[str, int]) -> dict[str, list[dict]]:
    by_scene = _candidate_assets_by_scene()
    existing_per_scene, assets_with_clips = _existing_pool_state()
    plan: dict[str, list[dict]] = {}
    for scene, budget in scene_budgets.items():
        remaining = int(budget) - existing_per_scene.get(scene, 0)
        entries: list[dict] = []
        if remaining > 0:
            added = 0
            for asset in by_scene.get(scene, []):
                if added >= remaining:
                    break
                if int(asset["id"]) in assets_with_clips:
                    continue
                segments = db.list_asset_segments(asset_id=int(asset["id"]), status="active", limit=20_000)
                picked = _pick_segments(segments)
                if not picked:
                    continue
                take = picked[: remaining - added]
                if not take:
                    break
                entries.append({"asset": asset, "segments": take})
                added += len(take)
        plan[scene] = entries
    return plan


def _ensure_parent(scene: str) -> int:
    parent = {
        "title": SCENE_TITLES[scene]["zh"],
        "summary": SCENE_TITLES[scene]["zh"],
        "source_url": f"buffalo://generic-logistics/{scene}",
        "publisher": "Buffalo 内部素材库",
        "published_at": "1970-01-01T00:00:00",
        "retrieved_at": "1970-01-01T00:00:00",
        "snapshot_sha256": hashlib.sha256(f"generic-logistics-{scene}".encode()).hexdigest(),
        "image_candidate_url": "",
    }
    hotspot_id, _created = db.upsert_hotspot(parent)
    return int(hotspot_id)


def apply_plan(plan: dict[str, list[dict]]) -> int:
    created_total = 0
    for scene, entries in plan.items():
        if not entries:
            continue
        hotspot_id = _ensure_parent(scene)
        for entry in entries:
            asset = entry["asset"]
            events = []
            for index, seg in enumerate(entry["segments"]):
                events.append({
                    "event_index": index,
                    "start_ms": int(seg["start_ms"]),
                    "end_ms": int(seg["end_ms"]),
                    "title_zh": SCENE_TITLES[scene]["zh"],
                    "title_en": SCENE_TITLES[scene]["en"],
                    "location": "",
                    "entities": [],
                    "keywords": [],
                    "evidence": {
                        "what_happened": SCENE_EVIDENCE[scene]["what_happened"],
                        "hook_reason": HOOK_REASON,
                        "logistics_question": SCENE_EVIDENCE[scene]["logistics_question"],
                        "event_identity": f"generic-{scene}-{asset['id']}",
                    },
                    "confidence": 0.95,
                    "review_status": "confirmed",
                    "hook_kind": "generic_logistics",
                    "logistics_scenes": [scene],
                    "segments": [seg],
                })
            created = db.replace_hotspot_event_clips(int(asset["id"]), hotspot_id, events)
            # replace hard-codes clip_status='pending'; flip to ready + set proxy path.
            with db.get_conn() as conn:
                for row in created:
                    conn.execute(
                        "UPDATE hotspot_event_clips SET clip_status='ready', clip_path=? WHERE id=?",
                        (asset["filepath"], int(row["id"])),
                    )
            created_total += len(created)
    return created_total


def _pool_clips() -> list[dict]:
    return [
        event for event in db.list_hotspot_event_clips()
        if str(event.get("hook_kind")) == "generic_logistics"
    ]


def check_pool(scene_budgets: dict[str, int]) -> int:
    clips = _pool_clips()
    failures: list[str] = []
    per_scene: dict[str, int] = {scene: 0 for scene in SCENE_CATEGORIES}
    for event in clips:
        clip_id = event.get("id")
        scenes = event.get("logistics_scenes") or []
        for scene in scenes:
            if scene in per_scene:
                per_scene[scene] += 1
        if not is_generic_logistics_eligible(event):
            failures.append(f"clip#{clip_id} is_generic_logistics_eligible=False")
        if not _is_confirmed_renderable_hotspot_hook(event):
            failures.append(f"clip#{clip_id} _is_confirmed_renderable_hotspot_hook=False")
        clip_path = str(event.get("clip_path") or "")
        if clip_path and not (STATIC_DIR / clip_path).exists():
            failures.append(f"clip#{clip_id} file missing: static/{clip_path}")
    total = len(clips)
    if not (TOTAL_MIN <= total <= TOTAL_MAX):
        failures.append(f"total {total} outside [{TOTAL_MIN},{TOTAL_MAX}]")
    for scene, count in per_scene.items():
        if count < 1:
            failures.append(f"scene {scene} has 0 clips")
    print(json.dumps({
        "total": total,
        "per_scene": per_scene,
        "budgets": scene_budgets,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def _print_plan(plan: dict[str, list[dict]], scene_budgets: dict[str, int]) -> int:
    total = 0
    for scene, entries in plan.items():
        clips = sum(len(entry["segments"]) for entry in entries)
        total += clips
        print(f"[{scene}] budget={scene_budgets.get(scene)} assets={len(entries)} new_clips={clips}")
        for entry in entries:
            asset = entry["asset"]
            segs = ",".join(f"{int(s['start_ms'])}-{int(s['end_ms'])}ms" for s in entry["segments"])
            print(f"    asset#{asset['id']} cat={asset.get('category')} src={asset.get('source')} segs=[{segs}]")
    print(f"PLAN new_clips_total={total}")
    return 0


def _parse_scene_budget(raw: str | None) -> dict[str, int]:
    budgets = dict(DEFAULT_SCENE_BUDGETS)
    if not raw:
        return budgets
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        scene, _, value = part.partition(":")
        scene = scene.strip()
        if scene not in budgets:
            raise SystemExit(f"未知场景: {scene}（可选 {','.join(budgets)}）")
        budgets[scene] = max(0, int(value.strip()))
    return budgets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build generic_logistics evergreen opener pool")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="打印计划（默认）")
    mode.add_argument("--apply", action="store_true", help="落库 + 置 ready")
    mode.add_argument("--check", action="store_true", help="只校验现有池")
    parser.add_argument("--scene-budget", default=None, help="warehouse:6,last_mile:4,border:4")
    args = parser.parse_args(argv)
    budgets = _parse_scene_budget(args.scene_budget)

    if args.check:
        return check_pool(budgets)
    plan = build_plan(budgets)
    if args.apply:
        created = apply_plan(plan)
        print(f"APPLIED created_clips={created}")
        return check_pool(budgets)
    return _print_plan(plan, budgets)


if __name__ == "__main__":
    raise SystemExit(main())
