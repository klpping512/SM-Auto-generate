"""Configure MiniMax Token Plan for the two text-only content-decision roles.

Run from the repository root:
    python3 scripts/configure_minimax_text_routes.py --verify

The real credential is read only from local ``.env`` / ``MINIMAX_API_KEY`` and
is never stored in SQLite, source control, logs, or command output.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
import model_router


MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_MODEL = "MiniMax-M2.7-highspeed"


def _route(role: str, *, max_tokens: int, timeout: int) -> dict:
    return {
        "role": role,
        "provider": "openai_compatible",
        "base_url": MINIMAX_BASE_URL,
        "api_key_env": "MINIMAX_API_KEY",
        "model": MINIMAX_MODEL,
        "capabilities": ["text"],
        "timeout": timeout,
        "max_tokens": max_tokens,
        "cost_profile": "medium",
        "request_options": {"reasoning_split": True},
        "enabled": True,
    }


async def _verify() -> None:
    job_id = model_router.route_scoped_job_id("minimax-token-plan-smoke-v2", "planner_text")
    model_router.create_budget(job_id, max_calls=1, max_input_tokens=1_000, max_output_tokens=512)
    result = await model_router.call_text(
        job_id,
        "planner_text",
        [
            {"role": "system", "content": "只返回 JSON，不要解释。"},
            {"role": "user", "content": "返回 {\"status\":\"ok\"}。"},
        ],
        prompt_version="minimax-token-plan-smoke-v2",
        max_output_tokens=512,
    )
    content = str(result.get("content") or "").strip()
    try:
        passed = json.loads(content) == {"status": "ok"}
    except json.JSONDecodeError:
        passed = False
    if not passed:
        raise RuntimeError("MiniMax 返回成功但未得到预期 JSON")
    usage = result.get("usage") or {}
    print(f"MiniMax 连通成功：model={MINIMAX_MODEL}，输入={usage.get('input_tokens', 0)}，输出={usage.get('output_tokens', 0)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="配置后发送一次最小文本请求")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not os.environ.get("MINIMAX_API_KEY"):
        raise SystemExit("缺少 MINIMAX_API_KEY；请仅在本地 .env 中配置")

    db.init_db()
    for role, max_tokens, timeout in (
        ("planner_text", 1_800, 75),
        ("critic", 1_400, 60),
    ):
        model_router.save_route(role, _route(role, max_tokens=max_tokens, timeout=timeout))
    print("MiniMax 已配置：planner_text、critic；Qwen-VL / Qwen TTS 保持不变")

    if args.verify:
        asyncio.run(_verify())


if __name__ == "__main__":
    main()
