"""Idempotently configure the hook_visual_critic model route (no secrets written)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import database as db
import model_router


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="only print the current route")
    args = parser.parse_args()
    db.init_db()
    route = {
        "provider": "mimo",
        "base_url": model_router.MIMO_BASE_URL,
        "api_key_env": "MIMO_API_KEY",
        "model": "mimo-v2.5",
        "capabilities": ["text", "vision"],
        "timeout": 90,
        "max_tokens": 900,
        "json_mode": False,
        "cost_profile": "medium",
        "enabled": True,
        "request_options": {"reasoning_split": True, "enable_thinking": False},
    }
    if not args.verify:
        saved = model_router.save_route("hook_visual_critic", route)
    else:
        saved = model_router.get_route("hook_visual_critic")
    print(json.dumps({
        "role": "hook_visual_critic",
        "model": saved.get("model"),
        "api_key_env": saved.get("api_key_env"),
        "capabilities": saved.get("capabilities"),
        "timeout": saved.get("timeout"),
        "enabled": saved.get("enabled"),
        "request_options": saved.get("request_options"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
