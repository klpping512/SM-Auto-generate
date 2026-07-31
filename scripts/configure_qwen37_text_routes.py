"""Restore Qwen 3.7 Plus for the high-stakes storyboard and fact-Critic roles.

The model key remains in local environment configuration; this script stores
only its environment-variable name in the database.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
import model_router


def _route(role: str, *, max_tokens: int, timeout: int, thinking_budget: int) -> dict:
    return {
        "role": role,
        "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "model": "qwen3.7-plus",
        "capabilities": ["text"],
        "timeout": timeout,
        "max_tokens": max_tokens,
        "cost_profile": "medium",
        "request_options": {"enable_thinking": True, "thinking_budget": thinking_budget},
        "enabled": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-only", action="store_true", help="只显示拟写入的模型名，不修改数据库")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise SystemExit("缺少 DASHSCOPE_API_KEY；请仅在本地 .env 中配置")
    routes = (
        ("planner_text", 1_800, 75, 3_000),
        ("critic", 1_400, 60, 2_400),
    )
    if args.print_only:
        print("Qwen 文本路由：planner_text、critic → qwen3.7-plus")
        return
    db.init_db()
    for role, max_tokens, timeout, thinking_budget in routes:
        model_router.save_route(role, _route(role, max_tokens=max_tokens, timeout=timeout, thinking_budget=thinking_budget))
    print("已启用 Qwen 3.7 Plus：planner_text、critic；Qwen-VL / Qwen TTS 保持不变")


if __name__ == "__main__":
    main()
