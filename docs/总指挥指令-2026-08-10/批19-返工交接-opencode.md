# 批19 返工交接 opencode（qcoder 额度耗尽 · 工作区实测清单）

日期：2026-08-10
执行方：opencode（新执行器）
验收方：总指挥侧独立复核（逐文件核对 `git diff` 实证，**不认叙述**）

---

## 〇、背景与结论

qcoder 执行 v4 指令中途额度耗尽。我（总指挥侧）对**工作区实际内容**逐文件独立核对后结论：

**已完成且验证正确（不要再动）：**
- P2 字幕静音对齐：`video_renderer.py` 三个函数（`_build_subtitle_cues_internal`:897 / `_detect_silence_points`:943 / `_align_cues_to_silence`:979）各**恰好 1 处**，中文标点正则已恢复（:907），`sec_to_real` 语音时间轴映射在（:1014），渲染调用点（:1498）已切到 `_build_subtitle_cues_internal`。
- P1 素材冷却：`app.py` `_tiebreak`:2133 + 排序 key:2139 已改对；惩罚块 :2094-2103 用 `min(usage,5)+_usage_freshness_penalty`、无 `*2`。
- P4 过期清理：`routes/admin.py`（`import asyncio`:2、prefix `/api/admin`:16、路径 `/media-cleanup`+`/hotspot-hook-library/cleanup`）、scheduler misfire_grace_time + 启动补跑、database.py 两处 `created_at` 均已正确。
- P3 模型：`models.py` `VideoProjectEnqueueRequest`（:398）形状已正确（无 project_id 字段）。

**未完成（本交接唯一要做的事，就 3 处）：**
1. **【新增·P0 崩溃】`video_renderer.py` 渲染路径 `NameError: audio_path`** —— 我复核时发现，qcoder 漏了 v3 要求的 `audio_path = wav if os.path.exists(wav) else None` 赋值行。**不修则每次渲染都崩溃**。
2. **P3 端点** `routes/video_generation_routes.py`：仍是旧 `/api/video-projects/enqueue`（无 `{project_id}`、ISO T 格式、job 联动错误、无属主/账号校验），且 models import 块缺 `Platform`。
3. **P3 前端** `static/video-project.html`：`enqueueToPublishQueue()`（:740）仍是 `{method:'POST'}` 无 body。

---

## 一、改动 A【P0，必须最先做】修复 `audio_path` NameError

**位置**：`video_renderer.py`，渲染调用点前。当前 :1497-1502 实际内容：

```python
            scene_durations.append(duration)
            cues = _build_subtitle_cues_internal(
                scene["voiceover"],
                min(speech_duration, duration),
                audio_path=audio_path,        # ← audio_path 未定义 → NameError
                ffmpeg=ffmpeg or "ffmpeg",
            )
```

**动作**：在 `cues = _build_subtitle_cues_internal(` 那一行**之前**插入一行（缩进 12 空格，与调用点同级）：

```python
            audio_path = wav if os.path.exists(wav) else None
```

即变成：

```python
            scene_durations.append(duration)
            audio_path = wav if os.path.exists(wav) else None
            cues = _build_subtitle_cues_internal(
                scene["voiceover"],
                min(speech_duration, duration),
                audio_path=audio_path,
                ffmpeg=ffmpeg or "ffmpeg",
            )
```

**背景（已核实，放心用）**：`wav` 在作用域内（:1330 定义，可能被 :1451/:1477 重新赋值）；`os` 已导入（:7）；`ffmpeg` 在 render_job 内 :1251 已解析。只缺这一行。

**实证**：
```bash
grep -n "audio_path = wav" video_renderer.py   # 期望：有 1 处，且在 :1497 之前
```

---

## 二、改动 B：P3 端点（routes/video_generation_routes.py）

**B-1**：models import 块（当前 :18-27）加 `Platform,`：

```python
from models import (
    Platform,          # ← 新增这一行
    UserRole,
    VideoGenerationManualReviewRequest,
    VideoGenerationRequest,
    VideoGenerationResumeRequest,
    VideoProjectCreateRequest,
    VideoProjectRevisionRequest,
    VideoQualityRequest,
    VideoProjectEnqueueRequest,
)
```

**B-2**：**整段替换** enqueue 端点（当前 :433 起，直到该函数结束；注释 `# P3: 发布队列入队入口` 之后到 `return` 之前）。用 v4 §3.4.2 的终稿代码：

