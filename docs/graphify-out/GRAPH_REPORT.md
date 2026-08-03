# Graph Report - /private/tmp/salogiflow-graphify-20260803  (2026-08-03)

> Snapshot scope: current working tree, including uncommitted files. `built_at_commit` identifies the base commit only.

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 2419 nodes · 5591 edges · 126 communities (117 shown, 9 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 117 edges (avg confidence: 0.66)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `7ae466f2`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- database.py
- test_ai_chat_platforms.py
- app.py
- video_evaluator.py
- video_generation.py
- test_media_retention.py
- scheduler.py
- asset_processing.py
- hotspot_media.py
- _generate_topic_brief_video
- init_db
- post
- video_renderer.py
- run_process
- VideoQualityInput
- check_scheduled_publish
- models.py
- ValueError
- xhs_cards.py
- model_router.py
- fetch_hotspots
- frame_extractor.py
- PublishResult
- build_event_clips
- semantic_matching.py
- _reprocess_media
- test_video_generation_rendering.py
- twitter.py
- add_audit_log
- ingest_file
- run
- hotspot_topic_packages.py
- hotspot_preview_narration.py
- ApiAdapter
- auth.py
- select_for_hook_ingestion
- plan_followup_scenes
- _queue_chat_dual_library_video_job
- normalize_url
- test_hotspot_hook_curation.py
- test_hotspot_hook_library_gates.py
- test_topic_briefs.py
- DouyinAdapter
- lifespan
- hotspot_hook_curator.py
- test_video_duration_budget.py
- rpa_base.py
- get_accounts
- hotspot_video_planner.py
- test_media_api.py
- test_video_generation_api.py
- transcript_service.py
- generate_bundle
- route_scoped_job_id
- users.py
- XiaohongshuAdapter
- build_brief
- common.js
- source_usage_report
- _decode_video_job
- run_cancelable_process
- test_hotspot_prewarm_workflow.py
- run_video_quality_mvp.py
- evaluate
- hotspot_video_sources.py
- video_generation_routes.py
- normalize_script
- test_model_router.py
- _run_hotspot_media_materialization
- _marketing_hook_candidates
- hotspot_package_service.py
- test_hotspot_video_sources.py
- test_upload_api.py
- schemas.py
- audit_eligible_hotspot_hook_pairs.py
- delete_hotspot_library
- _diversify_owned_candidates
- test_semantic_assets_db.py
- test_video_generation_db.py
- test_video_quality_schemas.py
- publisher.py
- _Scripts
- test_registry_dispatch.py
- config_routes.py
- create_semantic_match
- manifest.json
- test_editor_transfer.py
- test_sample_harness.py
- test_user_tenancy.py
- rank_hook_clips
- test_hotspot_media_db.py
- Platform
- dump_topic_docs.py
- test_chat_api.py
- page_routes.py
- configure_minimax_text_routes.py
- draw_truck
- editor-transfer.js
- test_chat_video_task_orchestration.py
- asset_reference_reasons
- _ensure_column
- test_reprocess_hotspot_hook_source.py
- HotspotMediaRightsRequest
- download_asr_model.py
- repositories/__init__.py
- routes/__init__.py
- run_douyin_tests.sh
- start.sh

## God Nodes (most connected - your core abstractions)
1. `get_conn()` - 194 edges
2. `add_audit_log()` - 65 edges
3. `_generate_topic_brief_video()` - 40 edges
4. `plan_followup_scenes()` - 37 edges
5. `fetch_hotspots()` - 36 edges
6. `PublishResult` - 35 edges
7. `render_job()` - 33 edges
8. `VideoEvaluationReport` - 32 edges
9. `_validate_evidence()` - 30 edges
10. `VideoQualityInput` - 28 edges

## Surprising Connections (you probably didn't know these)
- `_AccountDummy` --uses--> `PublishResult`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py
- `_Dummy` --uses--> `PublishResult`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py
- `_AccountDummy` --uses--> `PublishAdapter`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py
- `_Dummy` --uses--> `PublishAdapter`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/base.py
- `_AccountDummy` --uses--> `DouyinAdapter`  [INFERRED]
  tests/test_registry_dispatch.py → adapters/douyin.py

## Import Cycles
- None detected.

## Communities (126 total, 9 thin omitted)

### Community 0 - "database.py"
Cohesion: 0.03
Nodes (115): add_to_queue(), add_video_generation_event(), archive_stale_hotspot_media(), asset_active_reference_reasons(), confirm_brand_evidence(), count_published_today(), create_asset(), create_brand_evidence() (+107 more)

### Community 1 - "test_ai_chat_platforms.py"
Cohesion: 0.05
Nodes (70): chat(), _chat_one_platform(), chat_platforms(), _conservative_chat_body(), _conservative_chat_subject(), _conservative_douyin_scenes(), _fallback_content(), _format_asset_catalog() (+62 more)

### Community 2 - "app.py"
Cohesion: 0.05
Nodes (72): _chat_hook_candidates_debug(), _compact_long_formal_voiceovers(), create_douyin_render(), create_hotspot_source(), create_prompt_template(), _enforce_formal_scene_copy_contract(), _extend_short_formal_voiceovers(), fetch_hotspots_now() (+64 more)

### Community 3 - "video_evaluator.py"
Cohesion: 0.07
Nodes (71): asyncio, _report(), test_camera_shake_cannot_be_inferred_from_one_keyframe_without_detector_evidence(), test_cta_static_detector_hit_cannot_justify_a_freeze_summary_or_fix(), test_evidence_frame_number_is_canonicalized_when_qwen_reestimates_timestamp(), test_fenced_json_is_parsed_and_audit_evidence_is_attached(), test_final_normalization_recovers_when_unsupported_freeze_is_the_only_failure(), test_final_normalization_removes_an_unsupported_camera_shake_claim() (+63 more)

### Community 4 - "video_generation.py"
Cohesion: 0.05
Nodes (71): StageHandler, StrEnum, asyncio, parametrize, test_brand_endcard_path_is_safe_and_does_not_require_a_video_slot(), test_cancel_checkpoint_stops_every_pipeline_stage(), test_completed_semantic_failure_cannot_use_manual_preview_fallback(), test_dual_library_preview_rejects_a_silently_shortened_delivery() (+63 more)

### Community 5 - "test_media_retention.py"
Cohesion: 0.06
Nodes (59): hash_password(), _hotspot(), test_build_evidence_package_keeps_external_and_brand_claims_separate(), test_confirmed_public_brand_claim_can_enter_package(), test_evidence_package_and_brand_evidence_admin_api(), test_unconfirmed_brand_claim_cannot_enter_publishable_package(), _asset(), _hotspot() (+51 more)

### Community 6 - "scheduler.py"
Cohesion: 0.06
Nodes (49): _as_utc(), _asset_is_due(), _cleanup(), cleanup_hotspot_hook_library(), disk_guard(), preview_cleanup(), datetime, Path (+41 more)

### Community 7 - "asset_processing.py"
Cohesion: 0.08
Nodes (44): build_processing_plan(), _category_scores(), classify_evidence(), _contains(), datetime_now(), detect_scene_boundaries(), _extract_tags(), _make_video_preview() (+36 more)

### Community 8 - "hotspot_media.py"
Cohesion: 0.07
Nodes (41): discover_media_candidates(), download_authorized_image(), download_authorized_video(), fetch_source_page(), filter_reachable_image_candidates(), _jsonld_objects(), normalize_video_url(), _preferred_article_image() (+33 more)

### Community 9 - "_generate_topic_brief_video"
Cohesion: 0.07
Nodes (42): ai_chat(), autopilot_topic_brief_video(), _build_video_generation_handlers(), _chat_video_delivery_readiness(), cleanup_ineligible_hotspot_events(), _compact_topic_evidence(), _decorate_hotspot_event(), _generate_topic_brief_video() (+34 more)

### Community 10 - "init_db"
Cohesion: 0.06
Nodes (37): create_render_job(), get_render_job(), get_user_by_username(), init_db(), list_active_authorized_hotspot_media_for_full_intake(), list_hotspots(), mark_hotspot_discovery_request_matched(), Return the complete authorised active media library for the three-day Hook job.… (+29 more)

### Community 11 - "post"
Cohesion: 0.09
Nodes (38): cancel_local_asset_import(), confirm_hotspot_package(), create_account(), create_brand_evidence(), create_evidence_package(), create_inspiration_batch(), create_local_asset_import(), create_sample_bundle() (+30 more)

### Community 12 - "video_renderer.py"
Cohesion: 0.10
Nodes (37): _ass_time(), _audio_tempo_command(), build_subtitle_cues(), _clip_source_command(), compact_voiceover_to_fit_real_video(), _generate_text_overlay(), _generate_watermark(), _has_audio() (+29 more)

### Community 13 - "run_process"
Cohesion: 0.12
Nodes (32): Popen, test_corrupt_video_fails_before_semantic_review(), test_detection_output_becomes_timestamped_issues(), test_ffprobe_fraction_is_parsed(), test_local_source_is_resolved_without_copy(), test_process_runner_honors_preexisting_cancel_request(), test_process_runner_terminates_on_timeout(), test_remote_source_requires_https() (+24 more)

### Community 14 - "VideoQualityInput"
Cohesion: 0.14
Nodes (31): _login(), test_admin_video_quality_endpoint_uses_static_allowlist(), test_preprocessor_rejects_api_local_path_outside_allowlist(), test_video_quality_endpoint_is_admin_only(), _preprocessed(), asyncio, _report(), test_high_issue_runs_one_bounded_focus_review() (+23 more)

### Community 15 - "check_scheduled_publish"
Cohesion: 0.12
Nodes (33): get_adapter(), browser_launch_options(), _account_for_user(), add_queue(), delete_account(), manual_publish(), publish_batch(), publish_item() (+25 more)

### Community 16 - "models.py"
Cohesion: 0.11
Nodes (31): AccountCreateRequest, AccountCredentialsRequest, BrandEvidenceConfirmRequest, BrandEvidenceCreateRequest, ChatMessage, ChatRequest, EvidencePackageCreateRequest, GenerateRequest (+23 more)

### Community 17 - "ValueError"
Cohesion: 0.07
Nodes (30): backfill_visible_brand_tags(), create_asset_segment(), create_or_get_video_generation_job(), enqueue_hotspot_discovery_request(), list_brand_evidence(), _normalized_tag(), 从既有 OCR/描述恢复确定可见的品牌标签，不猜测、不更改主分类。, Store one source signal idempotently by source type and external identifier. (+22 more)

### Community 18 - "xhs_cards.py"
Cohesion: 0.16
Nodes (30): ImageDraw, Path, test_normalize_pages_caps_carousel_at_seven_pages(), test_normalize_pages_preserves_logo_editing_controls(), test_normalize_pages_sanitizes_and_limits_content(), test_pages_from_legacy_content_builds_carousel(), test_render_carousel_creates_publishable_pngs(), test_render_carousel_uses_available_brand_photos() (+22 more)

### Community 19 - "model_router.py"
Cohesion: 0.16
Nodes (28): _model_decide_marketing_hooks(), The deployed planner, not Codex or a human, makes the content decision. Rules…, 用 Qwen-VL 标注一个片段；异常降级但必须留下可审计原因。, _visual_analysis(), get_model_cache(), _call_planner(), BudgetExceeded, call_multimodal_json() (+20 more)

### Community 20 - "fetch_hotspots"
Cohesion: 0.12
Nodes (27): configured_feeds(), configured_source_rights(), _domain_allowed(), fetch_hotspots(), _is_bot_challenge(), _licensed_commons_image(), _og_image(), parse_feed() (+19 more)

### Community 21 - "frame_extractor.py"
Cohesion: 0.16
Nodes (28): parametrize, test_dedup_compares_against_last_kept_frame_and_deletes_dropped(), test_even_sampling_always_keeps_first_and_last(), test_focus_density_is_limited_to_ten_fps(), test_focus_mode_uses_requested_density_but_obeys_cap(), test_full_scan_budget_targets_forty_frames_for_one_minute_video(), test_mean_pixel_delta_detects_near_duplicates(), test_three_modes_have_explicit_engines() (+20 more)

### Community 22 - "PublishResult"
Cohesion: 0.14
Nodes (18): ABC, PublishAdapter, PublishResult, 发布适配器接口契约。所有平台适配器实现同一协议。, 默认恒为已登录（无状态/Token 型）。RPA 子类覆写。, FacebookAdapter, Facebook 主页发文：Graph API /{page_id}/feed。, HuimeiAdapter (+10 more)

### Community 23 - "build_event_clips"
Cohesion: 0.12
Nodes (22): build_event_clips(), _event_name(), 热点视频事件片段：只用已有字幕/OCR/镜头证据做本地分组和命名。, A source headline may label a clip only when analysed evidence corroborates it., 将有可解释证据的镜头按地点/实体连续性聚合为事件片段。, _should_split(), _signature(), _source_title_matches_event() (+14 more)

### Community 24 - "semantic_matching.py"
Cohesion: 0.19
Nodes (25): assign_candidates(), build_semantic_atoms(), extract_semantics(), _freshness(), _hard_conflict(), _ngrams(), _overlap(), rank_segments() (+17 more)

### Community 25 - "_reprocess_media"
Cohesion: 0.11
Nodes (22): list_hotspot_media(), replace_hotspot_event_clips(), update_asset_semantic_state(), update_hotspot_event_clip_media(), materialize_event_clip(), materialize_event_clips(), Path, Materialize short, low-resolution preview clips for hotspot events. The… (+14 more)

### Community 27 - "twitter.py"
Cohesion: 0.10
Nodes (20): _CallbackHandler, _exchange_code(), _is_token_expired(), _pkce_challenge(), X(Twitter) 发推：API v2 POST /2/tweets，OAuth 2.0 支持两种模式。, 交互式 OAuth 2.0 授权：打开浏览器让用户授权，返回 token dict。, 检查 access_token 是否过期（基于 token_obtained_at + expires_in）。, 获取可用的 user access_token，过期则自动刷新。 (+12 more)

### Community 28 - "add_audit_log"
Cohesion: 0.13
Nodes (26): backfill_buffalo_brand_tags(), clear_hotspot_library(), confirm_brand_evidence(), confirm_inspiration_rights(), delete_hotspot_event_asset(), delete_hotspot_event_clip(), _delete_hotspot_library_files(), delete_hotspot_media_item() (+18 more)

### Community 29 - "ingest_file"
Cohesion: 0.14
Nodes (20): configured_root(), discover(), ingest_one(), Path, resolve_source_path(), guess_category(), ingest_file(), _probe() (+12 more)

### Community 30 - "run"
Cohesion: 0.14
Nodes (19): Any, main(), AsyncClient, Response, Run C-end chat-to-video closure checks without manually choosing Hooks. Each…, Return a bounded 1-based slice so failed real scenarios can be rerun alone., _request(), _result_error() (+11 more)

### Community 31 - "hotspot_topic_packages.py"
Cohesion: 0.17
Nodes (23): _as_mapping(), _average_metric(), calculate_heat_score(), _can_cluster(), classify_event(), _clean_text(), cluster_signals(), _entities() (+15 more)

### Community 32 - "hotspot_preview_narration.py"
Cohesion: 0.14
Nodes (22): build_messages(), _call_critic(), deterministic_evidence_issues(), generate_narration(), _hotspot_facts(), parse_critique(), parse_narration(), Qwen-authored narration for an already evidence-locked dual-library preview.… (+14 more)

### Community 33 - "ApiAdapter"
Cohesion: 0.13
Nodes (13): ApiAdapter, 官方 API 适配器基类：httpx 请求 + 凭据解析。凭据从 accounts.credentials(JSON) 读。, 更新凭据到数据库（子类可覆盖）。默认通过 database.update_account_credentials 写回。, 执行 POST，返回 (status_code, body_dict)。单测里被 monkeypatch。, Reddit 发帖：OAuth2 refresh_token 取 access_token，再 POST /api/submit（self post）。, RedditAdapter, test_creds_empty_and_bad(), test_creds_parses_json() (+5 more)

### Community 34 - "auth.py"
Cohesion: 0.16
Nodes (20): create_access_token(), decode_token(), get_current_user(), _load_or_create_jwt_secret(), JWT Authentication & RBAC for SA-LogiFlow v3.0., 优先用环境变量；否则读取/生成一份本机持久化的随机密钥。 不用固定字符串兜底：源码里写死的默认密钥等于公开发布签名密钥， 任何读到代码的人都能伪造登录…, 从 Bearer token 解析当前用户。无 token 时拒绝访问。, require_role() (+12 more)

### Community 35 - "select_for_hook_ingestion"
Cohesion: 0.14
Nodes (19): _audit_prompt(), _candidate(), _parse_audit(), _parse_selections(), _prompt(), 热点 Hook 库的入库前模型筛选。 定时器只负责找出已授权、尚未下载且足够长的候选；是否值得占用下载和分析资源， 由项目内的 Qwen…, 仅由内置模型选出可下载的热点母片；没有模型则返回空集。, select_for_hook_ingestion() (+11 more)

### Community 36 - "plan_followup_scenes"
Cohesion: 0.14
Nodes (20): _owned_candidates(), plan_followup_scenes(), test_dynamic_script_avoids_repeating_same_voiceover(), test_formal_plan_drops_duplicate_visible_actions_instead_of_padding_duration(), test_formal_planner_does_not_insert_owned_images_as_context_transitions(), test_over_budget_plan_keeps_every_real_video_at_least_three_seconds(), test_owned_planner_prefers_visible_buffalo_brand_within_same_logistics_category(), test_planner_uses_measured_real_clip_budget_and_never_assumes_a_loop() (+12 more)

### Community 37 - "_queue_chat_dual_library_video_job"
Cohesion: 0.14
Nodes (21): _build_topic_brief_payload(), _chat_dual_library_idempotency_key(), _chat_video_logistics_nodes(), _chat_video_task_lease_heartbeat(), _chat_video_task_worker_loop(), create_topic_brief(), generate_chat_dual_library_video(), Event (+13 more)

### Community 38 - "normalize_url"
Cohesion: 0.18
Nodes (18): _store_inspiration(), build_ytdlp_options(), can_auto_materialize_official(), download_authorized_media(), fetch_oembed(), normalize_url(), Path, 构建受限、可观测的视频下载参数，避免整片长视频占满磁盘。 (+10 more)

### Community 39 - "test_hotspot_hook_curation.py"
Cohesion: 0.13
Nodes (9): _segments(), test_hook_curation_budget_identity_changes_when_analysis_evidence_changes(), test_hook_curation_context_is_part_of_prompt_and_cache_identity(), test_hook_curator_accepts_a_bare_model_candidate_list(), test_hook_curator_rejects_an_obvious_anchor_only_segment_before_model_audit(), test_hook_curator_rejects_invalid_duration_or_unexplained_model_output(), test_hook_curator_rejects_mixed_event_identities_from_one_news_compilation(), test_qwen_critic_rejects_hook_that_contradicts_verified_event_fact() (+1 more)

### Community 40 - "test_hotspot_hook_library_gates.py"
Cohesion: 0.17
Nodes (15): _add_owned_delivery_segments(), _admin_client(), _create_ready_chat_hook(), _create_ready_chat_hook_pair(), test_admin_can_delete_one_hook_without_deleting_mother_or_siblings(), test_chat_dual_library_video_accepts_one_locked_hook(), test_chat_dual_library_video_creates_locked_dual_library_project(), test_chat_hook_candidates_do_not_reuse_border_for_cost_risk_when_better_disruption_exists() (+7 more)

### Community 41 - "test_topic_briefs.py"
Cohesion: 0.10
Nodes (3): _client(), test_broad_topic_returns_angles_without_triggering_evidence_work(), test_explicit_topic_persists_structured_brief_and_only_reference_evidence()

### Community 42 - "DouyinAdapter"
Cohesion: 0.16
Nodes (11): DouyinAdapter, 抖音发布（RPA）：cookie 登录 + 创作平台上传视频 + 填文案 + 发布。, 打开抖音创作平台上传页，自动填好文案和话题标签，但**不点发布**，供人工复核后手动发布。, 关闭抖音页面上的各种弹窗（共创中心提示、位置权限、视频预览说明等）。, 填写抖音文案输入框。多策略兜底：Quill 编辑器 → contenteditable → textarea。, 点击抖音发布按钮。多策略兜底，避开页面标题「发布视频」。, 把当前页面截图存到 static/debug，返回可访问的相对路径。, test_identity() (+3 more)

### Community 43 - "lifespan"
Cohesion: 0.10
Nodes (20): lifespan(), create_hotspot_source(), create_user(), get_unfinished_render_jobs(), list_hotspot_sources(), Requeue only videos whose model curation failed after analysis completed. A…, Mark runs left active by a stopped process so the UI can start again., Make abandoned planning tasks claimable again after a worker restart. (+12 more)

### Community 44 - "hotspot_hook_curator.py"
Cohesion: 0.18
Nodes (18): _audit_hooks(), _audit_job_id(), _audit_prompt(), _compact_segment(), curate_hook_clips(), _curation_job_id(), _derive_hook_keywords(), _parse() (+10 more)

### Community 45 - "test_video_duration_budget.py"
Cohesion: 0.18
Nodes (17): test_budget_rejects_out_of_range_override(), test_budget_trims_last_scene_without_using_mother_clip(), test_event_clip_resolves_to_mother_range(), test_hotspot_mother_asset_without_event_ref_is_rejected(), test_platform_budget_defaults(), test_rebalance_preserves_every_scene_and_hits_budget_exactly(), ClipReferenceError, _event_lookup() (+9 more)

### Community 46 - "rpa_base.py"
Cohesion: 0.19
Nodes (12): build_credentials(), parse_cookies(), playwright_proxy_from_env(), Playwright RPA 适配器基类：cookie 登录态存取 + 登录骨架。, 把常见代理环境变量转换为 Playwright 的 proxy 配置。, RpaAdapter, 账号就绪度判定：给定平台 + 凭据 JSON，返回是否可发布 + 缺哪些字段。 必填字段由各适配器 REQUIRED_CREDENTIALS /…, test_parse_cookies_bad_json_is_safe() (+4 more)

### Community 47 - "get_accounts"
Cohesion: 0.12
Nodes (18): dashboard(), create_account(), get_accounts(), get_queue(), get_queue_item_by_id(), get_queue_stats(), get_recent_activity(), get_scheduled_items() (+10 more)

### Community 48 - "hotspot_video_planner.py"
Cohesion: 0.12
Nodes (18): append_brand_endcard_scenes(), _eligible_owned_categories(), _event_display_title(), _event_score(), _event_text(), _event_visual_range(), _limit_distinct_hotspot_hooks(), _owned_image_candidates() (+10 more)

### Community 49 - "test_media_api.py"
Cohesion: 0.15
Nodes (9): _ChunkOnlyUpload, _client_and_token(), _png(), asyncio, test_asset_upload_list_update_delete(), test_brand_filter_returns_delivery_asset_with_visible_buffalo_tag(), test_large_upload_is_streamed_in_bounded_chunks(), test_render_rejects_missing_capability() (+1 more)

### Community 50 - "test_video_generation_api.py"
Cohesion: 0.29
Nodes (17): _client(), _create_project(), _manual_preview_job(), _semantic_review_job(), test_cancel_pending_job_is_idempotent(), test_create_read_and_update_project_revision(), test_generate_is_idempotent_and_active_job_recovers(), test_legacy_landscape_project_request_is_stored_as_portrait() (+9 more)

### Community 51 - "transcript_service.py"
Cohesion: 0.22
Nodes (16): test_known_storyboard_skips_whisper(), test_missing_subtitles_and_model_produce_explicit_unavailable_status(), test_native_vtt_is_used_before_whisper(), test_transcript_range_keeps_overlapping_cues(), test_youtube_rolling_vtt_cues_are_collapsed(), build_transcript(), clip_segments(), _dedupe() (+8 more)

### Community 52 - "generate_bundle"
Cohesion: 0.17
Nodes (16): create_evidence_package(), _fts_query(), get_asset_segment(), get_evidence_package(), list_asset_segments(), list_inspiration_items(), search_asset_segments(), _segment_tags() (+8 more)

### Community 53 - "route_scoped_job_id"
Cohesion: 0.16
Nodes (14): Scope a reusable workflow job to the active model route. Prompt caches include…, route_scoped_job_id(), save_route(), main(), Configure Xiaomi MiMo Token Plan for the video_evaluator role. Qwen-VL…, _route(), _verify(), main() (+6 more)

### Community 54 - "users.py"
Cohesion: 0.16
Nodes (16): 公开注册只接受个人资料，不允许客户端指定权限。, RegisterRequest, SignupRequest, add_audit_log(), create(), get_by_username(), User repository facade. This keeps route modules from importing the monolithic…, update_last_login() (+8 more)

### Community 55 - "XiaohongshuAdapter"
Cohesion: 0.18
Nodes (9): 小红书图文发布（RPA）：cookie 登录 + 创作平台上传图片 + 填标题正文 + 发布。, 打开发布页并自动填好（标题/正文/图片/话题），但**不点发布**，供人工复核后手动发布。 话题用逐字输入 +…, 点击发布按钮。小红书的「发布」是自定义 div（非 <button>）， 故以精确文案定位为主，多策略兜底；用 exact=True 避开「发布笔记」。, 把当前页面截图存到 static/debug，返回可访问的相对路径，便于排查选择器。, XiaohongshuAdapter, test_identity(), test_requires_account(), test_requires_images() (+1 more)

### Community 56 - "build_brief"
Cohesion: 0.17
Nodes (13): build_brief(), classify_hotspot(), _custom_topic(), 动态热点与物流主题规划器。 该模块只负责把热点事实整理成可审查的内容简报，不直接生成成片，也不凭空…, Prefer the user's reviewed brief over the legacy fixed-topic mapping., _text(), build_scenes(), Build a 60-second evidence plan from one confirmed topic package only. (+5 more)

### Community 57 - "common.js"
Cohesion: 0.22
Nodes (13): apiFetch(), cancelVideoGeneration(), ensureVideoTaskCenter(), escapeHtml(), getCurrentUser(), initVideoTaskCenter(), NAV_ICONS, NAV_ITEMS (+5 more)

### Community 58 - "source_usage_report"
Cohesion: 0.21
Nodes (14): test_final_subtitle_timeline_accounts_for_crossfades(), test_legacy_infographic_scene_is_identified_for_hard_rejection(), test_source_usage_allows_only_two_distinct_non_overlapping_hooks_per_parent(), test_source_usage_rejects_duplicate_segment_and_buffalo_mother(), test_source_usage_rejects_reused_context_image(), is_explanation_scene(), is_real_video_scene(), _range() (+6 more)

### Community 59 - "_decode_video_job"
Cohesion: 0.16
Nodes (15): get_chat_dual_library_video_task(), claim_next_chat_video_task(), claim_next_video_generation_job(), create_or_get_chat_video_task(), _decode_chat_video_task(), _decode_video_job(), get_active_video_generation_job_by_idempotency(), get_chat_video_task() (+7 more)

### Community 60 - "run_cancelable_process"
Cohesion: 0.13
Nodes (14): main(), Synthesize one sample line with MiMo v2.5-tts so you can listen before…, cancel_render(), CompletedProcess, RuntimeError, 用 MiMo v2.5-tts 合成旁白；voice 留空时使用预置默认音色。 请求/返回格式来自官方文档 speech-…, 使用 macOS 内置语音生成内部预览，不发送任何文本到外部服务。, Raised when a running render is canceled cooperatively. (+6 more)

### Community 62 - "test_hotspot_prewarm_workflow.py"
Cohesion: 0.29
Nodes (11): _candidate(), asyncio, test_chat_targeted_refresh_rescans_authorised_sources_before_hook_intake(), test_prewarm_defaults_to_every_authorized_video_without_duration_filter(), test_prewarm_keeps_a_downloaded_mother_video_for_resume(), test_prewarm_never_calls_qwen_or_download_when_video_facts_cannot_be_read(), test_prewarm_reads_video_facts_and_materializes_every_authorized_candidate(), test_prewarm_requeues_an_authorized_download_interrupted_by_service_restart() (+3 more)

### Community 63 - "run_video_quality_mvp.py"
Cohesion: 0.30
Nodes (12): ArgumentParser, build_parser(), build_request(), main(), Namespace, Run the bounded Qwen video-quality MVP from a local terminal., _read_json(), _run() (+4 more)

### Community 64 - "evaluate"
Cohesion: 0.21
Nodes (11): compose(), Evidence-preserving hotspot-to-Buffalo draft composition., Compose facts and brand commentary without asking a model to invent facts., test_bad_or_unmapped_evidence_does_not_unlock_publish(), test_current_event_requires_sentence_level_evidence(), test_non_factual_advice_does_not_require_citation(), evaluate(), Deterministic provenance gate for factual/current-event content. (+3 more)

### Community 65 - "hotspot_video_sources.py"
Cohesion: 0.23
Nodes (13): _command(), _compact_text(), configured_channels(), _configured_source_authorization(), fetch_youtube_channel_hotspots(), _metadata_command(), _published_at(), 低成本 YouTube 频道热点发现：只读取单视频元数据，不下载媒体。 (+5 more)

### Community 66 - "video_generation_routes.py"
Cohesion: 0.14
Nodes (13): 人工验收仅适用于已通过技术检查的内部预览，不触发发布。, VideoGenerationManualReviewRequest, VideoGenerationRequest, VideoGenerationResumeRequest, VideoProjectCreateRequest, VideoProjectRevisionRequest, VideoQualityRequest, create_router() (+5 more)

### Community 67 - "normalize_script"
Cohesion: 0.20
Nodes (13): _png_bytes(), test_douyin_script_normalization_rejects_bad_scene_count(), test_douyin_script_normalization_rejects_legacy_infographic_scene(), test_douyin_script_removes_unknown_asset_ids(), test_ingest_image_validates_deduplicates_and_generates_thumbnail(), test_ingest_rejects_fake_image(), test_qwen_tts_downloads_returned_wav(), test_qwen_tts_normalizes_legacy_voice_to_current_default() (+5 more)

### Community 68 - "test_model_router.py"
Cohesion: 0.16
Nodes (4): asyncio, test_multimodal_json_call_uses_image_content_and_json_mode(), test_text_call_uses_compatible_endpoint_and_cache(), test_text_json_mode_is_part_of_request_and_cache_identity()

### Community 69 - "_run_hotspot_media_materialization"
Cohesion: 0.23
Nodes (13): attach_hotspot_video(), create_inspiration(), discover_hotspot_media(), materialize_hotspot_media(), _normalized_hotspot_intake_decision(), prepare_hotspot_media(), Response, Read legacy intake metadata without letting malformed JSON block curation. (+5 more)

### Community 70 - "_marketing_hook_candidates"
Cohesion: 0.18
Nodes (13): _chat_hook_event_profile(), _chat_hook_topic_profile(), list_brand_evidence(), list_hotspot_media(), list_hotspots(), _marketing_hook_candidates(), Small deterministic parser: do not spend a model call on basic topic extraction., Return bounded, role-separated candidates. This only stores references, never… (+5 more)

### Community 71 - "hotspot_package_service.py"
Cohesion: 0.35
Nodes (12): _decode_json_value(), get_hotspot_package(), list_hotspot_signals(), Persist event-level package fields while retaining the legacy hotspot record., update_hotspot_package_metrics(), _card(), confirm_package(), get_package_detail() (+4 more)

### Community 73 - "test_upload_api.py"
Cohesion: 0.31
Nodes (11): _admin_token(), _client(), Tests for file upload API + queue attachments., Create an admin user and return a JWT token., Create a TestClient with the tmp_db patched in., test_douyin_queue_requires_video(), test_generate_fallback_returns_xhs_publishable_images(), test_legacy_xhs_queue_submission_auto_generates_images() (+3 more)

### Community 75 - "schemas.py"
Cohesion: 0.19
Nodes (9): Bounded video preprocessing and MiMo quality evaluation services., Bounded automatic-regeneration policy; automatic mode is off by default., EvaluationIssue, BaseModel, model_validator, QualityScores, Strict input and output contracts for video quality evaluation., RegenerationPlan (+1 more)

### Community 76 - "audit_eligible_hotspot_hook_pairs.py"
Cohesion: 0.27
Nodes (10): _is_same_confirmed_hotspot_event(), Validate locked Hooks before a durable task consumes model capacity., _validated_chat_video_events(), eligible_hook_pairs(), main(), List strict, renderable same-event Hook pairs without changing the library.…, Return one usable non-overlapping pair for each factual source event., _event() (+2 more)

### Community 77 - "delete_hotspot_library"
Cohesion: 0.20
Nodes (12): _delete_hotspot_assets_in_conn(), delete_hotspot_event_asset(), delete_hotspot_library(), hotspot_library_cleanup_preview(), _hotspot_library_file_paths(), _hotspot_library_scope(), Return the database rows that belong to the disposable hotspot media library. A…, Collect only local relative paths; callers validate paths before unlinking. (+4 more)

### Community 78 - "_diversify_owned_candidates"
Cohesion: 0.18
Nodes (12): _diversify_owned_candidates(), _functional_categories(), _owned_action_key(), _owned_copy_anchor(), _owned_visual_family(), 从主分类和已识别画面语义共同推导可支持的物流能力。 主分类仍约束文案不能夸大（仓库前的车不能说成已完成末端交付），但不能因此…, Return a broad, visible-action family used to avoid warehouse monotony., Prefer distinct visible actions before reusing a broad visual family. (+4 more)

### Community 81 - "test_semantic_assets_db.py"
Cohesion: 0.29
Nodes (9): _create_parent_asset(), test_asset_processing_job_tracks_progress_and_segment_listing(), test_brand_tag_is_independently_searchable_from_primary_delivery_category(), test_match_session_persists_atoms_candidates_and_feedback(), test_ocr_brand_backfill_never_changes_primary_category(), test_pending_asset_batch_excludes_running_jobs(), test_segment_primary_scene_is_not_overwritten_by_legacy_asset_category(), test_segment_tags_are_normalized_and_searchable_in_chinese() (+1 more)

### Community 82 - "test_video_generation_db.py"
Cohesion: 0.41
Nodes (10): _project_and_revision(), test_cancel_is_immediate_for_pending_and_requested_for_running(), test_expired_cancel_requested_job_recovers_as_canceled(), test_generation_job_is_idempotent_for_same_revision(), test_idempotency_key_is_isolated_between_users(), test_job_events_and_quality_report_are_decoded(), test_project_revision_is_versioned_and_returned_as_json(), test_terminal_job_allows_new_job_with_same_idempotency_key() (+2 more)

### Community 83 - "test_video_quality_schemas.py"
Cohesion: 0.35
Nodes (11): test_clean_high_score_report_passes(), test_high_issue_fails_even_with_high_score(), test_low_score_fails_even_when_model_says_passed(), test_report_rejects_score_outside_zero_to_one_hundred(), test_report_rejects_unbounded_regeneration_advice(), test_report_rejects_unknown_severity(), test_video_quality_input_defaults_are_cost_bounded(), _valid_issue() (+3 more)

### Community 84 - "publisher.py"
Cohesion: 0.20
Nodes (7): get_huimei_platform(), list_huimei_accounts(), publish_batch(), publish_via_huimei(), Publisher module - wraps huimei CLI for auto-publishing., Publish to multiple platforms concurrently., Publish content using huimei CLI.

### Community 85 - "_Scripts"
Cohesion: 0.20
Nodes (3): HTMLParser, _Scripts, test_changed_asset_pages_have_valid_javascript()

### Community 86 - "test_registry_dispatch.py"
Cohesion: 0.24
Nodes (5): _AccountDummy, _Dummy, test_dispatch_loads_ready_account_from_database(), test_dispatch_routes_to_registered_adapter(), test_dispatch_unknown_platform()

### Community 88 - "config_routes.py"
Cohesion: 0.36
Nodes (7): set_api_key(), post, Runtime configuration routes., 保存通知告警配置（写入环境变量，运行时生效）。, save_notification_config(), set_api_key_endpoint(), set_dashscope_key()

### Community 89 - "create_semantic_match"
Cohesion: 0.25
Nodes (8): create_semantic_match(), select_semantic_match(), add_match_feedback(), create_match_session(), create_semantic_atom(), get_match_session(), replace_match_candidates(), update_semantic_atom_selection()

### Community 90 - "manifest.json"
Cohesion: 0.25
Nodes (7): background_color, display, icons, name, short_name, start_url, theme_color

### Community 91 - "test_editor_transfer.py"
Cohesion: 0.46
Nodes (7): _run_transfer(), test_build_draft_keeps_clicked_output_when_an_earlier_output_is_invalid(), test_build_draft_preserves_all_platforms_and_active_selection(), test_build_draft_preserves_generated_media_assets(), test_normalize_draft_falls_back_to_legacy_when_v2_contents_are_invalid(), test_normalize_draft_filters_unknown_and_keeps_distinct_platform_content(), test_normalize_draft_rejects_empty_payload_without_consuming_it()

### Community 92 - "test_sample_harness.py"
Cohesion: 0.46
Nodes (7): _package(), test_missing_brand_evidence_removes_performance_promises(), test_sample_bundle_api_returns_persisted_bundle(), test_three_samples_share_claim_ids_but_use_distinct_structures(), test_video_material_status_ready_only_when_both_libraries_have_candidates(), test_video_sample_writes_selected_segment_boundaries(), test_weak_candidate_is_not_treated_as_a_good_scene_match()

### Community 93 - "test_user_tenancy.py"
Cohesion: 0.43
Nodes (7): Registration and per-user account/content isolation., _signup_login(), test_dashboard_team_metrics_are_auditable_and_role_scoped(), test_one_wechat_article_can_route_to_multiple_owned_accounts(), test_queue_and_dashboard_are_scoped_to_creator(), test_signup_is_editor_and_accounts_are_isolated(), test_truth_gate_blocks_current_event_until_evidence_is_mapped()

### Community 94 - "rank_hook_clips"
Cohesion: 0.43
Nodes (5): rank_hook_clips(), Deterministic Hook ranking for pre-analysed hotspot event clips., Return explainable 5–12 second hook candidates; no model and no user picking., _text(), test_rank_hook_clips_prefers_short_traffic_event_with_explanation()

### Community 95 - "test_hotspot_media_db.py"
Cohesion: 0.52
Nodes (6): _hotspot(), test_changed_hotspot_snapshot_invalidates_cached_translation(), test_hotspot_media_rights_confirmation_is_independent_from_download(), test_hotspot_media_round_trip_deduplicates_by_hotspot_and_url(), test_hotspot_translation_cache_round_trip(), test_init_db_migrates_existing_hotspot_media_before_creating_authorization_index()

### Community 96 - "Platform"
Cohesion: 0.47
Nodes (6): Enum, AccountStatus, ContentStatus, Platform, 内容审批状态机: draft → pending_review → approved → queued → published, str

### Community 97 - "dump_topic_docs.py"
Cohesion: 0.53
Nodes (5): dump_docx(), dump_xlsx(), main(), Path, Dump the seller-topic source documents so the real user-side topics can be read…

### Community 99 - "page_routes.py"
Cohesion: 0.40
Nodes (4): create_router(), APIRouter, Path, Static HTML page routes.

### Community 100 - "configure_minimax_text_routes.py"
Cohesion: 0.60
Nodes (4): main(), Configure MiniMax Token Plan for the two text-only content-decision roles. Run…, _route(), _verify()

### Community 101 - "draw_truck"
Cohesion: 0.50
Nodes (4): draw_truck(), main(), Image, Draw a flat, modern truck silhouette on the given image.

### Community 102 - "editor-transfer.js"
Cohesion: 0.70
Nodes (4): buildDraft(), content(), normalizeDraft(), tags()

### Community 103 - "test_chat_video_task_orchestration.py"
Cohesion: 0.70
Nodes (4): _client_with_hook(), test_chat_video_request_returns_job_id_and_does_not_call_the_model(), test_chat_video_worker_creates_project_only_after_background_planning(), test_expired_chat_task_is_recoverable_after_worker_restart()

### Community 104 - "asset_reference_reasons"
Cohesion: 0.50
Nodes (4): asset_is_referenced(), asset_reference_reasons(), _json_reference_exists(), Return every known business reference before a destructive asset operation.

### Community 105 - "_ensure_column"
Cohesion: 0.50
Nodes (4): _ensure_column(), Return the set of column names for a given table., Add a column to a table if it doesn't exist., _table_columns()

### Community 110 - "test_reprocess_hotspot_hook_source.py"
Cohesion: 0.83
Nodes (3): _module(), test_legacy_batch_selects_only_confirmed_hooks_without_event_identity(), test_recuration_accepts_only_object_shaped_legacy_intake_metadata()

## Knowledge Gaps
- **12 isolated node(s):** `run_douyin_tests.sh script`, `start.sh script`, `NAV_ITEMS`, `NAV_ICONS`, `VIDEO_STAGE_LABELS` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `plan_followup_scenes()` connect `plan_followup_scenes` to `_generate_topic_brief_video`, `init_db`, `test_topic_briefs.py`, `test_video_duration_budget.py`, `_diversify_owned_candidates`, `hotspot_video_planner.py`, `ValueError`, `build_brief`, `source_usage_report`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Why does `RpaAdapter` connect `rpa_base.py` to `DouyinAdapter`, `app.py`, `PublishResult`, `XiaohongshuAdapter`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Why does `filter_reachable_image_candidates()` connect `hotspot_media.py` to `_run_hotspot_media_materialization`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Are the 60 inferred relationships involving `ValueError` (e.g. with `_enforce_formal_scene_copy_contract()` and `_extend_short_formal_voiceovers()`) actually correct?**
  _`ValueError` has 60 INFERRED edges - model-reasoned connections that need verification._
- **What connects `run_douyin_tests.sh script`, `start.sh script`, `NAV_ITEMS` to the rest of the system?**
  _12 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `database.py` be split into smaller, more focused modules?**
  _Cohesion score 0.03463268365817092 - nodes in this community are weakly interconnected._
- **Should `test_ai_chat_platforms.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05405405405405406 - nodes in this community are weakly interconnected._
