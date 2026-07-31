# Graph Report - /private/tmp/salogiflow-graphify-final  (2026-07-23)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1667 nodes · 3563 edges · 92 communities (88 shown, 4 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 111 edges (avg confidence: 0.65)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `49bd07f7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- database.py
- scheduler.py
- frame_extractor.py
- video_renderer.py
- fetch_hotspots
- ValueError
- test_media_retention.py
- app.py
- hotspot_media.py
- video_generation.py
- ai_engine.py
- xhs_cards.py
- semantic_matching.py
- models.py
- asset_processing.py
- PublishResult
- RpaAdapter
- build_event_clips
- TwitterAdapter
- add_audit_log
- model_usage
- model_usage
- model_usage
- get_accounts
- model_usage
- model_usage
- model_usage
- model_usage
- VideoQualityInput
- DouyinAdapter
- resume_video_generation_job
- lifespan
- model_router.py
- transcript_service.py
- process_asset_job
- XiaohongshuAdapter
- common.js
- test_media_api.py
- video_evaluator.py
- VideoEvaluationReport
- video_preprocessor.py
- get_hotspot
- auth.py
- run_video_quality_mvp.py
- ApiAdapter
- create_asset_processing_job
- test_upload_api.py
- test_video_generation_db.py
- RedditAdapter
- _Scripts
- test_registry_dispatch.py
- get_asset
- list_assets
- test_video_generation_rendering.py
- create_semantic_match
- static/manifest.json
- test_editor_transfer.py
- test_sample_harness.py
- test_semantic_assets_db.py
- test_user_tenancy.py
- test_video_generation_api.py
- schemas.py
- materialize_inspiration
- test_hotspot_fetch_runs.py
- test_chat_api.py
- test_hotspot_media_db.py
- _account_for_user
- create_video_project
- fetch_hotspots_now
- signup
- draw_truck
- editor-transfer.js
- kb_upload
- get_kb_documents
- update_model_route
- download_asr_model.py
- run_douyin_tests.sh
- start.sh

## God Nodes (most connected - your core abstractions)
1. `get_conn()` - 152 edges
2. `add_audit_log()` - 56 edges
3. `PublishResult` - 38 edges
4. `VideoQualityInput` - 29 edges
5. `process_asset_job()` - 24 edges
6. `fetch_hotspots()` - 24 edges
7. `VideoEvaluationReport` - 22 edges
8. `render_job()` - 22 edges
9. `DouyinAdapter` - 21 edges
10. `run_process()` - 20 edges

## Surprising Connections (you probably didn't know these)
- `test_requires_media()` --indirect_call--> `PublishResult`  [INFERRED]
  tests/test_douyin_adapter.py → adapters/base.py
- `_AccountDummy` --uses--> `PublishResult`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py
- `_Dummy` --uses--> `PublishResult`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py
- `test_requires_images()` --indirect_call--> `PublishResult`  [INFERRED]
  tests/test_xiaohongshu_adapter.py → adapters/base.py
- `_AccountDummy` --uses--> `PublishAdapter`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py

## Import Cycles
- None detected.

## Communities (92 total, 4 thin omitted)

### Community 0 - "database.py"
Cohesion: 0.05
Nodes (75): kb_update_document(), add_to_queue(), archive_stale_hotspot_media(), asset_active_reference_reasons(), confirm_brand_evidence(), count_published_today(), create_asset(), create_brand_evidence() (+67 more)

### Community 1 - "scheduler.py"
Cohesion: 0.05
Nodes (63): publish_accounts(), publish_batch(), publish_item(), 发送测试通知。body.channel: all/email/feishu/wecom（默认 all）。, _repair_xhs_queue_media(), review_item(), test_notification(), update_status() (+55 more)

### Community 2 - "frame_extractor.py"
Cohesion: 0.07
Nodes (60): Popen, test_dedup_compares_against_last_kept_frame_and_deletes_dropped(), test_even_sampling_always_keeps_first_and_last(), test_focus_density_is_limited_to_ten_fps(), test_focus_mode_uses_requested_density_but_obeys_cap(), test_full_scan_budget_targets_forty_frames_for_one_minute_video(), test_mean_pixel_delta_detects_near_duplicates(), test_three_modes_have_explicit_engines() (+52 more)

