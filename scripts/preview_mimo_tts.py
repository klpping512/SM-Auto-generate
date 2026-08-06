"""Synthesize one sample line with MiMo v2.5-tts so you can listen before

using it in the render pipeline of video_renderer.py. Does not
touch any job/render code; only calls video_renderer.synthesize_mimo_tts and
writes a local wav file.

Run from the repository root:
    python3 scripts/preview_mimo_tts.py
    python3 scripts/preview_mimo_tts.py --text "自定义一句话" --voice mimo_default
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import video_renderer

SAMPLE_TEXT = "R60公路发生货车侧翻，西开普的发货计划要不要按原路线走？先别急着承诺时效，跟承运方确认清楚这三件事。"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default=SAMPLE_TEXT)
    parser.add_argument("--voice", default=video_renderer.MIMO_TTS_VOICE)
    parser.add_argument("--style", default=video_renderer.MIMO_TTS_DEFAULT_STYLE)
    parser.add_argument("--output", default=str(ROOT / "static" / "uploads" / "mimo-tts-preview.wav"))
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    video_renderer.synthesize_mimo_tts(args.text, args.voice, output, style_instruction=args.style)
    print(f"已生成：{output}")
    print(f"音色：{args.voice}　风格指令：{args.style}")
    print(f"播放：afplay '{output}'")


if __name__ == "__main__":
    main()
