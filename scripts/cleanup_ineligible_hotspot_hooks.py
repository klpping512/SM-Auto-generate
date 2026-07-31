"""Preview or apply the same legacy-Hook cleanup used by the admin UI.

This tool never deletes hotspot mother videos.  It removes only short Hook rows
that fail the confirmed/factful/local-proxy/RAG-scope gate and their generated
Hook proxy files.  Run without ``--apply`` first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app
import database as db


def main() -> int:
    parser = argparse.ArgumentParser(description="清理不满足热点 Hook 硬门禁的旧记录")
    parser.add_argument("--apply", action="store_true", help="实际删除；默认只预览")
    args = parser.parse_args()

    db.init_db()
    invalid = [
        event for event in db.list_hotspot_event_clips()
        if not app._is_confirmed_renderable_hotspot_hook(event)
    ]
    result = {
        "mode": "apply" if args.apply else "preview",
        "event_clip_count": len(invalid),
        "event_clip_ids": [int(event["id"]) for event in invalid],
        "titles": [str(event.get("title_zh") or event.get("title_en") or "") for event in invalid],
    }
    if args.apply and invalid:
        deleted = db.delete_hotspot_event_clips(result["event_clip_ids"])
        files = app._delete_hotspot_library_files(deleted.pop("file_paths"))
        result.update(deleted)
        result.update(files)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