### Community 3 - "video_renderer.py"
Cohesion: 0.07
Nodes (54): cancel_video_generation_job(), Reject direct hotspot mother references before they reach a generation job., _validate_video_payload_clip_refs(), _png_bytes(), test_douyin_script_normalization_rejects_bad_scene_count(), test_douyin_script_removes_unknown_asset_ids(), test_ingest_image_validates_deduplicates_and_generates_thumbnail(), test_ingest_rejects_fake_image() (+46 more)

### Community 4 - "fetch_hotspots"
Cohesion: 0.06
Nodes (50): create_evidence_package(), hotspot_draft(), build_package(), _confirmed_brand_claims(), _fact_claims(), 把外部热点事实与 Buffalo 内部能力证据隔离后组成可复用证据包。, compose(), Evidence-preserving hotspot-to-Buffalo draft composition. (+42 more)

### Community 5 - "ValueError"
Cohesion: 0.07
Nodes (42): _store_inspiration(), _translation_payload(), upload_media_asset(), _refresh_inspiration_fts(), upsert_inspiration_item(), build_ytdlp_options(), can_auto_materialize_official(), download_authorized_media() (+34 more)

### Community 6 - "test_media_retention.py"
Cohesion: 0.08
Nodes (44): hash_password(), _hotspot(), test_build_evidence_package_keeps_external_and_brand_claims_separate(), test_confirmed_public_brand_claim_can_enter_package(), test_evidence_package_and_brand_evidence_admin_api(), test_unconfirmed_brand_claim_cannot_enter_publishable_package(), _create_hotspot(), _login() (+36 more)

### Community 7 - "app.py"
Cohesion: 0.05
Nodes (27): cancel_local_asset_import(), create_douyin_render(), create_hotspot_source(), get_active_video_generation_jobs(), get_asset_processing(), get_douyin_render(), get_hotspot_detail(), get_hotspot_fetch_status() (+19 more)

### Community 8 - "hotspot_media.py"
Cohesion: 0.08
Nodes (37): Client, discover_media_candidates(), download_authorized_image(), download_authorized_video(), fetch_source_page(), filter_reachable_image_candidates(), _jsonld_objects(), normalize_video_url() (+29 more)

### Community 9 - "video_generation.py"
Cohesion: 0.10
Nodes (39): generate_video_project(), Event, StageHandler, StrEnum, test_cancel_checkpoint_stops_every_pipeline_stage(), test_hard_script_failure_routes_to_issue_only_review(), test_idempotency_key_is_deterministic_and_revision_scoped(), test_low_match_quality_routes_to_review() (+31 more)

### Community 10 - "ai_engine.py"
Cohesion: 0.10
Nodes (36): chat(), _chat_one_platform(), chat_platforms(), _fallback_content(), _format_asset_catalog(), generate_content(), _get_category_priority_hint(), _normalize_douyin_scenes() (+28 more)

### Community 11 - "xhs_cards.py"
Cohesion: 0.14
Nodes (33): add_queue(), ai_chat(), render_xhs_assets(), ImageDraw, Path, test_normalize_pages_caps_carousel_at_seven_pages(), test_normalize_pages_preserves_logo_editing_controls(), test_normalize_pages_sanitizes_and_limits_content() (+25 more)

### Community 12 - "semantic_matching.py"
Cohesion: 0.13
Nodes (31): create_sample_bundle(), _carousel(), generate_bundle(), Path, 从单一证据包生成视频、图文和公众号三种内部样本。, _video(), _wechat(), _wechat_body() (+23 more)

### Community 13 - "models.py"
Cohesion: 0.11
Nodes (31): confirm_brand_evidence(), create_account(), create_brand_evidence(), create_inspiration_batch(), generate_content(), AccountCreateRequest, BrandEvidenceConfirmRequest, BrandEvidenceCreateRequest (+23 more)

### Community 14 - "asset_processing.py"
Cohesion: 0.12
Nodes (28): get_asset_processing_capabilities(), build_processing_plan(), _category_scores(), classify_evidence(), _contains(), datetime_now(), detect_scene_boundaries(), _extract_tags() (+20 more)

