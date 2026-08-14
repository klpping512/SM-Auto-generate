"""Configure the production model roles for MiniMax Token Plan.

The script never writes credentials.  The key must already exist in the
process environment as ``MINIMAX_TOKEN_PLAN_KEY`` when ``--apply`` is used.
Without ``--apply`` it only prints the intended route matrix.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import database as db
import model_router


def route_matrix() -> list[dict]:
    text_base = model_router.MINIMAX_OPENAI_BASE_URL
    vision_base = model_router.MINIMAX_ANTHROPIC_BASE_URL
    routes = []
    for role, model, max_tokens, cost in (
        ("chat_text", "MiniMax-M3", 2200, "medium"),
        ("planner_text", "MiniMax-M3", 1800, "high"),
        ("critic", "MiniMax-M3", 1400, "high"),
    ):
        routes.append({
            "role": role,
            "provider": "minimax_openai",
            "base_url": text_base,
            "api_key_env": "MINIMAX_TOKEN_PLAN_KEY",
            "model": model,
            "capabilities": ["text"],
            "timeout": 120 if role != "chat_text" else 90,
            "max_tokens": max_tokens,
            "cost_profile": cost,
            "request_options": {"reasoning_split": True, "enable_thinking": False},
            "enabled": True,
        })
    for role, timeout, max_tokens in (
        ("vision_tagger", 90, 900),
        ("video_evaluator", 180, 1800),
        ("hook_visual_critic", 120, 900),
    ):
        routes.append({
            "role": role,
            "provider": "minimax_anthropic",
            "base_url": vision_base,
            "api_key_env": "MINIMAX_TOKEN_PLAN_KEY",
            "model": "MiniMax-M3",
            "capabilities": ["text", "vision"],
            "timeout": timeout,
            "max_tokens": max_tokens,
            "cost_profile": "medium",
            "json_mode": False,
            "request_options": {"enable_thinking": False},
            "enabled": True,
        })
    routes.append({
        "role": "tts",
        "provider": "minimax_tts",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key_env": "MINIMAX_TOKEN_PLAN_KEY",
        "model": "speech-2.8-turbo",
        "capabilities": ["audio"],
        "timeout": 90,
        "max_tokens": 1,
        "cost_profile": "low",
        "request_options": {},
        "enabled": True,
    })
    return routes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the route matrix to model_role_configs")
    args = parser.parse_args()
    db.init_db()
    routes = route_matrix()
    if args.apply:
        if not str(os.environ.get("MINIMAX_TOKEN_PLAN_KEY") or "").strip():
            raise SystemExit("--apply 需要先配置 MINIMAX_TOKEN_PLAN_KEY；脚本不会写入密钥")
        for route in routes:
            db.upsert_model_route(route)
    print(json.dumps([
        {
            "role": route["role"],
            "provider": route["provider"],
            "base_url": route["base_url"],
            "api_key_env": route["api_key_env"],
            "model": route["model"],
            "capabilities": route["capabilities"],
            "enabled": route["enabled"],
        }
        for route in routes
    ], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
