"""Queue and inspect cloud MiniMax video runs. Does not publish."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path("/opt/distribution-manager")
if ROOT.exists():
    sys.path.insert(0, str(ROOT))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
import database as db  # noqa: E402
from models import ChatDualLibraryVideoRequest  # noqa: E402

TOPICS_30 = [
    "约翰内斯堡仓内如何避免错发漏发",
    "德班港拥堵时货代怎么改走法",
    "空运和海运发南非怎么按货值选型",
    "冷链断链后仓库要先做什么",
    "跨境清关文件不齐会卡在哪",
    "旺季爆仓时分拣线怎么分流",
    "同城当日达和次日达怎么排运力",
    "边境延误时客户通知怎么写",
    "超尺寸货进仓前要量哪三处",
    "退货反仓怎么避免二次错分",
    "危险品入南非仓的现场核对要点",
    "卡车上货顺序怎么减少压损",
    "末公里派送失败后如何二次预约",
    "港口到内陆仓的时效怎么拆",
    "库位编码混乱时怎么快速重整",
    "发票和装箱单对不上先查哪一步",
    "雨季路况对干线班次的影响",
    "拆柜入仓时如何防止混票",
    "保税仓转一般贸易要过哪些节点",
    "客户改地址后运单怎么重打",
    "周末值班仓如何处理紧急补货",
    "电池类货物仓储隔离怎么做",
    "派件超时赔偿口径怎么对客户说",
    "南非本地快递和跨境专线怎么选",
    "集装箱到港后滞箱费怎么控",
    "叉车作业区人车分流怎么落地",
    "盘点差异发现后当天怎么闭环",
    "跨境小包丢失后轨迹怎么追",
    "高温天冷藏车预冷要多久",
    "新仓开业前三天的作业检查清单",
]

TOPICS_100_EXTRA = [
    "开普敦仓晚班交接要核对哪几项",
    "空运到货后如何安排提货入仓",
    "海运整柜到港后拆箱顺序怎么定",
    "拼箱货入仓时如何避免串票",
    "报关编码填错后现场怎么纠偏",
    "查验通知来了仓库要先停哪一步",
    "放行后车辆排队出闸怎么提速",
    "超重货上架前要检查哪些承重点",
    "易碎品缠膜和打托要注意什么",
    "液体货入仓隔离区怎么划",
    "温度记录仪没电了冷链怎么补救",
    "冻柜开门作业最长能停多久",
    "干线晚点后末端怎么重排运力",
    "乡镇派送达不到门店时怎么交接",
    "客户拒收后货物回仓如何标识",
    "破损件拍照留证要拍哪几个面",
    "少件索赔前仓内要核对什么",
    "条码扫不上时人工录入怎么防错",
    "库位满了临时落地货怎么管",
    "先进先出在实际拣货里怎么执行",
    "波次拣货失败后如何重切波次",
    "播种墙堵塞时怎么快速疏导",
    "打包称重不准会引发哪些客诉",
    "面单打印失败时如何续打不重号",
    "一票多件如何防止漏装",
    "装车扫描漏件出了闸怎么追回",
    "雨天装车防湿要先备什么",
    "夜间卸货照明不足怎么保证核对",
    "叉车电量不足时作业怎么换班",
    "地牛通道被堵如何恢复动线",
    "消防通道被货占了怎么立刻清",
    "危险品泄漏演练仓库怎么做",
    "电池货退运前隔离检查清单",
    "木质包装熏蒸证明丢了怎么办",
    "原产地证和发票不一致先查哪联",
    "信用证交单缺页会卡在哪",
    "目的港滞港费怎么提前预警",
    "内陆拖车放空怎么减少往返",
    "边境排队超过一天客户怎么同步",
    "护照清关资料过期如何替换",
    "个人物品和商业货混装怎么拆",
    "电商大促前三天仓配准备清单",
    "直播间爆单后拣货如何扩容",
    "退货质检不合格怎么分拣处理",
    "换货出库如何绑定原订单",
    "赠品漏放会怎样影响签收",
    "代收货款件交接现金怎么对账",
    "签收码验证失败能否放货",
    "客户改电话后运单怎么更新",
    "地址只有小区名时怎么二次确认",
    "公司收货人下班后货物放哪",
    "周末无人仓如何处理到货",
    "公共假期运力不足怎么告知时效",
    "罢工影响港口时货代备选方案",
    "电力中断时仓内扫描怎么降级",
    "系统宕机时纸质交接怎么补录",
    "盘点抽盘和全盘分别何时做",
    "盘盈盘亏审批当天如何关账",
    "供应商送错货怎么拒收留痕",
    "越库作业跳过上架要满足什么",
    "跨境退运再出口文件怎么备",
    "保税维修货进出仓如何登记",
    "样品和正货混放怎么隔离",
    "高价值货保险拍照要覆盖哪些",
    "金库式笼车钥匙交接怎么记录",
    "监控盲区补位摄像头怎么提需求",
    "新员工上岗三天必会的核对动作",
    "客服升级投诉时仓配要回传什么",
    "品牌方验仓当天最常问哪三件事",
    "月底关账前必须清掉哪些在途异常",
]


def _run_dir() -> Path:
    path = Path(app.db.DB_PATH).resolve().parent / "cloud_reliability"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _topics(count: int) -> list[str]:
    pool = TOPICS_30 + TOPICS_100_EXTRA
    if count > len(pool):
        raise SystemExit(f"topic pool only has {len(pool)} entries")
    unique = []
    seen = set()
    for topic in pool:
        if topic in seen:
            continue
        seen.add(topic)
        unique.append(topic)
        if len(unique) >= count:
            break
    if len(unique) != count:
        raise SystemExit("topic list is not unique")
    return unique


def enqueue(run_id: str, count: int, start: int = 0) -> dict:
    user = db.get_first_admin_user()
    if not user:
        raise SystemExit("no_active_admin")
    tts_provider, voice = app.video_renderer.resolve_tts_selection(None, None, strict=False)
    topics = _topics(count)
    selected = topics[start:]
    results = []
    for offset, topic in enumerate(selected, start + 1):
        session_id = f"cloud-rel-{run_id}-{offset:03d}"
        try:
            queued = app._queue_chat_dual_library_video_job(
                ChatDualLibraryVideoRequest(
                    topic=topic,
                    hotspot_event_ids=[],
                    platform="douyin",
                    target_duration_ms=60_000,
                    chain_mode="owned_only",
                    session_id=session_id,
                    idempotency_key=f"cloud-rel-{run_id}-{offset:03d}",
                    tts_provider=tts_provider,
                    voice=voice,
                ),
                user,
            )
            job = queued.get("job") or {}
            results.append({
                "index": offset,
                "topic": topic,
                "job_id": queued.get("job_id"),
                "project_id": (queued.get("project") or {}).get("id"),
                "created": queued.get("created"),
                "job_status": job.get("status"),
                "job_stage": job.get("stage"),
                "chain_mode": "owned_only",
                "tts_provider": tts_provider,
            })
        except Exception as exc:
            results.append({
                "index": offset,
                "topic": topic,
                "error": str(exc)[:500],
            })
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    payload = {
        "run_id": run_id,
        "queued_at": _utc_now(),
        "tts_provider": tts_provider,
        "count": count,
        "queued": sum(bool(item.get("job_id")) for item in results),
        "items": results,
    }
    out = _run_dir() / f"{run_id}.json"
    if out.exists():
        previous = json.loads(out.read_text(encoding="utf-8"))
        merged = {item["index"]: item for item in previous.get("items") or []}
        for item in results:
            merged[item["index"]] = item
        payload["items"] = [merged[key] for key in sorted(merged)]
        payload["queued"] = sum(bool(item.get("job_id")) for item in payload["items"])
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "run_id": run_id,
        "queued": payload["queued"],
        "total": len(payload["items"]),
        "manifest": str(out),
        "tts_provider": tts_provider,
    }, ensure_ascii=False))
    return payload


def inspect_jobs(run_id: str) -> dict:
    manifest_path = _run_dir() / f"{run_id}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = []
    signatures = []
    for item in manifest.get("items") or []:
        job_id = item.get("job_id")
        job = db.get_video_generation_job(job_id) if job_id else None
        project = db.get_video_project(item.get("project_id")) if item.get("project_id") else None
        output_path = (job or {}).get("output_path") or ""
        abs_output = ""
        exists = False
        if output_path:
            abs_output = str(Path(app.STATIC_DIR) / output_path)
            exists = Path(abs_output).is_file()
        signature = ""
        report = (job or {}).get("quality_report") or {}
        if isinstance(report, str):
            try:
                report = json.loads(report)
            except json.JSONDecodeError:
                report = {}
        match = report.get("asset_matching") or report.get("match") or {}
        signature = str(match.get("asset_signature") or (job or {}).get("asset_signature") or "")
        if signature:
            signatures.append(signature)
        rows.append({
            "index": item.get("index"),
            "topic": item.get("topic"),
            "job_id": job_id,
            "project_id": item.get("project_id"),
            "status": (job or {}).get("status") or item.get("error") or "missing",
            "stage": (job or {}).get("stage"),
            "quality_status": (project or {}).get("quality_status") or (job or {}).get("quality_status"),
            "artifact_status": (project or {}).get("artifact_status") or (job or {}).get("artifact_status"),
            "output_path": output_path,
            "output_exists": exists,
            "error_message": ((job or {}).get("error_message") or item.get("error") or "")[:300],
            "asset_signature": signature,
            "copy_source": ((job or {}).get("source_snapshot") or {}).get("copy_source")
            if isinstance((job or {}).get("source_snapshot"), dict) else None,
        })
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    unique_sigs = set(signatures)
    repeated = len(signatures) - len(unique_sigs)
    summary = {
        "run_id": run_id,
        "inspected_at": _utc_now(),
        "counts": counts,
        "terminal": sum(counts.get(key, 0) for key in ("succeeded", "failed", "canceled")),
        "output_exists": sum(1 for row in rows if row.get("output_exists")),
        "ready_without_file": sum(
            1 for row in rows
            if row.get("status") in {"succeeded"} and not row.get("output_exists")
        ),
        "repeated_signatures": repeated,
        "items": rows,
    }
    out = _run_dir() / f"{run_id}-status.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "run_id": run_id,
        "counts": counts,
        "terminal": summary["terminal"],
        "output_exists": summary["output_exists"],
        "ready_without_file": summary["ready_without_file"],
        "repeated_signatures": repeated,
        "status_file": str(out),
    }, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["enqueue", "status"])
    parser.add_argument("--run-id", default="")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("rel-%Y%m%d-%H%M%S")
    if args.action == "enqueue":
        payload = enqueue(run_id, args.count, args.start)
        return 0 if payload["queued"] == len(payload["items"]) else 1
    inspect_jobs(run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
