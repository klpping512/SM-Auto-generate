"""Run C-end chat-to-video closure checks without manually choosing Hooks.

Each scenario is phrased as a seller would ask it.  The script creates an
isolated normal user, calls the same chat and video APIs used by chat.html, and
only proceeds when the internal model returns a same-event Hook pair.  A queued
or failed scenario remains a failed test result; it never substitutes another
event or generic video.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from typing import Any

import httpx


CEND_API_TIMEOUT_SECONDS = 180.0


SELLER_SCENARIOS = (
    "Beitbridge 边境卡车排队，我这票货要进南非海外仓。帮我做一条 60 秒视频，提醒我入库预约前先确认哪些事。",
    "Beitbridge 堵车时，我的 Takealot 补货还在路上。帮我生成一条给卖家看的 60 秒提醒视频，重点讲该先核对哪些库存节点。",
    "客户一直问 Beitbridge 这票货什么时候到，我不想乱承诺。帮我做一条 60 秒视频，教我怎么先把在途信息说清楚。",
    "Beitbridge 口岸卡车滞留，我广州仓的下一批货还要不要按原计划发？帮我生成一条 60 秒物流提醒视频。",
    "边境排队会影响南非仓库收货节奏吗？帮我做一条 60 秒视频，给跨境卖家讲入库和分拨前要确认什么。",
    "R60 从 Robertson 到 Worcester 有卡车侧翻，我的货车要走这段路。帮我生成一条 60 秒视频，提醒发货前怎么确认路线。",
    "R60 事故后，西开普的货还要按原路线送仓吗？帮我做一条 60 秒视频，讲卖家需要先问承运方哪些问题。",
    "仓库今天安排提货会经过 R60，听说有货车侧翻。帮我生成一条 60 秒提醒视频，别承诺时效，只讲怎么核对安排。",
    "R60 路况异常会不会影响我周末的配送计划？帮我做一条给南非跨境卖家看的 60 秒视频。",
    "客户想知道 R60 事故会不会影响这票货，我需要一条 60 秒视频说明怎么更新交付预期，帮我生成。",
)

# 第二轮使用用户明确给出的四个物流主题，而非围绕两个热点事件换问法。
SECOND_ROUND_SELLER_SCENARIOS = (
    "帮我生成一条 60 秒抖音视频：Takealot 真正拼的不是低价，而是库存、配送和用户体验。",
    "帮我生成一条 60 秒抖音视频：海外仓不是仓库，而是你在南非的本地团队。",
    "帮我生成一条 60 秒抖音视频：低价货代可能更贵，讲清南非物流最容易亏钱的 4 个坑。",
    "帮我生成一条 60 秒抖音视频：南非物流突发延误怎么办？讲一套备用供应链方案，避免全盘停摆。",
)

# 第三轮取自《十个主题优化版》里业务同事真实写的选题，按卖家在聊天框里
# 自然提问的口吻改写，用来检验系统面对真实业务选题（而不是围着已有热点
# 反推的问法）时能不能选到相关 Hook。三条覆盖三种不同的库内匹配预期，
# 便于把「系统能力问题」和「热点素材库内容太薄」区分开：
#   1. 路况货损  —— 库内有大量货车侧翻/事故镜头，预期强匹配。
#   2. 海关查验  —— 库内有边境卡车排队镜头，预期中等匹配。
#   3. 旺季爆仓  —— 库内没有任何仓储画面，预期匹配不到；此时正确行为是
#      如实拒绝或转入定向采集，而不是硬套一条无关热点。
REAL_TOPIC_SELLER_SCENARIOS = (
    "南非那边路况差、分拣搬运也粗暴，我这批货老是破损。帮我做一条 60 秒视频，讲发南非的包装该怎么加固。",
    "南非海关查验率听说有三到五成，我这票货怕被扣。帮我生成一条 60 秒视频，讲发货前要把哪些单证和编码核对清楚。",
    "南非黑五旺季要爆仓了，我不知道该提前多久备货。帮我做一条 60 秒视频，讲旺季备货节奏该怎么安排。",
)


def selected_seller_scenarios(
    start_index: int, limit: int, scenarios: tuple[str, ...] = SELLER_SCENARIOS,
) -> tuple[str, ...]:
    """Return a bounded 1-based slice so failed real scenarios can be rerun alone."""
    first = max(0, start_index - 1)
    return scenarios[first:first + max(1, limit)]


def _result_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    return str(payload.get("detail") or payload.get("message") or payload)[:500]


async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        return await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{method} {path} 请求失败：{exc}") from exc


async def _wait_for_terminal_job(
    client: httpx.AsyncClient, job_id: str, headers: dict[str, str], poll_seconds: float, timeout_seconds: int,
) -> dict[str, Any]:
    elapsed = 0.0
    while elapsed <= timeout_seconds:
        response = await _request(client, "GET", f"/api/video-generation/jobs/{job_id}", headers=headers)
        if response.status_code != 200:
            return {"status": "poll_failed", "error": _result_error(response)}
        job = response.json()
        if job.get("status") in {"succeeded", "failed", "needs_review", "canceled"}:
            return job
        await asyncio.sleep(poll_seconds)
        elapsed += poll_seconds
    return {"status": "timeout", "stage": "polling_timeout"}


async def run(
    base_url: str, *, timeout_seconds: int, poll_seconds: float, limit: int, start_index: int = 1,
    scenarios: tuple[str, ...] = SELLER_SCENARIOS,
) -> dict[str, Any]:
    username = f"cend-video-{secrets.token_hex(5)}"
    password = secrets.token_urlsafe(24)
    timeout = httpx.Timeout(CEND_API_TIMEOUT_SECONDS, connect=15.0)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        signup = await _request(client, "POST", "/api/auth/signup", json={
            "username": username, "password": password, "display_name": "C 端视频验收",
        })
        if signup.status_code != 201:
            raise RuntimeError(f"无法创建 C 端测试用户：{_result_error(signup)}")
        login = await _request(client, "POST", "/api/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            raise RuntimeError(f"无法登录 C 端测试用户：{_result_error(login)}")
        token = str(login.json().get("access_token") or "")
        if not token:
            raise RuntimeError("C 端测试登录未返回访问令牌")
        headers = {"Authorization": f"Bearer {token}"}
        rows = []
        selected = selected_seller_scenarios(start_index, limit, scenarios)
        for index, question in enumerate(selected, start=max(1, start_index)):
            row: dict[str, Any] = {"index": index, "seller_question": question}
            try:
                chat = await _request(client, "POST", "/api/ai/chat", headers=headers, json={
                    "messages": [{"role": "user", "content": question}], "platforms": ["douyin"],
                })
                if chat.status_code != 200:
                    row.update({"status": "chat_failed", "error": _result_error(chat)})
                    rows.append(row)
                    continue
                chat_body = chat.json()
                retrieval = chat_body.get("hotspot_retrieval") or {}
                video = retrieval.get("video") or {}
                hook_ids = video.get("hotspot_event_ids") or []
                if retrieval.get("status") != "matched" or not 1 <= len(hook_ids) <= 2:
                    row.update({
                        "status": "no_matching_hook_pair", "retrieval_status": retrieval.get("status"),
                        "retrieval_message": retrieval.get("message"),
                    })
                    rows.append(row)
                    continue
                row["hook_ids"] = hook_ids
                job_response = await _request(client, "POST", "/api/ai/chat/dual-library-video", headers=headers, json={
                    "topic": question, "hotspot_event_ids": hook_ids, "platform": "douyin",
                    "target_duration_ms": 60_000, "session_id": f"cend-e2e-{index}",
                    "idempotency_key": f"cend-e2e-{index}",
                })
                if job_response.status_code != 202:
                    row.update({"status": "task_create_failed", "error": _result_error(job_response)})
                    rows.append(row)
                    continue
                # The endpoint creates the project/job synchronously and returns
                # them directly (same contract static/chat.html's pollRenderStatus
                # relies on) — it does not wrap them in a pollable "task" object.
                job_payload = job_response.json()
                project_id = str((job_payload.get("project") or {}).get("id") or "")
                job_id = str((job_payload.get("job") or {}).get("id") or job_payload.get("job_id") or "")
                if not project_id or not job_id:
                    row.update({"status": "task_create_failed", "error": "接口没有返回视频项目/任务 ID"})
                    rows.append(row)
                    continue
                row["project_id"] = project_id
                row["job_id"] = job_id
                job = await _wait_for_terminal_job(client, job_id, headers, poll_seconds, timeout_seconds)
                row.update({
                    "status": job.get("status") or "unknown", "stage": job.get("stage"),
                    "preview_path": job.get("preview_path"), "output_path": job.get("output_path"),
                    "quality_report": job.get("quality_report"), "error": job.get("error_message") or job.get("error"),
                })
            except RuntimeError as exc:
                row.update({"status": "request_failed", "error": str(exc)})
            rows.append(row)
    return {
        "test_user": username,
        "requested": len(selected),
        "completed": sum(item.get("status") == "succeeded" for item in rows),
        "needs_review": sum(item.get("status") == "needs_review" for item in rows),
        "failed": sum(item.get("status") not in {"succeeded", "needs_review"} for item in rows),
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="通过真实聊天 API 执行 C 端热点双素材视频闭环")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--start-index", type=int, default=1, help="从第几条自然用户问法开始")
    parser.add_argument("--second-round", action="store_true", help="执行用户指定的四个第二轮主题")
    parser.add_argument("--real-topics", action="store_true", help="执行《十个主题优化版》改写的真实业务选题")
    args = parser.parse_args()
    if args.real_topics:
        scenarios = REAL_TOPIC_SELLER_SCENARIOS
    elif args.second_round:
        scenarios = SECOND_ROUND_SELLER_SCENARIOS
    else:
        scenarios = SELLER_SCENARIOS
    bound = len(scenarios)
    result = asyncio.run(run(
        args.base_url, timeout_seconds=max(30, args.timeout_seconds),
        poll_seconds=max(0.5, args.poll_seconds),
        limit=max(1, min(bound, args.limit)),
        start_index=max(1, min(bound, args.start_index)),
        scenarios=scenarios,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["completed"] + result["needs_review"] == result["requested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
