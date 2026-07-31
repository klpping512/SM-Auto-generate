"""Non-secret MiniMax JSON compatibility probe for a configured text role."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
import model_router


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("planner_text", "critic"), default="critic")
    parser.add_argument("--max-tokens", type=int, default=600)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    db.init_db()
    route = model_router.get_route(args.role)
    key = os.environ.get(str(route["api_key_env"]) or "")
    if not key:
        raise SystemExit("未配置该角色所需密钥")
    payload = {
        "model": route["model"],
        "messages": [
            {"role": "system", "content": "只返回一个 JSON 对象。"},
            {"role": "user", "content": "返回 {\"approved\":true,\"issues\":[]}。"},
        ],
        "max_tokens": max(32, args.max_tokens),
        "response_format": {"type": "json_object"},
        **model_router._safe_request_options(route),
    }
    with httpx.Client(timeout=float(route["timeout"])) as client:
        response = client.post(route["base_url"].rstrip("/") + "/chat/completions",
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload)
        response.raise_for_status()
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    summary = {
        "role": args.role,
        "model": route["model"],
        "finish_reason": choice.get("finish_reason"),
        "message_keys": sorted(message),
        "content_length": len(str(message.get("content") or "")),
        "reasoning_length": len(str(message.get("reasoning_content") or "")),
        "usage": data.get("usage") or {},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
