from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_chat_creates_durable_task_then_polls_before_generation():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "/api/ai/chat/dual-library-video" in page
    assert "taskResult.job.id" in page
    assert "pollRenderStatus(taskResult.job.id, taskResult.project.id" in page
    assert "idempotency_key: `chat-video-" in page
    assert "生成 60 秒正式成片" in page
    assert "tts_provider: selection.tts_provider" in page
    assert "voice: selection.voice" in page
    assert "/api/douyin/render" not in page
    assert "pollChatVideoTask" not in page
    assert "/api/ai/chat/dual-library-video/tasks/${taskId}" not in page


def test_chat_routes_confirmed_hooks_to_dual_library_and_disables_queued_video():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "/api/ai/chat/dual-library-video" in page
    assert "/api/ai/chat/owned-library-video" not in page
    assert "owned_only" not in page
    assert "const deliveryReadiness=hotspotRetrieval?.video?.delivery_readiness;" in page
    assert "deliveryReadiness?.delivery_ready??true" in page
    assert "const videoBlocked=!scriptState.canProduce || !videoReady;" in page
    assert "等待热点 Hook" in page
    assert "补充 Buffalo 素材" in page
    assert "文案草稿已生成 · 视频素材补采中" in page
    assert "正在补采相关热点素材；至少一段相关、已确认 Hook 入库后才能生成视频" in page
    assert "resultStateCard" in page
    assert "source_type: 'chat'" not in page


def test_chat_explains_that_the_short_script_preview_becomes_a_formal_dual_library_video():
    page = (ROOT / "static" / "chat.html").read_text()
    common = (ROOT / "static" / "common.js").read_text()

    assert "聊天预览脚本（正式成片将按 60 秒双素材重新规划）" in page or "聊天预览脚本（正式成片将按 60 秒双素材重新规划）" in common
    assert "历史预览，不可直接生产" in common
    assert "classifyDouyinScriptState" in page
    assert "target_duration_ms: 60000" in page


def test_new_chat_defaults_to_douyin_for_direct_video_requests():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "const selectedPlatforms=new Set(['douyin']);" in page
    assert "selectedPlatforms.has(k)?'active':''" in page


def test_chat_keeps_review_feedback_actionable_after_background_generation():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "job.status === 'needs_review'" in page
    assert "按当前规则重新生成" in page
    assert "issues.slice(0, 2)" in page


def test_review_feedback_is_actionable_and_does_not_dump_every_issue():
    chat = (ROOT / "static" / "chat.html").read_text()
    project = (ROOT / "static" / "video-project.html").read_text()

    assert "issues.slice(0, 2)" in chat
    assert "按当前规则重新生成" in chat
    assert "automatic_adjustments" in project
    assert "系统已自动调整" in project
    assert "issues.slice(0,3)" in project


def test_global_task_center_restores_active_jobs_and_can_cancel():
    common = (ROOT / "static" / "common.js").read_text()

    assert "/api/video-generation/jobs/active" in common
    assert "initVideoTaskCenter" in common
    assert "cancelVideoGeneration" in common
    assert "/cancel`" in common


def test_video_task_center_avoids_header_actions_and_opens_upward():
    styles = (ROOT / "static" / "design-system.css").read_text()
    common = (ROOT / "static" / "common.js").read_text()

    assert "video-task-center" in styles
    assert "ensureVideoTaskCenter" in common


def test_video_project_page_shows_progress_and_manual_review_controls():
    page = (ROOT / "static" / "video-project.html").read_text()

    assert "URLSearchParams" in page
    assert "/api/video-projects/" in page
    assert "质量检查" in page
    assert "待确认问题" in page
    assert "取消生成" in page
    assert "按当前规则重新生成" in page
    assert "preview_url" in page
    assert "output_url" in page
    assert "render_progress" in page
    assert "/manual-review" in page
    assert "/manual-finalize" in page
    assert "确认人工验收（不发布）" in page
    assert "生成高清成片（不发布）" in page
    assert "MANUAL_PREVIEW_CHECKS" in page
    assert "isManualReviewEligible" in page
    assert "manual_accepted" in (ROOT / "static" / "common.js").read_text()
    assert "预览可编辑、可下载" in (ROOT / "static" / "editor.html").read_text()
    assert "发送到编辑器" in (ROOT / "static" / "chat.html").read_text()
    assert "video-workbench.html?project_id=" in page
    assert "editor.html?project_id=" in page
    assert "TTS 音色（Qwen / MiMo）" in page
    assert "formatRenderProvenance" in page
    assert "Qwen TTS 音色" not in page


def test_editor_routes_chat_imports_to_formal_dual_library_and_shows_real_duration():
    page = (ROOT / "static" / "editor.html").read_text()
    common = (ROOT / "static" / "common.js").read_text()

    assert "draftVideoWorkflow" in page
    assert "/api/ai/chat/dual-library-video" in page
    assert "pollFormalEditorRender" in page
    assert "classifyDouyinScriptState" in page
    assert "聊天预览脚本" in common
    assert "正式分镜" in common
    assert "target_duration_ms:60000" in page
    assert "tts_provider:selection.tts_provider" in page
    assert "当前只是模型不可用时的提示文本，不能生成视频" in page
    assert "formatRenderProvenance" in page
    assert "Qwen TTS 音色" not in page


def test_match_review_is_rendered_as_paused_with_actionable_issues():
    common = (ROOT / "static" / "common.js").read_text()
    project = (ROOT / "static" / "video-project.html").read_text()

    assert "匹配质量不足，等待人工处理" in common
    assert "匹配质量不足，等待人工处理" in project
    assert "quality_report?.gate?.issues" in project
    assert "quality_report?.gate?.score" in project


def test_video_polling_reports_local_service_disconnect_instead_of_spinning_forever():
    common = (ROOT / "static" / "common.js").read_text()
    project = (ROOT / "static" / "video-project.html").read_text()

    assert "本地服务已断开" in common
    assert "videoTaskPollFailures >= 3" in common
    assert "本地服务已断开" in project
    assert "projectPollFailures>=3" in project


def test_common_helpers_expose_voice_options_and_provenance():
    common = (ROOT / "static" / "common.js").read_text()

    assert "voiceSelectMarkup" in common
    assert "parseVoiceSelection" in common
    assert "formatRenderProvenance" in common
    assert "MiMo 默认" in common
    assert "Qwen Cherry" in common
    assert "fallback_used" in common
    assert "FORMAL_MIN_SCENES = 7" in common
    assert "FORMAL_MAX_SCENES = 10" in common
