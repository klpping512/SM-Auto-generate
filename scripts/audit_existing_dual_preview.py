"""Run the internal fact audit against an already-rendered dual-library preview.

It does not download, render, publish, or alter a video.  This is used when a
newer content guard is introduced and existing internal QA renders need to be
rechecked without pretending that they were generated under that new guard.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
import hotspot_intake_sop
import hotspot_preview_narration


def main() -> int:
    parser = argparse.ArgumentParser(description="使用内部模型复核既有双素材库成片文案")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--event-id", required=True, type=int)
    parser.add_argument("--topic", required=True)
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    db.init_db()
    job = db.get_render_job(args.job_id)
    event = db.get_hotspot_event_clip(args.event_id)
    if not job or not event:
        raise SystemExit("找不到渲染任务或热点事件")
    script = job.get("script") or {}
    if isinstance(script, str):
        script = json.loads(script)
    scenes = [
        scene for scene in (script.get("scenes") or [])
        if scene.get("evidence_type") != "brand_endcard"
    ]
    proposal = {
        "title": str(script.get("title") or args.topic),
        "angle": "既有内部验收成片的事实复核",
        "scenes": [
            {"voiceover": str(scene.get("voiceover") or ""), "text_overlay": str(scene.get("text_overlay") or "")}
            for scene in scenes
        ],
    }
    source = db.get_hotspot(int(event["hotspot_id"])) or {}
    related = db.list_hotspot_event_clips(asset_id=event["asset_id"], hotspot_id=event["hotspot_id"])
    messages = hotspot_preview_narration.build_messages(
        args.topic,
        {"hotspot_title": source.get("title_zh") or source.get("title") or args.topic},
        scenes,
        related,
        hotspot_intake_sop.retrieve_service_evidence({"title": args.topic, "summary": source.get("summary") or ""}),
    )
    approved, critic_issues, critic_meta = hotspot_preview_narration._call_critic(  # noqa: SLF001
        messages, proposal, phase="existing-render-audit"
    )
    hard_issues = hotspot_preview_narration.deterministic_evidence_issues(proposal, related)
    result = {
        "job_id": args.job_id,
        "event_id": args.event_id,
        "approved": bool(approved and not hard_issues),
        "critic_issues": critic_issues,
        "hard_issues": hard_issues,
        "critic": critic_meta,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
