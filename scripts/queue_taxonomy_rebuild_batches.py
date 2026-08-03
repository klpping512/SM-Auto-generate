"""排队将存量素材重建到当前 PROCESSING_VERSION。

示例：
    python3 scripts/queue_taxonomy_rebuild_batches.py
    python3 scripts/queue_taxonomy_rebuild_batches.py --base-url http://127.0.0.1:8080 --batch-size 100

需要本机已登录可用的管理员 JWT；默认读取环境变量 ADMIN_TOKEN，
或使用 --username/--password 调登录接口。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="循环调用 rebuild-taxonomy / process-pending")
    parser.add_argument("--base-url", default=os.environ.get("LOGIFLOW_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=8)
    parser.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    parser.add_argument("--username", default=os.environ.get("ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD", ""))
    return parser.parse_args()


def _token(client: httpx.Client, args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    if not args.password:
        raise SystemExit("请提供 --token / ADMIN_TOKEN，或 --password / ADMIN_PASSWORD")
    response = client.post("/api/auth/login", json={"username": args.username, "password": args.password})
    response.raise_for_status()
    return response.json()["access_token"]


def main() -> None:
    args = _parse_args()
    with httpx.Client(base_url=args.base_url, timeout=120.0) as client:
        headers = {"Authorization": f"Bearer {_token(client, args)}"}
        queued = 0
        batches = []
        for index in range(args.max_batches):
            response = client.post(
                "/api/assets/rebuild-taxonomy",
                headers=headers,
                json={"limit": args.batch_size},
            )
            response.raise_for_status()
            payload = response.json()
            count = int(payload.get("count") or 0)
            batches.append(payload)
            queued += count
            if count == 0:
                break
        pending = client.post("/api/assets/process-pending", headers=headers)
        pending.raise_for_status()
        print(json.dumps({
            "queued_rebuild": queued,
            "batches": batches,
            "process_pending": pending.json(),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