### Community 15 - "PublishResult"
Cohesion: 0.14
Nodes (18): ABC, PublishAdapter, PublishResult, 发布适配器接口契约。所有平台适配器实现同一协议。, 默认恒为已登录（无状态/Token 型）。RPA 子类覆写。, FacebookAdapter, Facebook 主页发文：Graph API /{page_id}/feed。, HuimeiAdapter (+10 more)

### Community 16 - "RpaAdapter"
Cohesion: 0.14
Nodes (23): get_adapter(), browser_launch_options(), build_credentials(), parse_cookies(), playwright_proxy_from_env(), Playwright RPA 适配器基类：cookie 登录态存取 + 登录骨架。, 把常见代理环境变量转换为 Playwright 的 proxy 配置。, RpaAdapter (+15 more)

### Community 17 - "build_event_clips"
Cohesion: 0.12
Nodes (23): get_hotspot_event_matches(), list_asset_segments(), list_hotspot_events(), get_hotspot_event_clip(), list_hotspot_event_clips(), build_event_clips(), _event_name(), 热点视频事件片段：只用已有字幕/OCR/镜头证据做本地分组和命名。 (+15 more)

### Community 18 - "TwitterAdapter"
Cohesion: 0.10
Nodes (22): _CallbackHandler, _exchange_code(), _is_token_expired(), _pkce_challenge(), X(Twitter) 发推：API v2 POST /2/tweets，OAuth 2.0 支持两种模式。, 交互式 OAuth 2.0 授权：打开浏览器让用户授权，返回 token dict。, 检查 access_token 是否过期（基于 token_obtained_at + expires_in）。, 获取可用的 user access_token，过期则自动刷新。 (+14 more)

### Community 19 - "add_audit_log"
Cohesion: 0.09
Nodes (26): set_api_key(), create_inspiration(), create_prompt_template(), delete_prompt_template(), delete_queue_item(), import_media_assets(), kb_create_category(), kb_create_document() (+18 more)

### Community 20 - "model_usage"
Cohesion: 0.08
Nodes (24): bundle_id, claim_ids, evidence_package_id, fact_sources, https://www.sars.gov.za/latest-news/customs-weekly-list-of-unentered-goods-now-available-102/, model_usage, calls_used, created_at (+16 more)

### Community 21 - "model_usage"
Cohesion: 0.08
Nodes (24): bundle_id, claim_ids, evidence_package_id, fact_sources, 2 个镜头匹配低于质量门槛，必须人工换镜头后再成片, 热点素材未就绪：3 个事实分镜没有当前热点的已授权图片或视频片段, 部分口播没有可解释的本地镜头候选, model_usage (+16 more)

### Community 22 - "model_usage"
Cohesion: 0.08
Nodes (24): bundle_id, claim_ids, evidence_package_id, fact_sources, 2 个镜头匹配低于质量门槛，必须人工换镜头后再成片, 热点素材未就绪：3 个事实分镜没有当前热点的已授权图片或视频片段, 部分口播没有可解释的本地镜头候选, model_usage (+16 more)

### Community 23 - "get_accounts"
Cohesion: 0.10
Nodes (23): dashboard(), list_accounts(), list_queue(), create_account(), get_accounts(), get_queue(), get_queue_item_by_id(), get_queue_stats() (+15 more)

### Community 24 - "model_usage"
Cohesion: 0.08
Nodes (23): bundle_id, claim_ids, evidence_package_id, fact_sources, https://www.sars.gov.za/latest-news/customs-weekly-list-of-unentered-goods-now-available-102/, model_usage, calls_used, created_at (+15 more)

### Community 25 - "model_usage"
Cohesion: 0.08
Nodes (23): bundle_id, claim_ids, evidence_package_id, fact_sources, 品牌证据尚未确认，样本不得包含具体能力或业绩承诺, model_usage, calls_used, created_at (+15 more)

### Community 26 - "model_usage"
Cohesion: 0.08
Nodes (23): bundle_id, claim_ids, evidence_package_id, fact_sources, https://www.sars.gov.za/latest-news/customs-weekly-list-of-unentered-goods-now-available-102/, 品牌证据尚未确认，样本不得包含具体能力或业绩承诺, model_usage, calls_used (+15 more)

### Community 27 - "model_usage"
Cohesion: 0.08
Nodes (23): bundle_id, claim_ids, evidence_package_id, fact_sources, model_usage, calls_used, created_at, estimated_cost (+15 more)

