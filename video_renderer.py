"""TTS and deterministic FFmpeg vertical-video rendering."""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import re
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image, ImageDraw, ImageFont

import asset_taxonomy
import database as db
from video_clip_refs import ClipReferenceError, resolve_clip_ref

logger = logging.getLogger(__name__)
from video_composition_policy import (
    is_explanation_scene,
    source_usage_report,
    subtitle_timeline_report,
)
from video_duration_budget import rebalance_scenes_to_budget, platform_budget_ms

MIMO_TTS_VOICE = "mimo_default"
MINIMAX_TTS_VOICE = "male-qn-qingse"
# 正式成片编码档位（仅非-fast 路径生效；preview/fast 路径仍走 ultrafast+crf28 保持快）
# 原则：中间过渡片近视觉无损，只让"交付段"做真正压缩，减少三段重编码的代际损失。
RENDER_FINAL_PRESET = os.environ.get("RENDER_PRESET", "medium")          # was veryfast — 同 crf 下压缩更充分=更锐
RENDER_INTERMEDIATE_CRF = os.environ.get("RENDER_INTERMEDIATE_CRF", "18")  # 中间片近视觉无损
RENDER_FINAL_CRF = os.environ.get("RENDER_FINAL_CRF", "20")              # 交付段压缩档
FORMAL_MIN_SCENES = 7
FORMAL_MAX_SCENES = 10
FORMAL_MIN_DURATION_MS = 50_000
FORMAL_MAX_DURATION_MS = 90_000
TTS_BREATHING_ROOM_SECONDS = 0.35
# TTS 的实际语速会随停顿和专有名词改变。真实短 Hook 不能循环，
# 因此允许一次最多 25% 的保守加速来吸收这类测得的波动；超过此阈值仍
# 必须失败，而不是把听感明显失真的旁白强塞进现场画面。
MAX_NATURAL_TTS_SPEEDUP = 1.25
# 素材严重不足快速失败阈值：真实画面连旁白时长的一半都盖不住时，单次收缩+
# 重合成只会产出一句残缺旁白，并白烧一次外部 TTS（约 90 秒/次、还会重试）——
# 直接失败。轻度溢出（比例高于该值）仍走原有“最多加速 25% + 一次文本收缩”逻辑。
MIN_FOOTAGE_TO_NARRATION_RATIO = 0.5
# A completed sentence may breathe briefly, but multi-second silent tails make
# otherwise-grounded real footage look stalled and lower the audio QA result.
MAX_TRAILING_NARRATION_GAP_SECONDS = 0.75
TTS_MAX_ATTEMPTS = 3
TTS_RETRY_DELAY_SECONDS = 1.0
# A short video must read as one native mobile format.  Showing a complete
# landscape source inside a blurred portrait shell technically keeps 9:16
# output, but viewers still experience it as a horizontal card between vertical
# scenes.  Every production beat therefore fills one portrait frame; source
# edges may be cropped, never letterboxed or inset.
PORTRAIT_FRAME_POLICY = "full_bleed_center_crop"


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in str(text or "")
    )


def _subtitle_font_candidates() -> list[str]:
    configured = str(os.environ.get("SUBTITLE_FONT_PATH") or "").strip()
    return [item for item in [
        configured,
        # macOS development machine
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        # Rocky/RHEL package: google-noto-sans-cjk-ttc-fonts
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-sans-cjk-ttc/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Regular.otf",
    ] if item]


def _load_subtitle_font(text: str, font_size: int):
    """Load a font with CJK glyphs; never silently render Chinese as tofu boxes."""
    for font_path in _subtitle_font_candidates():
        if not Path(font_path).exists():
            continue
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            logger.warning("字幕字体无法读取: %s", font_path, exc_info=True)
    if _contains_cjk(text):
        raise RuntimeError(
            "云端未找到支持中文的字幕字体；请安装 Noto CJK，或设置 SUBTITLE_FONT_PATH"
        )
    return ImageFont.load_default()


def tts_voice_options(*, mimo_available: bool | None = None) -> list[dict]:
    """Return selectable TTS voices with provider availability flags."""
    mimo_ok = bool(os.environ.get("MIMO_API_KEY")) if mimo_available is None else bool(mimo_available)
    minimax_ok = bool(os.environ.get("MINIMAX_TOKEN_PLAN_KEY"))
    options = [
        {
            "provider": "mimo",
            "id": MIMO_TTS_VOICE,
            "label": "MiMo 默认",
            "available": mimo_ok,
            "disabled_reason": "" if mimo_ok else "未配置 MIMO_API_KEY",
            "preview_supported": True,
        },
        {
            "provider": "minimax",
            "id": MINIMAX_TTS_VOICE,
            "label": "MiniMax Speech 2.8 Turbo",
            "available": minimax_ok,
            "disabled_reason": "未配置 MINIMAX_TOKEN_PLAN_KEY" if not minimax_ok else "",
            "preview_supported": True,
        },
    ]
    if (os.environ.get("TTS_PROVIDER") or "mimo").strip().lower() == "minimax":
        return [options[1], options[0]]
    return options


