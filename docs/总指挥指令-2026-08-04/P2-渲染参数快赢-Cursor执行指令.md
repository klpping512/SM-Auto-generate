# Cursor 执行指令 · P2 渲染参数快赢（正式成片锐化）

> 总指挥背景（先读再动手）：
> 已核实 `render_job(preview=False)` 是正式成片，一路 `fast=preview=False`。故正式片走**非-fast 路径**，
> 旧文档里的 `crf 28` 只活在预览路径、不是元凶。真凶是正式片要过**三段 libx264 重编码**、代际损失叠加，
> 且中间"缩放+烧字幕"那段**没有显式 crf（掉到默认 23）+ veryfast**——它恰好承载画面缩放与字幕清晰度。
>
> 本指令目标：让**中间过渡片近视觉无损（crf 18）**、**交付段用 medium 预设**，减少代际损失、锐化字幕。
> **纯参数改动，零逻辑风险**；预览/fast 路径**完全不动**，保证预览仍然快。

---

## 三段编码的现状（已核实）

| 段 | 函数/行 | 非-fast 现值 | 问题 |
|---|---|---|---|
| ① 物化镜头 | `_clip_source_command` L869-873 | `crf 20` `preset veryfast` | 中间片，被后续再编码 2 次，应更接近无损 |
| ② 缩放+烧字幕 | 段渲染函数 L851-854（docstring「逐句 PNG overlay 烧录字幕」） | **无显式 crf（默认 23）** `preset veryfast` | 最弱一环，字幕/缩放在此定清晰度 |
| ③ 转场合成（交付） | `_transition_concat_command` L945 | `crf 20` `preset veryfast` | 真·交付编码，preset 太快=同 crf 下不够锐 |

---

## 改动 1：新增集中可调档位（单一旋钮，便于 A/B）

在 `video_renderer.py` 顶部（`MIMO_TTS_VOICE = "mimo_default"` 附近，`os` 已 import）新增：

```python
# 正式成片编码档位（仅非-fast 路径生效；preview/fast 路径仍走 ultrafast+crf28 保持快）
# 原则：中间过渡片近视觉无损，只让"交付段"做真正压缩，减少三段重编码的代际损失。
RENDER_FINAL_PRESET = os.environ.get("RENDER_PRESET", "medium")          # was veryfast — 同 crf 下压缩更充分=更锐
RENDER_INTERMEDIATE_CRF = os.environ.get("RENDER_INTERMEDIATE_CRF", "18")  # 中间片近视觉无损
RENDER_FINAL_CRF = os.environ.get("RENDER_FINAL_CRF", "20")              # 交付段压缩档
```

---

## 改动 2：`_clip_source_command`（L869-873）非-fast 路径

**现状：**
```python
    preset = "ultrafast" if fast else "veryfast"
    crf = "28" if fast else "20"
    return command + [
        "-r", "30", "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
```
**改为：**
```python
    preset = "ultrafast" if fast else RENDER_FINAL_PRESET
    crf = "28" if fast else RENDER_INTERMEDIATE_CRF
    return command + [
        "-r", "30", "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
```

---

## 改动 3：段渲染+烧字幕函数（L851-854）非-fast 路径 —— 关键一环

**现状：**
```python
    preset = "ultrafast" if fast else "veryfast"
    crf_args = ["-crf", "28"] if fast else []
    return command + ["-r", "30", "-c:v", "libx264", "-preset", preset, *crf_args,
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(segment)]
```
**改为**（给非-fast 显式补上 crf 18，替代原来的空默认 23）：
```python
    preset = "ultrafast" if fast else RENDER_FINAL_PRESET
    crf_args = ["-crf", "28"] if fast else ["-crf", RENDER_INTERMEDIATE_CRF]
    return command + ["-r", "30", "-c:v", "libx264", "-preset", preset, *crf_args,
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(segment)]
```

---

## 改动 4：`_transition_concat_command`（L945）交付段

**现状：**
```python
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
```
**改为：**
```python
        "-r", "30", "-c:v", "libx264", "-preset", RENDER_FINAL_PRESET, "-crf", RENDER_FINAL_CRF,
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
```

---

## 硬边界（不得越界）

- **只改这 4 处编码参数**；不改滤镜链（scale/crop/lanczos/overlay/zoompan）、不改字幕生成、不改选片/时长/转场时长逻辑。
- **fast/preview 分支保持原样**（`ultrafast` + `crf 28`），预览必须仍然快。
- 不动 `-r 30`、`1080x1920`、`-pix_fmt yuv420p`（这些不是当前糊的原因，douyin 竖屏没问题）。
- 不碰 TTS / 音色（那是另一档，见下）。

---

## ⚠️ 代价与回退（总指挥已知情）

- `veryfast→medium` + `crf 20→18` 会让**编码耗时上升约 2–4×**。50–90 秒竖屏片可接受，但需实测。
- 若单个 job 编码耗时逼近 `render_job` 的超时清理（L1159 一带），把 `RENDER_PRESET` 环境变量回退到 `fast`（medium 与 veryfast 之间的折中），无需改代码。
- 产物文件会变大（crf 更低），属预期。

---

## 不纳入本次 P2（保持窄、低风险）

- **TTS 单音色（mimo_default）**：`tts_voice_options` / `resolve_tts_selection` 基建已在，但加音色变化会动 job/产品流，归 P3 单独做。
- fps / 分辨率 / 转场时长：非缺陷，不动。

---

## 验证（Cursor 自检后回报）

1. `pytest` 全绿（本次纯参数，理应不破坏用例；若有断言硬编码 `veryfast`/`crf` 字符串的用例失败，如实回报，别擅改）。
2. **实拍验证**：跑 1 个真实正式 job（preview=False），肉眼对比字幕边缘锐度与整体清晰度 vs 改前；记录该 job 编码耗时（改前/改后）与产物体积。
3. 确认**预览路径未变**：跑一次 preview=True，确认仍是 `ultrafast`（命令里应仍出现 ultrafast + crf 28）。

## 回报格式

- 4 处改动 diff（或 commit hash）。
- 一个真实 job 的：编码耗时改前/改后、产物体积改前/改后、字幕锐度主观判断（糊/清晰）。
- 预览路径未受影响的确认。
