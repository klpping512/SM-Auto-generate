"""Configure Xiaomi MiMo Token Plan for the vision_tagger role.

Qwen-VL (DashScope) ran out of quota, same account as video_evaluator.
mimo-v2.5 keeps native vision understanding, so it is a same-shape drop-in
for the existing OpenAI-compatible image_url payloads in
asset_processing.py::_visual_analysis.

Run from the repository root:
    python3 scripts/configure_mimo_vision_tagger.py --verify

The real credential is read only from local ``.env`` / ``MIMO_API_KEY`` and is
never stored in SQLite, source control, logs, or command output.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
import model_router


MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.5"


def _route() -> dict:
    return {
        "role": "vision_tagger",
        "provider": "mimo",
        "base_url": MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY",
        "model": MIMO_MODEL,
        "capabilities": ["text", "vision"],
        "timeout": 60,
        "max_tokens": 900,
        "json_mode": False,
        "cost_profile": "medium",
        "enabled": True,
    }


async def _verify() -> None:
    job_id = model_router.route_scoped_job_id("mimo-vision-tagger-smoke-v1", "vision_tagger")
    result = await model_router.call_multimodal_json(
        job_id,
        "vision_tagger",
        [
            {"role": "system", "content": "只返回一个 JSON 对象，不要解释，不要 Markdown。"},
            {"role": "user", "content": [{"type": "text", "text": "返回 {\"status\":\"ok\"}。"}]},
        ],
        prompt_version="mimo-vision-tagger-smoke-v1",
    )
    content = str(result.get("content") or "").strip()
    usage = result.get("usage") or {}
    print(f"MiMo 连通成功：model={MIMO_MODEL}，输入={usage.get('input_tokens', 0)}，输出={usage.get('output_tokens', 0)}")
    print(f"返回内容：{content}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="配置后发送一次最小多模态请求")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not os.environ.get("MIMO_API_KEY"):
        raise SystemExit("缺少 MIMO_API_KEY；请仅在本地 .env 中配置")

    db.init_db()
    model_router.save_route("vision_tagger", _route())
    print(f"vision_tagger 已切换到 MiMo：{MIMO_MODEL} @ {MIMO_BASE_URL}")

    if args.verify:
        asyncio.run(_verify())


if __name__ == "__main__":
    main()