def synthesize_tts_preview(
    text: str,
    *,
    tts_provider: str | None = None,
    voice: str | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Synthesize a short preview clip without creating a video generation job."""
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        raise ValueError("试听文本不能为空")
    if len(cleaned) > 120:
        cleaned = cleaned[:120]
    provider, resolved_voice = resolve_tts_selection(tts_provider, voice, strict=True)
    root = Path(output_dir) if output_dir else Path(__file__).resolve().parent / "static" / "uploads" / "tts-previews"
    root.mkdir(parents=True, exist_ok=True)
    stamp = uuid.uuid4().hex[:12]
    output = root / f"preview-{provider}-{stamp}.wav"
    if provider == "minimax":
        synthesize_minimax_tts(cleaned, resolved_voice, output)
    else:
        synthesize_mimo_tts(cleaned, resolved_voice, output)
    rel = f"uploads/tts-previews/{output.name}"
    return {
        "audio_path": rel,
        "audio_url": f"/static/{rel}",
        "tts_provider": provider,
        "voice": resolved_voice,
        "text": cleaned,
    }


def resolve_tts_selection(
    provider: str | None,
    voice: str | None,
    *,
    strict: bool = False,
) -> tuple[str, str]:
    """Resolve provider/voice pair. Qwen is retired; legacy 'qwen'/'Cherry'
    normalize to MiMo so historical projects can still re-render."""
    normalized_provider = (provider or os.environ.get("TTS_PROVIDER", "mimo") or "mimo").strip().lower()
    if normalized_provider in {"qwen", "dashscope"}:
        normalized_provider, voice = "mimo", ""
    candidate = str(voice or "").strip()
    if normalized_provider == "mimo":
        allowed = {MIMO_TTS_VOICE, "mimo_default", ""}
        if candidate and candidate not in allowed:
            # 历史遗留音色（如 Cherry）统一回落默认，不抛错
            return "mimo", MIMO_TTS_VOICE
        return "mimo", candidate or MIMO_TTS_VOICE
    if normalized_provider == "minimax":
        allowed = {MINIMAX_TTS_VOICE, "minimax_default", ""}
        if candidate and candidate not in allowed:
            return "minimax", MINIMAX_TTS_VOICE
        return "minimax", candidate or os.environ.get("MINIMAX_TTS_VOICE", MINIMAX_TTS_VOICE)
    if strict:
        raise ValueError(f"未知 TTS 服务商：{normalized_provider}")
    return "mimo", MIMO_TTS_VOICE


def formal_scene_bounds(target_duration_ms: int, *, adapted: bool = False) -> tuple[int, int]:
    """Return min/max scene counts for a target duration.

    Adapted chat plans may shrink structure when Buffalo inventory is thin;
    keep a lower floor so production continues instead of hard-stopping.
    """
    if adapted:
        return 4, FORMAL_MAX_SCENES
    if int(target_duration_ms) >= FORMAL_MIN_DURATION_MS:
        return FORMAL_MIN_SCENES, FORMAL_MAX_SCENES
    return 4, 8


class RenderCanceled(RuntimeError):
    """Raised when a running render is canceled cooperatively."""


_ACTIVE_PROCESSES: dict[str, set[subprocess.Popen]] = {}
_ACTIVE_PROCESSES_LOCK = threading.Lock()


def _scene_tts_concurrency() -> int:
    try:
        return max(1, min(8, int(os.environ.get("SCENE_TTS_CONCURRENCY", "2"))))
    except ValueError:
        return 2


def _scene_ffmpeg_concurrency() -> int:
    try:
        return max(1, min(4, int(os.environ.get("SCENE_FFMPEG_CONCURRENCY", "2"))))
    except ValueError:
        return 2


def cancel_render(job_id: str) -> bool:
    """Terminate every active FFmpeg process group for a render job."""
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES.get(job_id) or ())
    if not processes:
        return False
    terminated = False
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process_group = os.getpgid(process.pid)
            os.killpg(process_group, signal.SIGTERM)
            if hasattr(process, "wait"):
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process_group, signal.SIGKILL)
            terminated = True
        except ProcessLookupError:
            continue
    return terminated


def run_cancelable_process(
    job_id: str,
    command: list[str],
    *,
    timeout: float = 180,
    cancel_check: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command in its own process group with bounded cancellation checks."""
    if cancel_check and cancel_check():
        raise RenderCanceled("视频生成已取消")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.setdefault(job_id, set()).add(process)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_check and cancel_check():
                cancel_render(job_id)
                raise RenderCanceled("视频生成已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cancel_render(job_id)
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, command, output=stdout, stderr=stderr
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        with _ACTIVE_PROCESSES_LOCK:
            active = _ACTIVE_PROCESSES.get(job_id)
            if active is not None:
                active.discard(process)
                if not active:
                    _ACTIVE_PROCESSES.pop(job_id, None)

# Back-compat aliases; canonical keyword/priority tables live in asset_taxonomy.
CATEGORY_KEYWORDS = asset_taxonomy.CATEGORY_KEYWORDS
CATEGORY_PRIORITY = asset_taxonomy.CATEGORY_PRIORITY


def _match_asset_by_scene(scene_visual: str, available_assets: list[dict],
                          used_asset_ids: set[int] | None = None,
                          topic: str = "") -> dict | None:
    """根据场景画面描述 + 整体话题，从可用素材中匹配最合适的素材。

    匹配规则（优先级从高到低）：
    1. 素材名称直接命中 visual 中的关键词 → 最精准
    2. 话题关键词 → 分类匹配（整体主题一致性）
    3. visual 关键词 → 分类匹配（场景级）
    4. 同分时按 CATEGORY_PRIORITY 排序（warehouse > delivery > ... > staff）
    5. 已使用的素材降权（最后一轮才允许重复）
    """
    if not available_assets:
        return None
    used = used_asset_ids or set()

    visual_lower = (scene_visual or "").lower()
    topic_lower = (topic or "").lower()

    # 计算每个分类的匹配得分（visual + topic 双重打分）
    cat_scores: dict[str, float] = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        visual_score = sum(1 for kw in keywords if kw.lower() in visual_lower)
        topic_score = sum(1.0 for kw in keywords if kw.lower() in topic_lower)
        total = visual_score + topic_score
        if total > 0:
            cat_scores[cat] = total

    if not cat_scores:
        return None

    # 按得分降序，平分时按 CATEGORY_PRIORITY 排序（index 小的优先）
    def sort_key(cat):
        priority_idx = CATEGORY_PRIORITY.index(cat) if cat in CATEGORY_PRIORITY else 99
        return (-cat_scores[cat], priority_idx)

    ranked_cats = sorted(cat_scores, key=sort_key)

    # 第一轮：分类匹配 + 排除已使用素材
    for cat in ranked_cats:
        matches = [a for a in available_assets if a.get("category") == cat and a["id"] not in used]
        if matches:
            return matches[0]

    # 第二轮：忽略已使用，允许重复
    for cat in ranked_cats:
        matches = [a for a in available_assets if a.get("category") == cat]
        if matches:
            return matches[0]

    return None


def normalize_script(
    script: dict,
    asset_ids: set[int],
    *,
    asset_lookup: dict[int, dict] | None = None,
    event_lookup: dict[int, dict] | None = None,
    platform: str = "douyin",
    target_duration_ms: int | None = None,
) -> dict:
    target = platform_budget_ms(platform, target_duration_ms or script.get("duration_target_ms") or 60_000)
    scenes = []
    repairs: list[str] = []
    for index, raw in enumerate(script.get("scenes") or []):
        if not isinstance(raw, dict):
            continue
        voiceover = str(raw.get("voiceover") or "").strip()
        text_overlay = str(raw.get("text_overlay") or "").strip()
        visual = str(raw.get("visual") or "").strip()
        if not voiceover and text_overlay:
            voiceover = text_overlay.replace("|", "，").replace("｜", "，")
            repairs.append(f"已用字幕补齐第{index + 1}镜旁白")
        has_structure = any(
            raw.get(key) not in (None, "", 0)
            for key in ("duration", "duration_ms", "asset_id", "event_clip_id", "asset_segment_id")
        )
        if not voiceover and not text_overlay and not visual and not has_structure:
            repairs.append(f"已移除第{index + 1}个空白分镜")
            continue
        asset_id = raw.get("asset_id")
        event_clip_id = raw.get("event_clip_id")
        event_ref = None
        if event_clip_id is not None:
            try:
                event_clip_id = int(event_clip_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("热点事件片段 ID 无效") from exc
            event_ref = (event_lookup or {}).get(event_clip_id)
            if event_ref:
                # 热点母片可以是 inactive，但它引用的事件代理片仍然是合法素材。
                asset_id = event_ref.get("asset_id")
            elif asset_id not in asset_ids:
                raise ValueError("热点事件片段不存在")
        if asset_id not in asset_ids and not event_ref:
            asset_id = None
        asset_segment_id = raw.get("asset_segment_id") if asset_id else None
        event_clip_id = event_clip_id if asset_id else None
        try:
            asset_segment_id = int(asset_segment_id) if asset_segment_id is not None else None
            # 已选择的热点事件也可以进一步定位到母片内的分析子片段。
            # 过去只为 asset_segment_id 保留范围，导致热点 Hook 又退回事件起点（常为主播画面）。
            has_precise_range = bool(asset_segment_id or event_ref)
            asset_start_ms = max(0, int(raw.get("asset_start_ms") or 0)) if has_precise_range else 0
            asset_end_ms = max(asset_start_ms, int(raw.get("asset_end_ms") or 0)) if has_precise_range else 0
        except (TypeError, ValueError):
            asset_segment_id, asset_start_ms, asset_end_ms, event_clip_id = None, 0, 0, None
        evidence_type = str(raw.get("evidence_type") or "")[:32]
        scene_role = str(raw.get("scene_role") or "")[:32]
        if is_explanation_scene({"evidence_type": evidence_type, "scene_role": scene_role}):
            raise ValueError("信息图、流程图和 PPT 卡片已禁用；请使用热点 Hook、Buffalo 自有视频或自有图片")
        raw_duration = float(raw.get("duration") or 5)
        # 自有静态图只承担 1–2 秒的节奏过渡，不能被通用视频最短时长
        # 规则拉长为 3 秒；否则它既会破坏图片占比，又会迫使真实视频被压缩。
        minimum_duration = 1.0 if evidence_type == "image" else 3.0
        scenes.append({
            "scene": len(scenes) + 1,
            "scene_role": scene_role,
            "evidence_type": evidence_type,
            "match_reasons": list(raw.get("match_reasons") or [])[:8],
            "duration": max(minimum_duration, min(8.0, raw_duration)),
            "visual": (visual or "Buffalo 素材")[:80],
            "voiceover": voiceover[:180],
            "text_overlay": (text_overlay or voiceover)[:24],
            "asset_id": asset_id,
            "asset_segment_id": asset_segment_id,
            "asset_start_ms": asset_start_ms,
            "asset_end_ms": asset_end_ms,
            "event_clip_id": event_clip_id,
            "brand_endcard_path": str(raw.get("brand_endcard_path") or "")[:240],
        })
    min_scenes, max_scenes = formal_scene_bounds(
        target,
        adapted=bool((script.get("adaptation") or {}).get("adapted")),
    )
    if not min_scenes <= len(scenes) <= max_scenes:
        raise ValueError(f"当前时长需要 {min_scenes}–{max_scenes} 个完整分镜")
    if asset_lookup is not None:
        for scene in scenes:
            if not scene.get("asset_id"):
                continue
            try:
                scene["clip_ref"] = resolve_clip_ref(
                    scene, asset_lookup.get(int(scene["asset_id"])), event_lookup or {}
                )
            except ClipReferenceError as exc:
                raise ValueError(str(exc)) from exc
    requested_total = sum(round(float(scene["duration"]) * 1000) for scene in scenes)
    fitted = rebalance_scenes_to_budget(scenes, target, minimum_scene_ms=1_500)
    for scene in fitted:
        if (
            scene.get("asset_id")
            and scene.get("evidence_type") != "image"
            and int(scene["duration_ms"]) < 3_000
        ):
            raise ValueError("真实视频分镜不能短于 3 秒；请减少分镜或补充未使用的 Buffalo 自有图片")
    usage = source_usage_report(fitted)
    if not usage["passed"]:
        raise ValueError("素材重复硬门禁未通过：" + "；".join(usage["issues"]))
    if requested_total > target:
        repairs.append(
            f"已将分镜总时长从 {requested_total / 1000:g} 秒压缩至 {target / 1000:g} 秒"
        )
    requested_clips = script.get("selected_clip_scenes") or []
    selected_clip_scenes = sorted({int(value) for value in requested_clips if str(value).isdigit() and 1 <= int(value) <= len(scenes)})
    return {**script, "duration_target": round(target / 1000, 3), "duration_target_ms": target,
            "duration_used_ms": sum(int(scene["duration_ms"]) for scene in fitted),
            "duration_remaining_ms": max(0, target - sum(int(scene["duration_ms"]) for scene in fitted)),
            "scenes": fitted,
            "output_mode": "full_and_clips" if script.get("output_mode") == "full_and_clips" else "full",
            "selected_clip_scenes": selected_clip_scenes,
            "source_usage": usage,
            "normalization": {"auto_repaired": bool(repairs), "actions": repairs}}


MIMO_TTS_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MINIMAX_TTS_BASE_URL = "https://api.minimaxi.com/v1"
# MiMo v2.5-tts 允许在 user 消息里用自然语言描述语气/语速，这条默认风格
# 只是让旁白读起来像真人口语播报，不是台词内容，不会进入成片文本。
MIMO_TTS_DEFAULT_STYLE = "播报语气自然亲切、像真人口语跟卖家说话，语速适中偏快，不要机械平铺直叙。"


def synthesize_mimo_tts(text: str, voice: str, output: Path, api_key: str | None = None,
                         style_instruction: str = MIMO_TTS_DEFAULT_STYLE):
    """用 MiMo v2.5-tts 合成旁白；voice 留空时使用预置默认音色。

    请求/返回格式来自官方文档 speech-synthesis-v2.5：POST /chat/completions，
    合成文本放在 assistant 消息，风格控制放在 user 消息，返回
    message.audio.data 是 base64 编码的 wav，不是下载 URL。
    """
    key = api_key or os.environ.get("MIMO_API_KEY", "")
    if not key:
        raise RuntimeError("未配置 MIMO_API_KEY")
    payload = {
        "model": "mimo-v2.5-tts",
        "messages": [
            {"role": "user", "content": style_instruction},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav", "voice": voice or MIMO_TTS_VOICE},
    }
    last_error: Exception | None = None
    for attempt in range(1, TTS_MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=90, trust_env=False) as client:
                response = client.post(
                    f"{MIMO_TTS_BASE_URL}/chat/completions",
                    headers={"api-key": key, "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
                audio_b64 = body["choices"][0]["message"]["audio"]["data"]
            output.write_bytes(base64.b64decode(audio_b64))
            return
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < TTS_MAX_ATTEMPTS:
                time.sleep(TTS_RETRY_DELAY_SECONDS * attempt)
    raise RuntimeError(f"MiMo TTS 请求或音频解码失败，已重试 {TTS_MAX_ATTEMPTS} 次") from last_error


def synthesize_minimax_tts(
    text: str,
    voice: str,
    output: Path,
    api_key: str | None = None,
    model: str | None = None,
):
    """Use MiniMax Speech 2.8 to synthesize a WAV narration.

    The Token Plan T2A endpoint returns hexadecimal audio bytes rather than a
    URL.  Request WAV because the renderer's historical contract is a local
    single-track WAV file.
    """
    key = api_key or os.environ.get("MINIMAX_TOKEN_PLAN_KEY", "")
    if not key:
        raise RuntimeError("未配置 MINIMAX_TOKEN_PLAN_KEY")
    payload = {
        "model": model or os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
        "text": str(text or ""),
        "stream": False,
        "voice_setting": {
            "voice_id": voice or os.environ.get("MINIMAX_TTS_VOICE", MINIMAX_TTS_VOICE),
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 24000,
            "bitrate": 128000,
            "format": "wav",
            "channel": 1,
        },
        "subtitle_enable": False,
    }
    last_error: Exception | None = None
    for attempt in range(1, TTS_MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=90, trust_env=False) as client:
                response = client.post(
                    f"{os.environ.get('MINIMAX_TTS_BASE_URL', MINIMAX_TTS_BASE_URL).rstrip('/')}/t2a_v2",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            status = (body.get("base_resp") or {}).get("status_code")
            if status not in (None, 0):
                raise RuntimeError((body.get("base_resp") or {}).get("status_msg") or "MiniMax TTS 返回失败")
            audio_hex = str((body.get("data") or {}).get("audio") or "")
            if not audio_hex:
                raise RuntimeError("MiniMax TTS 未返回音频数据")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(bytes.fromhex(audio_hex))
            return
        except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < TTS_MAX_ATTEMPTS:
                time.sleep(TTS_RETRY_DELAY_SECONDS * attempt)
    raise RuntimeError(f"MiniMax TTS 请求或音频解码失败，已重试 {TTS_MAX_ATTEMPTS} 次") from last_error


def synthesize_local_macos(text: str, output: Path, voice: str = "Tingting"):
    """使用 macOS 内置语音生成内部预览，不发送任何文本到外部服务。"""
    say = shutil.which("say")
    ffmpeg = shutil.which("ffmpeg")
    if not say or not ffmpeg:
        raise RuntimeError("本地旁白需要 macOS say 与 FFmpeg")
    aiff = output.with_suffix(".aiff")
    try:
        subprocess.run(
            [say, "-v", voice, "-r", "190", "-o", str(aiff), text],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [ffmpeg, "-y", "-i", str(aiff), "-ar", "24000", "-ac", "1", str(output)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    finally:
        aiff.unlink(missing_ok=True)


def synthesize_scene_voiceover(
    text: str,
    output: Path,
    *,
    tts_provider: str | None = None,
    voice: str = "",
    style_instruction: str | None = None,
) -> dict:
    """Route one scene voiceover through MiMo TTS (single track).

    Returns metadata describing the provider actually used. MiMo failures
    bubble up directly; there is no silent fallback.
    """
    import hashlib

    provider = (tts_provider or os.environ.get("TTS_PROVIDER", "mimo") or "mimo").strip().lower()
    if provider not in {"mimo", "minimax", "local_macos"}:
        # 历史项目可能存了已下线的 TTS 服务商，统一归一到 MiMo 单轨。
        provider = "mimo"
    if provider == "minimax":
        mimo_model = os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo")
        mimo_voice = voice or os.environ.get("MINIMAX_TTS_VOICE", MINIMAX_TTS_VOICE)
    else:
        mimo_model = os.environ.get("MIMO_TTS_MODEL", "mimo-v2.5-tts")
        mimo_voice = voice or os.environ.get("MIMO_TTS_VOICE", MIMO_TTS_VOICE)
    style = style_instruction or MIMO_TTS_DEFAULT_STYLE
    meta = {
        "provider": provider,
        "model": mimo_model,
        "voice": mimo_voice,
        "style": style,
        "attempts": 0,
        "elapsed_ms": 0,
        "cache_hit": False,
    }
    cache_root = Path(__file__).resolve().parent / "data" / "tts_cache"
    cache_key = hashlib.sha256(
        f"{text}|{provider}|{meta['model']}|{meta['voice']}|{meta['style']}".encode("utf-8")
    ).hexdigest()
    cache_path = cache_root / f"{cache_key}{output.suffix or '.wav'}"

    def _copy_cache() -> bool:
        if cache_path.is_file() and cache_path.stat().st_size > 64:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(cache_path.read_bytes())
            meta["cache_hit"] = True
            return True
        return False

    def _store_cache() -> None:
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            if output.is_file() and output.stat().st_size > 64:
                cache_path.write_bytes(output.read_bytes())
        except OSError:
            pass

    if provider == "local_macos":
        synthesize_local_macos(text, output)
        meta.update({"provider": "local_macos", "model": "macos_say", "attempts": 1})
        return meta

    started = time.perf_counter()
    if _copy_cache():
        meta["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return meta

    meta["attempts"] += 1
    if provider == "minimax":
        synthesize_minimax_tts(text, mimo_voice, output, model=mimo_model)
    else:
        synthesize_mimo_tts(text, mimo_voice, output, style_instruction=style)
    _store_cache()
    meta["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return meta


def _generate_text_overlay(
    text: str,
    output: Path,
    width: int = 1080,
    *,
    height: int | None = None,
    mask_source_lower_third: bool = False,
):
    """生成全片统一的底部字幕遮罩；不依赖 FFmpeg 的可选 libass/drawtext。

    热点和 Buffalo 自有素材使用同一条全宽渐变底栏、同一字号和同一安全区，
    既压住母片自带的英文 ticker，也避免素材切换时字幕样式跳变。
    ``mask_source_lower_third`` 保留为兼容参数，但不再切换第二套视觉规范。
    """
    # A 16:9 output is wide but short.  Width-only scaling made its subtitles
    # much larger than vertical subtitles.  The short edge is the safe scale.
    scale = min(width / 1080, (height or 1920) / 1920)
    # One full-width 18% band is used for every source type. It is large enough
    # to cover source tickers after the 7.5% bottom safe area, but compact enough
    # not to bury the actual footage.
    overlay_height = max(round((height or 1920) * 0.18), round(120 * scale))
    font_size = max(20, round(34 * scale))
    stroke_width = max(2, round(3 * scale))
    img = Image.new('RGBA', (width, overlay_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    font = _load_subtitle_font(text, font_size)
    
    lines, line = [], ""
    for char in text:
        candidate = line + char
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > width * 0.72:
            lines.append(line); line = char
        else:
            line = candidate
    if line:
        lines.append(line)
    lines = lines[:2]
    line_height = max(28, round(50 * scale))
    y0 = overlay_height / 2 - (len(lines) - 1) * line_height / 2
    for y in range(overlay_height):
        # 顶部较透、底部加深，覆盖原片 ticker，同时保留画面层次。
        alpha = int(150 + 72 * (y / max(1, overlay_height - 1)))
        draw.line((0, y, width, y), fill=(7, 11, 10, alpha))
    for line_index, value in enumerate(lines):
        y = y0 + line_index * line_height
        draw.text((width / 2, y), value, font=font, fill="white", stroke_width=stroke_width,
                  stroke_fill="black", anchor="mm")
    img.save(output)


def _generate_watermark(text: str, output: Path, width: int):
    overlay_width = max(320, min(width - 40, 720))
    image = Image.new("RGBA", (overlay_width, 64), (25, 20, 14, 190))
    draw = ImageDraw.Draw(image)
    font = _load_subtitle_font(text, 26)
    draw.text((overlay_width / 2, 32), text, font=font, fill="white", anchor="mm")
    image.save(output)


def _subtitle_safe_bottom_margin(height: int, subtitle_layout: str = "standard") -> int:
    """Keep burned-in captions clear of mobile navigation and home-indicator areas.

    The old 28px-at-1920 margin placed the subtitle slab almost on the bottom
    edge (only 14px in a 540×960 preview).  A 7.5% lower safe area keeps the
    subtitle readable without turning it into a middle-screen caption.  News
    clips use a full lower-third mask, so that mask must reach the bottom edge
    while its caption sits near the top of the mask.
    """
    return max(48, round(max(1, int(height)) * 0.075))


def is_standard_portrait_size(output_size: tuple[int, int]) -> bool:
    """Production renders are always an exact 9:16 mobile canvas."""
    try:
        width, height = int(output_size[0]), int(output_size[1])
    except (IndexError, TypeError, ValueError):
        return False
    return width > 0 and height > 0 and width * 16 == height * 9


def _scene_command(ffmpeg: str, ffprobe: str, source: Path, is_video: bool,
                   wav: Path, cues: list[dict], duration: float, segment: Path,
                   work_root: Path, scene_index: int, source_start: float = 0,
                   source_end: float | None = None,
                   output_size: tuple[int, int] = (1080, 1920),
                   watermark_text: str = "",
                   subtitle_layout: str = "standard",
                   animate_image: bool = False,
                   fast: bool = False) -> list[str]:
    """用逐句 PNG overlay 烧录字幕，兼容未编译 libass 的 FFmpeg。"""
    width, height = output_size
    if is_video:
        # 真实视频永远不循环。render_job 会在调用前确认可用镜头长度足以覆盖旁白。
        visual_input = []
        if source_start > 0:
            visual_input += ["-ss", str(source_start)]
        visual_input += ["-t", str(duration)]
        visual_input += ["-i", str(source)]
    else:
        visual_input = ["-loop", "1", "-i", str(source)]
    overlays = []
    mask_source_lower_third = subtitle_layout == "hotspot_news"
    for cue_index, cue in enumerate(cues):
        overlay = work_root / f"subtitle-{scene_index}-{cue_index}.png"
        _generate_text_overlay(
            cue["text"], overlay, width, height=height,
            mask_source_lower_third=mask_source_lower_third,
        )
        overlays.append(overlay)
    subtitle_count = len(overlays)
    if watermark_text:
        watermark = work_root / f"watermark-{scene_index}.png"
        _generate_watermark(watermark_text, watermark, width)
        overlays.append(watermark)
    command = [ffmpeg, "-y", *visual_input]
    for overlay in overlays:
        command += ["-loop", "1", "-i", str(overlay)]
    audio_index = len(overlays) + 1
    command += ["-i", str(wav), "-t", str(duration)]
    # All source orientations use the same full-bleed center crop. A blurred
    # landscape shell beside a full portrait source made the production feel
    # like two different templates; content selection must handle the crop,
    # while the renderer keeps one stable canvas rule.
    filters = [
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height}:exact=1,setsar=1[portrait]",
    ]
    if animate_image and not is_video:
        # 自有上下文图片是短暂的真实证据，不应在 9:16 画面中显得像一张突然插入的卡片。
        # 仅做 3.5% 的居中推进；品牌 CTA 则保持稳定，便于识别并避免过度装饰。
        filters += [
            f"[portrait]zoompan=z='min(zoom+0.0007,1.035)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
            f"s={width}x{height}:fps=30,setsar=1[v0]",
        ]
    else:
        filters.append("[portrait]fps=30,setsar=1[v0]")
    current = "v0"
    # 所有来源字幕都位于同一移动端底部安全区之上；统一遮罩负责压住
    # 热点母片自带的英文新闻条，避免素材来源切换时字幕动线跳变。
    subtitle_bottom_margin = _subtitle_safe_bottom_margin(height, subtitle_layout)
    for cue_index, cue in enumerate(cues):
        next_label = f"v{cue_index + 1}"
        filters.append(
            f"[{current}][{cue_index + 1}:v]overlay=0:H-h-{subtitle_bottom_margin}:enable='between(t,{cue['start']},{cue['end']})'[{next_label}]"
        )
        current = next_label
    if watermark_text:
        watermark_label = f"v{subtitle_count + 1}"
        filters.append(
            f"[{current}][{subtitle_count + 1}:v]overlay=20:20[{watermark_label}]"
        )
        current = watermark_label
    if is_video and _has_audio(ffprobe, source):
        filters += ["[0:a]volume=0.12[source_audio]", f"[source_audio][{audio_index}:a]amix=inputs=2:duration=longest[mixed_audio]"]
        command += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "[mixed_audio]"]
    else:
        command += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", f"{audio_index}:a"]
    preset = "ultrafast" if fast else RENDER_FINAL_PRESET
    crf_args = ["-crf", "28"] if fast else ["-crf", RENDER_INTERMEDIATE_CRF]
    return command + ["-r", "30", "-c:v", "libx264", "-preset", preset, *crf_args,
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(segment)]


def _clip_source_command(
    ffmpeg: str,
    source: Path,
    output: Path,
    source_start: float,
    source_end: float | None,
    fast: bool = False,
) -> list[str]:
    """先物化已确认镜头范围，后续只播放一次，不允许循环该范围。"""
    command = [ffmpeg, "-y", "-ss", str(max(0, source_start)), "-i", str(source)]
    if source_end is not None and source_end > source_start:
        command += ["-t", str(round(source_end - source_start, 3))]
    preset = "ultrafast" if fast else RENDER_FINAL_PRESET
    crf = "28" if fast else RENDER_INTERMEDIATE_CRF
    return command + [
        "-r", "30", "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]


def _has_audio(ffprobe: str, path: Path) -> bool:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def _probe_media(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration": round(float((data.get("format") or {}).get("duration") or 0), 3),
        "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"),
        "has_audio": bool(audio),
    }


def _probe_dimensions(ffprobe: str, path: Path) -> tuple[int | None, int | None]:
    """Return coded (width, height) of the first video stream, or (None, None) on failure."""
    try:
        info = _probe_media(ffprobe, path)
    except Exception:
        return None, None
    width, height = int(info.get("width") or 0), int(info.get("height") or 0)
    return (width or None), (height or None)


def _transition_concat_command(
    ffmpeg: str,
    segments: list[Path],
    durations: list[float],
    output: Path,
    *,
    transition_duration: float = 0.22,
) -> list[str]:
    """统一时间基准并用极短交叉淡化连接分镜，避免硬拼接时间戳顿挫。"""
    if len(segments) < 2 or len(segments) != len(durations):
        raise ValueError("平滑拼接至少需要两个时长完整的分镜")
    transition = max(0.08, min(float(transition_duration), 0.5))
    command = [ffmpeg, "-y"]
    filters = []
    for index, segment in enumerate(segments):
        command += ["-i", str(segment)]
        filters.append(
            f"[{index}:v]fps=30,settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{index}]"
        )
        segment_duration = max(0.1, float(durations[index]))
        filters.append(
            f"[{index}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={segment_duration:.3f},atrim=duration={segment_duration:.3f}[a{index}]"
        )

    video_label, audio_label = "v0", "a0"
    combined_duration = float(durations[0])
    for index in range(1, len(segments)):
        next_video, next_audio = f"vx{index}", f"ax{index}"
        offset = max(0, combined_duration - transition)
        filters.append(
            f"[{video_label}][v{index}]xfade=transition=fade:duration={transition:g}:"
            f"offset={offset:.3f}[{next_video}]"
        )
        filters.append(
            f"[{audio_label}][a{index}]acrossfade=d={transition:g}:c1=tri:c2=tri[{next_audio}]"
        )
        video_label, audio_label = next_video, next_audio
        combined_duration += float(durations[index]) - transition

    command += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
        "-r", "30", "-c:v", "libx264", "-preset", RENDER_FINAL_PRESET, "-crf", RENDER_FINAL_CRF,
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
        "-movflags", "+faststart", str(output),
    ]
    return command


def _safe_concat_command(ffmpeg: str, segments: list[Path], output: Path) -> list[str]:
    """Compatibility fallback when a long xfade graph is rejected by local FFmpeg builds.

    All scene files are normalized before this point, so a hard cut is preferable to
    failing an otherwise valid internal preview. The report records this fallback.
    """
    manifest = output.with_suffix(".concat.txt")
    manifest.write_text(
        "".join(f"file '{segment.as_posix()}'\n" for segment in segments), encoding="utf-8"
    )
    # The segments have identical H.264/AAC settings, so the concat demuxer avoids
    # another long filter graph and is substantially more portable on macOS FFmpeg.
    return [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ]


def build_subtitle_cues(text: str, duration: float) -> list[dict]:
    """按语义停顿分句并按字符比例分配时长；无音频时的默认实现。"""
    return _build_subtitle_cues_internal(text, duration, audio_path=None)


def _build_subtitle_cues_internal(
    text: str,
    duration: float,
    *,
    audio_path: Path | None = None,
    silence_threshold: float = -50.0,
    min_pause_duration: float = 0.3,
    ffmpeg: str = "ffmpeg",
) -> list[dict]:
    """构建字幕时间轴：有音频时用 ffmpeg silencedetect 把边界吸到真实语音段；无音频时字符比例。"""
    parts = [p.strip() for p in re.split(r"(?<=[，。！？；,.!?;])", text or "") if p.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    compact_parts = []
    for part in parts:
        remaining = part
        while len(remaining) > 36:
            cut = remaining.rfind(" ", 0, 37)
            if cut < 18:
                cut = 36
            compact_parts.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            compact_parts.append(remaining)
    parts = compact_parts
    total = sum(max(1, len(part)) for part in parts) or 1
    cursor, cues = 0.0, []

    if not audio_path or not os.path.exists(audio_path):
        for index, part in enumerate(parts):
            end = duration if index == len(parts) - 1 else cursor + duration * max(1, len(part)) / total
            cues.append({"start": round(cursor, 3), "end": round(end, 3), "text": part})
            cursor = end
        return cues

    silence_points = _detect_silence_points(str(audio_path), silence_threshold, min_pause_duration, ffmpeg=ffmpeg)
    if silence_points:
        return _align_cues_to_silence(parts, duration, silence_points, tolerance=0.12)

    for index, part in enumerate(parts):
        end = duration if index == len(parts) - 1 else cursor + duration * max(1, len(part)) / total
        cues.append({"start": round(cursor, 3), "end": round(end, 3), "text": part})
        cursor = end
    return cues


def _detect_silence_points(
    audio_path: str,
    threshold: float = -50.0,
    min_duration: float = 0.3,
    ffmpeg: str = "ffmpeg",
) -> list[tuple[float, float]]:
    """用 ffmpeg silencedetect 检测静音段，返回 (start, end) 列表。"""
    try:
        cmd = [
            ffmpeg,
            "-i", audio_path,
            "-af", f"silencedetect=noise={threshold}dB:d={min_duration}",
            "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        stderr = result.stderr or ""
        starts = [float(m.group(1)) for m in re.finditer(r"silence_start: ([\d.]+)", stderr)]
        ends = [float(m.group(1)) for m in re.finditer(r"silence_end: ([\d.]+)", stderr)]
        points = []
        si = ei = 0
        while si < len(starts) and ei < len(ends):
            if starts[si] < ends[ei]:
                points.append((starts[si], ends[ei]))
                si += 1
                ei += 1
            else:
                ei += 1
        return points
    except Exception:
        logger.warning("静音检测失败：%s", audio_path, exc_info=True)
        return []


def _align_cues_to_silence(
    parts: list[str],
    total_duration: float,
    silence_points: list[tuple[float, float]],
    tolerance: float = 0.12,
) -> list[dict]:
    """语音时间轴映射：把字符比例边界投影到真实语音段，保证每个 cue 不跨静音、末条 end=总时长。"""
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(silence_points, key=lambda x: x[0]):
        if start > cursor + tolerance:
            speech.append((cursor, min(start, total_duration)))
        cursor = max(cursor, end)
    if cursor < total_duration - tolerance:
        speech.append((cursor, total_duration))

    total_chars = sum(max(1, len(p)) for p in parts) or 1

    # 无有效语音段 → 回退字符比例
    if not speech:
        cur = 0.0
        cues = []
        for i, part in enumerate(parts):
            end = total_duration if i == len(parts) - 1 else cur + total_duration * max(1, len(part)) / total_chars
            cues.append({"start": round(cur, 3), "end": round(end, 3), "text": part})
            cur = end
        return cues

    speech_total = sum(e - s for s, e in speech)
    cum = 0.0
    boundaries_sec = [0.0]
    for p in parts[:-1]:
        cum += max(1, len(p))
        boundaries_sec.append(speech_total * cum / total_chars)

    def sec_to_real(sec: float) -> float:
        for s, e in speech:
            span = e - s
            if sec <= span:
                return s + sec
            sec -= span
        return speech[-1][1]

    cues = []
    for i, part in enumerate(parts):
        start = sec_to_real(boundaries_sec[i])
        # Cover the complete measured audio window, including a natural
        # trailing silence. Otherwise valid TTS files with a short tail are
        # rejected by subtitle_sync_report even though no subtitle starts
        # early or runs beyond the audio.
        end = sec_to_real(boundaries_sec[i + 1]) if i + 1 < len(parts) else total_duration
        cues.append({
            "start": round(max(start, 0.0), 3),
            "end": round(min(max(end, start + 0.05), total_duration), 3),
            "text": part,
        })
    return cues


def subtitle_sync_report(cues: list[dict], audio_duration: float, tolerance: float = 0.12) -> dict:
    """校验字幕覆盖真实 TTS 音频窗口，避免字幕提前结束或越过旁白。"""
    measured = round(max(0.0, float(audio_duration or 0)), 3)
    normalized = sorted(cues or [], key=lambda item: float(item.get("start") or 0))
    subtitle_end = round(float(normalized[-1].get("end") or 0), 3) if normalized else 0.0
    gaps = []
    previous_end = 0.0
    for cue in normalized:
        start = float(cue.get("start") or 0)
        end = float(cue.get("end") or 0)
        if start > previous_end + tolerance:
            gaps.append(round(start - previous_end, 3))
        if end < start or end > measured + tolerance:
            gaps.append(round(max(0.0, end - measured), 3))
        previous_end = max(previous_end, end)
    return {
        "passed": bool(normalized) and abs(subtitle_end - measured) <= tolerance and not gaps,
        "audio_duration": measured,
        "subtitle_end": subtitle_end,
        "cue_count": len(normalized),
        "gaps": gaps,
    }


def tts_speedup_factor(
    speech_duration: float,
    available_video_seconds: float,
    *,
    breathing_room: float = TTS_BREATHING_ROOM_SECONDS,
    max_speedup: float = MAX_NATURAL_TTS_SPEEDUP,
) -> float | None:
    """Return a bounded tempo adjustment to keep one narration pass inside a real clip."""
    available_audio = max(0.0, float(available_video_seconds) - float(breathing_room))
    speech = max(0.0, float(speech_duration))
    if not speech or speech <= available_audio:
        return None
    factor = speech / available_audio if available_audio else float("inf")
    return factor if 1.0 < factor <= max_speedup else None


def compact_voiceover_to_fit_real_video(
    text: str,
    speech_duration: float,
    available_video_seconds: float,
    *,
    breathing_room: float = TTS_BREATHING_ROOM_SECONDS,
) -> str | None:
    """Shorten only an overflowing narration tail before re-synthesizing it.

    This is a last renderer-side guard for real clips. TTS may read the same
    number of characters at quite different speeds because of punctuation and
    brand names. We preserve the opening factual clause, prefer an existing
    phrase boundary, and never stretch or repeat the video to hide the result.
    """
    compact = "".join(str(text or "").split())
    available_audio = max(0.0, float(available_video_seconds) - float(breathing_room))
    measured = max(0.0, float(speech_duration))
    if not compact or not measured or measured <= available_audio:
        return None
    # Keep a conservative margin because the second TTS call can vary a little
    # even for the shortened text.
    target = max(8, int(len(compact) * available_audio / measured * 0.84))
    if target >= len(compact):
        return None
    # Scene descriptions frequently contain vehicle codes or brand strings
    # (for example "CE KEMACH 18" / "BUFFALO BOS"). When they alone cause an
    # overflow, remove those opaque labels first so the remaining Chinese
    # action clause stays intelligible rather than ending at "BUF" mid-word.
    without_codes = re.sub(r"[A-Za-z0-9]+", "", compact).strip()
    if 5 <= len(without_codes) <= target:
        return without_codes if without_codes.endswith(("。", "！", "？", "；")) else without_codes + "。"
    prefix = compact[:target]
    boundary = max((prefix.rfind(mark) for mark in "。！？；，、"), default=-1)
    if boundary >= max(4, int(target * 0.5)):
        marker = prefix[boundary]
        shortened = prefix[:boundary + 1]
        if marker in "，、":
            shortened = shortened[:-1].rstrip() + "。"
    else:
        shortened = prefix[:max(1, target - 1)].rstrip("，、；：- ") + "。"
    return shortened if shortened != compact else None


def scene_render_duration(
    planned_duration: float,
    speech_duration: float,
    *,
    is_brand_endcard: bool = False,
    preserve_planned_duration: bool = False,
    breathing_room: float = TTS_BREATHING_ROOM_SECONDS,
    max_trailing_gap: float = MAX_TRAILING_NARRATION_GAP_SECONDS,
) -> float:
    """Fit a single narration pass without leaving a long, silent visual tail.

    Normal scenes may shorten below their planning allocation when TTS is
    naturally concise. Formal dual-library videos preserve their planned beat
    duration, just like brand endcards, so a promised 50–90 second delivery is
    not silently compressed into a much shorter video. Neither branch ever
    cuts off spoken audio.
    """
    planned = max(0.0, float(planned_duration))
    speech = max(0.0, float(speech_duration))
    required = speech + max(0.0, float(breathing_room))
    if is_brand_endcard or preserve_planned_duration:
        return round(max(planned, required), 3)
    capped = min(planned, speech + max(float(breathing_room), float(max_trailing_gap)))
    return round(max(1.0, required, capped), 3)


def _audio_tempo_command(ffmpeg: str, source: Path, output: Path, factor: float) -> list[str]:
    """Build a bounded FFmpeg tempo command; callers only use a near-natural speedup."""
    return [
        ffmpeg, "-y", "-i", str(source), "-filter:a", f"atempo={factor:.6f}",
        "-vn", str(output),
    ]


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    return f"{centiseconds // 360000}:{(centiseconds // 6000) % 60:02d}:{(centiseconds // 100) % 60:02d}.{centiseconds % 100:02d}"


def _write_ass(cues: list[dict], output: Path):
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,PingFang SC,44,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,120,120,180,1\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"""
    lines = []
    for cue in cues:
        safe = cue["text"].replace("\\", "\\\\").replace("{", "（").replace("}", "）").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(cue['start'])},{_ass_time(cue['end'])},Default,,0,0,0,,{safe}")
    output.write_text(header + "\n".join(lines), encoding="utf-8")


def _quality_report(
    ffprobe: str,
    path: Path,
    expected_duration: float,
    subtitle_cues: int,
    expected_size: tuple[int, int] = (1080, 1920),
) -> dict:
    media = _probe_media(ffprobe, path)
    expected_width, expected_height = expected_size
    checks = {
        "expected_resolution": (
            media["width"] == expected_width and media["height"] == expected_height
        ),
        "portrait_9_16": is_standard_portrait_size((media["width"], media["height"])),
        "has_audio": media["has_audio"],
        "duration_aligned": abs(media["duration"] - expected_duration) <= 0.35,
        "has_timed_subtitles": subtitle_cues > 0,
    }
    return {**media, "expected_duration": round(expected_duration, 3), "subtitle_cues": subtitle_cues,
            "expected_width": expected_width, "expected_height": expected_height,
            "checks": checks, "status": "passed" if all(checks.values()) else "failed"}


# 渲染任务超时时间（秒）
RENDER_TIMEOUT = int(os.environ.get("RENDER_TIMEOUT_SECONDS", "900"))


def cleanup_stale_jobs():
    """清理卡住的渲染任务：running 超过 5 分钟的杀进程组并标 canceled，pending 超过 10 分钟的标 failed。

    running 分支必须标 canceled 而非 failed：render_job 的 is_canceled 只认
    cancel_requested/canceled，标 failed 时渲染线程照跑并在完成后覆盖成 succeeded。
    """
    import time
    now = time.time()
    stale_count = 0
    for job in db.get_unfinished_render_jobs():
        created = job.get("created_at", "")
        if not created:
            continue
        try:
            from datetime import datetime, timezone
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
            # created_at 存的是 UTC 无时区串（datetime('now')）。naive 值直接
            # .timestamp() 会按进程本地时区解释，+08:00 机器上 age 恒多 8 小时，
            # 导致 running 任务在首个清理周期即被误判超时杀掉——一律按 UTC 归一。
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            job_time = parsed.timestamp()
        except (ValueError, TypeError):
            continue
        age = now - job_time
        if job["status"] == "running" and age > RENDER_TIMEOUT:
            cancel_render(job["id"])  # 真杀进程组，防僵尸 ffmpeg 继续烧资源
            db.update_render_job(
                job["id"], status="canceled", stage="超时清理",
                error=f"渲染超过 {RENDER_TIMEOUT} 秒自动终止",
            )
            stale_count += 1
        elif job["status"] == "pending" and age > RENDER_TIMEOUT * 2:
            db.update_render_job(
                job["id"], status="failed", stage="超时清理",
                error="排队超过 10 分钟自动取消",
            )
            stale_count += 1
    if stale_count:
        print(f"🧹 已清理 {stale_count} 个超时渲染任务")


def render_job(
    job_id: str,
    static_dir: Path,
    cancel_check: Callable[[], bool] | None = None,
    output_size: tuple[int, int] = (1080, 1920),
    output_name: str | None = None,
    tts_provider: str = "mimo",
    preview: bool = False,
):
    if not is_standard_portrait_size(output_size):
        job = db.get_render_job(job_id)
        if job:
            db.update_render_job(
                job_id, status="failed", stage="画幅校验失败",
                error="正式视频只支持统一的 9:16 竖屏画幅",
            )
        return
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        db.update_render_job(job_id, status="failed", stage="依赖缺失", error="未安装 FFmpeg/ffprobe")
        return

    job = db.get_render_job(job_id)
    if not job:
        return
    script_usage = source_usage_report(job.get("script", {}).get("scenes") or [])
    if not script_usage["passed"]:
        db.update_render_job(
            job_id, status="failed", stage="素材重复硬门禁",
            error="；".join(script_usage["issues"])[:500],
        )
        return

    work_root = static_dir / "uploads" / "render" / job_id
    output_rel = Path("uploads") / "video" / (output_name or f"douyin-{job_id}.mp4")
    output = static_dir / output_rel
    work_root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    event_clips = {int(item["id"]): item for item in db.list_hotspot_event_clips()}

    def is_canceled() -> bool:
        if cancel_check and cancel_check():
            return True
        latest = db.get_render_job(job_id)
        return bool(latest and latest.get("status") in ("cancel_requested", "canceled"))

    def check_canceled():
        if is_canceled():
            raise RenderCanceled("视频生成已取消")

    try:
        db.update_render_job(job_id, status="running", stage="生成旁白", progress=5, error=None)
        segments = []
        scene_durations = []
        subtitle_count = 0
        subtitle_sync_reports = []
        used_clip_refs: list[dict] = []
        scene_subtitles: list[dict] = []
        rendered_scene_sources: list[dict] = []
        audio_tempo_adjustments: list[dict] = []
        voiceover_compactions: list[dict] = []

        # 从脚本标题或首场景旁白提取整体话题，用于素材匹配时加权
        topic = job["script"].get("title", "") or ""
        if not topic and job["script"].get("scenes"):
            topic = job["script"]["scenes"][0].get("voiceover", "")[:40]
        preserve_planned_duration = (
            str(job["script"].get("source_type") or "") == "topic_brief_dual_library"
            or int(job["script"].get("duration_target_ms") or 0) >= 50_000
        )

        scenes = list(job["script"]["scenes"])
        # Parallel first-pass TTS: each scene writes an indexed WAV; video-fit
        # re-TTS stays serial inside the per-scene loop below.
        check_canceled()
        tts_reports: list[dict] = []
        with ThreadPoolExecutor(max_workers=_scene_tts_concurrency()) as pool:
            tts_futures = {
                index: pool.submit(
                    synthesize_scene_voiceover,
                    scene["voiceover"],
                    work_root / f"voice-{index}.wav",
                    tts_provider=tts_provider,
                    voice=job["voice"],
                )
                for index, scene in enumerate(scenes)
            }
            for index, future in tts_futures.items():
                check_canceled()
                scene_meta = future.result() or {}
                tts_reports.append({"scene_index": index, **dict(scene_meta)})

        pending_scene_renders: list[dict] = []
        for index, scene in enumerate(scenes):
            # 1. 使用并行预生成的 TTS 音频
            check_canceled()
            wav = work_root / f"voice-{index}.wav"
            check_canceled()
            speech_duration = _probe_media(ffprobe, wav)["duration"]
            # 永不截断旁白。短旁白不再被原计划镜头强行拉成数秒静音尾部。
            duration = scene_render_duration(
                float(scene["duration"]), speech_duration,
                is_brand_endcard=bool(scene.get("brand_endcard_path")),
                preserve_planned_duration=preserve_planned_duration,
            )

            # 2. 获取素材。没有对应的真实热点/自有素材时直接失败，
            # 不允许以信息图、流程图或循环画面填满旁白。
            asset_id = scene.get("asset_id")
            asset = None
            clip_ref = None
            endcard_rel = str(scene.get("brand_endcard_path") or "")
            animate_image = False
            if is_explanation_scene(scene):
                raise ValueError("信息图、流程图和 PPT 卡片已禁用；请补充真实热点 Hook 或 Buffalo 自有素材")
            elif endcard_rel:
                candidate = (static_dir / endcard_rel).resolve()
                if not candidate.is_relative_to(static_dir.resolve()) or not candidate.is_file():
                    raise ValueError("品牌结尾图片不存在或路径不安全")
                asset = {"id": None, "name": "Buffalo 品牌结尾", "file_type": "image", "filepath": endcard_rel,
                         "hotspot_id": None}
            else:
                asset = db.get_asset(asset_id) if asset_id else None
            if not asset or asset.get("file_type") not in ("video", "image"):
                raise ValueError(
                    f"第{index + 1}镜没有对应素材；请补充未使用的热点 Hook、Buffalo 自有视频或自有图片，禁止循环填充旁白"
                )

            if asset["file_type"] == "video":
                try:
                    clip_ref = resolve_clip_ref(scene, asset, event_clips)
                except ClipReferenceError as exc:
                    raise ValueError(str(exc)) from exc
                matched_segment = db.get_asset_segment(scene.get("asset_segment_id")) if scene.get("asset_segment_id") else None
                if matched_segment and matched_segment.get("asset_id") != asset["id"]:
                    matched_segment = None
                if clip_ref.get("library_origin") == "hotspot_event":
                    source = static_dir / asset["filepath"]
                    event_start, event_end = int(clip_ref["start_ms"]), int(clip_ref["end_ms"])
                    chosen_start = int(scene.get("asset_start_ms") or event_start)
                    chosen_end = int(scene.get("asset_end_ms") or event_end)
                    # 事件代理片段可用于默认预览；但当内容分析已在事件内部选定
                    # 更具体的现场镜头时，必须从母片按这个精确范围取画面。
                    if event_start <= chosen_start < chosen_end <= event_end:
                        source_start, source_end = chosen_start / 1000, chosen_end / 1000
                        clip_ref = {**clip_ref, "start_ms": chosen_start, "end_ms": chosen_end,
                                    "duration_ms": chosen_end - chosen_start}
                    else:
                        source_start, source_end = event_start / 1000, event_end / 1000
                    matched_segment = None
                elif matched_segment and matched_segment.get("preview_path"):
                    source = static_dir / matched_segment["preview_path"]
                    source_start = 0
                    # 片段预览文件本身已经是精确范围，只播放一次。
                    source_end = None
                else:
                    source = static_dir / asset["filepath"]
                    source_start = (matched_segment.get("start_ms", 0) if matched_segment else scene.get("asset_start_ms", 0)) / 1000
                    end_ms = matched_segment.get("end_ms", 0) if matched_segment else scene.get("asset_end_ms", 0)
                    source_end = end_ms / 1000 if end_ms and end_ms / 1000 > source_start else None
                is_video = True
                subtitle_layout = (
                    "hotspot_news" if clip_ref.get("library_origin") == "hotspot_event"
                    else "standard"
                )
                used_clip_refs.append(dict(clip_ref))
                print(f"✅ 场景 {index+1}: 使用视频 {asset['name']}，起点 {source_start:g} 秒")
            else:
                if asset.get("hotspot_id"):
                    raise ValueError("热点图片也必须通过事件片段或人工确认后使用")
                source = static_dir / asset["filepath"]
                is_video = False
                source_start = 0
                source_end = None
                subtitle_layout = "standard"
                # 品牌结尾也采用极轻的慢推进。保持 Logo 与 CTA 可读，同时避免
                # 3–5 秒完全静止的卡片在短视频中被感知为卡顿或冻结帧。
                animate_image = True
                print(f"✅ 场景 {index+1}: 使用图片 {asset['name']}")

            if is_video and (source_start > 0 or source_end is not None):
                clipped_source = work_root / f"visual-source-{index}.mp4"
                run_cancelable_process(
                    job_id,
                    _clip_source_command(
                        ffmpeg, source, clipped_source, source_start, source_end,
                        fast=preview,
                    ),
                    timeout=180,
                    cancel_check=is_canceled,
                )
                source, source_start, source_end = clipped_source, 0, None

            if is_video:
                available_seconds = max(0.0, _probe_media(ffprobe, source)["duration"] - source_start)
                # 防御加固：确有溢出且真实素材严重不足（连一半旁白都盖不住）时快速失败，
                # 不再进入“缩短旁白→外部 TTS 重合成（~90s/次、会重试）”的慢路径硬凑残缺旁白。
                if (
                    available_seconds + 0.12 < duration
                    and (available_seconds - TTS_BREATHING_ROOM_SECONDS)
                    < speech_duration * MIN_FOOTAGE_TO_NARRATION_RATIO
                ):
                    raise ValueError(
                        f"第{index + 1}镜真实视频仅 {available_seconds:.1f} 秒，远不足以覆盖 "
                        f"{speech_duration:.1f} 秒旁白；请更换足够长的真实素材 Beat，禁止循环或以残缺旁白硬凑"
                    )
                # One local text contraction is allowed after TTS has
                # been measured.  It protects real 3–7 second beats from a
                # punctuation-heavy voiceover without looping their footage.
                for text_attempt in range(2):
                    speedup = tts_speedup_factor(speech_duration, available_seconds)
                    if speedup is not None and available_seconds + 0.12 < duration:
                        fitted_wav = work_root / f"voice-{index}-fitted.wav"
                        run_cancelable_process(
                            job_id, _audio_tempo_command(ffmpeg, wav, fitted_wav, speedup),
                            timeout=60, cancel_check=is_canceled,
                        )
                        wav = fitted_wav
                        original_duration = speech_duration
                        speech_duration = _probe_media(ffprobe, wav)["duration"]
                        duration = scene_render_duration(
                            float(scene["duration"]), speech_duration,
                            is_brand_endcard=bool(scene.get("brand_endcard_path")),
                            preserve_planned_duration=preserve_planned_duration,
                        )
                        audio_tempo_adjustments.append({
                            "scene": index + 1,
                            "tempo": round(speedup, 4),
                            "audio_seconds_before": round(original_duration, 3),
                            "audio_seconds_after": round(speech_duration, 3),
                        })
                    if available_seconds + 0.12 >= duration or text_attempt:
                        break
                    shortened_voiceover = compact_voiceover_to_fit_real_video(
                        scene["voiceover"], speech_duration, available_seconds,
                    )
                    if not shortened_voiceover:
                        break
                    original_voiceover = scene["voiceover"]
                    scene = {**scene, "voiceover": shortened_voiceover}
                    # Keep the quality storyboard aligned with the audio the
                    # user receives, while preserving the planned source refs.
                    job["script"]["scenes"][index]["voiceover"] = shortened_voiceover
                    wav = work_root / f"voice-{index}-shortened.wav"
                    synthesize_scene_voiceover(
                        shortened_voiceover, wav, tts_provider=tts_provider, voice=job["voice"],
                    )
                    speech_duration = _probe_media(ffprobe, wav)["duration"]
                    duration = scene_render_duration(
                        float(scene["duration"]), speech_duration,
                        is_brand_endcard=bool(scene.get("brand_endcard_path")),
                        preserve_planned_duration=preserve_planned_duration,
                    )
                    voiceover_compactions.append({
                        "scene": index + 1,
                        "original": original_voiceover,
                        "rendered": shortened_voiceover,
                    })
                if available_seconds + 0.12 < duration:
                    raise ValueError(
                        f"第{index + 1}镜真实视频仅 {available_seconds:.1f} 秒，旁白需 {duration:.1f} 秒；"
                        "必须拆分成可用的真实素材 Beat 或补充未使用的 Buffalo 自有图片，禁止循环真实视频"
                    )
            scene_durations.append(duration)
            audio_path = wav if os.path.exists(wav) else None
            cues = _build_subtitle_cues_internal(
                scene["voiceover"],
                min(speech_duration, duration),
                audio_path=audio_path,
                ffmpeg=ffmpeg or "ffmpeg",
            )
            subtitle_sync = subtitle_sync_report(cues, speech_duration)
            subtitle_sync_reports.append(subtitle_sync)
            scene_subtitles.append({"render_duration": duration, "sync": subtitle_sync})
            subtitle_count += len(cues)
            subtitle_file = work_root / f"subtitle-{index}.ass"
            _write_ass(cues, subtitle_file)
            rendered_scene_sources.append({
                **scene,
                "clip_ref": clip_ref,
                "render_duration": duration,
            })

            segment = work_root / f"segment-{index}.mp4"
            # 3. 用逐句 PNG overlay 烧录字幕，避免依赖 FFmpeg 可选的 libass。
            command = _scene_command(ffmpeg, ffprobe, source, is_video, wav, cues,
                                     duration, segment, work_root, index,
                                     source_start=source_start, source_end=source_end,
                                     output_size=output_size,
                                     watermark_text=str(job["script"].get("watermark") or ""),
                                     subtitle_layout=subtitle_layout,
                                     animate_image=animate_image,
                                     fast=preview)
            pending_scene_renders.append({
                "index": index,
                "segment": segment,
                "command": command,
            })

        # Parallel scene FFmpeg: cancel tracks every child process for this job.
        segments = [None] * len(pending_scene_renders)
        scene_total = max(1, len(pending_scene_renders))

        def _render_pending_scene(item: dict) -> tuple[int, Path]:
            run_cancelable_process(
                job_id, item["command"], timeout=180, cancel_check=is_canceled,
            )
            return item["index"], item["segment"]

        with ThreadPoolExecutor(max_workers=_scene_ffmpeg_concurrency()) as pool:
            futures = [pool.submit(_render_pending_scene, item) for item in pending_scene_renders]
            completed = 0
            for future in as_completed(futures):
                check_canceled()
                index, segment = future.result()
                segments[index] = segment
                completed += 1
                db.update_render_job(
                    job_id,
                    stage=f"合成场景 {completed}/{scene_total}",
                    progress=10 + int(75 * completed / scene_total),
                )
        if any(segment is None for segment in segments):
            raise RuntimeError("分镜并行渲染未产出完整片段列表")

        # 5. 统一音画时间基准并做极短交叉淡化，避免分镜边界出现停帧或时间戳跳变。
        check_canceled()
        segment_durations = [_probe_media(ffprobe, segment)["duration"] for segment in segments]
        # A very short crossfade still reads as a hard cut when warehouse and
        # delivery beats alternate on a phone. Keep the edit brisk but give the
        # picture and narration a perceptibly smoother handoff.
        transition_duration = 0.5
        # xfade/acrossfade overlap adjacent beats. Formal dual-library videos
        # promise a 50–90s rendered duration, so a valid storyboard must not
        # lose its lower-bound duration merely because many transitions overlap.
        if preserve_planned_duration and len(segment_durations) > 1:
            available_overlap = (
                sum(segment_durations) - 50.0
            ) / (len(segment_durations) - 1)
            if available_overlap < transition_duration:
                transition_duration = max(0.0, available_overlap)
        # 预览只用于内部质检/查看画面与字幕，不需要 crossfade 的全片重编码；
        # 直接走 -c copy 的硬切拼接可以把这一步的耗时从数十秒降到接近零。
        # 真正的交叉淡化过渡只在最终成片时渲染一次。
        transition_fallback = preview
        if preview:
            run_cancelable_process(
                job_id, _safe_concat_command(ffmpeg, segments, output), timeout=240,
                cancel_check=is_canceled,
            )
            output.with_suffix(".concat.txt").unlink(missing_ok=True)
        else:
            try:
                run_cancelable_process(
                    job_id,
                    _transition_concat_command(
                        ffmpeg, segments, segment_durations, output,
                        transition_duration=transition_duration,
                    ),
                    timeout=240,
                    cancel_check=is_canceled,
                )
            except subprocess.CalledProcessError:
                # FFmpeg builds differ in their xfade graph support; do not discard a
                # fully rendered, source-verified preview solely for that reason.
                transition_fallback = True
                run_cancelable_process(
                    job_id, _safe_concat_command(ffmpeg, segments, output), timeout=240,
                    cancel_check=is_canceled,
                )
                output.with_suffix(".concat.txt").unlink(missing_ok=True)
        expected_duration = sum(segment_durations) - (0 if transition_fallback else transition_duration * (len(segments) - 1))
        report = _quality_report(
            ffprobe, output, expected_duration, subtitle_count,
            expected_size=output_size,
        )
        report["transition"] = {
            "type": "hard_cut_fallback" if transition_fallback else "crossfade",
            "duration": 0 if transition_fallback else transition_duration,
            "count": len(segments) - 1,
        }
        report["frame_policy"] = {
            "id": PORTRAIT_FRAME_POLICY,
            "canvas": f"{output_size[0]}x{output_size[1]}",
            "description": "每个镜头满版居中裁切到统一 9:16，禁止横向插卡或信箱边框",
        }
        report["audio_sync"] = {
            "passed": all(item["passed"] for item in subtitle_sync_reports),
            "scenes": subtitle_sync_reports,
        }
        report["tts"] = {
            "requested_provider": tts_provider,
            "scenes": tts_reports,
            "cache_hits": sum(1 for item in tts_reports if item.get("cache_hit")),
        }
        report["audio_tempo_adjustments"] = audio_tempo_adjustments
        report["voiceover_compactions"] = voiceover_compactions
        source_usage = source_usage_report(rendered_scene_sources)
        final_subtitles = subtitle_timeline_report(
            scene_subtitles,
            final_duration=report["duration"],
            transition_duration=0 if transition_fallback else transition_duration,
        )
        # These are final-output gates, not scene-level hints.  A video only
        # passes when the render manifest has no duplicate source/range and its
        # measured final duration still agrees with the post-transition subtitle
        # timeline and audio stream.
        report["source_usage"] = source_usage
        report["final_subtitle_timeline"] = final_subtitles
        # The semantic QA service receives this actual (post-TTS, post-transition)
        # timeline, rather than guessing static-image windows from planned lengths.
        report["render_timeline"] = final_subtitles.get("timeline") or []
        report["transition_audio_sync"] = {
            "passed": bool(
                report["checks"]["has_audio"]
                and report["checks"]["duration_aligned"]
                and report["audio_sync"]["passed"]
                and final_subtitles["passed"]
            ),
            "transition_type": report["transition"]["type"],
        }
        report["checks"].update({
            "no_repeated_source_or_range": source_usage["passed"],
            "final_subtitle_timeline_aligned": final_subtitles["passed"],
            "transition_audio_video_sync": report["transition_audio_sync"]["passed"],
            "production_duration_50_90s": (
                50.0 <= float(report.get("duration") or 0) <= 90.0
                if int(job["script"].get("duration_target_ms") or 0) >= 50_000
                else True
            ),
        })
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
        clips = []
        if job["script"].get("output_mode") == "full_and_clips":
            clip_dir = static_dir / "uploads" / "video" / "clips"
            clip_dir.mkdir(parents=True, exist_ok=True)
            for scene_number in job["script"].get("selected_clip_scenes") or []:
                source_segment = segments[scene_number - 1]
                clip_rel = Path("uploads") / "video" / "clips" / f"douyin-{job_id}-scene-{scene_number}.mp4"
                shutil.copy2(source_segment, static_dir / clip_rel)
                clip_report = _quality_report(
                    ffprobe, static_dir / clip_rel, scene_durations[scene_number - 1],
                    len(build_subtitle_cues(job['script']['scenes'][scene_number - 1]['voiceover'], scene_durations[scene_number - 1])),
                    expected_size=output_size,
                )
                clips.append({"scene": scene_number, "type": "video", "path": clip_rel.as_posix(),
                              "url": "/static/" + clip_rel.as_posix(), "filename": clip_rel.name,
                              "quality_report": clip_report, "quality_status": clip_report["status"]})
        if report["status"] != "passed":
            raise RuntimeError("视频质量门禁未通过：" + "、".join(key for key, ok in report["checks"].items() if not ok))
        report["clip_refs"] = used_clip_refs
        # 批13 D3 复用治理：渲染成功后记录本次实际用到的素材（含 za_stock），
        # 供 _owned_candidates 的使用惩罚/冷却降权，避免老素材霸榜。
        used_asset_ids = sorted({
            int(item.get("asset_id")) for item in rendered_scene_sources if item.get("asset_id")
        })
        if used_asset_ids:
            db.bump_asset_usage(used_asset_ids, datetime.now(timezone.utc).isoformat())
        db.update_render_job(job_id, status="succeeded", stage="质量检查通过", progress=100,
                             output_path=output_rel.as_posix(), clips=clips, quality_report=report, error=None)

    except RenderCanceled:
        output.unlink(missing_ok=True)
        db.update_render_job(job_id, status="canceled", stage="已取消", error=None)
    except Exception as exc:
        db.update_render_job(job_id, status="failed", stage="渲染失败", error=str(exc)[:500])
