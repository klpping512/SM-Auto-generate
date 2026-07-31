"""Run the bounded Qwen video-quality MVP from a local terminal."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db
from video_quality.schemas import VideoQualityInput
from video_quality.service import run_quality_mvp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成视频自动质检 MVP")
    parser.add_argument("--input-json", help="包含 MVP 输入结构的 JSON 文件")
    parser.add_argument("--video-source", help="本地视频路径或 HTTPS 视频 URL")
    parser.add_argument("--original-prompt", help="原始视频生成提示词")
    parser.add_argument("--storyboard-json", help="分镜 JSON 文件")
    parser.add_argument("--reference-image", action="append", dest="reference_images")
    parser.add_argument("--target-platform")
    parser.add_argument("--mode", choices=("efficient", "balanced", "detailed"))
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--auto-regenerate", action="store_true", default=None)
    parser.add_argument("--output-dir", help="质检产物目录")
    return parser


def _read_json(path: str) -> dict:
    value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 文件必须是对象：{path}")
    return value


def build_request(args: argparse.Namespace) -> VideoQualityInput:
    payload = _read_json(args.input_json) if args.input_json else {}
    if args.storyboard_json:
        storyboard = _read_json(args.storyboard_json)
        payload["storyboard"] = storyboard
        if not payload.get("original_prompt"):
            payload["original_prompt"] = str(storyboard.get("title") or "")
    overrides = {
        "video_source": args.video_source,
        "original_prompt": args.original_prompt,
        "reference_images": args.reference_images,
        "target_platform": args.target_platform,
        "mode": args.mode,
        "max_frames": args.max_frames,
        "auto_regenerate": args.auto_regenerate,
    }
    payload.update({key: value for key, value in overrides.items() if value is not None})
    if not payload.get("video_source"):
        raise ValueError("必须通过 --video-source 或 --input-json 提供 video_source")
    return VideoQualityInput.model_validate(payload)


async def _run(args: argparse.Namespace) -> dict:
    request = build_request(args)
    output_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir
        else PROJECT_ROOT / "data" / "video-quality-runs" / (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        )
    )
    return await run_quality_mvp(
        request,
        output_dir,
        job_id=f"cli-video-quality-{uuid4().hex}",
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    db.init_db()
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        print(f"视频质检失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "run_dir": result["run_dir"],
        "overall_score": result["report"]["overall_score"],
        "passed": result["report"]["passed"],
        "issue_count": len(result["problem_segments"]),
        "regeneration_action": result["regeneration_decision"]["action"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
