"""MiMo TTS and deterministic FFmpeg vertical-video rendering."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

import database as db

MIMO_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
VOICES = {"冰糖", "茉莉", "苏打", "白桦"}

# 与 media_assets.CATEGORY_KEYWORDS 保持同步，用于渲染兜底时的场景关键词→分类匹配
CATEGORY_KEYWORDS = {
    "warehouse": ("海外仓", "仓库", "仓储", "warehouse", "storage", "stock", "货架", "外景",
                  "打包", "分拣", "理货", "拣货", "上架", "作业", "操作员", "搬运",
                  "入库", "出库", "库存", "堆场", "月台", "托盘", "扫描", "货物"),
    "delivery": ("配送", "快递", "物流", "运输", "派送", "卡车", "车辆", "路线",
                 "delivery", "courier", "shipping", "logistics"),
    "customs": ("清关", "海关", "报关", "通关", "customs", "clearance", "文件", "单据"),
    "brand": ("品牌", "商标", "brand", "logo", "标识", "信息卡", "结尾"),
    "staff": ("工作人员", "员工", "职员", "staff", "团队", "培训", "工人",
              "办公", "开会", "会议", "面试", "合照", "团建"),
    "facility": ("设备", "设施", "facility", "叉车", "传送带", "机器", "流水线"),
    "customer": ("客户", "customer", "案例", "好评", "反馈", "见证", "采访"),
}

# 分类优先级：平分时按此顺序选择（物流场景仓库优先于人员）
CATEGORY_PRIORITY = ["warehouse", "delivery", "customs", "facility", "brand", "customer", "staff"]


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


def normalize_script(script: dict, asset_ids: set[int]) -> dict:
    scenes = []
    for index, raw in enumerate(script.get("scenes") or []):
        if not isinstance(raw, dict):
            continue
        asset_id = raw.get("asset_id")
        if asset_id not in asset_ids:
            asset_id = None
        scenes.append({
            "scene": index + 1,
            "duration": max(3.0, min(8.0, float(raw.get("duration") or 5))),
            "visual": str(raw.get("visual") or "品牌信息卡")[:80],
            "voiceover": str(raw.get("voiceover") or "")[:180],
            "text_overlay": str(raw.get("text_overlay") or raw.get("voiceover") or "")[:24],
            "asset_id": asset_id,
        })
    if not 4 <= len(scenes) <= 6:
        raise ValueError("抖音脚本必须包含 4–6 个场景")
    total = sum(scene["duration"] for scene in scenes)
    factor = 30 / total
    for scene in scenes:
        scene["duration"] = round(max(3, min(8, scene["duration"] * factor)), 2)
    return {**script, "duration_target": 30, "scenes": scenes}


def synthesize_mimo(text: str, voice: str, output: Path, api_key: str | None = None):
    key = api_key or os.environ.get("MIMO_API_KEY", "")
    if not key:
        raise RuntimeError("未配置 MIMO_API_KEY")
    if voice not in VOICES:
        raise ValueError("不支持的 MiMo 音色")
    payload = {
        "model": "mimo-v2.5-tts",
        "messages": [
            {"role": "user", "content": "专业、可信、自然的中文短视频旁白，语速略快，重点清晰。"},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav", "voice": voice},
    }
    with httpx.Client(timeout=90, trust_env=False) as client:
        response = client.post(MIMO_URL, headers={"api-key": key, "Content-Type": "application/json"}, json=payload)
        response.raise_for_status()
    try:
        encoded = response.json()["choices"][0]["message"]["audio"]["data"]
        output.write_bytes(base64.b64decode(encoded, validate=True))
    except Exception as exc:
        raise RuntimeError("MiMo 返回了无效音频") from exc


def _font(size=70):
    for path in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _fallback_card(text: str, output: Path):
    """生成品牌信息卡图片（纯文字兜底）"""
    image = Image.new("RGB", (1080, 1920), "#F6F3ED")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((80, 160, 1000, 1760), radius=54, fill="white")
    draw.text((130, 240), "BUFFALO · SA LOGIFLOW", font=_font(40), fill="#B98A4C")
    y, line = 650, ""
    for char in text[:60]:
        if draw.textbbox((0, 0), line + char, font=_font())[2] > 790:
            draw.text((145, y), line, font=_font(), fill="#171717")
            y += 105
            line = char
        else:
            line += char
    if line:
        draw.text((145, y), line, font=_font(), fill="#171717")
    image.save(output)


def _generate_text_overlay(text: str, output: Path):
    """用 PIL 生成透明背景的文字叠加图片"""
    img = Image.new('RGBA', (1080, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    font = None
    for fp in ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]:
        if Path(fp).exists():
            try:
                font = ImageFont.truetype(fp, 42)
                break
            except Exception:
                pass
    if not font:
        font = ImageFont.load_default()
    
    x, y = 540, 60
    for dx in [-2, 0, 2]:
        for dy in [-2, 0, 2]:
            draw.text((x+dx, y+dy), text, font=font, fill="black", anchor="mm")
    draw.text((x, y), text, font=font, fill="white", anchor="mm")
    img.save(output)


def _has_audio(ffprobe: str, path: Path) -> bool:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    return bool(result.stdout.strip())


# 渲染任务超时时间（秒）
RENDER_TIMEOUT = 300  # 5 分钟


def cleanup_stale_jobs():
    """清理卡住的渲染任务：running 超过 5 分钟的标记为 failed，pending 超过 10 分钟的也清理。"""
    import time
    now = time.time()
    stale_count = 0
    for job in db.get_unfinished_render_jobs():
        created = job.get("created_at", "")
        if not created:
            continue
        try:
            from datetime import datetime
            job_time = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        age = now - job_time
        if job["status"] == "running" and age > RENDER_TIMEOUT:
            db.update_render_job(job["id"], status="failed", stage="超时清理",
                                 error=f"渲染超过 {RENDER_TIMEOUT} 秒自动终止")
            stale_count += 1
        elif job["status"] == "pending" and age > RENDER_TIMEOUT * 2:
            db.update_render_job(job["id"], status="failed", stage="超时清理",
                                 error="排队超过 10 分钟自动取消")
            stale_count += 1
    if stale_count:
        print(f"🧹 已清理 {stale_count} 个超时渲染任务")


def render_job(job_id: str, static_dir: Path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        db.update_render_job(job_id, status="failed", stage="依赖缺失", error="未安装 FFmpeg/ffprobe")
        return

    job = db.get_render_job(job_id)
    if not job:
        return

    work_root = static_dir / "uploads" / "render" / job_id
    output_rel = Path("uploads") / "video" / f"douyin-{job_id}.mp4"
    output = static_dir / output_rel
    work_root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        db.update_render_job(job_id, status="running", stage="生成旁白", progress=5, error=None)
        segments = []
        used_asset_ids: set[int] = set()  # 跟踪已分配素材，避免重复

        # 从脚本标题或首场景旁白提取整体话题，用于素材匹配时加权
        topic = job["script"].get("title", "") or ""
        if not topic and job["script"].get("scenes"):
            topic = job["script"]["scenes"][0].get("voiceover", "")[:40]

        for index, scene in enumerate(job["script"]["scenes"]):
            # 1. 生成 TTS 音频
            wav = work_root / f"voice-{index}.wav"
            synthesize_mimo(scene["voiceover"], job["voice"], wav)

            # 2. 获取素材（强制使用素材库视频）
            asset_id = scene.get("asset_id")
            asset = db.get_asset(asset_id) if asset_id else None

            if not asset or asset["file_type"] not in ("video", "image"):
                all_videos = db.list_assets(file_type="video", status="active")
                if all_videos:
                    # 优先按场景画面描述匹配素材分类，兜底才用轮询
                    visual = scene.get("visual", "")
                    matched = _match_asset_by_scene(visual, all_videos,
                                                    used_asset_ids=used_asset_ids,
                                                    topic=topic)
                    if matched:
                        asset = matched
                        used_asset_ids.add(asset["id"])
                        print(f"✅ 场景 {index+1}: 按画面描述匹配素材 {asset['name']} (category={asset.get('category')})")
                    else:
                        # 所有素材都已用过，轮询选一个未用的
                        unused = [a for a in all_videos if a["id"] not in used_asset_ids]
                        pool = unused if unused else all_videos
                        asset = pool[index % len(pool)]
                        used_asset_ids.add(asset["id"])
                        print(f"⚠️ 场景 {index+1}: 无分类匹配，轮询选用 {asset['name']}")
                else:
                    card_path = work_root / f"card-{index}.png"
                    _fallback_card(scene["visual"], card_path)
                    source = card_path
                    is_video = False
                    print(f"❌ 场景 {index+1}: 素材库为空，使用品牌信息卡")
                    duration = scene["duration"]
                    segment = work_root / f"segment-{index}.mp4"
                    frames = int(duration * 30)
                    vf = f"zoompan=z=min(zoom+0.001\\,1.15):s=1080:1920:fps=30"
                    command = [ffmpeg, "-y", "-loop", "1", "-i", str(source), "-i", str(wav),
                               "-t", str(duration), "-vf", vf,
                               "-map", "0:v", "-map", "1:a",
                               "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
                               "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                               str(segment)]
                    subprocess.run(command, capture_output=True, timeout=180, check=True)
                    segments.append(segment)
                    continue

            if asset["file_type"] == "video":
                source = static_dir / asset["filepath"]
                is_video = True
                print(f"✅ 场景 {index+1}: 使用视频 {asset['name']}")
            else:
                source = static_dir / asset["filepath"]
                is_video = False
                print(f"✅ 场景 {index+1}: 使用图片 {asset['name']}")

            duration = scene["duration"]
            segment = work_root / f"segment-{index}.mp4"

            # 3. 生成字幕叠加图片
            subtitle_text = scene.get("text_overlay", "")
            overlay_img = work_root / f"overlay-{index}.png"
            if subtitle_text:
                _generate_text_overlay(subtitle_text, overlay_img)
                print(f"  ✅ 已添加字幕: {subtitle_text}")

            # 4. 构建 FFmpeg 命令
            visual_input = ["-stream_loop", "-1", "-i", str(source)] if is_video else ["-loop", "1", "-i", str(source)]
            
            if is_video:
                vf_base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
            else:
                vf_base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
            
            # 如果有字幕，添加 overlay
            if subtitle_text and overlay_img.exists():
                command = [ffmpeg, "-y", *visual_input, "-i", str(overlay_img), "-i", str(wav), "-t", str(duration)]
                if is_video and _has_audio(ffprobe, source):
                    command += ["-filter_complex", 
                                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[v0];[v0][1:v]overlay=0:main_h-overlay_h[v];[0:a]volume=0.15[bg];[bg][2:a]amix=inputs=2:duration=first[a]",
                                "-map", "[v]", "-map", "[a]"]
                else:
                    command += ["-filter_complex",
                                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[v0];[v0][1:v]overlay=0:main_h-overlay_h[v]",
                                "-map", "[v]", "-map", "2:a"]
            else:
                command = [ffmpeg, "-y", *visual_input, "-i", str(wav), "-t", str(duration), "-vf", vf_base]
                if is_video and _has_audio(ffprobe, source):
                    command += ["-filter_complex", "[0:a]volume=0.15[bg];[bg][1:a]amix=inputs=2:duration=first[a]", "-map", "0:v", "-map", "[a]"]
                else:
                    command += ["-map", "0:v", "-map", "1:a"]
            
            command += ["-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(segment)]
            subprocess.run(command, capture_output=True, timeout=180, check=True)
            segments.append(segment)
            db.update_render_job(job_id, stage=f"合成场景 {index + 1}/{len(job['script']['scenes'])}", progress=10 + int(75 * (index + 1) / len(job["script"]["scenes"])))

        # 5. 拼接所有段
        concat = work_root / "concat.txt"
        concat.write_text("\n".join(f"file '{path.resolve().as_posix()}'" for path in segments), encoding="utf-8")
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(output)],
            capture_output=True, timeout=240, check=True
        )
        db.update_render_job(job_id, status="succeeded", stage="渲染完成", progress=100, output_path=output_rel.as_posix(), error=None)

    except Exception as exc:
        db.update_render_job(job_id, status="failed", stage="渲染失败", error=str(exc)[:500])
