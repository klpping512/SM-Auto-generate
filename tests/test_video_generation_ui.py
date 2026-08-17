from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_chat_creates_project_and_redirects_to_workbench():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "/api/ai/chat/dual-library-video" in page
    assert "createSixtySecondVideoProject" in page
    assert "创建60秒视频项目" in page
    assert "video-project.html?id=" in page
    assert "stage=brief" in page
    assert "idempotency_key: `chat-video-" in page
    assert "tts_provider: selection.tts_provider" in page
    assert "voice: selection.voice" in page
    assert "/api/douyin/render" not in page
    assert "pollRenderStatus" not in page
    assert "pollChatVideoTask" not in page
    assert "douyinScenesMarkup" not in page


def test_chat_routes_confirmed_hooks_to_dual_library_and_disables_queued_video():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "/api/ai/chat/dual-library-video" in page
    assert "/api/ai/chat/owned-library-video" not in page
    assert "owned_only" not in page
    assert "deliveryReadiness?.delivery_ready??true" in page
    assert "等待热点 Hook" in page
    assert "任务 ID" in page
    assert "bindHookToOriginalTopic" in page
    assert "选择开场 Hook" in page
    assert "useProducibleTopic" not in page
    assert "已填入可生产选题" not in page
    assert "创建60秒视频项目" in page
    assert "前往修复" not in page or "brand_assets_insufficient" in page
    assert "resultStateCard" in page
    assert "当前情况" not in page or "result-state-hint" in page
    assert "source_type: 'chat'" not in page


def test_chat_explains_that_the_short_script_preview_becomes_a_formal_dual_library_video():
    page = (ROOT / "static" / "chat.html").read_text()
    common = (ROOT / "static" / "common.js").read_text()

    assert "正式成片将在视频工作台按 60 秒双素材规划" in page
    assert "历史预览，不可直接生产" in common
    assert "classifyDouyinScriptState" in page
    assert "isLegacyVideoDraft" in common
    assert "target_duration_ms: 60000" in page


def test_new_chat_defaults_to_douyin_for_direct_video_requests():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "const selectedPlatforms=new Set(['douyin']);" in page
    assert "selectedPlatforms.has(k)?'active':''" in page


def test_chat_no_longer_embeds_generation_progress_or_review_loop():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "按当前规则重新生成" not in page
    assert "pollRenderStatus" not in page
    assert "选择音色" not in page


def test_review_feedback_lives_on_workbench_acceptance_stage():
    project = (ROOT / "static" / "video-project.html").read_text()

    assert "automatic_adjustments" in project
    assert "系统已自动调整" in project
    assert "按当前规则重新生成" in project
    assert "生成验收" in project
    assert "stage=review" in project or "currentStage==='review'" in project or "'review'" in project


def test_sidebar_video_project_badge_replaces_floating_task_center():
    common = (ROOT / "static" / "common.js").read_text()
    styles = (ROOT / "static" / "design-system.css").read_text()

    assert "/api/video-generation/jobs/active" in common
    assert "initVideoTaskBadge" in common
    assert "videoProjectNavBadge" in common
    assert "cancelVideoGeneration" in common
    assert "视频项目" in common
    assert "ensureVideoTaskCenter" not in common
    assert "video-task-center" not in styles
    assert "nav-badge" in styles


def test_video_project_workbench_has_five_stages_and_manual_review():
    page = (ROOT / "static" / "video-project.html").read_text()

    assert "URLSearchParams" in page
    assert "/api/video-projects/" in page
    for stage in ("需求确认", "脚本分镜", "素材匹配", "配音字幕", "生成验收"):
        assert stage in page
    assert "stage=match" in page or "'match'" in page
    assert "待确认问题" in page
    assert "取消生成" in page
    assert "按当前规则重新生成" in page
    assert "preview_url" in page
    assert "output_url" in page
    assert "/manual-review" in page
    assert "/manual-finalize" in page
    assert "确认人工验收（不发布）" in page
    assert "生成高清成片（不发布）" in page
    assert "MANUAL_PREVIEW_CHECKS" in page
    assert "isManualReviewEligible" in page
    assert "MiMo → Qwen" not in page
    assert "fallbackConfirm" not in page
    assert "voicePickerMarkup" in page
    assert "配音服务与音色" in page
    assert "先保存工作台当前选择" in page
    assert "重试也必须使用当前 revision" in page
    assert "formatRenderProvenance" in page
    assert "热点 Hook" in page
    assert "Buffalo 自有" in page
    assert "manual_accepted" in (ROOT / "static" / "common.js").read_text()


def test_editor_no_longer_hosts_video_production_panel():
    page = (ROOT / "static" / "editor.html").read_text()
    common = (ROOT / "static" / "common.js").read_text()

    assert "video-project.html?id=" in page
    assert "location.replace(`/video-project.html?id=" in page
    assert "render-panel" not in page or "视频生产已迁至" in page
    assert "视频工作台" in page
    assert "聊天预览脚本" in common
    assert "正式分镜" in common
    assert "isLegacyVideoDraft" in page or "isLegacyVideoDraft" in common


def test_workbench_legacy_urls_redirect_to_match_stage():
    workbench = (ROOT / "static" / "video-workbench.html").read_text()
    migrate = (ROOT / "static" / "video-migrate.html").read_text()

    assert "stage=match" in workbench
    assert "location.replace" in workbench
    assert "历史草稿" in migrate
    assert "按60秒规则重建视频项目" in migrate
    assert "仅查看旧内容" in migrate


def test_match_review_is_rendered_as_paused_with_actionable_issues():
    project = (ROOT / "static" / "video-project.html").read_text()

    assert "匹配质量不足，等待人工处理" in project
    assert "gate?.issues" in project or "quality_report?.gate?.issues" in project
    assert "gate?.score" in project or "gate.score" in project


def test_video_polling_reports_local_service_disconnect_instead_of_spinning_forever():
    common = (ROOT / "static" / "common.js").read_text()
    project = (ROOT / "static" / "video-project.html").read_text()

    assert "本地服务已断开" in common or "videoTaskPollFailures" in common
    assert "videoTaskPollFailures >= 3" in common
    assert "本地服务已断开" in project
    assert "projectPollFailures>=3" in project


def test_common_helpers_expose_voice_options_and_provenance():
    common = (ROOT / "static" / "common.js").read_text()

    assert "voiceSelectMarkup" in common
    assert "voicePickerMarkup" in common
    assert "optgroup" in common
    assert "parseVoiceSelection" in common
    assert "formatRenderProvenance" in common
    assert "previewSelectedVoice" in common
    assert "/api/media/tts-preview" in common
    assert "MiMo 默认" in common
    assert "Qwen Cherry" not in common
    assert "fallback_used" not in common
    assert "FORMAL_MIN_SCENES = 7" in common
    assert "FORMAL_MAX_SCENES = 10" in common