### Community 28 - "VideoQualityInput"
Cohesion: 0.22
Nodes (18): evaluate_video_quality(), _preprocessed(), _report(), test_high_issue_runs_one_bounded_focus_review(), test_passing_scan_does_not_run_focus_review(), test_prompt_optimizer_reuses_report_without_extra_model_call(), test_regeneration_is_manual_by_default(), test_regeneration_stops_when_score_declines_or_improves_less_than_three() (+10 more)

### Community 29 - "DouyinAdapter"
Cohesion: 0.16
Nodes (11): DouyinAdapter, 抖音发布（RPA）：cookie 登录 + 创作平台上传视频 + 填文案 + 发布。, 打开抖音创作平台上传页，自动填好文案和话题标签，但**不点发布**，供人工复核后手动发布。, 关闭抖音页面上的各种弹窗（共创中心提示、位置权限、视频预览说明等）。, 填写抖音文案输入框。多策略兜底：Quill 编辑器 → contenteditable → textarea。, 点击抖音发布按钮。多策略兜底，避开页面标题「发布视频」。, 把当前页面截图存到 static/debug，返回可访问的相对路径。, test_identity() (+3 more)

### Community 30 - "resume_video_generation_job"
Cohesion: 0.13
Nodes (20): get_video_generation_job(), pin_video_generation_output(), resume_video_generation_job(), retry_video_generation_job(), unpin_video_generation_output(), add_video_generation_event(), claim_next_video_generation_job(), create_or_get_video_generation_job() (+12 more)

### Community 31 - "lifespan"
Cohesion: 0.11
Nodes (18): lifespan(), register(), create_user(), _ensure_column(), get_user_by_username(), init_db(), Mark runs left active by a stopped process so the UI can start again., 进程启动时标记上次未完成任务，允许管理员批量重试。 (+10 more)

### Community 32 - "model_router.py"
Cohesion: 0.27
Nodes (17): get_model_route(), translate_hotspot(), get_model_cache(), BudgetExceeded, call_multimodal_json(), call_text(), create_budget(), _estimate_multimodal_tokens() (+9 more)

### Community 33 - "transcript_service.py"
Cohesion: 0.22
Nodes (16): test_known_storyboard_skips_whisper(), test_missing_subtitles_and_model_produce_explicit_unavailable_status(), test_native_vtt_is_used_before_whisper(), test_transcript_range_keeps_overlapping_cues(), test_youtube_rolling_vtt_cues_are_collapsed(), build_transcript(), clip_segments(), _dedupe() (+8 more)

### Community 34 - "process_asset_job"
Cohesion: 0.17
Nodes (16): update_segment_classification(), process_asset_job(), 执行单个素材的镜头化、分类和标签入库；所有可选模型失败时安全降级。, create_asset_segment(), get_asset_segment(), list_asset_segments(), _normalized_tag(), _refresh_segment_fts() (+8 more)

### Community 35 - "XiaohongshuAdapter"
Cohesion: 0.18
Nodes (9): 小红书图文发布（RPA）：cookie 登录 + 创作平台上传图片 + 填标题正文 + 发布。, 点击发布按钮。小红书的「发布」是自定义 div（非 <button>），         故以精确文案定位为主，多策略兜底；用 exact=True 避开「发布, 把当前页面截图存到 static/debug，返回可访问的相对路径，便于排查选择器。, 打开发布页并自动填好（标题/正文/图片/话题），但**不点发布**，供人工复核后手动发布。         话题用逐字输入 + 回车选中，触发小红书话题识别，生, XiaohongshuAdapter, test_identity(), test_requires_account(), test_requires_images() (+1 more)

### Community 36 - "common.js"
Cohesion: 0.22
Nodes (13): apiFetch(), cancelVideoGeneration(), ensureVideoTaskCenter(), escapeHtml(), getCurrentUser(), initVideoTaskCenter(), NAV_ICONS, NAV_ITEMS (+5 more)

### Community 37 - "test_media_api.py"
Cohesion: 0.17
Nodes (7): _ChunkOnlyUpload, _client_and_token(), _png(), test_asset_upload_list_update_delete(), test_large_upload_is_streamed_in_bounded_chunks(), test_render_rejects_missing_capability(), test_stream_upload_rejects_limit_and_removes_partial_file()

