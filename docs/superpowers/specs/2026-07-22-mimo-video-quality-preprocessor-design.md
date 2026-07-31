# MiMo 视频自动质检预处理层设计

## 目标

在不替换现有视频项目、任务队列、取消与 FFmpeg 渲染流程的前提下，引入一个可独立调用的视频质检包。它接收本地视频或视频 URL，输出元数据、技术检测、带时间戳关键帧、字幕、MiMo 严格 JSON 质检报告和下一轮提示词，并在现有 `preview_quality_check` 阶段对预览 MP4 执行低成本两阶段审核。

自动重新生成默认关闭。质检失败时保留报告和优化提示词，进入人工确认；后续如启用自动重新生成，也只能复用现有视频项目修订和任务状态机，最多两轮。

## 上游复用边界

上游来源为 `bradautomates/claude-video`，固定参考提交 `83da59fa78c3eee9e20f515fe75c438bb5166efd`，许可证为 MIT，版权为 `Copyright (c) 2026 Bradley Bonanno`。

直接抽取并改造以下模型无关逻辑：

- ffprobe 元数据读取；
- 关键帧、场景变化、均匀时间点抽帧；
- 自动抽帧预算和首尾覆盖；
- 512 像素等比例缩放；
- 16×16 灰度缩略图的近似重复帧去重；
- VTT 解析、YouTube 滚动字幕去重、时间范围裁剪；
- yt-dlp 视频、元数据和原生字幕下载策略。

需要改造：

- 将 `SystemExit` 改为可由服务层处理的异常；
- FFmpeg、ffprobe、yt-dlp 增加超时、取消检查和错误摘要；
- 将上游最高 2 fps 的聚焦抽帧扩展为 5～10 fps，但每个风险窗口仍执行去重和数量上限；
- Whisper 优先使用项目已有的可选 `faster-whisper` 本地模型；
- 生成视频优先使用已有脚本和字幕时间轴，不重复 ASR。

不引入 `SKILL.md`、`/watch` 命令、Claude Read/AskUserQuestion 说明、插件安装器、Claude 目录结构、Groq/OpenAI Whisper 密钥读取和任何 Claude 会话外壳。

项目内新增 `third_party/claude_video/LICENSE` 和 `NOTICE.md`，所有包含上游算法的文件保留来源注释。

## 架构

```text
本地 MP4 / HTTPS 视频 URL
        │
        ▼
video_preprocessor
  ├─ source_resolver：本地路径或 yt-dlp 下载、原生字幕
  ├─ technical_validator：ffprobe + 解码 + 黑帧/冻结/静音
  ├─ transcript_service：项目脚本 > VTT > 本地 faster-whisper
  └─ frame_extractor：efficient / balanced / detailed + 去重
        │
        ▼
MiMo 第一阶段：24～40 张全片关键帧 + 技术结果 + 字幕 + 提示词/分镜
        │
        ├─ 通过：输出报告
        │
        └─ high 风险时间段：5～10 fps 局部抽帧（总计最多 40 张）
                    │
                    ▼
              MiMo 第二阶段复核
        │
        ▼
prompt_optimizer（复用报告中的 regeneration 字段，不额外调用模型）
        │
        ▼
regeneration_controller（默认人工确认，最多两轮、提升不足 3 分即停止）
```

## 文件边界

- `video_quality/schemas.py`：输入和严格输出模型。
- `video_quality/process_runner.py`：可超时、可取消的媒体子进程。
- `video_quality/source_resolver.py`：本地视频与 HTTPS URL 统一解析。
- `video_quality/frame_extractor.py`：三档抽帧、局部高密度抽帧与去重。
- `video_quality/transcript_service.py`：VTT、项目脚本和可选本地 Whisper。
- `video_quality/technical_validator.py`：媒体信息、解码、黑帧、冻结和静音。
- `video_quality/video_evaluator.py`：MiMo 消息、JSON 校验、证据帧检查和局部复检。
- `video_quality/prompt_optimizer.py`：将报告转换为下一轮生成输入。
- `video_quality/regeneration_controller.py`：有限轮次和停止条件。
- `video_quality/video_preprocessor.py`：编排预处理并写入索引。
- `video_quality/service.py`：完整 MVP 编排和运行产物落盘。
- `scripts/run_video_quality_mvp.py`：本地命令行入口。