```python
    # P3: 发布队列入队入口
    @router.post("/api/video-projects/{project_id}/enqueue")
    async def enqueue_video_project(
        project_id: str,
        request: VideoProjectEnqueueRequest,
        user=Depends(get_current_user),
    ) -> dict:
        """将已完成渲染的视频项目加入发布队列（立即或定时）。"""
        project = db.get_video_project(project_id, created_by=user["id"])
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        job = db.get_video_generation_job(project.get("active_job_id"))
        if not job or job.get("status") != "succeeded" or not job.get("output_path"):
            raise HTTPException(status_code=400, detail="成片尚未就绪，无法入队")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scheduled_at = request.scheduled_at or now_str
        platforms = request.platforms or [project.get("platform", "douyin")]
        valid_platforms = {p.value for p in Platform}
        for platform in platforms:
            if platform not in valid_platforms:
                raise HTTPException(status_code=400, detail=f"不支持的平台：{platform}")

        created_ids = []
        for platform in platforms:
            target_ids = request.account_targets.get(platform) or [None]
            for target_id in target_ids:
                if target_id is not None:
                    account = db.get_account(target_id)
                    if not account or account.get("owner_id") != user["id"]:
                        raise HTTPException(status_code=403, detail="不能操作其他用户的账号")
                    if account.get("platform") != platform:
                        raise HTTPException(status_code=400, detail="目标账号与发布平台不匹配")
                queue_id = db.add_to_queue(
                    title=request.title or project.get("title") or "",
                    body="",
                    platform=platform,
                    scheduled_at=scheduled_at,
                    status="queued",
                    created_by=user["id"],
                    attachments=[{"type": "video", "path": job["output_path"]}],
                    target_account_id=target_id,
                )
                created_ids.append(queue_id)

        db.add_audit_log(
            user["id"], user["username"], "enqueue_video_project",
            target=project_id, detail=json.dumps({"queue_ids": created_ids}),
        )
        return {"status": "queued", "queue_ids": created_ids, "message": f"已入队 {len(created_ids)} 条"}
```

**已核实**：`json`(:4)、`datetime`(:5) 均已导入；`HTTPException`/`Depends`/`get_current_user`/`db` 文件内已在用。

**实证**：
```bash
grep -n "@router.post(\"/api/video-projects/{project_id}/enqueue\")" routes/video_generation_routes.py   # 期望：有
grep -n "db.execute\|publish_queue\|VideoGenerationJobStatus" routes/video_generation_routes.py          # 期望：无输出
grep -n "strftime(\"%Y-%m-%d %H:%M:%S\")" routes/video_generation_routes.py                              # 期望：有
grep -n "get_video_project(project_id, created_by=user" routes/video_generation_routes.py                # 期望：有
grep -n "datetime.now(timezone" routes/video_generation_routes.py                                        # 期望：无输出（旧 ISO T 写法已被替换）
```

---

## 三、改动 C：P3 前端（static/video-project.html）

**动作**：`enqueueToPublishQueue()`（:740 起）**函数体整段替换**为带 body 的版本（v4 §3.4.3 终稿）：

```js
// P3: 加入发布队列
async function enqueueToPublishQueue(){
  if(!project?.id){showToast('项目 ID 无效','error');return;}
  try{
    const payload={
      platforms:[project?.platform || 'douyin'],
      account_targets:{},
      scheduled_at:null,
    };
    const response=await apiFetch(`/api/video-projects/${project.id}/enqueue`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload),
    });
    const result=await response.json();
    if(response.ok){
      showToast('✅ 已成功加入发布队列，系统将在 1 分钟内自动发布');
      setTimeout(()=>location.reload(),1500);
    }else{
      showToast('❌ '+(result.detail||'入队失败'),'error');
    }
  }catch(error){
    showToast('❌ 加入失败：'+error.message,'error');
  }
}
```

**实证**：
```bash
grep -n "body:JSON.stringify(payload)" static/video-project.html   # 期望：有
grep -n "method:'POST'}" static/video-project.html                 # 期望：无输出（旧无 body 写法已消失）
```

---

## 四、纪律（沿用 v4，必须遵守）

1. **只做上面 3 处改动，不准"顺手改进"、不准发明新函数名、不准重新实现 P2/P1/P4/models**（已验证正确，别碰）。
2. **每完成一处，贴 `git diff -- <文件>` 的实际输出**。只回"我改了"不算数。
3. 若发现某处代码与文档描述不符，**停下来**贴出实际内容问我，不要自己猜。
4. 全部完成后贴最终实证：

```bash
cd /Users/ylanlll/Desktop/商务部/distribution-manager
git diff --stat
git diff -- video_renderer.py routes/video_generation_routes.py static/video-project.html
```

以及改动 A/B/C 各实证命令的完整输出。

## 五、回归

宿主有 pytest 则跑：`test_video_generation_ui.py`、`test_chat_intent.py`、`test_hotspot_hook_library_gates.py`、`test_media_retention.py`、`test_matching_diagnostics.py`、`test_hotspot_event_clips.py`；否则说明原因。特别确认视频渲染路径不再报 `NameError`（改动 A 的直接目的）。

提交注明"批19 返工 v5（交接 opencode）"。**只认 diff 实证，不认叙述。**