### Community 38 - "video_evaluator.py"
Cohesion: 0.25
Nodes (13): test_fenced_json_is_parsed_and_audit_evidence_is_attached(), test_messages_interleave_frame_ids_timestamps_and_images(), test_unknown_evidence_frame_is_retried_once_then_rejected(), build_evaluation_messages(), _data_url(), evaluate_video(), EvaluationResponseError, _frame_id() (+5 more)

### Community 39 - "VideoEvaluationReport"
Cohesion: 0.28
Nodes (13): test_clean_high_score_report_passes(), test_high_issue_fails_even_with_high_score(), test_low_score_fails_even_when_model_says_passed(), test_report_rejects_score_outside_zero_to_one_hundred(), test_report_rejects_unknown_severity(), test_video_quality_input_defaults_are_cost_bounded(), _valid_issue(), _valid_report() (+5 more)

### Community 40 - "video_preprocessor.py"
Cohesion: 0.26
Nodes (13): _login(), test_admin_video_quality_endpoint_uses_static_allowlist(), test_preprocessor_rejects_api_local_path_outside_allowlist(), test_video_quality_endpoint_is_admin_only(), TranscriptResult, _local_path_allowed(), preprocess_video(), PreprocessedVideo (+5 more)

### Community 41 - "get_hotspot"
Cohesion: 0.21
Nodes (14): attach_hotspot_video(), discover_hotspot_media(), list_hotspot_sample_bundles(), materialize_hotspot_media(), _run_hotspot_media_materialization(), update_hotspot_media_rights(), get_asset_processing_job(), get_hotspot() (+6 more)

### Community 42 - "auth.py"
Cohesion: 0.19
Nodes (13): login(), create_access_token(), decode_token(), get_current_user(), JWT Authentication & RBAC for SA-LogiFlow v3.0., 从 Bearer token 解析当前用户。无 token 时拒绝访问。, require_role(), verify_password() (+5 more)

### Community 43 - "run_video_quality_mvp.py"
Cohesion: 0.30
Nodes (12): ArgumentParser, build_parser(), build_request(), main(), Namespace, Run the bounded MiMo video-quality MVP from a local terminal., _read_json(), _run() (+4 more)

### Community 44 - "ApiAdapter"
Cohesion: 0.21
Nodes (7): ApiAdapter, 官方 API 适配器基类：httpx 请求 + 凭据解析。凭据从 accounts.credentials(JSON) 读。, 更新凭据到数据库（子类可覆盖）。默认通过 database.update_account_credentials 写回。, 执行 POST，返回 (status_code, body_dict)。单测里被 monkeypatch。, test_creds_empty_and_bad(), test_creds_parses_json(), test_require_returns_missing_keys()

### Community 45 - "create_asset_processing_job"
Cohesion: 0.22
Nodes (13): create_local_asset_import(), process_media_asset(), process_pending_assets(), _run_asset_processing_job(), _run_local_asset_import_job(), create_asset_processing_job(), create_or_get_local_asset_import_job(), get_local_asset_import_job() (+5 more)

### Community 46 - "test_upload_api.py"
Cohesion: 0.31
Nodes (11): _admin_token(), _client(), Tests for file upload API + queue attachments., Create an admin user and return a JWT token., Create a TestClient with the tmp_db patched in., test_douyin_queue_requires_video(), test_generate_fallback_returns_xhs_publishable_images(), test_legacy_xhs_queue_submission_auto_generates_images() (+3 more)

### Community 49 - "test_video_generation_db.py"
Cohesion: 0.41
Nodes (10): _project_and_revision(), test_cancel_is_immediate_for_pending_and_requested_for_running(), test_expired_cancel_requested_job_recovers_as_canceled(), test_generation_job_is_idempotent_for_same_revision(), test_idempotency_key_is_isolated_between_users(), test_job_events_and_quality_report_are_decoded(), test_project_revision_is_versioned_and_returned_as_json(), test_terminal_job_allows_new_job_with_same_idempotency_key() (+2 more)

### Community 50 - "RedditAdapter"
Cohesion: 0.33
Nodes (6): Reddit 发帖：OAuth2 refresh_token 取 access_token，再 POST /api/submit（self post）。, RedditAdapter, _ret(), test_missing_credentials(), test_submit_returns_errors(), test_success()

