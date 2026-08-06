"""Configure all production model roles onto Xiaomi MiMo Token Plan.

Decision / planning / critic  → mimo-v2.5-pro
Chat / vision / video QA      → mimo-v2.5
TTS                           → mimo-v2.5-tts
ASR (speech only, optional)   → mimo-v2.5-asr

Run from the repository root:
    python3 scripts/configure_mimo_all_routes.py
    python3 scripts/configure_mimo_all_routes.py --verify

Credentials stay in local ``.env`` / ``MIMO_API_KEY`` only.
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

MIMO_BASE_URL = model_router.MIMO_BASE_URL


def _routes() -> list[dict]:
    return [
        {
            "role": "planner_text", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-pro",
            "capabilities": ["text"], "timeout": 90, "max_tokens": 1800,
            "cost_profile": "high", "enabled": True,
            # Portable flags: model_router maps enable_thinking=false → MiMo
            # thinking.type=disabled (MiMo ignores MiniMax reasoning_split).
            "request_options": {"reasoning_split": True, "enable_thinking": False},
        },
        {
            "role": "chat_text", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
            "capabilities": ["text"], "timeout": 60, "max_tokens": 2200,
            "cost_profile": "medium", "enabled": True,
        },
        {
            "role": "critic", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-pro",
            "capabilities": ["text"], "timeout": 90, "max_tokens": 1400,
            "cost_profile": "high", "enabled": True,
            "request_options": {"reasoning_split": True, "enable_thinking": False},
        },
        {
            "role": "vision_tagger", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
            "capabilities": ["text", "vision"], "timeout": 60, "max_tokens": 900,
            "json_mode": False, "cost_profile": "medium", "enabled": True,
        },
        {
            "role": "video_evaluator", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5",
            "capabilities": ["text", "vision"], "timeout": 120, "max_tokens": 1800,
            "cost_profile": "medium", "enabled": True,
        },
        {
            "role": "tts", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-tts",
            "capabilities": ["audio"], "timeout": 60, "max_tokens": 0,
            "cost_profile": "low", "enabled": True,
        },
        {
            "role": "asr", "provider": "mimo", "base_url": MIMO_BASE_URL,
            "api_key_env": "MIMO_API_KEY", "model": "mimo-v2.5-asr",
            "capabilities": ["audio"], "timeout": 120, "max_tokens": 0,
            "cost_profile": "medium", "enabled": True,
        },
    ]


async def _verify() -> None:
    job_id = model_router.route_scoped_job_id("mimo-all-routes-smoke-v1", "chat_text")
    result = await model_router.call_text(
        job_id,
        "chat_text",
        [
            {"role": "system", "content": "只返回一个 JSON 对象，不要解释。"},
            {"role": "user", "content": "返回 {\"status\":\"ok\"}"},
        ],
        prompt_version="mimo-all-routes-smoke-v1",
        json_mode=True,
        max_output_tokens=64,
    )
    usage = result.get("usage") or {}
    print(
        f"MiMo chat_text 连通成功：model={model_router.get_route('chat_text').get('model')}，"
        f"输入={usage.get('input_tokens', 0)}，输出={usage.get('output_tokens', 0)}"
    )
    print(f"返回内容：{str(result.get('content') or '').strip()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="配置后发送一次最小文本请求")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not os.environ.get("MIMO_API_KEY"):
        raise SystemExit("缺少 MIMO_API_KEY；请仅在本地 .env 中配置")

    db.init_db()
    for route in _routes():
        model_router.save_route(route["role"], route)
        print(f"{route['role']} → {route['model']} @ {route['base_url']}")

    if args.verify:
        asyncio.run(_verify())


if __name__ == "__main__":
    main()
