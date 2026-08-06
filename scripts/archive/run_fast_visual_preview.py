"""Create a fast, silent internal preview from Buffalo warehouse proof only.

This deliberately excludes generic port/ship stock: it cannot prove customs
clearance, South African last-mile delivery, or Buffalo operations.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db


def main() -> int:
    db.init_db()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("缺少 FFmpeg/ffprobe")
    owned = [
        item for item in db.list_assets(file_type="video", status="active")
        if not item.get("hotspot_id")
        and str(item.get("source") or "") in {"upload", "directory", "manual", "local"}
        and str(item.get("primary_category") or item.get("category") or "") in {"warehouse", "staff", "facility"}
    ]
    if len(owned) < 4:
        raise SystemExit("可核验的 Buffalo 仓储/分拣原素材不足 4 条")
    # Keep this deliberately small: it is an immediate internal proof preview,
    # not the final 60-second deliverable.
    sources = [PROJECT_ROOT / "static" / item["filepath"] for item in owned[:4]]
    output = PROJECT_ROOT / "static" / "uploads" / "video" / "buffalo-warehouse-proof-preview.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y"]
    for source in sources:
        command += ["-stream_loop", "-1", "-i", str(source)]
    filters = []
    for index in range(len(sources)):
        filters.append(
            f"[{index}:v]trim=duration=4,setpts=PTS-STARTPTS,scale=360:640:force_original_aspect_ratio=increase,"
            f"crop=360:640,setsar=1,format=yuv420p[v{index}]"
        )
    chain = "".join(f"[v{index}]" for index in range(len(sources)))
    filters.append(f"{chain}concat=n={len(sources)}:v=1:a=0[outv]")
    command += ["-filter_complex", ";".join(filters), "-map", "[outv]", "-r", "24", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=240)
    if result.returncode:
        raise SystemExit(result.stderr[-4000:])
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(output)], check=True, capture_output=True, text=True)
    print(json.dumps({
        "output": str(output),
        "scope": "仅 Buffalo 仓储/分拣执行画面；不声称清关或末端配送",
        "sources": [str(item) for item in sources],
        "probe": json.loads(probe.stdout),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
