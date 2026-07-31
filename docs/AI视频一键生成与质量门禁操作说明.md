# AI 视频一键生成与质量门禁操作说明

## 入口

本地打开：`http://localhost:8080/chat.html`

登录后，在 AI 对话中选择“抖音”，输入视频需求。AI 返回 4–6 个分镜后，点击“生成视频”。

## 完整链路

1. AI 对话生成标题、正文、旁白、字幕和分镜。
2. 点击“生成视频”后，系统先创建持久视频项目，再创建唯一生成任务。
3. 后台依次执行：脚本检查、素材匹配、匹配质量检查、低清预览、预览质量检查、高清成片、最终质量检查。
4. 全部检查通过后，才输出可下载的 1080×1920 MP4。
5. 质量不足时不会勉强成片，而是在视频项目页列出具体镜头问题，等待人工处理。

## 跳页、刷新和重复点击

- 任务保存在服务端数据库，不依赖当前页面。
- 跳到内容编辑器、素材库或其他页面后，任务仍会继续。
- 每个页面右上角都有“视频任务”入口；刷新后会自动恢复进行中或待确认任务。
- 对同一项目修订重复点击“生成视频”，只复用同一个活动任务，不会重复排队。

## 取消生成

- 在 AI 对话生成卡片、视频项目页或右上角任务中心点击“取消生成”。
- 尚未开始的任务立即取消。
- 正在执行的任务进入“正在停止”，系统会终止当前 FFmpeg 进程，并在安全检查点停止 TTS 或下一镜头。
- 取消后可在视频项目页点击“重新生成”。

## 质量不足时怎么处理

视频项目页会显示“待确认问题”，例如：

- 某个镜头没有本地素材；
- 素材语义证据偏弱；
- 横屏素材用于竖屏时需要裁切；
- 素材片段时长不足；
- 字幕、音频、时长或输出分辨率检查未通过。

可选择：

1. 点击“视频精准匹配”，人工选择更合适的镜头并回写项目；
2. 点击“内容编辑器”，修改旁白或分镜描述并保存；
3. 回“内容资产”补充并分类素材；
4. 在项目页修改后点击“继续生成”。

“视频精准匹配”属于质量异常时使用的高级工具，因此不显示在左侧主导航；只能从对应视频项目的“视频精准匹配”按钮进入，避免普通生成流程被误解为必须手工匹配。

## MiMo 成片自动质检

预览 MP4 通过原有分辨率、时长、音轨和字幕数量检查后，系统会继续执行一次真正的画面审核：

1. 本地使用 ffprobe/FFmpeg 检查视频是否可完整解码，并记录编码、帧率、宽高比、黑帧、冻结画面和连续静音。
2. 优先使用项目已有分镜和字幕，不重复调用 ASR；URL 视频优先读取平台字幕，没有字幕时才尝试本地 faster-whisper。
3. 普通扫描默认使用场景变化和关键帧，去重后最多提交 40 张 512 像素图片给 MiMo。
4. MiMo 必须返回严格 JSON，并为每个问题提供时间段、严重程度、对应帧和修正建议。证据帧不在提交索引中的报告会被拒绝。
5. 第一阶段发现 high 风险时，只对对应时间段按 5～10 fps 追加一次局部复检；全片正常时不会产生第二次视觉调用。
6. 总分低于 80 或存在 high 问题时进入人工确认，并生成下一轮 `revised_prompt`、`negative_prompt` 和问题片段列表。
7. 自动重新生成默认关闭，不会因为一份低分报告立即消耗第二轮视频生成费用。

质检产物位于：

```text
static/uploads/video-quality/<视频任务ID>/
├── metadata.json
├── technical-report.json
├── frames/index.json
├── transcript.vtt
├── evaluation.json
├── evaluation-stages.json
├── problem-segments.json
├── optimized-generation.json
└── manifest.json
```

### 独立测试一段本地视频

项目已有成片可直接执行：

```bash
cd /Users/ylanlll/Desktop/商务部/distribution-manager
python3 scripts/run_video_quality_mvp.py \
  --video-source static/uploads/video/sample-24edc50dd82d49ab9d69b4f357344bcd.mp4 \
  --storyboard-json data/samples/24edc50dd82d49ab9d69b4f357344bcd/video-script.json \
  --target-platform 抖音 \
  --output-dir data/video-quality-runs/local-mvp
```

也可以使用完整输入 JSON：

```json
{
  "video_source": "本地文件路径或HTTPS视频URL",
  "original_prompt": "原始视频生成提示词",
  "storyboard": "原始分镜脚本或分镜对象",
  "reference_images": [],
  "target_platform": "抖音",
  "mode": "balanced",
  "max_frames": 40,
  "auto_regenerate": false
}
```

保存为 `input.json` 后执行：

```bash
python3 scripts/run_video_quality_mvp.py --input-json input.json
```

### 依赖与环境变量

当前 Mac 已有 FFmpeg/ffprobe。URL 下载和无字幕转写是可选能力：

```bash
pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-media-ai.txt
```

`.env` 至少需要：

```dotenv
MIMO_API_KEY=你的MiMo密钥
VIDEO_QUALITY_ENABLED=1
VIDEO_QUALITY_AUTO_REGENERATE=0
```

- 未安装 `yt-dlp`：本地 MP4 仍可质检，HTTPS 视频 URL 会返回明确依赖错误。
- 未安装或未配置 faster-whisper：已有分镜/字幕的视频不受影响；无字幕视频以空字幕继续视觉审核，并在报告中标记不可用。
- 单个视频默认限制为 300 MB、10 分钟和 100 张关键帧；日常模式实际最多 40 张。
- MiMo 超时、返回非 JSON 或证据帧无效时不会伪装通过，预览会保留并转人工确认。