### Community 51 - "_Scripts"
Cohesion: 0.22
Nodes (3): HTMLParser, _Scripts, test_changed_asset_pages_have_valid_javascript()

### Community 52 - "test_registry_dispatch.py"
Cohesion: 0.24
Nodes (6): _AccountDummy, _Dummy, test_core_and_huimei_adapters_registered(), test_dispatch_loads_ready_account_from_database(), test_dispatch_routes_to_registered_adapter(), test_dispatch_unknown_platform()

### Community 53 - "get_asset"
Cohesion: 0.25
Nodes (9): delete_media_asset(), update_media_asset(), asset_is_referenced(), asset_reference_reasons(), delete_asset(), get_asset(), _json_reference_exists(), Return every known business reference before a destructive asset operation. (+1 more)

### Community 54 - "list_assets"
Cohesion: 0.25
Nodes (9): retry_douyin_render(), create_render_job(), get_render_job(), get_unfinished_render_jobs(), list_assets(), update_render_job(), _render_internal_preview(), cleanup_stale_jobs() (+1 more)

### Community 55 - "test_video_generation_rendering.py"
Cohesion: 0.25
Nodes (3): _input_bound(), Path, test_scene_command_enforces_selected_shot_start_and_end()

### Community 56 - "create_semantic_match"
Cohesion: 0.25
Nodes (8): create_semantic_match(), select_semantic_match(), add_match_feedback(), create_match_session(), create_semantic_atom(), get_match_session(), replace_match_candidates(), update_semantic_atom_selection()

### Community 57 - "static/manifest.json"
Cohesion: 0.25
Nodes (7): background_color, display, icons, name, short_name, start_url, theme_color

### Community 58 - "test_editor_transfer.py"
Cohesion: 0.46
Nodes (7): _run_transfer(), test_build_draft_keeps_clicked_output_when_an_earlier_output_is_invalid(), test_build_draft_preserves_all_platforms_and_active_selection(), test_build_draft_preserves_generated_media_assets(), test_normalize_draft_falls_back_to_legacy_when_v2_contents_are_invalid(), test_normalize_draft_filters_unknown_and_keeps_distinct_platform_content(), test_normalize_draft_rejects_empty_payload_without_consuming_it()

### Community 60 - "test_sample_harness.py"
Cohesion: 0.46
Nodes (7): _package(), test_missing_brand_evidence_removes_performance_promises(), test_sample_bundle_api_returns_persisted_bundle(), test_three_samples_share_claim_ids_but_use_distinct_structures(), test_video_material_status_ready_only_when_both_libraries_have_candidates(), test_video_sample_writes_selected_segment_boundaries(), test_weak_candidate_is_not_treated_as_a_good_scene_match()

### Community 62 - "test_semantic_assets_db.py"
Cohesion: 0.39
Nodes (5): _create_parent_asset(), test_asset_processing_job_tracks_progress_and_segment_listing(), test_match_session_persists_atoms_candidates_and_feedback(), test_pending_asset_batch_excludes_running_jobs(), test_segment_tags_are_normalized_and_searchable_in_chinese()

### Community 63 - "test_user_tenancy.py"
Cohesion: 0.43
Nodes (7): Registration and per-user account/content isolation., _signup_login(), test_dashboard_team_metrics_are_auditable_and_role_scoped(), test_one_wechat_article_can_route_to_multiple_owned_accounts(), test_queue_and_dashboard_are_scoped_to_creator(), test_signup_is_editor_and_accounts_are_isolated(), test_truth_gate_blocks_current_event_until_evidence_is_mapped()

### Community 64 - "test_video_generation_api.py"
Cohesion: 0.61
Nodes (7): _client(), _create_project(), test_cancel_pending_job_is_idempotent(), test_create_read_and_update_project_revision(), test_generate_is_idempotent_and_active_job_recovers(), test_project_and_job_are_private_to_owner(), test_project_revision_rejects_hotspot_mother_without_event_ref()

### Community 66 - "schemas.py"
Cohesion: 0.36
Nodes (6): EvaluationIssue, BaseModel, QualityScores, Strict input and output contracts for video quality evaluation., RegenerationPlan, StrictModel

