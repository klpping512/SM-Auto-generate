from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_chat_creates_durable_task_then_polls_before_generation():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "/api/ai/chat/dual-library-video" in page
    assert "taskResult.job.id" in page
    assert "pollRenderStatus(taskResult.job.id, taskResult.project.id" in page
    assert "idempotency_key: `chat-video-" in page
    assert "生成视频" in page
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
    assert "const videoBlocked=!videoReady;" in page
    assert "正在补采热点素材" in page
    assert "补充 Buffalo 素材" in page
    assert "正在补采相关热点素材；至少一段相关、已确认 Hook 入库后才能生成视频" in page
    assert "source_type: 'chat'" not in page


def test_chat_explains_that_the_short_script_preview_becomes_a_formal_dual_library_video():
    page = (ROOT / "static" / "chat.html").read_text()

    assert "脚本预览 · 正式双素材成片约" in page
    assert "target_duration_ms||60000" in page


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

    assert ".video-task-center{position:fixed;right:22px;bottom:24px;top:auto;" in styles
    assert ".video-task-panel{position:absolute;right:0;bottom:46px;top:auto;" in styles
    assert "@media(max-width:760px){.video-task-center{right:12px;bottom:84px;top:auto;" in styles


def test_precision_matcher_is_hidden_from_primary_nav_but_kept_in_project():
    common = (ROOT / "static" / "common.js").read_text()
    project_page = (ROOT / "static" / "video-project.html").read_text()
    nav_items = common.split("const NAV_ITEMS = [", 1)[1].split("];", 1)[0]

    assert "视频精准匹配" not in nav_items
    assert "'/video-workbench.html'" not in nav_items
    assert "video-workbench.html?project_id=" in project_page
    assert "视频精确调整" in project_page


def test_video_project_page_has_quality_review_and_output_controls():
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