现有文件只做小范围接入：

- `model_router.py`：新增可替换的 `video_evaluator` 角色和多模态 JSON 调用。
- `video_generation.py`：在已有预览技术门禁通过后调用质检服务。
- `models.py`、`app.py`：增加管理员可用的独立质检 API；API 本地路径只允许 `static/` 内文件，避免读取任意系统文件。
- `static/config.html`：显示视频质检模型路由，不新增独立模型配置系统。

## 质量与成本门禁

- 全片默认 `balanced`，最多 40 张；50～100 是可配置上限，不是每次目标。
- 高风险窗口来自第一阶段 `severity=high` 的问题；窗口前后各扩 0.5 秒并合并重叠区间。
- 每个高风险窗口最多 20 张，全部窗口最多 40 张。
- 单次质检最多两次视觉调用；模型返回无效 JSON 时只允许一次纠正重试，且不能突破任务模型调用预算。
- 总分低于 80 或存在 high 问题即不通过。
- 自动重新生成默认关闭；开启后最多两轮，评分下降、提升不足 3 分或连续两轮没有明显提升即转人工。
- 预处理失败或 MiMo 不可用不伪装通过，现有视频任务进入 `needs_review`，但保留已经生成的预览 MP4。

## 异常与清理

- 缺失 FFmpeg/ffprobe：立即返回明确依赖错误。
- 缺失 yt-dlp：本地视频仍可质检，URL 输入返回明确错误。
- 下载失败或超时：清理未完成下载文件，保留错误摘要。
- 无字幕：尝试本地 Whisper；不可用时以空字幕继续视觉/技术审核，并在报告中标明。
- 视频损坏或时长为 0：停止模型调用，输出技术失败。
- 视频超过默认 10 分钟或 300 MB：拒绝自动处理，避免意外成本。
- 抽帧超过预算：去重后均匀保留首尾帧。
- MiMo 非 JSON：提取代码围栏或首尾 JSON；仍无效时再请求一次，失败则人工审核。
- 运行目录保留可审计产物；只清理未完成的临时文件，不删除原视频。

## YouTube 热点频道边界

YouTube 频道作为“热点发现源”时，默认只保存标题、双语摘要、发布时间、频道、缩略图、视频 URL 和字幕。它不占用现有五个官方事实信源名额，也不能单独作为事实定论。下载视频并素材化必须经过现有授权确认流程；未确认时只进入灵感链接库。

第一版视频质检不同时实现频道定时抓取，避免把两个独立子系统混在一次 MVP 中。后续可在 `hotspot_fetcher.py` 外新增 `youtube_channel_fetcher.py`，使用 yt-dlp flat-playlist 或 YouTube Data API，仅将元数据写入热点候选。

## 验收标准

1. 本地 MP4 能生成元数据、技术报告、关键帧和时间戳索引、字幕文件。
2. HTTPS URL 在安装 yt-dlp 后可下载视频及原生字幕；缺失依赖时错误可理解。
3. MiMo 请求包含原始提示词、分镜、技术结果、字幕和每张关键帧的时间戳标签。
4. MiMo 输出通过严格模型校验；问题证据帧必须存在于提交索引中。
5. high 风险触发局部高密度复检，普通通过视频只有一次模型调用。
6. 总分低于 80 或存在 high 问题时输出优化提示词并要求人工确认。
7. 现有视频任务、取消、恢复、渲染及热点功能测试不回归。
8. 使用项目已有本地测试视频完成一次真实端到端运行并保存实际产物。
