"""真实 job 编码参数 A/B 对比：同一正式 job 输入，改前/改后两套参数各渲染一次。

用法：python3 scripts/ab_compare_render_params.py [source_job_id]
方法：完整重放 render_job(preview=False)，但把 TTS 替换为源 job 工作目录里
已有的真实旁白 wav（零 API 调用、逐位相同输入），只对比 FFmpeg 编码路径：
总耗时、逐条 ffmpeg 耗时、产物体积。测试 job 行跑完自动清理。
"""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db  # noqa: E402
import video_renderer as vr  # noqa: E402

SOURCE_JOB = sys.argv[1] if len(sys.argv) > 1 else "a5104b2e81364036a1760d7c3c269b60"
SOURCE_WORK = ROOT / "static" / "uploads" / "render" / SOURCE_JOB


def _build_voice_map() -> dict[str, Path]:
    """按文案建立 文案 -> 源 wav 的确定性映射（含压缩后文案）。"""
    source = db.get_render_job(SOURCE_JOB)
    report = source.get("quality_report") or {}
    compactions = {item["original"]: item["rendered"]
                   for item in report.get("voiceover_compactions") or []}
    voice_map: dict[str, Path] = {}
    for index, scene in enumerate(source["script"]["scenes"]):
        original = scene.get("voiceover") or ""
        voice_map[original] = SOURCE_WORK / f"voice-{index}.wav"
        if original in compactions:
            voice_map[compactions[original]] = SOURCE_WORK / f"voice-{index}-shortened.wav"
    return voice_map


VOICE_MAP = _build_voice_map()


def _replay_voiceover(text: str, output: Path, *, tts_provider=None, voice="",
                      style_instruction=None) -> dict:
    """用源 job 的真实旁白 wav 替代 TTS：输入逐位一致，且无任何 API 调用。"""
    source_wav = VOICE_MAP.get((text or "").strip()) or VOICE_MAP.get(text)
    if not source_wav or not source_wav.is_file():
        raise RuntimeError(f"源 job 没有与文案匹配的旁白 wav: {text[:30]}…")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(source_wav.read_bytes())
    return {"provider": "replay", "cache_hit": True, "attempts": 1,
            "elapsed_ms": 0, "fallback_used": False, "fallback_reason": None,
            "replayed_from": source_wav.name}


# 逐条 ffmpeg 命令计时
_ffmpeg_timings: list[dict] = []
_orig_run = vr.run_cancelable_process


def _timed_run(job_id, command, *, timeout=180, cancel_check=None):
    started = time.monotonic()
    try:
        return _orig_run(job_id, command, timeout=timeout, cancel_check=cancel_check)
    finally:
        _ffmpeg_timings.append({"seconds": round(time.monotonic() - started, 2),
                                "timeout": timeout, "argv": command})


vr.synthesize_scene_voiceover = _replay_voiceover
vr.run_cancelable_process = _timed_run


def run_variant(tag: str, preset: str, intermediate_crf: str, final_crf: str,
                preview: bool = False) -> dict:
    vr.RENDER_FINAL_PRESET = preset
    vr.RENDER_INTERMEDIATE_CRF = intermediate_crf
    vr.RENDER_FINAL_CRF = final_crf
    source = db.get_render_job(SOURCE_JOB)
    if not source:
        raise SystemExit(f"源 job 不存在: {SOURCE_JOB}")
    job_id = f"abtest-{tag}-{uuid.uuid4().hex[:8]}"
    db.create_render_job(job_id, json.loads(json.dumps(source["script"])), source["voice"],
                         source.get("created_by") or 1)
    output_name = f"abtest-{tag}-{job_id}.mp4"
    _ffmpeg_timings.clear()
    started = time.monotonic()
    vr.render_job(job_id, ROOT / "static", output_name=output_name, preview=preview)
    total = round(time.monotonic() - started, 2)
    job = db.get_render_job(job_id)
    output = ROOT / "static" / (job.get("output_path") or "")
    # 抽样实际 ffmpeg 命令，确认分支参数落在预期档位
    sample_scene = next((item["argv"] for item in _ffmpeg_timings
                         if "-filter_complex" in item["argv"] and "xfade" not in ";".join(item["argv"])), [])
    result = {
        "tag": tag,
        "job_id": job_id,
        "params": {"preset": preset, "intermediate_crf": intermediate_crf, "final_crf": final_crf},
        "status": job.get("status"),
        "stage": job.get("stage"),
        "error": job.get("error"),
        "total_seconds": total,
        "ffmpeg_seconds": round(sum(item["seconds"] for item in _ffmpeg_timings), 2),
        "ffmpeg_calls": len(_ffmpeg_timings),
        "per_call_seconds": [item["seconds"] for item in _ffmpeg_timings],
        "output_path": str(output),
        "output_bytes": output.stat().st_size if output.is_file() else None,
        "quality_status": (job.get("quality_report") or {}).get("status"),
        "final_duration": (job.get("quality_report") or {}).get("duration"),
        "scene_cmd_preset": sample_scene[sample_scene.index("-preset") + 1] if "-preset" in sample_scene else None,
        "scene_cmd_crf": sample_scene[sample_scene.index("-crf") + 1] if "-crf" in sample_scene else None,
        "transition_type": (job.get("quality_report") or {}).get("transition", {}).get("type"),
    }
    # 清理测试 job 行，避免污染工作台
    with db.get_conn() as conn:
        conn.execute("DELETE FROM video_render_jobs WHERE id=?", (job_id,))
    return result


def main():
    print(f"源 job: {SOURCE_JOB}（TTS 用源 wav 重放，无 API 调用）")
    old = run_variant("old", preset="veryfast", intermediate_crf="23", final_crf="20")
    print("OLD:", json.dumps(old, ensure_ascii=False))
    new = run_variant("new", preset="medium", intermediate_crf="18", final_crf="20")
    print("NEW:", json.dumps(new, ensure_ascii=False))
    preview = run_variant("preview", preset="medium", intermediate_crf="18", final_crf="20", preview=True)
    print("PREVIEW:", json.dumps(preview, ensure_ascii=False))
    print("\n===== 对比 =====")
    print(json.dumps({"old": old, "new": new, "preview": preview}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