### Community 67 - "materialize_inspiration"
Cohesion: 0.38
Nodes (7): confirm_inspiration_rights(), materialize_inspiration(), _run_inspiration_materialization(), get_inspiration_item(), update_inspiration_materialization(), InspirationMaterializeRequest, InspirationRightsRequest

### Community 68 - "test_hotspot_fetch_runs.py"
Cohesion: 0.48
Nodes (5): _admin_client(), _hotspot(), test_fetch_run_distinguishes_partial_and_all_source_failures(), test_fetch_run_is_persisted_and_status_api_reports_source_health(), test_hotspot_detail_and_related_sample_bundle_api()

### Community 71 - "test_hotspot_media_db.py"
Cohesion: 0.60
Nodes (5): _hotspot(), test_changed_hotspot_snapshot_invalidates_cached_translation(), test_hotspot_media_rights_confirmation_is_independent_from_download(), test_hotspot_media_round_trip_deduplicates_by_hotspot_and_url(), test_hotspot_translation_cache_round_trip()

### Community 72 - "_account_for_user"
Cohesion: 0.40
Nodes (5): _account_for_user(), delete_account(), scan_login_status(), set_account_credentials(), AccountCredentialsRequest

### Community 73 - "create_video_project"
Cohesion: 0.40
Nodes (5): create_video_project(), get_video_project(), update_video_project_revision(), VideoProjectCreateRequest, VideoProjectRevisionRequest

### Community 74 - "fetch_hotspots_now"
Cohesion: 0.50
Nodes (5): fetch_hotspots_now(), create_hotspot_fetch_run(), _decode_hotspot_fetch_run(), finish_hotspot_fetch_run(), get_latest_hotspot_fetch_run()

### Community 75 - "signup"
Cohesion: 0.40
Nodes (5): 创建普通运营账号；角色固定为 editor，防止注册请求越权。, signup(), 公开注册只接受个人资料，不允许客户端指定权限。, SignupRequest, Request

### Community 76 - "draw_truck"
Cohesion: 0.50
Nodes (4): draw_truck(), main(), Image, Draw a flat, modern truck silhouette on the given image.

### Community 77 - "editor-transfer.js"
Cohesion: 0.70
Nodes (4): buildDraft(), content(), normalizeDraft(), tags()

### Community 78 - "kb_upload"
Cohesion: 0.50
Nodes (4): kb_upload(), 上传 TXT/MD 文件，解析为纯文本返回（前端再决定存哪个分类）。, upload_file(), UploadFile

### Community 81 - "get_kb_documents"
Cohesion: 0.67
Nodes (3): kb_list_documents(), get_kb_documents(), 文档列表（不含全文，只给摘要），按分类过滤。

### Community 82 - "update_model_route"
Cohesion: 0.67
Nodes (3): update_model_route(), save_route(), ModelRouteRequest

## Knowledge Gaps
- **150 isolated node(s):** `bundle_id`, `evidence_package_id`, `20f7135f84c548528db9703b500aa603`, `24b4d7d1a141404382187309ed42dbf4`, `4b978ffb28ca43acb56ea0397b9d8a76` (+145 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `RpaAdapter` connect `RpaAdapter` to `XiaohongshuAdapter`, `app.py`, `DouyinAdapter`, `PublishResult`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `XiaohongshuAdapter` connect `XiaohongshuAdapter` to `RpaAdapter`, `test_registry_dispatch.py`, `PublishResult`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Why does `VideoQualityInput` connect `VideoQualityInput` to `schemas.py`, `VideoEvaluationReport`, `video_preprocessor.py`, `video_generation.py`, `app.py`, `run_video_quality_mvp.py`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Are the 12 inferred relationships involving `PublishResult` (e.g. with `DouyinAdapter` and `FacebookAdapter`) actually correct?**
  _`PublishResult` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `ValueError` (e.g. with `_translation_payload()` and `process_asset_job()`) actually correct?**
  _`ValueError` has 35 INFERRED edges - model-reasoned connections that need verification._
- **What connects `bundle_id`, `evidence_package_id`, `20f7135f84c548528db9703b500aa603` to the rest of the system?**
  _150 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `database.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05263157894736842 - nodes in this community are weakly interconnected._