### YouTube 频道热点

YouTube 频道适合作为热点发现源，但不应直接替代官方事实信源。推荐只抓取标题、频道、发布时间、简介、缩略图、链接和字幕，用于选题和素材匹配；未确认授权的视频只进入“灵感链接库”，不自动下载进本地素材库。自有或已经授权的视频才能执行下载和素材化。

频道元数据抓取与成片质检是两个独立子系统，本次 MVP 只完成视频 URL 的单条质检入口；频道定时抓取后续单独接入热点候选池，避免占用现有五个官方事实源名额。

## 素材片段规则

- 视频严格使用选中片段的 `start_ms` 和 `end_ms`，不会只从起点无限向后取画面。
- 横屏素材允许用于竖屏，但会降权并标记为需要复核，防止因为画幅硬过滤导致完全无候选。
- 低清预览使用 540×960；最终成片使用 1080×1920。

## 当前素材库的真实限制

目前本地素材以横屏为主，自动语义标签、ASR 和镜头覆盖仍不完整。因此系统可能在“素材匹配检查”暂停。这是质量门禁在阻止低质量成片，不是任务卡死。补充竖屏仓库、扫码、分拣、包裹检查和品牌结尾素材，并完成分类/标签后，自动通过率会明显提升。

## 方案 B：从南非热点生成三份内部样本

入口：`http://localhost:8080/hotspots.html`

页面按一条主链路组织：`抓取热点 → 选择热点 → 核验证据 → 检查素材 → 生成样本`。

1. 点击“抓取最新热点”。系统按来源独立读取 SAnews、SARS、南非交通部、南非政府和南非储备银行的公开 RSS / Atom；单个来源失败不会阻塞其他来源。
2. 顶部状态区显示上次抓取时间、信源健康度、新增/更新数量、许可配图数量和跳过数量。刷新或离开页面后，再回来仍能看到本次抓取是成功、部分失败还是失败。
3. 从左侧热点池选择一条记录。右侧必须显示发布机构、发布时间、抓取时间、原文链接和摘要；先打开原始来源核对适用范围。
4. 在“核验证据”中选择允许公开引用的 Buffalo 品牌证据，再点击“核验并锁定证据”。没有已确认品牌证据时仍可生成内部样本，但系统不会写具体时效、覆盖率或业绩承诺。
5. 在“检查素材”中确认本地可分析视频、图片和热点许可配图数量。热点事实与画面分开处理，自有素材只用于展示日常流程，不伪装成新闻事件现场。
6. 点击“生成视频、图文和公众号样本”。页面用三个标签分别展示 5 个视频分镜、6 页图文和 800–1200 字公众号软文，并列出来源和质量问题。
7. 候选镜头低于匹配质量门槛时，分镜会标为“需人工换镜头”，质量检查同时列出低质量镜头数量；有候选不等于匹配合格。
8. 三份文件同时保存在 `data/samples/<bundle_id>/`：`video-script.json`、`carousel.json`、`wechat.md`、`manifest.json`。
9. 这一步只生成内部审查样本，不会自动发布。页面显示“内部测试，不可发布”，`manifest.json` 中的 `publish_allowed` 固定为 `false`。

点击“系统如何采集？”可查看完整边界：抓取使用固定关键词和域名校验，不调用文本大模型；版权不明确的媒体不会自动下载。点击“信源管理”可查看五个信源的启用状态和上次健康结果。

需要从终端生成带水印的视频预览时：

```bash
cd /Users/ylanlll/Desktop/商务部/distribution-manager
python3 scripts/run_sample_harness.py --hotspot-id 8 --brand-evidence-id 1 --render-video --output data/samples
```

`--render-video` 使用 macOS 本地语音，不调用外部模型；生成的 540×960 MP4 位于 `static/uploads/video/`。如果终端没有 macOS 语音权限，系统会明确失败，不会静默生成无声视频。

当前验收样本：

- 样本目录：`data/samples/24edc50dd82d49ab9d69b4f357344bcd/`
- 视频：`static/uploads/video/sample-24edc50dd82d49ab9d69b4f357344bcd.mp4`
- 状态：内部预览、不可发布；事实源为 SARS，品牌段只引用本地素材可证明的作业画面，不包含时效或业绩数字。

## 模型更换与成本控制

管理员进入“设置 → 可替换模型角色”，可分别更换脚本规划、视觉标注、质量审查和语音合成模型。只填写 HTTPS 基础地址、模型名和密钥环境变量名，不在页面填写真实 Key。

- 本地切片、OCR、匹配、技术质检和内部 TTS 默认零远程调用。
- 同一输入、模型和提示词版本命中缓存时不重复计费。
- 每个样本默认上限：4 次调用、20,000 输入 Token、6,000 输出 Token。
- 预算耗尽直接停止远程调用，保留确定性样本和问题清单，不无限重试。

## 本地版本与备份

- 测试端口：`8080`（未推送服务器）
- 改动前源码与数据库备份：`/Users/ylanlll/Desktop/商务部/distribution-manager-backups/pre-quality-workflow-2026-07-21/source-and-database.tar.gz`
- Git 历史备份：`/Users/ylanlll/Desktop/商务部/distribution-manager-backups/pre-quality-workflow-2026-07-21/git-history.bundle`
