"""SQLite database for SA-LogiFlow v3.0."""
import sqlite3
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "logiflow.db"
HOTSPOT_MEDIA_AUTHORIZATION_STATUSES = {"authorized", "pending_review", "blocked"}
_LEGACY_RIGHTS_TO_AUTHORIZATION = {"green": "authorized", "yellow": "pending_review", "red": "blocked"}
_AUTHORIZATION_TO_LEGACY_RIGHTS = {value: key for key, value in _LEGACY_RIGHTS_TO_AUTHORIZATION.items()}


@contextmanager
def get_conn():
    """Context manager: auto-commit on success, rollback on error."""
    DB_PATH.parent.mkdir(exist_ok=True)
    # WAL is a database-wide persistence setting, not a per-request setting.
    # Re-applying journal_mode=WAL for every short read/write can itself wait on
    # a writer lock, which made the old synchronous chat-to-video handoff look
    # stalled after the model had already returned.
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn, table: str) -> set[str]:
    """Return the set of column names for a given table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(conn, table: str, column: str, col_def: str):
    """Add a column to a table if it doesn't exist."""
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        logger.info("数据库迁移: %s.%s 已添加", table, column)


def init_db():
    with get_conn() as conn:
        # Set once during bootstrap/migration.  Do not move this back into
        # get_conn(): it requires a database-wide journal lock.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'editor',
                display_name TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                last_login TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                name TEXT NOT NULL,
                account_id TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'active',
                config_summary TEXT DEFAULT '',
                credentials TEXT DEFAULT '{}',
                last_sync TEXT,
                owner_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
                ,FOREIGN KEY (owner_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                platform TEXT NOT NULL,
                hashtags TEXT DEFAULT '[]',
                status TEXT DEFAULT 'draft',
                scheduled_at TEXT,
                error_msg TEXT,
                created_by INTEGER,
                reviewer_id INTEGER,
                review_note TEXT,
                reviewed_at TEXT,
                retry_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id),
                FOREIGN KEY (reviewer_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS publish_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id INTEGER,
                platform TEXT,
                title TEXT,
                status TEXT,
                error_msg TEXT,
                published_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (queue_id) REFERENCES queue(id)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                target TEXT,
                detail TEXT,
                ip TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS kb_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS kb_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_type TEXT DEFAULT 'text',
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (category_id) REFERENCES kb_categories(id)
            );

            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                content TEXT NOT NULL,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                file_type TEXT NOT NULL,
                category TEXT DEFAULT 'other',
                duration REAL,
                width INTEGER,
                height INTEGER,
                size INTEGER NOT NULL,
                thumbnail TEXT,
                sha256 TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL DEFAULT 'upload',
                status TEXT NOT NULL DEFAULT 'active',
                retention_class TEXT NOT NULL DEFAULT 'permanent',
                last_used_at TEXT,
                pinned_at TEXT,
                purge_after TEXT,
                file_status TEXT NOT NULL DEFAULT 'available',
                purged_at TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS video_render_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT DEFAULT '等待渲染',
                progress INTEGER DEFAULT 0,
                script TEXT NOT NULL,
                voice TEXT NOT NULL,
                output_path TEXT,
                clips TEXT DEFAULT '[]',
                quality_report TEXT DEFAULT '{}',
                error TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS video_projects (
                id TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_snapshot TEXT NOT NULL DEFAULT '{}',
                title TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT 'douyin',
                target_duration_ms INTEGER NOT NULL DEFAULT 30000,
                target_orientation TEXT NOT NULL DEFAULT 'portrait',
                status TEXT NOT NULL DEFAULT 'draft',
                current_revision_id TEXT,
                active_job_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS video_project_revisions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                revision_no INTEGER NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_by INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, revision_no),
                FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS video_generation_jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                cancel_requested_at TEXT,
                canceled_at TEXT,
                error_code TEXT,
                error_message TEXT,
                preview_path TEXT,
                output_path TEXT,
                output_pinned_at TEXT,
                output_purged_at TEXT,
                quality_report TEXT NOT NULL DEFAULT '{}',
                attempt INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE,
                FOREIGN KEY (revision_id) REFERENCES video_project_revisions(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_video_generation_active_key
            ON video_generation_jobs(created_by,idempotency_key)
            WHERE status IN ('pending','running','needs_review','cancel_requested');

            CREATE TABLE IF NOT EXISTS video_generation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (job_id) REFERENCES video_generation_jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS hotspots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                title_zh TEXT,
                summary_zh TEXT,
                translation_status TEXT NOT NULL DEFAULT 'pending',
                translation_snapshot_sha256 TEXT,
                translated_at TEXT,
                translation_model TEXT,
                source_url TEXT NOT NULL UNIQUE,
                publisher TEXT NOT NULL,
                published_at TEXT,
                retrieved_at TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                image_candidate_url TEXT,
                asset_id INTEGER,
                status TEXT NOT NULL DEFAULT 'new',
                heat_score REAL NOT NULL DEFAULT 0,
                heat_state TEXT NOT NULL DEFAULT 'unconfirmed',
                event_type TEXT NOT NULL DEFAULT 'unknown',
                locations_json TEXT NOT NULL DEFAULT '[]',
                entities_json TEXT NOT NULL DEFAULT '[]',
                signal_count INTEGER NOT NULL DEFAULT 0,
                media_count INTEGER NOT NULL DEFAULT 0,
                logistics_relevance REAL NOT NULL DEFAULT 0,
                package_status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (asset_id) REFERENCES assets(id)
            );

            CREATE TABLE IF NOT EXISTS hotspot_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotspot_id INTEGER NOT NULL,
                source_name TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                retrieved_at TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                raw_payload_json TEXT NOT NULL DEFAULT '{}',
                cluster_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source_type, external_id),
                FOREIGN KEY (hotspot_id) REFERENCES hotspots(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_hotspot_signals_hotspot_published
            ON hotspot_signals(hotspot_id, published_at);

            CREATE TABLE IF NOT EXISTS hotspot_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotspot_id INTEGER NOT NULL,
                media_kind TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'direct',
                platform_media_id TEXT,
                source_page_url TEXT NOT NULL,
                original_media_url TEXT NOT NULL,
                embed_url TEXT,
                thumbnail_url TEXT,
                local_path TEXT,
                publisher TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                mime_type TEXT,
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                intake_title TEXT NOT NULL DEFAULT '',
                intake_summary TEXT NOT NULL DEFAULT '',
                intake_metadata_status TEXT NOT NULL DEFAULT 'pending',
                intake_metadata_checked_at TEXT,
                intake_decision_json TEXT,
                authorization_status TEXT NOT NULL DEFAULT 'pending_review',
                rights_tier TEXT NOT NULL DEFAULT 'yellow',
                rights_note TEXT NOT NULL DEFAULT '',
                license_name TEXT,
                rights_evidence_url TEXT,
                attribution TEXT,
                download_status TEXT NOT NULL DEFAULT 'discovered',
                download_progress INTEGER NOT NULL DEFAULT 0,
                progress_detail TEXT,
                processing_status TEXT NOT NULL DEFAULT 'not_started',
                error_message TEXT,
                sha256 TEXT,
                asset_id INTEGER,
                confirmed_by INTEGER,
                confirmed_at TEXT,
                lifecycle_status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(hotspot_id, original_media_url),
                FOREIGN KEY (hotspot_id) REFERENCES hotspots(id) ON DELETE CASCADE,
                FOREIGN KEY (asset_id) REFERENCES assets(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hotspot_media_filters
            ON hotspot_media(hotspot_id,media_kind,rights_tier,created_at);

            CREATE TABLE IF NOT EXISTS hotspot_discovery_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                topic_key TEXT NOT NULL UNIQUE,
                requested_by INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT,
                error_message TEXT,
                matched_media_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (requested_by) REFERENCES users(id),
                FOREIGN KEY (matched_media_id) REFERENCES hotspot_media(id)
            );

            CREATE INDEX IF NOT EXISTS idx_hotspot_discovery_requests_status
            ON hotspot_discovery_requests(status,created_at);

            CREATE TABLE IF NOT EXISTS hotspot_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                feed_url TEXT NOT NULL UNIQUE,
                allowed_domains TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS hotspot_fetch_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'running',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_by INTEGER,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS brand_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim TEXT NOT NULL,
                evidence_note TEXT NOT NULL,
                disclosure_level TEXT NOT NULL DEFAULT 'public',
                status TEXT NOT NULL DEFAULT 'draft',
                confirmed_by INTEGER,
                confirmed_at TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (confirmed_by) REFERENCES users(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS evidence_packages (
                id TEXT PRIMARY KEY,
                hotspot_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (hotspot_id) REFERENCES hotspots(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS evidence_claims (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                source_url TEXT,
                source_title TEXT,
                publisher TEXT,
                excerpt TEXT,
                published_at TEXT,
                retrieved_at TEXT,
                brand_evidence_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (package_id) REFERENCES evidence_packages(id) ON DELETE CASCADE,
                FOREIGN KEY (brand_evidence_id) REFERENCES brand_evidence(id)
            );

            CREATE TABLE IF NOT EXISTS topic_briefs (
                id TEXT PRIMARY KEY,
                raw_input TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                audience TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                angle TEXT NOT NULL DEFAULT '',
                locations_json TEXT NOT NULL DEFAULT '[]',
                logistics_nodes_json TEXT NOT NULL DEFAULT '[]',
                freshness_mode TEXT NOT NULL DEFAULT 'recent_or_evergreen',
                time_window_days INTEGER NOT NULL DEFAULT 7,
                platforms_json TEXT NOT NULL DEFAULT '["douyin"]',
                content_form TEXT NOT NULL DEFAULT 'video',
                must_include_json TEXT NOT NULL DEFAULT '[]',
                must_avoid_json TEXT NOT NULL DEFAULT '[]',
                source_hotspot_package_id INTEGER,
                status TEXT NOT NULL DEFAULT 'draft',
                created_by INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (source_hotspot_package_id) REFERENCES hotspots(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS topic_evidence_items (
                id TEXT PRIMARY KEY,
                topic_brief_id TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                content_role TEXT NOT NULL,
                relevance_score REAL NOT NULL DEFAULT 0,
                match_reason TEXT NOT NULL DEFAULT '',
                rights_status TEXT NOT NULL DEFAULT 'unknown',
                selected INTEGER NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(topic_brief_id, evidence_type, source_id, content_role),
                FOREIGN KEY (topic_brief_id) REFERENCES topic_briefs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_topic_evidence_brief_selected
            ON topic_evidence_items(topic_brief_id, selected, evidence_type);

            CREATE TABLE IF NOT EXISTS model_role_configs (
                role TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key_env TEXT NOT NULL,
                model TEXT NOT NULL,
                capabilities TEXT NOT NULL DEFAULT '[]',
                timeout INTEGER NOT NULL DEFAULT 30,
                max_tokens INTEGER NOT NULL DEFAULT 1200,
                cost_profile TEXT NOT NULL DEFAULT 'low',
                request_options TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS model_budgets (
                job_id TEXT PRIMARY KEY,
                max_calls INTEGER NOT NULL,
                max_input_tokens INTEGER NOT NULL,
                max_output_tokens INTEGER NOT NULL,
                calls_used INTEGER NOT NULL DEFAULT 0,
                input_tokens_used INTEGER NOT NULL DEFAULT 0,
                output_tokens_used INTEGER NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS model_call_cache (
                cache_key TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                model TEXT NOT NULL,
                response_json TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                last_accessed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS model_call_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                role TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0,
                cache_hit INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (job_id) REFERENCES model_budgets(job_id)
            );

            CREATE TABLE IF NOT EXISTS sample_bundles (
                id TEXT PRIMARY KEY,
                evidence_package_id TEXT NOT NULL,
                status TEXT NOT NULL,
                publish_allowed INTEGER NOT NULL DEFAULT 0,
                quality_issues TEXT NOT NULL DEFAULT '[]',
                video_json TEXT NOT NULL,
                carousel_json TEXT NOT NULL,
                wechat_json TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                preview_path TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (evidence_package_id) REFERENCES evidence_packages(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS asset_processing_jobs (
                id TEXT PRIMARY KEY,
                asset_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting',
                progress INTEGER NOT NULL DEFAULT 0,
                processing_version TEXT NOT NULL DEFAULT 'v1',
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                requested_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (asset_id) REFERENCES assets(id)
            );

            CREATE TABLE IF NOT EXISTS local_asset_import_jobs (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting',
                total INTEGER NOT NULL DEFAULT 0,
                scanned INTEGER NOT NULL DEFAULT 0,
                imported INTEGER NOT NULL DEFAULT 0,
                duplicated INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                current_file TEXT NOT NULL DEFAULT '',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                errors TEXT NOT NULL DEFAULT '[]',
                requested_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                updated_at TEXT DEFAULT (datetime('now')),
                finished_at TEXT,
                FOREIGN KEY (requested_by) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_local_asset_import_jobs_active
            ON local_asset_import_jobs(requested_by,root_path,status);

            CREATE TABLE IF NOT EXISTS asset_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                segment_index INTEGER NOT NULL,
                start_ms INTEGER NOT NULL DEFAULT 0,
                end_ms INTEGER NOT NULL DEFAULT 0,
                preview_path TEXT,
                thumbnail_path TEXT,
                transcript TEXT NOT NULL DEFAULT '',
                ocr_text TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                primary_category TEXT,
                primary_category_source TEXT NOT NULL DEFAULT 'legacy',
                quality_score REAL NOT NULL DEFAULT 0,
                orientation TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL DEFAULT 'active',
                processing_version TEXT NOT NULL DEFAULT 'v1',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(asset_id, segment_index, processing_version),
                FOREIGN KEY (asset_id) REFERENCES assets(id)
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                display_value TEXT NOT NULL,
                UNIQUE(dimension, normalized_value)
            );

            CREATE TABLE IF NOT EXISTS segment_tags (
                segment_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'rule',
                confirmed INTEGER NOT NULL DEFAULT 0,
                updated_by INTEGER,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (segment_id, tag_id),
                FOREIGN KEY (segment_id) REFERENCES asset_segments(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inspiration_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_role TEXT NOT NULL DEFAULT 'creative_reference',
                source_url TEXT NOT NULL,
                canonical_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                thumbnail_url TEXT,
                media_kind TEXT NOT NULL DEFAULT 'link',
                primary_category TEXT,
                rights_status TEXT NOT NULL DEFAULT 'unknown',
                license_name TEXT,
                attribution TEXT,
                rights_evidence_url TEXT,
                rights_confirmed_by INTEGER,
                rights_confirmed_at TEXT,
                materialization_status TEXT NOT NULL DEFAULT 'reference_only',
                asset_id INTEGER,
                hotspot_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (asset_id) REFERENCES assets(id),
                FOREIGN KEY (hotspot_id) REFERENCES hotspots(id)
            );

            CREATE TABLE IF NOT EXISTS match_sessions (
                id TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                source_payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS semantic_atoms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                text TEXT NOT NULL,
                semantics TEXT NOT NULL DEFAULT '{}',
                duration_ms INTEGER NOT NULL DEFAULT 0,
                constraints TEXT NOT NULL DEFAULT '{}',
                selected_segment_id INTEGER,
                locked INTEGER NOT NULL DEFAULT 0,
                review_confirmed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(session_id, position),
                FOREIGN KEY (session_id) REFERENCES match_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (selected_segment_id) REFERENCES asset_segments(id)
            );

            CREATE TABLE IF NOT EXISTS match_candidates (
                atom_id INTEGER NOT NULL,
                segment_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                match_score REAL NOT NULL,
                reasons TEXT NOT NULL DEFAULT '[]',
                review_required INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (atom_id, segment_id),
                FOREIGN KEY (atom_id) REFERENCES semantic_atoms(id) ON DELETE CASCADE,
                FOREIGN KEY (segment_id) REFERENCES asset_segments(id)
            );

            CREATE TABLE IF NOT EXISTS match_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                atom_id INTEGER NOT NULL,
                segment_id INTEGER,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES match_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (atom_id) REFERENCES semantic_atoms(id) ON DELETE CASCADE,
                FOREIGN KEY (segment_id) REFERENCES asset_segments(id)
            );

            CREATE TABLE IF NOT EXISTS segment_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment_id INTEGER NOT NULL,
                render_job_id TEXT,
                session_id TEXT,
                used_by INTEGER,
                used_at TEXT DEFAULT (datetime('now')),
                UNIQUE(segment_id, render_job_id),
                FOREIGN KEY (segment_id) REFERENCES asset_segments(id),
                FOREIGN KEY (render_job_id) REFERENCES video_render_jobs(id),
                FOREIGN KEY (session_id) REFERENCES match_sessions(id)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS asset_segment_fts
            USING fts5(segment_id UNINDEXED, content, tokenize='trigram');

            CREATE TABLE IF NOT EXISTS hotspot_event_clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                hotspot_id INTEGER NOT NULL,
                event_index INTEGER NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                title_zh TEXT NOT NULL,
                title_en TEXT NOT NULL,
                location TEXT,
                entities_json TEXT NOT NULL DEFAULT '[]',
                keywords_json TEXT NOT NULL DEFAULT '[]',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'review_required',
                virtual_asset_id TEXT,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                thumbnail_path TEXT,
                clip_path TEXT,
                clip_status TEXT NOT NULL DEFAULT 'pending',
                clip_error TEXT,
                library_origin TEXT NOT NULL DEFAULT 'hotspot_event',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
                FOREIGN KEY (hotspot_id) REFERENCES hotspots(id) ON DELETE CASCADE,
                UNIQUE(asset_id, event_index)
            );

            CREATE TABLE IF NOT EXISTS hotspot_event_segment_links (
                event_clip_id INTEGER NOT NULL,
                segment_id INTEGER NOT NULL,
                sequence_no INTEGER NOT NULL,
                PRIMARY KEY (event_clip_id, segment_id),
                FOREIGN KEY (event_clip_id) REFERENCES hotspot_event_clips(id) ON DELETE CASCADE,
                FOREIGN KEY (segment_id) REFERENCES asset_segments(id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS inspiration_fts
            USING fts5(inspiration_id UNINDEXED, content, tokenize='trigram');
        """)
        # 迁移旧的全局幂等索引：相同客户端键只能约束同一用户，不能跨租户串任务。
        conn.execute("DROP INDEX IF EXISTS uq_video_generation_active_key")
        conn.execute(
            """CREATE UNIQUE INDEX uq_video_generation_active_key
               ON video_generation_jobs(created_by,idempotency_key)
               WHERE status IN ('pending','running','needs_review','cancel_requested')"""
        )
        # 迁移：为旧数据库添加缺失的列
        _ensure_column(conn, "queue", "created_by", "INTEGER")
        _ensure_column(conn, "queue", "reviewer_id", "INTEGER")
        _ensure_column(conn, "queue", "review_note", "TEXT")
        _ensure_column(conn, "queue", "reviewed_at", "TEXT")
        _ensure_column(conn, "queue", "retry_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "accounts", "credentials", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "accounts", "owner_id", "INTEGER")
        _ensure_column(conn, "model_call_cache", "last_accessed_at", "TEXT")
        _ensure_column(conn, "queue", "attachments", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "queue", "source_refs", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "queue", "verification_status", "TEXT DEFAULT 'not_checked'")
        _ensure_column(conn, "queue", "target_account_id", "INTEGER")
        _ensure_column(conn, "assets", "source_url", "TEXT")
        _ensure_column(conn, "assets", "license", "TEXT")
        _ensure_column(conn, "assets", "attribution", "TEXT")
        _ensure_column(conn, "assets", "hotspot_id", "INTEGER")
        _ensure_column(conn, "assets", "primary_category", "TEXT")
        _ensure_column(conn, "assets", "primary_category_source", "TEXT NOT NULL DEFAULT 'legacy'")
        _ensure_column(conn, "asset_segments", "primary_category_source", "TEXT NOT NULL DEFAULT 'legacy'")
        _ensure_column(conn, "assets", "rights_status", "TEXT DEFAULT 'unknown'")
        _ensure_column(conn, "assets", "event_at", "TEXT")
        _ensure_column(conn, "assets", "expires_at", "TEXT")
        _ensure_column(conn, "assets", "processing_status", "TEXT DEFAULT 'pending'")
        _ensure_column(conn, "assets", "processing_version", "TEXT")
        _ensure_column(conn, "assets", "retention_class", "TEXT NOT NULL DEFAULT 'permanent'")
        _ensure_column(conn, "assets", "last_used_at", "TEXT")
        _ensure_column(conn, "assets", "pinned_at", "TEXT")
        _ensure_column(conn, "assets", "purge_after", "TEXT")
        _ensure_column(conn, "assets", "file_status", "TEXT NOT NULL DEFAULT 'available'")
        _ensure_column(conn, "assets", "purged_at", "TEXT")
        _ensure_column(conn, "hotspots", "title_zh", "TEXT")
        _ensure_column(conn, "hotspots", "summary_zh", "TEXT")
        _ensure_column(conn, "hotspots", "translation_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "hotspots", "translation_snapshot_sha256", "TEXT")
        _ensure_column(conn, "hotspots", "translated_at", "TEXT")
        _ensure_column(conn, "hotspots", "translation_model", "TEXT")
        for name, definition in {
            "heat_score": "REAL NOT NULL DEFAULT 0",
            "heat_state": "TEXT NOT NULL DEFAULT 'unconfirmed'",
            "event_type": "TEXT NOT NULL DEFAULT 'unknown'",
            "locations_json": "TEXT NOT NULL DEFAULT '[]'",
            "entities_json": "TEXT NOT NULL DEFAULT '[]'",
            "signal_count": "INTEGER NOT NULL DEFAULT 0",
            "media_count": "INTEGER NOT NULL DEFAULT 0",
            "logistics_relevance": "REAL NOT NULL DEFAULT 0",
            "package_status": "TEXT NOT NULL DEFAULT 'new'",
        }.items():
            _ensure_column(conn, "hotspots", name, definition)
        _ensure_column(conn, "hotspot_media", "lifecycle_status", "TEXT NOT NULL DEFAULT 'active'")
        _ensure_column(conn, "hotspot_media", "download_progress", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "hotspot_media", "progress_detail", "TEXT")
        _ensure_column(conn, "hotspot_event_clips", "virtual_asset_id", "TEXT")
        _ensure_column(conn, "hotspot_event_clips", "duration_ms", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "hotspot_event_clips", "thumbnail_path", "TEXT")
        _ensure_column(conn, "hotspot_event_clips", "clip_path", "TEXT")
        _ensure_column(conn, "hotspot_event_clips", "clip_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "hotspot_event_clips", "clip_error", "TEXT")
        _ensure_column(conn, "hotspot_event_clips", "library_origin", "TEXT NOT NULL DEFAULT 'hotspot_event'")
        _ensure_column(conn, "hotspot_event_clips", "hook_kind", "TEXT NOT NULL DEFAULT 'timely_event'")
        _ensure_column(conn, "hotspot_event_clips", "logistics_scenes_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "model_role_configs", "request_options", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "hotspot_media", "intake_title", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "hotspot_media", "intake_summary", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "hotspot_media", "intake_metadata_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "hotspot_media", "intake_metadata_checked_at", "TEXT")
        _ensure_column(conn, "hotspot_media", "intake_decision_json", "TEXT")
        _ensure_column(conn, "hotspot_media", "authorization_status", "TEXT NOT NULL DEFAULT 'pending_review'")
        _ensure_column(conn, "hotspot_discovery_requests", "stage", "TEXT")
        _ensure_column(conn, "hotspot_discovery_requests", "error_message", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hotspot_media_authorization "
            "ON hotspot_media(authorization_status,lifecycle_status,created_at)"
        )
        # Existing rows used red/yellow/green labels.  Migrate their meaning once
        # into an explicit processing authorization state; the old column remains
        # only to keep historical exports and old integrations readable.
        conn.execute(
            """UPDATE hotspot_media
               SET authorization_status=CASE rights_tier
                   WHEN 'green' THEN 'authorized'
                   WHEN 'red' THEN 'blocked'
                   ELSE 'pending_review' END
               WHERE authorization_status IS NULL OR authorization_status='' OR authorization_status='pending_review'"""
        )
        _ensure_column(conn, "video_render_jobs", "clips", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "video_render_jobs", "quality_report", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "video_generation_jobs", "output_pinned_at", "TEXT")
        _ensure_column(conn, "video_generation_jobs", "output_purged_at", "TEXT")
        _ensure_column(conn, "sample_bundles", "preview_path", "TEXT")
        _seed_defaults(conn)
    logger.info("数据库初始化完成: %s", DB_PATH)


def _seed_defaults(conn):
    """首次初始化时写入默认知识库分类与 Prompt 模板。"""
    if conn.execute("SELECT COUNT(*) FROM kb_categories").fetchone()[0] == 0:
        default_cats = [
            ("公司介绍", "企业背景、团队、资质"),
            ("清关知识", "行业专业知识、清关税务"),
            ("成功案例", "客户案例、复盘"),
            ("市场资讯", "行业新闻、政策动态"),
            ("产品资料", "服务介绍、产品说明"),
            ("品牌规范", "品牌表达方式、话术规范"),
        ]
        conn.executemany("INSERT INTO kb_categories (name, description) VALUES (?,?)", default_cats)
        logger.info("已写入 %d 个默认知识库分类", len(default_cats))

    if conn.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0] == 0:
        default_tpls = [
            ("港口资讯", "资讯", "围绕南非主要港口（德班/开普敦）的最新动态、拥堵、船期，生成简洁专业的资讯，突出时效与应对建议。"),
            ("客户案例", "案例", "以真实客户故事为线索，讲述我们如何帮助卖家解决物流难题，强调结果与数据，结尾引导咨询。"),
            ("行业热点", "热点", "结合当前跨境物流行业热点话题，输出有观点、有价值的解读，避免空话，给出可执行建议。"),
            ("节日营销", "营销", "围绕节日/大促节点，生成有节日氛围的营销文案，突出物流保障与限时优惠，激发行动。"),
            ("公司新闻", "公司", "以官方口吻发布公司新闻（新航线/合作/获奖等），专业可信，体现公司实力与价值。"),
        ]
        conn.executemany("INSERT INTO prompt_templates (name, category, content) VALUES (?,?,?)", default_tpls)
        logger.info("已写入 %d 个默认 Prompt 模板", len(default_tpls))


# ==================== Users ====================

def get_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(username: str, password_hash: str, role: str = "editor", display_name: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name) VALUES (?,?,?,?)",
            (username, password_hash, role, display_name),
        )
        return cur.lastrowid


def get_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id, username, role, display_name, status, last_login, created_at FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def update_user_last_login(user_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().strftime("%Y-%m-%d %H:%M"), user_id))


def update_user_status(user_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))


# ==================== Accounts ====================

def _decode_account(row) -> dict | None:
    if not row:
        return None
    import credential_store
    result = dict(row)
    result["credentials"] = credential_store.decrypt_credentials(result.get("credentials") or "{}")
    return result


def get_accounts(platform: str = None, owner_id: int = None):
    with get_conn() as conn:
        query, params = "SELECT * FROM accounts WHERE 1=1", []
        if platform:
            query += " AND platform=?"
            params.append(platform)
        if owner_id is not None:
            query += " AND owner_id=?"
            params.append(owner_id)
        rows = conn.execute(query + " ORDER BY id", params).fetchall()
        return [_decode_account(r) for r in rows]


def create_account(platform, name, account_id, config_summary="", credentials="{}", owner_id=None):
    import credential_store
    stored = credential_store.encrypt_credentials(credentials or "{}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (platform, name, account_id, status, config_summary, credentials, last_sync, owner_id) VALUES (?,?,?,?,?,?,?,?)",
            (platform, name, account_id, "active", config_summary, stored, datetime.now().strftime("%Y-%m-%d %H:%M"), owner_id),
        )


def get_account(account_pk: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_pk,)).fetchone()
        return _decode_account(row)


def delete_account(account_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))


def update_account_status(account_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET status=? WHERE id=?", (status, account_id))


def update_account_credentials(account_id: str, credentials: str):
    """按业务 account_id（非自增主键）更新凭据 JSON，并刷新 last_sync。"""
    import credential_store
    stored = credential_store.encrypt_credentials(credentials or "{}")
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET credentials=?, last_sync=? WHERE account_id=?",
            (stored, datetime.now().strftime("%Y-%m-%d %H:%M"), account_id),
        )


# ==================== Queue ====================

def get_queue(status: str = None, platform: str = None, created_by: int = None):
    with get_conn() as conn:
        query = "SELECT * FROM queue WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if platform:
            query += " AND platform=?"
            params.append(platform)
        if created_by is not None:
            query += " AND created_by=?"
            params.append(created_by)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_parse_queue_row(r) for r in rows]


def get_queue_item_by_id(item_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM queue WHERE id=?", (item_id,)).fetchone()
        return _parse_queue_row(row) if row else None


def add_to_queue(title, body, platform, hashtags=None, scheduled_at=None, status="draft", created_by=None, attachments=None, source_refs=None, verification_status="not_checked", target_account_id=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO queue (title, body, platform, hashtags, status, scheduled_at, created_by, attachments, source_refs, verification_status, target_account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (title, body, platform, json.dumps(hashtags or []), status, scheduled_at, created_by, json.dumps(attachments or [], ensure_ascii=False), json.dumps(source_refs or [], ensure_ascii=False), verification_status, target_account_id),
        )


def update_queue_evidence(item_id: int, source_refs: list[dict], verification_status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue SET source_refs=?, verification_status=? WHERE id=?",
            (json.dumps(source_refs or [], ensure_ascii=False), verification_status, item_id),
        )


_UNSET = object()

def update_queue_status(item_id, status, error_msg=None, scheduled_at=_UNSET):
    with get_conn() as conn:
        if scheduled_at is _UNSET:
            conn.execute("UPDATE queue SET status=?, error_msg=? WHERE id=?", (status, error_msg, item_id))
        else:
            conn.execute("UPDATE queue SET status=?, error_msg=?, scheduled_at=? WHERE id=?", (status, error_msg, scheduled_at, item_id))


def update_queue_attachments(item_id: int, attachments: list[dict]):
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue SET attachments=? WHERE id=?",
            (json.dumps(attachments or [], ensure_ascii=False), item_id),
        )


def update_queue_review(item_id: int, reviewer_id: int, status: str, review_note: str = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue SET status=?, reviewer_id=?, review_note=?, reviewed_at=? WHERE id=?",
            (status, reviewer_id, review_note, datetime.now().strftime("%Y-%m-%d %H:%M"), item_id),
        )


def increment_retry_count(item_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE queue SET retry_count = retry_count + 1 WHERE id=?", (item_id,))


def get_retry_count(item_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT retry_count FROM queue WHERE id=?", (item_id,)).fetchone()
        return row["retry_count"] if row else 0


def delete_queue_item(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM publish_log WHERE queue_id=?", (item_id,))
        conn.execute("DELETE FROM queue WHERE id=?", (item_id,))


def get_queue_stats(created_by: int = None):
    with get_conn() as conn:
        stats = {}
        for status in ["draft", "pending_review", "approved", "queued", "published", "failed"]:
            if created_by is None:
                stats[status] = conn.execute("SELECT COUNT(*) FROM queue WHERE status=?", (status,)).fetchone()[0]
            else:
                stats[status] = conn.execute("SELECT COUNT(*) FROM queue WHERE status=? AND created_by=?", (status, created_by)).fetchone()[0]
        if created_by is None:
            stats["total"] = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        else:
            stats["total"] = conn.execute("SELECT COUNT(*) FROM queue WHERE created_by=?", (created_by,)).fetchone()[0]
        return stats


def get_recent_activity(limit=10, created_by: int = None):
    with get_conn() as conn:
        if created_by is None:
            rows = conn.execute("SELECT * FROM queue ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM queue WHERE created_by=? ORDER BY created_at DESC LIMIT ?",
                (created_by, limit),
            ).fetchall()
        return [_parse_queue_row(r) for r in rows]


def get_scheduled_items():
    """Get queued items with scheduled_at in the past."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status='queued' AND scheduled_at IS NOT NULL AND scheduled_at <= ? ORDER BY scheduled_at",
            (now,),
        ).fetchall()
        return [_parse_queue_row(r) for r in rows]


def _parse_queue_row(row) -> dict:
    d = dict(row)
    d["hashtags"] = json.loads(d.get("hashtags", "[]"))
    d["source_refs"] = json.loads(d.get("source_refs") or "[]")
    return d


# ==================== Publish Log ====================

def add_publish_log(queue_id: int, platform: str, title: str, status: str, error_msg: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO publish_log (queue_id, platform, title, status, error_msg) VALUES (?,?,?,?,?)",
            (queue_id, platform, title, status, error_msg),
        )
    logger.info("发布日志: queue_id=%d, platform=%s, status=%s", queue_id, platform, status)


def get_publish_logs(limit: int = 50, created_by: int | None = None):
    with get_conn() as conn:
        if created_by is None:
            rows = conn.execute(
                "SELECT * FROM publish_log ORDER BY published_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT pl.* FROM publish_log pl
                   JOIN queue q ON q.id = pl.queue_id
                   WHERE q.created_by=?
                   ORDER BY pl.published_at DESC LIMIT ?""",
                (created_by, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def count_published_today(platform: str) -> int:
    """今日（UTC 自然日）某平台成功发布条数。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM publish_log "
            "WHERE platform=? AND status='published' AND date(published_at)=date('now')",
            (platform,),
        ).fetchone()[0]


def minutes_since_last_publish(platform: str) -> float | None:
    """距该平台上次成功发布的分钟数；从未发布返回 None。库内 UTC 计算。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(MAX(published_at))) * 1440 AS mins "
            "FROM publish_log WHERE platform=? AND status='published'",
            (platform,),
        ).fetchone()
    return row["mins"] if row and row["mins"] is not None else None


# ==================== Audit Logs ====================

def add_audit_log(user_id: int, username: str, action: str, target: str = None, detail: str = None, ip: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_logs (user_id, username, action, target, detail, ip) VALUES (?,?,?,?,?,?)",
            (user_id, username, action, target, detail, ip),
        )


def get_audit_logs(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ==================== Dashboard Stats ====================

def get_weekly_stats(created_by: int = None) -> dict:
    """Get stats for the current week (Monday to now)."""
    from datetime import timedelta
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d 00:00")
    with get_conn() as conn:
        owner_sql = " AND created_by=?" if created_by is not None else ""
        params = (monday, created_by) if created_by is not None else (monday,)
        published = conn.execute("SELECT COUNT(*) FROM queue WHERE status='published' AND created_at >= ?" + owner_sql, params).fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM queue WHERE status='failed' AND created_at >= ?" + owner_sql, params).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM queue WHERE created_at >= ?" + owner_sql, params).fetchone()[0]
        # Most active platform
        row = conn.execute(
            "SELECT platform, COUNT(*) as cnt FROM queue WHERE created_at >= ?" + owner_sql + " GROUP BY platform ORDER BY cnt DESC LIMIT 1",
            params,
        ).fetchone()
        top_platform = row["platform"] if row else "-"
        return {"published": published, "failed": failed, "total": total, "top_platform": top_platform}


def get_team_performance(user_id: int | None = None) -> list[dict]:
    """可审计的人员产出与阻塞指标；所有数字直接来自队列和账号状态。"""
    with get_conn() as conn:
        where, params = ("WHERE u.id=?", (user_id,)) if user_id is not None else ("", ())
        rows = conn.execute(f"""
            SELECT u.id,u.username,u.display_name,
              SUM(CASE WHEN q.status='published' THEN 1 ELSE 0 END) AS published,
              SUM(CASE WHEN q.status='pending_review' THEN 1 ELSE 0 END) AS pending_review,
              SUM(CASE WHEN q.status='rejected' THEN 1 ELSE 0 END) AS rejected,
              SUM(CASE WHEN q.status='failed' THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN q.verification_status='needs_evidence' THEN 1 ELSE 0 END) AS evidence_blocked,
              COUNT(DISTINCT q.id) AS total
            FROM users u LEFT JOIN queue q ON q.created_by=u.id
            {where} GROUP BY u.id ORDER BY published DESC,total DESC,u.id
        """, params).fetchall()
        result = []
        for raw in rows:
            item = dict(raw)
            account_row = conn.execute(
                "SELECT COUNT(*) total,SUM(CASE WHEN status!='active' THEN 1 ELSE 0 END) unavailable FROM accounts WHERE owner_id=?",
                (item["id"],),
            ).fetchone()
            item["account_total"] = account_row["total"] or 0
            item["account_unavailable"] = account_row["unavailable"] or 0
            pain_points = []
            for key, label in (("evidence_blocked", "证据不足"), ("failed", "发布失败"),
                               ("rejected", "审核退回"), ("account_unavailable", "账号不可用")):
                if item[key]:
                    pain_points.append({"type": key, "label": label, "count": item[key]})
            item["pain_points"] = sorted(pain_points, key=lambda value: value["count"], reverse=True)
            result.append(item)
        return result


# ==================== Knowledge Base ====================

def get_kb_categories() -> list[dict]:
    """分类列表，附带每个分类的文档数。"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.description, c.created_at,
                   (SELECT COUNT(*) FROM kb_documents d WHERE d.category_id = c.id) AS doc_count
            FROM kb_categories c ORDER BY c.id
        """).fetchall()
        return [dict(r) for r in rows]


def create_kb_category(name: str, description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO kb_categories (name, description) VALUES (?,?)", (name, description))
        return cur.lastrowid


def delete_kb_category(cat_id: int):
    """删除分类，同时删除其下所有文档。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM kb_documents WHERE category_id=?", (cat_id,))
        conn.execute("DELETE FROM kb_categories WHERE id=?", (cat_id,))


def get_kb_documents(category_id: int = None) -> list[dict]:
    """文档列表（不含全文，只给摘要），按分类过滤。"""
    with get_conn() as conn:
        if category_id:
            rows = conn.execute(
                "SELECT id, category_id, title, source_type, created_at, updated_at, substr(content,1,80) AS preview, length(content) AS length "
                "FROM kb_documents WHERE category_id=? ORDER BY updated_at DESC", (category_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, category_id, title, source_type, created_at, updated_at, substr(content,1,80) AS preview, length(content) AS length "
                "FROM kb_documents ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_kb_document(doc_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM kb_documents WHERE id=?", (doc_id,)).fetchone()
        return dict(row) if row else None


def create_kb_document(category_id: int, title: str, content: str, source_type: str = "text", created_by: int = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO kb_documents (category_id, title, content, source_type, created_by) VALUES (?,?,?,?,?)",
            (category_id, title, content, source_type, created_by),
        )
        return cur.lastrowid


def update_kb_document(doc_id: int, title: str, content: str, category_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE kb_documents SET title=?, content=?, category_id=?, updated_at=? WHERE id=?",
            (title, content, category_id, datetime.now().strftime("%Y-%m-%d %H:%M"), doc_id),
        )


def delete_kb_document(doc_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM kb_documents WHERE id=?", (doc_id,))


def get_kb_context(category_ids: list[int], max_docs: int = 6, max_chars: int = 4000) -> str:
    """按分类取文档全文，拼成注入 prompt 的上下文（限量防止超长）。"""
    if not category_ids:
        return ""
    placeholders = ",".join("?" * len(category_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT c.name AS cat, d.title, d.content FROM kb_documents d "
            f"LEFT JOIN kb_categories c ON c.id = d.category_id "
            f"WHERE d.category_id IN ({placeholders}) ORDER BY d.updated_at DESC LIMIT ?",
            (*category_ids, max_docs),
        ).fetchall()
    if not rows:
        return ""
    parts, total = [], 0
    for r in rows:
        block = f"【{r['cat'] or '未分类'}·{r['title']}】\n{r['content']}"
        if total + len(block) > max_chars:
            block = block[: max(0, max_chars - total)]
        parts.append(block)
        total += len(block)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


# ==================== Prompt Templates ====================

def get_prompt_templates() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM prompt_templates ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_prompt_template(tpl_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM prompt_templates WHERE id=?", (tpl_id,)).fetchone()
        return dict(row) if row else None


def create_prompt_template(name: str, category: str, content: str, created_by: int = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO prompt_templates (name, category, content, created_by) VALUES (?,?,?,?)",
            (name, category, content, created_by),
        )
        return cur.lastrowid


def update_prompt_template(tpl_id: int, name: str, category: str, content: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE prompt_templates SET name=?, category=?, content=? WHERE id=?",
            (name, category, content, tpl_id),
        )


def delete_prompt_template(tpl_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM prompt_templates WHERE id=?", (tpl_id,))


# ==================== Media Assets ====================

def list_assets(file_type=None, category=None, query=None, status="active") -> list[dict]:
    with get_conn() as conn:
        sql, params = "SELECT * FROM assets WHERE 1=1", []
        if file_type:
            sql += " AND file_type=?"; params.append(file_type)
        if category:
            # 「品牌」是可见露出维度，不应和配送/仓储等主功能互斥。
            # 因此 Buffalo 配送车既可留在 delivery，也能从品牌筛选找到。
            if category == "brand":
                sql += """ AND (category='brand' OR EXISTS (
                    SELECT 1 FROM asset_segments s
                    JOIN segment_tags st ON st.segment_id=s.id
                    JOIN tags t ON t.id=st.tag_id
                    WHERE s.asset_id=assets.id AND s.status='active' AND t.dimension='brand'
                ))"""
            else:
                sql += " AND category=?"; params.append(category)
        if status:
            sql += " AND status=?"; params.append(status)
        if query:
            sql += """ AND (name LIKE ? OR EXISTS (
                SELECT 1 FROM asset_segments s
                JOIN segment_tags st ON st.segment_id=s.id
                JOIN tags t ON t.id=st.tag_id
                WHERE s.asset_id=assets.id AND s.status='active' AND t.display_value LIKE ?
            ))"""
            params.extend((f"%{query}%", f"%{query}%"))
        sql += " ORDER BY created_at DESC, id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def list_asset_brand_tags(asset_ids: list[int]) -> dict[int, list[str]]:
    """返回每个素材当前可检索的品牌露出标签，不改变主场景分类。"""
    ids = sorted({int(asset_id) for asset_id in asset_ids if asset_id is not None})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT s.asset_id,t.display_value
                 FROM asset_segments s
                 JOIN segment_tags st ON st.segment_id=s.id
                 JOIN tags t ON t.id=st.tag_id
                 WHERE s.asset_id IN ({placeholders})
                   AND s.status='active' AND t.dimension='brand'
                 ORDER BY s.asset_id,t.display_value""",
            ids,
        ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        item = dict(row)
        result.setdefault(int(item["asset_id"]), []).append(str(item["display_value"]))
    return result


def backfill_visible_brand_tags(brand: str, markers: tuple[str, ...]) -> dict:
    """从既有 OCR/描述恢复确定可见的品牌标签，不猜测、不更改主分类。"""
    clean_brand = str(brand or "").strip()
    normalized = _normalized_tag(clean_brand)
    normalized_markers = tuple(_normalized_tag(marker) for marker in markers if _normalized_tag(marker))
    if not clean_brand or not normalized or not normalized_markers:
        raise ValueError("品牌标记不能为空")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tags (dimension,normalized_value,display_value) VALUES ('brand',?,?)
               ON CONFLICT(dimension,normalized_value) DO UPDATE SET display_value=excluded.display_value""",
            (normalized, clean_brand),
        )
        tag_id = int(conn.execute(
            "SELECT id FROM tags WHERE dimension='brand' AND normalized_value=?", (normalized,)
        ).fetchone()["id"])
        rows = conn.execute(
            """SELECT s.id,s.asset_id,s.description,s.transcript,s.ocr_text
                 FROM asset_segments s JOIN assets a ON a.id=s.asset_id
                 WHERE s.status='active' AND a.status='active' AND a.hotspot_id IS NULL"""
        ).fetchall()
        matched_assets: set[int] = set()
        matched_segments = 0
        created_tags = 0
        for row in rows:
            item = dict(row)
            evidence = " ".join(str(item.get(field) or "") for field in ("description", "transcript", "ocr_text"))
            if not any(marker in _normalized_tag(evidence) for marker in normalized_markers):
                continue
            matched_segments += 1
            matched_assets.add(int(item["asset_id"]))
            exists = conn.execute(
                "SELECT 1 FROM segment_tags WHERE segment_id=? AND tag_id=?", (item["id"], tag_id)
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO segment_tags (segment_id,tag_id,confidence,source,confirmed)
                       VALUES (?,?,?,?,?)""",
                    (item["id"], tag_id, 0.84, "ocr_backfill", 0),
                )
                _refresh_segment_fts(conn, int(item["id"]))
                created_tags += 1
        return {
            "brand": clean_brand, "matched_segments": matched_segments,
            "affected_assets": len(matched_assets), "created_tags": created_tags,
        }


def get_asset(asset_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return dict(row) if row else None


def get_asset_by_hash(sha256: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM assets WHERE sha256=?", (sha256,)).fetchone()
        return dict(row) if row else None


def create_asset(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO assets
            (name,filepath,file_type,category,duration,width,height,size,thumbnail,sha256,source,status,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(data.get(key) for key in (
                "name", "filepath", "file_type", "category", "duration", "width", "height",
                "size", "thumbnail", "sha256", "source", "status", "created_by",
            )),
        )
        return cur.lastrowid


def set_asset_retention(asset_id: int, retention_class: str, retention_days: int = 7) -> dict | None:
    """为可回收素材设置生命周期；永久素材不设置到期时间。"""
    purge_after = None
    if retention_class != "permanent":
        purge_after = (datetime.now(timezone.utc) + timedelta(days=max(1, retention_days))).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE assets
               SET retention_class=?, purge_after=?, file_status='available', purged_at=NULL
               WHERE id=?""",
            (retention_class, purge_after, asset_id),
        )
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return dict(row) if row else None


def update_asset_provenance(asset_id: int, source_url: str, license_name: str, attribution: str, hotspot_id: int = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE assets SET source_url=?, license=?, attribution=?, hotspot_id=? WHERE id=?",
            (source_url, license_name, attribution, hotspot_id, asset_id),
        )


# ==================== South Africa Hotspots ====================

def upsert_hotspot(data: dict) -> tuple[int, bool]:
    with get_conn() as conn:
        row = conn.execute("SELECT id,snapshot_sha256 FROM hotspots WHERE source_url=?", (data["source_url"],)).fetchone()
        if row:
            changed = row["snapshot_sha256"] != data["snapshot_sha256"]
            conn.execute(
                """UPDATE hotspots SET title=?,summary=?,publisher=?,published_at=?,retrieved_at=?,
                   snapshot_sha256=?,image_candidate_url=?,status=?,
                   translation_status=CASE WHEN ? THEN 'stale' ELSE translation_status END
                   WHERE id=?""",
                (data["title"], data.get("summary", ""), data["publisher"], data.get("published_at"),
                 data["retrieved_at"], data["snapshot_sha256"], data.get("image_candidate_url"),
                 "updated" if changed else "unchanged", changed, row["id"]),
            )
            return row["id"], False
        cur = conn.execute(
            "INSERT INTO hotspots (title,summary,source_url,publisher,published_at,retrieved_at,snapshot_sha256,image_candidate_url,status) VALUES (?,?,?,?,?,?,?,?,?)",
            (data["title"], data.get("summary", ""), data["source_url"], data["publisher"], data.get("published_at"), data["retrieved_at"], data["snapshot_sha256"], data.get("image_candidate_url"), "new"),
        )
        return cur.lastrowid, True


def link_hotspot_asset(hotspot_id: int, asset_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE hotspots SET asset_id=?,status='ready' WHERE id=?", (asset_id, hotspot_id))


def list_hotspots(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM hotspots ORDER BY retrieved_at DESC,id DESC LIMIT ?", (limit,)).fetchall()]


def get_hotspot(hotspot_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM hotspots WHERE id=?", (hotspot_id,)).fetchone()
        return dict(row) if row else None


def _decode_json_value(value, fallback):
    try:
        decoded = json.loads(value or json.dumps(fallback))
    except (TypeError, json.JSONDecodeError):
        return fallback
    return decoded if isinstance(decoded, type(fallback)) else fallback


def update_hotspot_package_metrics(
    hotspot_id: int,
    *,
    heat_score: float,
    heat_state: str,
    event_type: str,
    logistics_relevance: float,
    locations: list[str],
    entities: list[str],
    package_status: str,
) -> dict | None:
    """Persist event-level package fields while retaining the legacy hotspot record."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspots
               SET heat_score=?, heat_state=?, event_type=?, logistics_relevance=?,
                   locations_json=?, entities_json=?, package_status=?
               WHERE id=?""",
            (
                max(0, min(float(heat_score), 100)),
                heat_state,
                event_type,
                max(0, min(float(logistics_relevance), 100)),
                json.dumps(locations or [], ensure_ascii=False),
                json.dumps(entities or [], ensure_ascii=False),
                package_status,
                hotspot_id,
            ),
        )
    return get_hotspot_package(hotspot_id)


def upsert_hotspot_signal(data: dict) -> tuple[int, bool]:
    """Store one source signal idempotently by source type and external identifier."""
    required = ("hotspot_id", "source_type", "external_id", "retrieved_at")
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise ValueError(f"热点信号缺少字段: {', '.join(missing)}")
    payload = {
        "source_name": "",
        "title": "",
        "summary": "",
        "source_url": "",
        "published_at": None,
        "metrics": {},
        "raw_payload": {},
        "cluster_status": "pending",
        **data,
    }
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM hotspot_signals WHERE source_type=? AND external_id=?",
            (payload["source_type"], payload["external_id"]),
        ).fetchone()
        values = (
            payload["hotspot_id"], payload["source_name"], payload["title"],
            payload["summary"], payload["source_url"], payload["published_at"],
            payload["retrieved_at"], json.dumps(payload["metrics"] or {}, ensure_ascii=False),
            json.dumps(payload["raw_payload"] or {}, ensure_ascii=False), payload["cluster_status"],
        )
        if row:
            conn.execute(
                """UPDATE hotspot_signals
                   SET hotspot_id=?,source_name=?,title=?,summary=?,source_url=?,published_at=?,
                       retrieved_at=?,metrics_json=?,raw_payload_json=?,cluster_status=?,
                       updated_at=datetime('now')
                   WHERE id=?""",
                values + (row["id"],),
            )
            signal_id, created = row["id"], False
        else:
            cur = conn.execute(
                """INSERT INTO hotspot_signals
                   (hotspot_id,source_name,source_type,external_id,title,summary,source_url,
                    published_at,retrieved_at,metrics_json,raw_payload_json,cluster_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["hotspot_id"], payload["source_name"], payload["source_type"],
                    payload["external_id"], payload["title"], payload["summary"],
                    payload["source_url"], payload["published_at"], payload["retrieved_at"],
                    json.dumps(payload["metrics"] or {}, ensure_ascii=False),
                    json.dumps(payload["raw_payload"] or {}, ensure_ascii=False),
                    payload["cluster_status"],
                ),
            )
            signal_id, created = cur.lastrowid, True
        conn.execute(
            """UPDATE hotspots
               SET signal_count=(SELECT COUNT(*) FROM hotspot_signals WHERE hotspot_id=hotspots.id)
               WHERE id IN (?, (SELECT hotspot_id FROM hotspot_signals WHERE id=?))""",
            (payload["hotspot_id"], signal_id),
        )
    return signal_id, created


def list_hotspot_signals(hotspot_id: int | None = None, limit: int = 200) -> list[dict]:
    query = "SELECT * FROM hotspot_signals"
    params: list = []
    if hotspot_id is not None:
        query += " WHERE hotspot_id=?"
        params.append(hotspot_id)
    params.append(max(1, min(int(limit), 500)))
    with get_conn() as conn:
        rows = conn.execute(
            query + " ORDER BY datetime(retrieved_at) DESC,id DESC LIMIT ?", params
        ).fetchall()
    signals = []
    for row in rows:
        signal = dict(row)
        signal["metrics"] = _decode_json_value(signal.pop("metrics_json", "{}"), {})
        signal["raw_payload"] = _decode_json_value(signal.pop("raw_payload_json", "{}"), {})
        signals.append(signal)
    return signals


def get_hotspot_package(hotspot_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT h.*,
                      (SELECT COUNT(*) FROM hotspot_signals hs WHERE hs.hotspot_id=h.id) AS current_signal_count,
                      (SELECT COUNT(*) FROM hotspot_media hm WHERE hm.hotspot_id=h.id) AS current_media_count
               FROM hotspots h WHERE h.id=?""",
            (hotspot_id,),
        ).fetchone()
    if not row:
        return None
    package = dict(row)
    package["locations"] = _decode_json_value(package.pop("locations_json", "[]"), [])
    package["entities"] = _decode_json_value(package.pop("entities_json", "[]"), [])
    package["signal_count"] = package.pop("current_signal_count")
    package["media_count"] = package.pop("current_media_count")
    return package


def update_hotspot_translation(
    hotspot_id: int,
    title_zh: str,
    summary_zh: str,
    snapshot_sha256: str,
    model: str,
):
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspots SET title_zh=?,summary_zh=?,translation_status='ready',
               translation_snapshot_sha256=?,translated_at=?,translation_model=? WHERE id=?""",
            (title_zh, summary_zh, snapshot_sha256, datetime.now().isoformat(timespec="seconds"), model, hotspot_id),
        )


def update_hotspot_translation_status(hotspot_id: int, status: str):
    if status not in {"pending", "translating", "ready", "stale", "failed"}:
        raise ValueError("热点翻译状态无效")
    with get_conn() as conn:
        conn.execute("UPDATE hotspots SET translation_status=? WHERE id=?", (status, hotspot_id))


def upsert_hotspot_media(data: dict) -> tuple[int, bool]:
    fields = (
        "hotspot_id", "media_kind", "platform", "platform_media_id", "source_page_url",
        "original_media_url", "embed_url", "thumbnail_url", "local_path", "publisher", "author",
        "published_at", "mime_type", "duration_seconds", "width", "height", "intake_title",
        "intake_summary", "intake_metadata_status", "intake_metadata_checked_at", "intake_decision_json", "authorization_status", "rights_tier",
        "rights_note", "license_name", "rights_evidence_url", "attribution", "download_status",
        "processing_status", "error_message", "sha256", "asset_id", "lifecycle_status",
        "download_progress", "progress_detail",
    )
    defaults = {
        "platform": "direct", "publisher": "", "author": "", "authorization_status": "pending_review", "rights_tier": "yellow",
        "rights_note": "", "download_status": "discovered", "processing_status": "not_started",
        "lifecycle_status": "active", "download_progress": 0, "intake_title": "",
        "intake_summary": "", "intake_metadata_status": "pending", "intake_decision_json": None,
    }
    payload = {**defaults, **data}
    if "authorization_status" not in data:
        payload["authorization_status"] = _LEGACY_RIGHTS_TO_AUTHORIZATION.get(
            str(payload.get("rights_tier") or ""), "pending_review"
        )
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM hotspot_media WHERE hotspot_id=? AND original_media_url=?",
            (payload["hotspot_id"], payload["original_media_url"]),
        ).fetchone()
        if row:
            mutable = (
                "media_kind", "platform", "platform_media_id", "source_page_url", "embed_url",
                "thumbnail_url", "publisher", "author", "published_at", "mime_type",
                "duration_seconds", "width", "height",
            )
            conn.execute(
                f"UPDATE hotspot_media SET {','.join(f'{name}=?' for name in mutable)},updated_at=datetime('now') WHERE id=?",
                tuple(payload.get(name) for name in mutable) + (row["id"],),
            )
            return row["id"], False
        cur = conn.execute(
            f"INSERT INTO hotspot_media ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
            tuple(payload.get(name) for name in fields),
        )
        return cur.lastrowid, True


def get_hotspot_media(media_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM hotspot_media WHERE id=?", (media_id,)).fetchone()
        return dict(row) if row else None


def list_hotspot_media(
    hotspot_id: int | None = None,
    media_kind: str | None = None,
    rights_tier: str | None = None,
    authorization_status: str | None = None,
    lifecycle_status: str | None = None,
    freshness_days: int | None = None,
    limit: int = 200,
) -> list[dict]:
    query = "SELECT * FROM hotspot_media WHERE 1=1"
    params: list = []
    if hotspot_id is not None:
        query += " AND hotspot_id=?"
        params.append(hotspot_id)
    if media_kind:
        query += " AND media_kind=?"
        params.append(media_kind)
    if rights_tier:
        query += " AND rights_tier=?"
        params.append(rights_tier)
    if authorization_status:
        query += " AND authorization_status=?"
        params.append(authorization_status)
    if lifecycle_status:
        query += " AND lifecycle_status=?"
        params.append(lifecycle_status)
    if freshness_days is not None:
        days = max(1, min(int(freshness_days), 3650))
        query += " AND datetime(COALESCE(published_at,created_at)) >= datetime('now',?)"
        params.append(f"-{days} days")
    params.append(max(1, min(int(limit), 500)))
    with get_conn() as conn:
        rows = conn.execute(
            query + " ORDER BY datetime(COALESCE(published_at,created_at)) DESC,id DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def list_active_authorized_hotspot_media_for_full_intake() -> list[dict]:
    """Return the complete authorised active media library for the three-day Hook job.

    The user-facing listing endpoint is deliberately capped, but the scheduled
    intake job must not silently stop after its first 500 rows.  It applies its
    own media-kind and retry gates after this complete, stable ordering.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM hotspot_media
               WHERE lifecycle_status='active' AND authorization_status='authorized'
               ORDER BY datetime(COALESCE(published_at,created_at)) DESC,id DESC"""
        ).fetchall()
        return [dict(row) for row in rows]


def update_hotspot_media_authorization(
    media_id: int,
    authorization_status: str,
    rights_note: str,
    license_name: str | None,
    attribution: str | None,
    rights_evidence_url: str | None,
    confirmed_by: int | None,
):
    if authorization_status not in HOTSPOT_MEDIA_AUTHORIZATION_STATUSES:
        raise ValueError("热点素材授权状态无效")
    confirmed_at = datetime.now().isoformat(timespec="seconds") if authorization_status == "authorized" and confirmed_by is not None else None
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspot_media SET authorization_status=?,rights_tier=?,rights_note=?,license_name=?,attribution=?,
               rights_evidence_url=?,confirmed_by=?,confirmed_at=?,updated_at=datetime('now') WHERE id=?""",
            (authorization_status, _AUTHORIZATION_TO_LEGACY_RIGHTS[authorization_status], rights_note, license_name, attribution, rights_evidence_url,
             confirmed_by, confirmed_at, media_id),
        )


def update_hotspot_media_rights(
    media_id: int,
    rights_tier: str,
    rights_note: str,
    license_name: str | None,
    attribution: str | None,
    rights_evidence_url: str | None,
    confirmed_by: int | None,
):
    """Compatibility wrapper for historical callers using red/yellow/green."""
    authorization_status = _LEGACY_RIGHTS_TO_AUTHORIZATION.get(rights_tier)
    if not authorization_status:
        raise ValueError("热点素材授权状态无效")
    result = update_hotspot_media_authorization(
        media_id, authorization_status, rights_note, license_name, attribution, rights_evidence_url, confirmed_by,
    )
    # Older integrations used yellow as a confirmed, internal-use record.  Keep
    # its timestamp behavior while new callers use explicit authorization states.
    if rights_tier in {"green", "yellow"} and confirmed_by is not None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE hotspot_media SET confirmed_at=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), media_id),
            )
    return result


def update_hotspot_media_state(media_id: int, **changes):
    allowed = {
        "media_kind", "local_path", "mime_type", "duration_seconds", "width", "height",
        "download_status", "processing_status", "error_message", "sha256", "asset_id",
        "download_progress", "progress_detail", "intake_decision_json",
    }
    values = {key: value for key, value in changes.items() if key in allowed}
    if not values:
        return
    with get_conn() as conn:
        conn.execute(
            f"UPDATE hotspot_media SET {','.join(f'{key}=?' for key in values)},updated_at=datetime('now') WHERE id=?",
            tuple(values.values()) + (media_id,),
        )


def recover_retryable_hotspot_hook_curation() -> int:
    """Requeue only videos whose model curation failed after analysis completed.

    A temporary planner/critic failure must not look like a valid “no Hook”
    decision.  Their local mother video and analyzed segments stay intact; the
    next full intake can therefore retry curation without another download.
    """
    with get_conn() as conn:
        cursor = conn.execute(
            """UPDATE hotspot_media
               SET processing_status='processing_failed',
                   error_message=COALESCE(error_message, progress_detail),
                   updated_at=datetime('now')
               WHERE download_status='downloaded'
                 AND processing_status='ready'
                 AND progress_detail LIKE '%内置 Hook 策展暂时不可用%'"""
        )
        return cursor.rowcount


def update_hotspot_media_intake_metadata(
    media_id: int,
    title: str,
    summary: str,
    status: str,
) -> dict | None:
    """保存下载前的授权视频事实，不把它混入热点新闻摘要。"""
    if status not in {"pending", "ready", "failed"}:
        raise ValueError("热点视频入库元数据状态无效")
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspot_media
               SET intake_title=?, intake_summary=?, intake_metadata_status=?,
                   intake_metadata_checked_at=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                str(title or "").strip()[:300],
                str(summary or "").strip()[:2000],
                status,
                datetime.now().isoformat(timespec="seconds"),
                media_id,
            ),
        )
    return get_hotspot_media(media_id)


def _decode_hotspot_fetch_run(row) -> dict | None:
    if not row:
        return None
    result = dict(row)
    result["result"] = json.loads(result.pop("result_json") or "{}")
    return result


def create_hotspot_fetch_run(created_by: int | None = None) -> dict:
    run_id = uuid4().hex
    started_at = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO hotspot_fetch_runs
               (id,status,result_json,created_by,started_at)
               VALUES (?,?,?,?,?)""",
            (run_id, "running", "{}", created_by, started_at),
        )
        row = conn.execute(
            "SELECT * FROM hotspot_fetch_runs WHERE id=?", (run_id,)
        ).fetchone()
    return _decode_hotspot_fetch_run(row)


def finish_hotspot_fetch_run(run_id: str, status: str, result: dict) -> dict | None:
    finished_at = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspot_fetch_runs
               SET status=?,result_json=?,finished_at=? WHERE id=?""",
            (status, json.dumps(result, ensure_ascii=False), finished_at, run_id),
        )
        row = conn.execute(
            "SELECT * FROM hotspot_fetch_runs WHERE id=?", (run_id,)
        ).fetchone()
    return _decode_hotspot_fetch_run(row)


def recover_interrupted_hotspot_fetch_runs() -> int:
    """Mark runs left active by a stopped process so the UI can start again."""
    finished_at = datetime.now().isoformat(timespec="seconds")
    result = json.dumps({
        "error": "本地服务在抓取完成前停止，任务已标记为中断，请重新抓取",
        "source_health": [],
    }, ensure_ascii=False)
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE hotspot_fetch_runs
               SET status='failed',result_json=?,finished_at=?
               WHERE status='running'""",
            (result, finished_at),
        )
        return cur.rowcount


def get_latest_hotspot_fetch_run() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM hotspot_fetch_runs
               ORDER BY started_at DESC,rowid DESC LIMIT 1"""
        ).fetchone()
    return _decode_hotspot_fetch_run(row)


def list_sample_bundles_for_hotspot(
    hotspot_id: int, limit: int = 10, created_by: int | None = None,
) -> list[dict]:
    with get_conn() as conn:
        sql = """SELECT sb.* FROM sample_bundles sb
               JOIN evidence_packages ep ON ep.id=sb.evidence_package_id
               WHERE ep.hotspot_id=?"""
        params: list = [hotspot_id]
        if created_by is not None:
            sql += " AND sb.created_by=?"
            params.append(created_by)
        sql += " ORDER BY sb.created_at DESC,sb.rowid DESC LIMIT ?"
        params.append(max(1, min(int(limit), 50)))
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        bundle = dict(row)
        bundle["publish_allowed"] = bool(bundle["publish_allowed"])
        for column, target, fallback in (
            ("quality_issues", "quality_issues", "[]"),
            ("video_json", "video", "{}"),
            ("carousel_json", "carousel", "{}"),
            ("wechat_json", "wechat", "{}"),
            ("manifest_json", "manifest", "{}"),
        ):
            bundle[target] = json.loads(bundle.pop(column) or fallback)
        result.append(bundle)
    return result


def list_hotspot_sources(enabled_only: bool = False) -> list[dict]:
    with get_conn() as conn:
        sql = "SELECT * FROM hotspot_sources"
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = conn.execute(sql + " ORDER BY id").fetchall()
        result = []
        for row in rows:
            item = dict(row); item["allowed_domains"] = json.loads(item["allowed_domains"] or "[]"); item["enabled"] = bool(item["enabled"]); result.append(item)
        return result


def create_hotspot_source(
    name: str,
    feed_url: str,
    allowed_domains: list[str],
    created_by: int,
    enabled: bool = True,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO hotspot_sources (name,feed_url,allowed_domains,enabled,created_by) VALUES (?,?,?,?,?)",
            (
                name,
                feed_url,
                json.dumps(allowed_domains, ensure_ascii=False),
                1 if enabled else 0,
                created_by,
            ),
        )
        return cur.lastrowid


def update_hotspot_source(source_id: int, name: str, feed_url: str, allowed_domains: list[str], enabled: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE hotspot_sources SET name=?,feed_url=?,allowed_domains=?,enabled=? WHERE id=?",
            (name, feed_url, json.dumps(allowed_domains, ensure_ascii=False), 1 if enabled else 0, source_id),
        )


def delete_hotspot_source(source_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM hotspot_sources WHERE id=?", (source_id,))


def create_brand_evidence(data: dict, created_by: int | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO brand_evidence
               (claim,evidence_note,disclosure_level,status,created_by)
               VALUES (?,?,?,?,?)""",
            (
                str(data.get("claim") or "").strip(),
                str(data.get("evidence_note") or "").strip(),
                str(data.get("disclosure_level") or "public"),
                str(data.get("status") or "draft"),
                created_by,
            ),
        )
        return cur.lastrowid


def get_brand_evidence(evidence_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM brand_evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        return dict(row) if row else None


def list_brand_evidence(status: str | None = None, ids: list[int] | None = None) -> list[dict]:
    sql = "SELECT * FROM brand_evidence"
    params: list = []
    where = []
    if status:
        where.append("status=?")
        params.append(status)
    if ids is not None:
        if not ids:
            return []
        where.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    if where:
        sql += " WHERE " + " AND ".join(where)
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(sql + " ORDER BY id", params).fetchall()]


def confirm_brand_evidence(evidence_id: int, confirmed_by: int, status: str = "confirmed") -> dict | None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE brand_evidence
               SET status=?,confirmed_by=?,confirmed_at=? WHERE id=?""",
            (status, confirmed_by, datetime.now().isoformat(timespec="seconds"), evidence_id),
        )
    return get_brand_evidence(evidence_id)


def create_evidence_package(
    hotspot_id: int,
    fact_claims: list[dict],
    brand_claims: list[dict],
    status: str,
    created_by: int | None = None,
) -> dict:
    package_id = uuid4().hex
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO evidence_packages (id,hotspot_id,status,created_by) VALUES (?,?,?,?)",
            (package_id, hotspot_id, status, created_by),
        )
        for claim_type, claims in (("fact", fact_claims), ("brand", brand_claims)):
            for claim in claims:
                conn.execute(
                    """INSERT INTO evidence_claims
                       (id,package_id,claim_type,claim_text,source_url,source_title,
                        publisher,excerpt,published_at,retrieved_at,brand_evidence_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid4().hex,
                        package_id,
                        claim_type,
                        claim["claim"],
                        claim.get("source_url"),
                        claim.get("source_title"),
                        claim.get("publisher"),
                        claim.get("excerpt"),
                        claim.get("published_at"),
                        claim.get("retrieved_at"),
                        claim.get("brand_evidence_id"),
                    ),
                )
    return get_evidence_package(package_id)


def get_evidence_package(package_id: str) -> dict | None:
    with get_conn() as conn:
        package = conn.execute(
            "SELECT * FROM evidence_packages WHERE id=?", (package_id,)
        ).fetchone()
        if not package:
            return None
        claim_rows = conn.execute(
            "SELECT * FROM evidence_claims WHERE package_id=? ORDER BY created_at,id",
            (package_id,),
        ).fetchall()
    result = dict(package)
    result["fact_claims"] = []
    result["brand_claims"] = []
    for row in claim_rows:
        item = dict(row)
        item["claim"] = item.pop("claim_text")
        target = "fact_claims" if item.pop("claim_type") == "fact" else "brand_claims"
        result[target].append(item)
    return result


# ==================== Custom topic briefs and task evidence ====================

def _decode_topic_brief(row) -> dict | None:
    if not row:
        return None
    result = dict(row)
    for key in ("locations", "logistics_nodes", "platforms", "must_include", "must_avoid"):
        try:
            result[key] = json.loads(result.pop(f"{key}_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            result[key] = []
    return result


def create_topic_brief(data: dict, created_by: int) -> dict:
    brief_id = uuid4().hex
    fields = {
        "raw_input": str(data.get("raw_input") or "").strip(),
        "subject": str(data.get("subject") or "").strip(),
        "audience": str(data.get("audience") or "").strip(),
        "goal": str(data.get("goal") or "").strip(),
        "angle": str(data.get("angle") or "").strip(),
        "locations_json": json.dumps(data.get("locations") or [], ensure_ascii=False),
        "logistics_nodes_json": json.dumps(data.get("logistics_nodes") or [], ensure_ascii=False),
        "freshness_mode": str(data.get("freshness_mode") or "recent_or_evergreen"),
        "time_window_days": max(1, int(data.get("time_window_days") or 7)),
        "platforms_json": json.dumps(data.get("platforms") or ["douyin"], ensure_ascii=False),
        "content_form": str(data.get("content_form") or "video"),
        "must_include_json": json.dumps(data.get("must_include") or [], ensure_ascii=False),
        "must_avoid_json": json.dumps(data.get("must_avoid") or [], ensure_ascii=False),
        "source_hotspot_package_id": data.get("source_hotspot_package_id"),
        "status": str(data.get("status") or "draft"),
    }
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO topic_briefs
               (id,raw_input,subject,audience,goal,angle,locations_json,logistics_nodes_json,
                freshness_mode,time_window_days,platforms_json,content_form,must_include_json,
                must_avoid_json,source_hotspot_package_id,status,created_by)
               VALUES (:id,:raw_input,:subject,:audience,:goal,:angle,:locations_json,:logistics_nodes_json,
                :freshness_mode,:time_window_days,:platforms_json,:content_form,:must_include_json,
                :must_avoid_json,:source_hotspot_package_id,:status,:created_by)""",
            {**fields, "id": brief_id, "created_by": created_by},
        )
    return get_topic_brief(brief_id, created_by)


def get_topic_brief(brief_id: str, created_by: int | None = None) -> dict | None:
    sql, params = "SELECT * FROM topic_briefs WHERE id=?", [brief_id]
    if created_by is not None:
        sql += " AND created_by=?"
        params.append(created_by)
    with get_conn() as conn:
        return _decode_topic_brief(conn.execute(sql, params).fetchone())


def update_topic_brief(brief_id: str, data: dict, created_by: int) -> dict | None:
    current = get_topic_brief(brief_id, created_by)
    if not current:
        return None
    merged = {**current, **{key: value for key, value in data.items() if value is not None}}
    with get_conn() as conn:
        conn.execute(
            """UPDATE topic_briefs SET raw_input=?,subject=?,audience=?,goal=?,angle=?,locations_json=?,
               logistics_nodes_json=?,freshness_mode=?,time_window_days=?,platforms_json=?,content_form=?,
               must_include_json=?,must_avoid_json=?,source_hotspot_package_id=?,status=?,updated_at=datetime('now')
               WHERE id=? AND created_by=?""",
            (merged["raw_input"], merged.get("subject", ""), merged["audience"], merged["goal"], merged["angle"],
             json.dumps(merged["locations"], ensure_ascii=False), json.dumps(merged["logistics_nodes"], ensure_ascii=False),
             merged["freshness_mode"], int(merged["time_window_days"]), json.dumps(merged["platforms"], ensure_ascii=False),
             merged["content_form"], json.dumps(merged["must_include"], ensure_ascii=False),
             json.dumps(merged["must_avoid"], ensure_ascii=False), merged.get("source_hotspot_package_id"),
             merged.get("status", "draft"), brief_id, created_by),
        )
    return get_topic_brief(brief_id, created_by)


def replace_topic_evidence_items(brief_id: str, items: list[dict]) -> list[dict]:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO topic_evidence_items
                   (id,topic_brief_id,evidence_type,source_id,content_role,relevance_score,match_reason,rights_status,selected,review_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(topic_brief_id,evidence_type,source_id,content_role) DO UPDATE SET
                     relevance_score=excluded.relevance_score,match_reason=excluded.match_reason,
                     rights_status=excluded.rights_status,updated_at=datetime('now')""",
                (uuid4().hex, brief_id, item["evidence_type"], str(item["source_id"]), item["content_role"],
                 float(item.get("relevance_score") or 0), str(item.get("match_reason") or ""),
                 str(item.get("rights_status") or "unknown"), 1 if item.get("selected") else 0,
                 str(item.get("review_status") or "candidate")),
            )
    return list_topic_evidence_items(brief_id)


def list_topic_evidence_items(brief_id: str) -> list[dict]:
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM topic_evidence_items WHERE topic_brief_id=? ORDER BY evidence_type, relevance_score DESC, id",
            (brief_id,),
        ).fetchall()]


def update_topic_evidence_item(brief_id: str, item_id: str, selected: bool, review_status: str) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE topic_evidence_items SET selected=?,review_status=?,updated_at=datetime('now') WHERE id=? AND topic_brief_id=?",
            (1 if selected else 0, review_status, item_id, brief_id),
        )
        row = conn.execute("SELECT * FROM topic_evidence_items WHERE id=? AND topic_brief_id=?", (item_id, brief_id)).fetchone()
        return dict(row) if row else None


def upsert_model_route(data: dict) -> dict:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO model_role_configs
               (role,provider,base_url,api_key_env,model,capabilities,timeout,max_tokens,cost_profile,request_options,enabled,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(role) DO UPDATE SET
                 provider=excluded.provider,base_url=excluded.base_url,
                 api_key_env=excluded.api_key_env,model=excluded.model,
                capabilities=excluded.capabilities,timeout=excluded.timeout,
                max_tokens=excluded.max_tokens,cost_profile=excluded.cost_profile,
                request_options=excluded.request_options,
                enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (
                data["role"], data["provider"], data["base_url"], data["api_key_env"],
                data["model"], json.dumps(data.get("capabilities") or [], ensure_ascii=False),
                int(data.get("timeout") or 30), int(data.get("max_tokens") or 1200),
                data.get("cost_profile") or "low",
                json.dumps(data.get("request_options") or {}, ensure_ascii=False),
                1 if data.get("enabled", True) else 0,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
    return get_model_route(data["role"])


def get_model_route(role: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM model_role_configs WHERE role=?", (role,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["capabilities"] = json.loads(result.get("capabilities") or "[]")
    result["request_options"] = json.loads(result.get("request_options") or "{}")
    result["enabled"] = bool(result["enabled"])
    return result


def create_model_budget(
    job_id: str,
    max_calls: int,
    max_input_tokens: int,
    max_output_tokens: int,
) -> dict:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO model_budgets
               (job_id,max_calls,max_input_tokens,max_output_tokens)
               VALUES (?,?,?,?)""",
            (job_id, max_calls, max_input_tokens, max_output_tokens),
        )
    return get_model_budget(job_id)


def get_model_budget(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM model_budgets WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def get_model_cache(cache_key: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM model_call_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """UPDATE model_call_cache
               SET last_accessed_at=datetime('now') WHERE cache_key=?""",
            (cache_key,),
        )
    result = dict(row)
    result["response"] = json.loads(result.pop("response_json"))
    return result


def record_model_call(
    job_id: str,
    role: str,
    model: str,
    cache_key: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    response: dict,
) -> dict:
    with get_conn() as conn:
        cached = conn.execute(
            "SELECT response_json FROM model_call_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if cached:
            conn.execute(
                """UPDATE model_call_cache
                   SET last_accessed_at=datetime('now') WHERE cache_key=?""",
                (cache_key,),
            )
            conn.execute(
                """INSERT INTO model_call_usage
                   (job_id,role,cache_key,cache_hit) VALUES (?,?,?,1)""",
                (job_id, role, cache_key),
            )
            return {"cache_hit": True, "response": json.loads(cached["response_json"])}
        conn.execute(
            """INSERT INTO model_call_cache
               (cache_key,role,model,response_json,input_tokens,output_tokens,
                created_at,last_accessed_at)
               VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (cache_key, role, model, json.dumps(response, ensure_ascii=False), input_tokens, output_tokens),
        )
        conn.execute(
            """INSERT INTO model_call_usage
               (job_id,role,cache_key,input_tokens,output_tokens,estimated_cost,cache_hit)
               VALUES (?,?,?,?,?,?,0)""",
            (job_id, role, cache_key, input_tokens, output_tokens, estimated_cost),
        )
        conn.execute(
            """UPDATE model_budgets SET calls_used=calls_used+1,
               input_tokens_used=input_tokens_used+?,output_tokens_used=output_tokens_used+?,
               estimated_cost=estimated_cost+?,updated_at=? WHERE job_id=?""",
            (input_tokens, output_tokens, estimated_cost, datetime.now().isoformat(timespec="seconds"), job_id),
        )
    return {"cache_hit": False, "response": response}


def cleanup_model_call_cache(
    *,
    ttl_days: int = 30,
    max_rows: int = 5_000,
) -> dict:
    """Expire old cache rows and enforce an LRU size cap.

    TTL defaults to 30 days and is clamped to 1..90. Usage rows that only
    reference deleted cache keys are removed afterwards.
    """
    days = max(1, min(int(ttl_days), 90))
    limit = max(1, int(max_rows))
    with get_conn() as conn:
        expired = conn.execute(
            """DELETE FROM model_call_cache
               WHERE datetime(COALESCE(last_accessed_at, created_at))
                     < datetime('now', ?)""",
            (f"-{days} days",),
        ).rowcount
        overflow = 0
        total = int(conn.execute("SELECT COUNT(*) FROM model_call_cache").fetchone()[0])
        if total > limit:
            stale_keys = [
                row["cache_key"]
                for row in conn.execute(
                    """SELECT cache_key FROM model_call_cache
                       ORDER BY datetime(COALESCE(last_accessed_at, created_at)) ASC,
                                cache_key ASC
                       LIMIT ?""",
                    (total - limit,),
                ).fetchall()
            ]
            if stale_keys:
                placeholders = ",".join("?" for _ in stale_keys)
                overflow = conn.execute(
                    f"DELETE FROM model_call_cache WHERE cache_key IN ({placeholders})",
                    stale_keys,
                ).rowcount
        orphan_usage = conn.execute(
            """DELETE FROM model_call_usage
               WHERE cache_key NOT IN (SELECT cache_key FROM model_call_cache)
                 AND cache_hit=1"""
        ).rowcount
        remaining = int(conn.execute("SELECT COUNT(*) FROM model_call_cache").fetchone()[0])
    return {
        "ttl_days": days,
        "max_rows": limit,
        "expired": expired,
        "overflow": overflow,
        "orphan_usage_deleted": orphan_usage,
        "remaining": remaining,
    }


def create_sample_bundle(data: dict, created_by: int | None = None) -> dict:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sample_bundles
               (id,evidence_package_id,status,publish_allowed,quality_issues,
                video_json,carousel_json,wechat_json,manifest_json,output_dir,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["id"], data["evidence_package_id"], data["status"],
                1 if data.get("publish_allowed") else 0,
                json.dumps(data.get("quality_issues") or [], ensure_ascii=False),
                json.dumps(data["video"], ensure_ascii=False),
                json.dumps(data["carousel"], ensure_ascii=False),
                json.dumps(data["wechat"], ensure_ascii=False),
                json.dumps(data["manifest"], ensure_ascii=False),
                data["output_dir"], created_by,
            ),
        )
    return get_sample_bundle(data["id"])


def get_sample_bundle(bundle_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sample_bundles WHERE id=?", (bundle_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["publish_allowed"] = bool(result["publish_allowed"])
    for column, target in (
        ("quality_issues", "quality_issues"),
        ("video_json", "video"),
        ("carousel_json", "carousel"),
        ("wechat_json", "wechat"),
        ("manifest_json", "manifest"),
    ):
        result[target] = json.loads(result[column] or "[]")
        if column != target:
            result.pop(column, None)
    return result


def update_sample_bundle_preview(bundle_id: str, preview_path: str, render_report: dict) -> dict | None:
    bundle = get_sample_bundle(bundle_id)
    if not bundle:
        return None
    manifest = dict(bundle.get("manifest") or {})
    manifest["video_preview"] = {
        "path": preview_path,
        "quality_report": render_report,
        "publish_allowed": False,
    }
    with get_conn() as conn:
        conn.execute(
            "UPDATE sample_bundles SET preview_path=?,manifest_json=? WHERE id=?",
            (preview_path, json.dumps(manifest, ensure_ascii=False), bundle_id),
        )
    return get_sample_bundle(bundle_id)


def update_asset(asset_id: int, name: str, category: str, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE assets SET name=?, category=?, status=? WHERE id=?", (name, category, status, asset_id))


def set_asset_pinned(asset_id: int, pinned: bool) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE assets SET pinned_at=CASE WHEN ? THEN datetime('now') ELSE NULL END
               WHERE id=?""",
            (1 if pinned else 0, asset_id),
        )
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return dict(row) if row else None


def mark_asset_category_manual(asset_id: int, category: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE assets SET category=?,primary_category=?,primary_category_source='manual' WHERE id=?",
            (category, category, asset_id),
        )


def _json_reference_exists(conn, table: str, column: str, asset_id: int) -> bool:
    spaced = f'%"asset_id": {int(asset_id)}%'
    compact = f'%"asset_id":{int(asset_id)}%'
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE {column} LIKE ? OR {column} LIKE ? LIMIT 1",
        (spaced, compact),
    ).fetchone()
    return row is not None


def asset_reference_reasons(asset_id: int) -> list[str]:
    """Return every known business reference before a destructive asset operation."""
    reasons: list[str] = []
    with get_conn() as conn:
        direct_checks = (
            ("hotspot", "SELECT 1 FROM hotspots WHERE asset_id=? LIMIT 1"),
            ("hotspot_media", "SELECT 1 FROM hotspot_media WHERE asset_id=? LIMIT 1"),
            ("inspiration", "SELECT 1 FROM inspiration_items WHERE asset_id=? LIMIT 1"),
        )
        for label, sql in direct_checks:
            if conn.execute(sql, (asset_id,)).fetchone():
                reasons.append(label)

        json_checks = (
            ("queue_attachment", "queue", "attachments"),
            ("video_render_job", "video_render_jobs", "script"),
            ("video_project_revision", "video_project_revisions", "payload"),
            ("sample_bundle_video", "sample_bundles", "video_json"),
            ("sample_bundle_manifest", "sample_bundles", "manifest_json"),
        )
        for label, table, column in json_checks:
            if _json_reference_exists(conn, table, column, asset_id):
                reasons.append(label)

        if conn.execute(
            """SELECT 1 FROM semantic_atoms a
               JOIN asset_segments s ON s.id=a.selected_segment_id
               JOIN match_sessions m ON m.id=a.session_id
               WHERE s.asset_id=? AND m.status IN ('draft','active') LIMIT 1""",
            (asset_id,),
        ).fetchone():
            reasons.append("match_session")
    return reasons


def asset_active_reference_reasons(asset_id: int) -> list[str]:
    """Return only references that still need the source file to remain available."""
    reasons: list[str] = []
    spaced = f'%"asset_id": {int(asset_id)}%'
    compact = f'%"asset_id":{int(asset_id)}%'
    with get_conn() as conn:
        if conn.execute(
            """SELECT 1 FROM video_generation_jobs j
               JOIN video_project_revisions r ON r.id=j.revision_id
               WHERE j.status IN ('pending','running','needs_review','cancel_requested')
                 AND (r.payload LIKE ? OR r.payload LIKE ?) LIMIT 1""",
            (spaced, compact),
        ).fetchone():
            reasons.append("active_video_generation")
        if conn.execute(
            """SELECT 1 FROM video_render_jobs
               WHERE status IN ('pending','running') AND (script LIKE ? OR script LIKE ?) LIMIT 1""",
            (spaced, compact),
        ).fetchone():
            reasons.append("active_video_render")
        if conn.execute(
            """SELECT 1 FROM queue
               WHERE status IN ('draft','pending_review','queued')
                 AND (attachments LIKE ? OR attachments LIKE ?) LIMIT 1""",
            (spaced, compact),
        ).fetchone():
            reasons.append("active_queue")
        if conn.execute(
            """SELECT 1 FROM semantic_atoms a
               JOIN asset_segments s ON s.id=a.selected_segment_id
               JOIN match_sessions m ON m.id=a.session_id
               WHERE s.asset_id=? AND m.status IN ('draft','active') LIMIT 1""",
            (asset_id,),
        ).fetchone():
            reasons.append("active_match_session")
    return reasons


def asset_is_referenced(asset_id: int) -> bool:
    return bool(asset_reference_reasons(asset_id))


def list_retention_asset_candidates() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM assets
               WHERE retention_class='hotspot_source' AND file_status='available'
               ORDER BY COALESCE(purge_after,last_used_at,created_at),id"""
        ).fetchall()
        return [dict(row) for row in rows]


def mark_asset_file_purged(asset_id: int, purged_at: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE assets SET file_status='purged',purged_at=?,status='inactive'
               WHERE id=?""",
            (purged_at, asset_id),
        )
        conn.execute(
            """UPDATE hotspot_media SET lifecycle_status='purged',local_path=NULL,
               updated_at=datetime('now') WHERE asset_id=?""",
            (asset_id,),
        )


def archive_stale_hotspot_media(days: int = 30) -> int:
    days = max(1, min(int(days), 3650))
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE hotspot_media SET lifecycle_status='archived',updated_at=datetime('now')
               WHERE lifecycle_status='active' AND confirmed_at IS NULL
                 AND datetime(created_at) < datetime('now',?)""",
            (f"-{days} days",),
        )
        return cur.rowcount


def list_hotspot_hook_cleanup_candidates(retention_days: int = 10, protect_days: int = 3) -> list[dict]:
    """返回可轮换删除的热点母片，永远保护最近 ``protect_days`` 的内容。

    热点事实和信源不在这个范围内；删除的是已下载的母片、其 Hook 代理与分析索引。
    """
    retention_days = max(1, min(int(retention_days), 3650))
    protect_days = max(0, min(int(protect_days), retention_days))
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM hotspot_media
               WHERE lifecycle_status='active'
                 AND asset_id IS NOT NULL
                 AND download_status='downloaded'
                 AND processing_status='ready'
                 AND datetime(COALESCE(confirmed_at,created_at)) < datetime('now', ?)
                 AND datetime(COALESCE(confirmed_at,created_at)) < datetime('now', ?)
               ORDER BY datetime(COALESCE(confirmed_at,created_at)),id""",
            (f"-{retention_days} days", f"-{protect_days} days"),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_asset(asset_id: int):
    with get_conn() as conn:
        segment_ids = [row["id"] for row in conn.execute(
            "SELECT id FROM asset_segments WHERE asset_id=?", (asset_id,)
        ).fetchall()]
        for segment_id in segment_ids:
            conn.execute("DELETE FROM asset_segment_fts WHERE segment_id=?", (str(segment_id),))
        conn.execute("DELETE FROM segment_tags WHERE segment_id IN (SELECT id FROM asset_segments WHERE asset_id=?)", (asset_id,))
        conn.execute("DELETE FROM asset_segments WHERE asset_id=?", (asset_id,))
        conn.execute("DELETE FROM asset_processing_jobs WHERE asset_id=?", (asset_id,))
        conn.execute(
            """UPDATE inspiration_items SET asset_id=NULL,materialization_status='reference_only',
               updated_at=datetime('now') WHERE asset_id=?""", (asset_id,),
        )
        conn.execute("UPDATE hotspots SET asset_id=NULL WHERE asset_id=?", (asset_id,))
        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))


def _hotspot_library_scope(conn, media_id: int | None = None) -> dict | None:
    """Return the database rows that belong to the disposable hotspot media library.

    A hotspot headline/source stays in the hotspot pool.  This scope is intentionally
    limited to material candidates, their downloaded source assets and derived clips.
    """
    if media_id is None:
        media_rows = [dict(row) for row in conn.execute("SELECT * FROM hotspot_media").fetchall()]
    else:
        row = conn.execute("SELECT * FROM hotspot_media WHERE id=?", (media_id,)).fetchone()
        if not row:
            return None
        media_rows = [dict(row)]

    asset_ids = {int(row["asset_id"]) for row in media_rows if row.get("asset_id")}
    if media_id is None:
        asset_ids.update(
            int(row["id"])
            for row in conn.execute(
                """SELECT DISTINCT a.id FROM assets a
                   LEFT JOIN hotspot_media hm ON hm.asset_id=a.id
                   LEFT JOIN hotspot_event_clips he ON he.asset_id=a.id
                   LEFT JOIN hotspots h ON h.asset_id=a.id
                   WHERE a.hotspot_id IS NOT NULL OR a.retention_class='hotspot_source'
                      OR hm.asset_id IS NOT NULL OR he.asset_id IS NOT NULL OR h.asset_id IS NOT NULL"""
            ).fetchall()
        )

    if not asset_ids:
        return {"media_rows": media_rows, "asset_rows": [], "event_rows": [], "segment_rows": []}

    marks = ",".join("?" for _ in asset_ids)
    ordered_ids = sorted(asset_ids)
    asset_rows = [dict(row) for row in conn.execute(
        f"SELECT * FROM assets WHERE id IN ({marks})", ordered_ids
    ).fetchall()]
    event_rows = [dict(row) for row in conn.execute(
        f"SELECT * FROM hotspot_event_clips WHERE asset_id IN ({marks})", ordered_ids
    ).fetchall()]
    segment_rows = [dict(row) for row in conn.execute(
        f"SELECT * FROM asset_segments WHERE asset_id IN ({marks})", ordered_ids
    ).fetchall()]
    return {
        "media_rows": media_rows,
        "asset_rows": asset_rows,
        "event_rows": event_rows,
        "segment_rows": segment_rows,
    }


def _hotspot_library_file_paths(scope: dict, asset_ids: set[int] | None = None) -> list[str]:
    """Collect only local relative paths; callers validate paths before unlinking."""
    # An empty set means a single-card delete found a source shared by another card:
    # do not mistake that for a full-library cleanup and unlink the shared source file.
    all_assets = asset_ids is None
    selected = set(asset_ids or ())
    paths: set[str] = set()
    for row in scope["media_rows"]:
        asset_id = int(row["asset_id"]) if row.get("asset_id") else None
        if not asset_id or all_assets or asset_id in selected:
            if row.get("local_path"):
                paths.add(str(row["local_path"]))
    for row in scope["asset_rows"]:
        if not all_assets and int(row["id"]) not in selected:
            continue
        for column in ("filepath", "thumbnail"):
            if row.get(column):
                paths.add(str(row[column]))
    for row in scope["event_rows"]:
        if not all_assets and int(row["asset_id"]) not in selected:
            continue
        for column in ("clip_path", "thumbnail_path"):
            if row.get(column):
                paths.add(str(row[column]))
    for row in scope["segment_rows"]:
        if not all_assets and int(row["asset_id"]) not in selected:
            continue
        for column in ("preview_path", "thumbnail_path"):
            if row.get(column):
                paths.add(str(row[column]))
    return sorted(paths)


def hotspot_library_cleanup_preview() -> dict:
    """Non-destructive count and local file list for the admin confirmation dialog."""
    with get_conn() as conn:
        scope = _hotspot_library_scope(conn)
        assert scope is not None
        return {
            "media_count": len(scope["media_rows"]),
            "active_media_count": sum(
                1
                for row in scope["media_rows"]
                if row.get("download_status") in {"pending", "downloading"}
                or row.get("processing_status") == "processing"
            ),
            "asset_count": len(scope["asset_rows"]),
            "event_clip_count": len(scope["event_rows"]),
            "segment_count": len(scope["segment_rows"]),
            "file_paths": _hotspot_library_file_paths(scope),
        }


def hotspot_library_media_is_busy(media_id: int | None = None) -> bool:
    """Do not delete a row while its background downloader/analysis task owns it."""
    query = """SELECT 1 FROM hotspot_media
               WHERE (download_status IN ('pending','downloading')
                      OR processing_status='processing')"""
    params: list[int] = []
    if media_id is not None:
        query += " AND id=?"
        params.append(int(media_id))
    with get_conn() as conn:
        return conn.execute(query, params).fetchone() is not None


def _delete_hotspot_assets_in_conn(conn, asset_ids: set[int]) -> None:
    """Delete source assets plus derived search/match references in one transaction."""
    if not asset_ids:
        return
    marks = ",".join("?" for _ in asset_ids)
    values = sorted(asset_ids)
    segment_rows = conn.execute(
        f"SELECT id FROM asset_segments WHERE asset_id IN ({marks})", values
    ).fetchall()
    segment_ids = [int(row["id"]) for row in segment_rows]
    if segment_ids:
        segment_marks = ",".join("?" for _ in segment_ids)
        conn.execute(f"DELETE FROM asset_segment_fts WHERE segment_id IN ({segment_marks})", [str(item) for item in segment_ids])
        conn.execute(f"DELETE FROM segment_usage WHERE segment_id IN ({segment_marks})", segment_ids)
        conn.execute(f"DELETE FROM match_candidates WHERE segment_id IN ({segment_marks})", segment_ids)
        conn.execute(f"UPDATE semantic_atoms SET selected_segment_id=NULL WHERE selected_segment_id IN ({segment_marks})", segment_ids)
        conn.execute(f"DELETE FROM match_feedback WHERE segment_id IN ({segment_marks})", segment_ids)
        conn.execute(f"DELETE FROM segment_tags WHERE segment_id IN ({segment_marks})", segment_ids)
        conn.execute(f"DELETE FROM asset_segments WHERE id IN ({segment_marks})", segment_ids)
    conn.execute(f"DELETE FROM asset_processing_jobs WHERE asset_id IN ({marks})", values)
    conn.execute(
        f"""UPDATE inspiration_items SET asset_id=NULL,materialization_status='reference_only',
               updated_at=datetime('now') WHERE asset_id IN ({marks})""",
        values,
    )
    conn.execute(f"UPDATE hotspots SET asset_id=NULL WHERE asset_id IN ({marks})", values)
    conn.execute(f"DELETE FROM assets WHERE id IN ({marks})", values)


def delete_hotspot_library(media_id: int | None = None) -> dict | None:
    """Remove hotspot media cards and every local derivative in their scope.

    This deliberately does not delete hotspot facts, sources, user projects or Buffalo
    owned assets.  Existing projects are retained as records but must be re-selected
    before a future re-render when they referenced a removed hotspot asset.
    """
    with get_conn() as conn:
        scope = _hotspot_library_scope(conn, media_id)
        if scope is None:
            return None
        all_asset_ids = {int(row["id"]) for row in scope["asset_rows"]}
        media_ids = [int(row["id"]) for row in scope["media_rows"]]
        if media_ids:
            media_marks = ",".join("?" for _ in media_ids)
            conn.execute(f"DELETE FROM hotspot_media WHERE id IN ({media_marks})", media_ids)

        # A single-card deletion must not remove a source asset still used by another
        # hotspot card.  A full clear has already removed every such card.
        deletable_asset_ids: set[int] = set()
        for asset_id in all_asset_ids:
            remaining = conn.execute(
                "SELECT 1 FROM hotspot_media WHERE asset_id=? LIMIT 1", (asset_id,)
            ).fetchone()
            if not remaining:
                deletable_asset_ids.add(asset_id)

        event_rows = [
            row for row in scope["event_rows"] if int(row["asset_id"]) in deletable_asset_ids
        ]
        if deletable_asset_ids:
            asset_marks = ",".join("?" for _ in deletable_asset_ids)
            asset_values = sorted(deletable_asset_ids)
            conn.execute(
                f"DELETE FROM hotspot_event_clips WHERE asset_id IN ({asset_marks})", asset_values
            )
            _delete_hotspot_assets_in_conn(conn, deletable_asset_ids)

        segment_count = sum(
            1 for row in scope["segment_rows"] if int(row["asset_id"]) in deletable_asset_ids
        )
        cleanup_scope = {**scope, "event_rows": event_rows}
        return {
            "media_count": len(media_ids),
            "asset_count": len(deletable_asset_ids),
            "event_clip_count": len(event_rows),
            "segment_count": segment_count,
            "file_paths": _hotspot_library_file_paths(cleanup_scope, deletable_asset_ids),
        }


def delete_hotspot_event_asset(asset_id: int) -> dict | None:
    """Delete one hotspot mother asset and all of its virtual event clips.

    Event cards are virtual views over a mother asset, so deleting a single event must
    remove the complete source group instead of leaving sibling cards with a missing
    source file.
    """
    with get_conn() as conn:
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset:
            return None
        media_rows = [dict(row) for row in conn.execute(
            "SELECT * FROM hotspot_media WHERE asset_id=?", (asset_id,)
        ).fetchall()]
        event_rows = [dict(row) for row in conn.execute(
            "SELECT * FROM hotspot_event_clips WHERE asset_id=?", (asset_id,)
        ).fetchall()]
        is_hotspot_asset = bool(
            asset["hotspot_id"]
            or asset["retention_class"] == "hotspot_source"
            or media_rows
            or event_rows
        )
        if not is_hotspot_asset:
            return None
        segment_rows = [dict(row) for row in conn.execute(
            "SELECT * FROM asset_segments WHERE asset_id=?", (asset_id,)
        ).fetchall()]
        scope = {
            "media_rows": media_rows,
            "asset_rows": [dict(asset)],
            "event_rows": event_rows,
            "segment_rows": segment_rows,
        }
        if media_rows:
            media_ids = [int(row["id"]) for row in media_rows]
            marks = ",".join("?" for _ in media_ids)
            conn.execute(f"DELETE FROM hotspot_media WHERE id IN ({marks})", media_ids)
        conn.execute("DELETE FROM hotspot_event_clips WHERE asset_id=?", (asset_id,))
        _delete_hotspot_assets_in_conn(conn, {int(asset_id)})
        return {
            "media_count": len(media_rows),
            "asset_count": 1,
            "event_clip_count": len(event_rows),
            "segment_count": len(segment_rows),
            "file_paths": _hotspot_library_file_paths(scope, {int(asset_id)}),
        }


def delete_hotspot_event_clip(event_clip_id: int) -> dict | None:
    """Delete one derived Hook, preserving its hotspot mother video and siblings."""
    with get_conn() as conn:
        event = conn.execute(
            "SELECT * FROM hotspot_event_clips WHERE id=?", (event_clip_id,)
        ).fetchone()
        if not event:
            return None
        row = dict(event)
        conn.execute("DELETE FROM hotspot_event_segment_links WHERE event_clip_id=?", (event_clip_id,))
        conn.execute("DELETE FROM hotspot_event_clips WHERE id=?", (event_clip_id,))
        return {
            "event_clip_count": 1,
            "asset_id": int(row["asset_id"]),
            "file_paths": [
                str(value) for value in (row.get("clip_path"), row.get("thumbnail_path")) if value
            ],
        }


def delete_hotspot_event_clips(event_clip_ids: list[int]) -> dict:
    """Delete derived Hook rows in bulk, always preserving source mother assets."""
    ids = []
    for value in event_clip_ids:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in ids:
            ids.append(item)
    if not ids:
        return {"event_clip_count": 0, "file_paths": []}
    marks = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM hotspot_event_clips WHERE id IN ({marks})", ids
        ).fetchall()]
        if not rows:
            return {"event_clip_count": 0, "file_paths": []}
        found_ids = [int(row["id"]) for row in rows]
        found_marks = ",".join("?" for _ in found_ids)
        conn.execute(f"DELETE FROM hotspot_event_segment_links WHERE event_clip_id IN ({found_marks})", found_ids)
        conn.execute(f"DELETE FROM hotspot_event_clips WHERE id IN ({found_marks})", found_ids)
        return {
            "event_clip_count": len(rows),
            "file_paths": [
                str(value) for row in rows for value in (row.get("clip_path"), row.get("thumbnail_path")) if value
            ],
        }


# ==================== Video Render Jobs ====================

def create_render_job(job_id: str, script: dict, voice: str, created_by: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO video_render_jobs (id,script,voice,created_by) VALUES (?,?,?,?)",
            (job_id, json.dumps(script, ensure_ascii=False), voice, created_by),
        )


def get_render_job(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM video_render_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row); result["script"] = json.loads(result["script"])
        result["clips"] = json.loads(result.get("clips") or "[]")
        result["quality_report"] = json.loads(result.get("quality_report") or "{}")
        return result


def update_render_job(job_id: str, **fields):
    allowed = {"status", "stage", "progress", "output_path", "error", "clips", "quality_report", "voice", "script"}
    values = {key: value for key, value in fields.items() if key in allowed}
    for key in ("clips", "quality_report", "script"):
        if key in values and not isinstance(values[key], str):
            values[key] = json.dumps(values[key], ensure_ascii=False)
    if not values:
        return
    with get_conn() as conn:
        assignments = ",".join(f"{key}=?" for key in values)
        conn.execute(
            f"UPDATE video_render_jobs SET {assignments}, updated_at=datetime('now') WHERE id=?",
            [*values.values(), job_id],
        )


def get_unfinished_render_jobs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM video_render_jobs WHERE status IN ('pending','running')").fetchall()
        return [dict(row) for row in rows]


# ==================== Quality-gated Video Generation ====================

_ACTIVE_VIDEO_JOB_STATUSES = ("pending", "running", "needs_review", "cancel_requested")
_TERMINAL_VIDEO_JOB_STATUSES = ("succeeded", "failed", "canceled")
_GENERATING_VIDEO_JOB_STATUSES = ("pending", "running", "cancel_requested")


def _project_status_for_job_status(job_status: str) -> str:
    """Map generation job status onto video_projects.status."""
    status = str(job_status or "").strip()
    if status in _GENERATING_VIDEO_JOB_STATUSES:
        return "generating"
    if status == "needs_review":
        return "needs_review"
    if status in _TERMINAL_VIDEO_JOB_STATUSES:
        return "ready"
    return "ready"


def _sync_video_project_for_job(conn, job_row) -> None:
    """Keep project.status aligned when its active job changes status.

    Only updates projects whose active_job_id points at this job, so older
    terminal jobs do not overwrite a newer active job pointer.
    """
    if not job_row:
        return
    job = dict(job_row)
    job_id = job.get("id")
    project_id = job.get("project_id")
    if not job_id or not project_id:
        return
    project_status = _project_status_for_job_status(job.get("status"))
    conn.execute(
        """UPDATE video_projects
           SET status=?, updated_at=datetime('now')
           WHERE id=? AND active_job_id=?""",
        (project_status, project_id, job_id),
    )


def _decode_video_revision(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.get("payload") or "{}")
    return item


def _decode_video_job(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["quality_report"] = json.loads(item.get("quality_report") or "{}")
    return item


def recent_session_hook_event_ids(session_id: str, created_by: int, limit: int = 5) -> set[int]:
    """已锁定 Hook 的最近同一聊天 session 记录，用于对话内 Hook 选择去重降权。"""
    if not session_id:
        return set()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT source_snapshot FROM video_projects
               WHERE created_by=? ORDER BY created_at DESC LIMIT ?""",
            (created_by, max(1, limit) * 4),
        ).fetchall()
    used: set[int] = set()
    matched_sessions = 0
    for row in rows:
        if matched_sessions >= limit:
            break
        try:
            snapshot = json.loads(row["source_snapshot"] or "{}")
        except (TypeError, ValueError):
            continue
        if snapshot.get("session_id") != session_id:
            continue
        matched_sessions += 1
        for event_id in snapshot.get("matched_event_clip_ids") or []:
            try:
                used.add(int(event_id))
            except (TypeError, ValueError):
                continue
    return used


def create_video_project(
    created_by: int,
    source_type: str,
    source_snapshot: dict,
    title: str = "",
    platform: str = "douyin",
    target_duration_ms: int = 30000,
    target_orientation: str = "portrait",
) -> dict:
    project_id = str(uuid4())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO video_projects
               (id,created_by,source_type,source_snapshot,title,platform,
                target_duration_ms,target_orientation)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                project_id, created_by, source_type,
                json.dumps(source_snapshot or {}, ensure_ascii=False), title, platform,
                int(target_duration_ms), target_orientation,
            ),
        )
        row = conn.execute("SELECT * FROM video_projects WHERE id=?", (project_id,)).fetchone()
        return dict(row)


def create_video_project_revision(project_id: str, payload: dict, created_by: int) -> dict:
    revision_id = str(uuid4())
    with get_conn() as conn:
        project = conn.execute(
            "SELECT id FROM video_projects WHERE id=? AND created_by=?",
            (project_id, created_by),
        ).fetchone()
        if not project:
            raise ValueError("video project not found")
        revision_no = int(conn.execute(
            "SELECT COALESCE(MAX(revision_no),0)+1 FROM video_project_revisions WHERE project_id=?",
            (project_id,),
        ).fetchone()[0])
        conn.execute(
            """INSERT INTO video_project_revisions
               (id,project_id,revision_no,payload,created_by) VALUES (?,?,?,?,?)""",
            (
                revision_id, project_id, revision_no,
                json.dumps(payload or {}, ensure_ascii=False), created_by,
            ),
        )
        conn.execute(
            """UPDATE video_projects SET current_revision_id=?,status='ready',
               updated_at=datetime('now') WHERE id=?""",
            (revision_id, project_id),
        )
        row = conn.execute(
            "SELECT * FROM video_project_revisions WHERE id=?", (revision_id,)
        ).fetchone()
        return _decode_video_revision(row)


def update_video_project_revision_payload(
    revision_id: str,
    payload: dict,
    created_by: int,
    *,
    title: str | None = None,
    target_duration_ms: int | None = None,
) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT r.id,r.project_id FROM video_project_revisions r
               JOIN video_projects p ON p.id=r.project_id
               WHERE r.id=? AND p.created_by=?""",
            (revision_id, created_by),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE video_project_revisions SET payload=? WHERE id=?",
            (json.dumps(payload or {}, ensure_ascii=False), revision_id),
        )
        project_row = conn.execute(
            "SELECT active_job_id FROM video_projects WHERE id=?",
            (row["project_id"],),
        ).fetchone()
        active_job_id = str((project_row["active_job_id"] if project_row else None) or "").strip()
        project_status = "ready"
        if active_job_id:
            job_row = conn.execute(
                "SELECT status FROM video_generation_jobs WHERE id=?",
                (active_job_id,),
            ).fetchone()
            if job_row:
                project_status = _project_status_for_job_status(job_row["status"])
            else:
                project_status = "ready"
        assignments = [
            "status=?",
            "updated_at=datetime('now')",
        ]
        params: list = [project_status]
        if title is not None:
            assignments.append("title=?")
            params.append(title)
        if target_duration_ms is not None:
            assignments.append("target_duration_ms=?")
            params.append(int(target_duration_ms))
        params.append(row["project_id"])
        conn.execute(
            f"UPDATE video_projects SET {','.join(assignments)} WHERE id=?",
            params,
        )
        updated = conn.execute(
            "SELECT * FROM video_project_revisions WHERE id=?", (revision_id,)
        ).fetchone()
        return _decode_video_revision(updated)


def get_video_project(project_id: str, created_by: int | None = None) -> dict | None:
    with get_conn() as conn:
        sql = "SELECT * FROM video_projects WHERE id=?"
        params: list = [project_id]
        if created_by is not None:
            sql += " AND created_by=?"
            params.append(created_by)
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        item = dict(row)
        revision = None
        if item.get("current_revision_id"):
            revision = conn.execute(
                "SELECT * FROM video_project_revisions WHERE id=?",
                (item["current_revision_id"],),
            ).fetchone()
        item["current_revision"] = _decode_video_revision(revision)
        return item


def get_video_project_revision(revision_id: str, created_by: int | None = None) -> dict | None:
    with get_conn() as conn:
        sql = """SELECT r.* FROM video_project_revisions r
                 JOIN video_projects p ON p.id=r.project_id WHERE r.id=?"""
        params: list = [revision_id]
        if created_by is not None:
            sql += " AND p.created_by=?"
            params.append(created_by)
        return _decode_video_revision(conn.execute(sql, params).fetchone())


def get_active_video_generation_job_by_idempotency(
    created_by: int,
    idempotency_key: str,
) -> dict | None:
    placeholders = ",".join("?" for _ in _ACTIVE_VIDEO_JOB_STATUSES)
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT * FROM video_generation_jobs
                WHERE created_by=? AND idempotency_key=? AND status IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1""",
            (created_by, idempotency_key, *_ACTIVE_VIDEO_JOB_STATUSES),
        ).fetchone()
        return _decode_video_job(row)


def create_or_get_video_generation_job(
    project_id: str,
    revision_id: str,
    created_by: int,
    idempotency_key: str,
) -> tuple[dict, bool]:
    job_id = str(uuid4())
    placeholders = ",".join("?" for _ in _ACTIVE_VIDEO_JOB_STATUSES)
    with get_conn() as conn:
        existing = conn.execute(
            f"""SELECT * FROM video_generation_jobs
                WHERE idempotency_key=? AND created_by=?
                  AND status IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1""",
            (idempotency_key, created_by, *_ACTIVE_VIDEO_JOB_STATUSES),
        ).fetchone()
        if existing:
            return _decode_video_job(existing), False

        revision = conn.execute(
            """SELECT r.id FROM video_project_revisions r
               JOIN video_projects p ON p.id=r.project_id
               WHERE r.id=? AND r.project_id=? AND p.created_by=?""",
            (revision_id, project_id, created_by),
        ).fetchone()
        if not revision:
            raise ValueError("video project revision not found")
        try:
            conn.execute(
                """INSERT INTO video_generation_jobs
                   (id,project_id,revision_id,created_by,idempotency_key)
                   VALUES (?,?,?,?,?)""",
                (job_id, project_id, revision_id, created_by, idempotency_key),
            )
        except sqlite3.IntegrityError:
            existing = conn.execute(
                f"""SELECT * FROM video_generation_jobs
                    WHERE idempotency_key=? AND created_by=?
                      AND status IN ({placeholders})
                    ORDER BY created_at DESC LIMIT 1""",
                (idempotency_key, created_by, *_ACTIVE_VIDEO_JOB_STATUSES),
            ).fetchone()
            if existing:
                return _decode_video_job(existing), False
            raise
        conn.execute(
            """UPDATE video_projects SET active_job_id=?,status='generating',
               updated_at=datetime('now') WHERE id=?""",
            (job_id, project_id),
        )
        row = conn.execute("SELECT * FROM video_generation_jobs WHERE id=?", (job_id,)).fetchone()
        return _decode_video_job(row), True


def get_video_generation_job(job_id: str, created_by: int | None = None) -> dict | None:
    with get_conn() as conn:
        sql = "SELECT * FROM video_generation_jobs WHERE id=?"
        params: list = [job_id]
        if created_by is not None:
            sql += " AND created_by=?"
            params.append(created_by)
        return _decode_video_job(conn.execute(sql, params).fetchone())


def list_active_video_generation_jobs(created_by: int) -> list[dict]:
    placeholders = ",".join("?" for _ in _ACTIVE_VIDEO_JOB_STATUSES)
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM video_generation_jobs
                WHERE created_by=? AND status IN ({placeholders})
                ORDER BY created_at DESC""",
            (created_by, *_ACTIVE_VIDEO_JOB_STATUSES),
        ).fetchall()
        return [_decode_video_job(row) for row in rows]


def list_expired_video_outputs(retention_days: int = 30) -> list[dict]:
    days = max(1, min(int(retention_days), 3650))
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM video_generation_jobs
               WHERE status='succeeded' AND output_path IS NOT NULL AND output_path!=''
                 AND output_purged_at IS NULL AND output_pinned_at IS NULL
                 AND datetime(finished_at) < datetime('now',?)
               ORDER BY finished_at,id""",
            (f"-{days} days",),
        ).fetchall()
        return [_decode_video_job(row) for row in rows]


def set_video_output_pinned(job_id: str, pinned: bool) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE video_generation_jobs
               SET output_pinned_at=CASE WHEN ? THEN datetime('now') ELSE NULL END,
                   updated_at=datetime('now') WHERE id=?""",
            (1 if pinned else 0, job_id),
        )
        return _decode_video_job(
            conn.execute("SELECT * FROM video_generation_jobs WHERE id=?", (job_id,)).fetchone()
        )


def mark_video_output_purged(job_id: str, purged_at: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE video_generation_jobs SET output_purged_at=?,updated_at=datetime('now')
               WHERE id=?""",
            (purged_at, job_id),
        )


def update_video_generation_job(job_id: str, **fields) -> dict | None:
    allowed = {
        "status", "stage", "progress", "lease_owner", "lease_expires_at",
        "heartbeat_at", "cancel_requested_at", "canceled_at", "error_code",
        "error_message", "preview_path", "output_path", "quality_report", "attempt",
        "started_at", "finished_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if "quality_report" in values and not isinstance(values["quality_report"], str):
        values["quality_report"] = json.dumps(values["quality_report"] or {}, ensure_ascii=False)
    if not values:
        return get_video_generation_job(job_id)
    with get_conn() as conn:
        assignments = ",".join(f"{key}=?" for key in values)
        conn.execute(
            f"""UPDATE video_generation_jobs SET {assignments},updated_at=datetime('now')
                WHERE id=?""",
            (*values.values(), job_id),
        )
        row = conn.execute("SELECT * FROM video_generation_jobs WHERE id=?", (job_id,)).fetchone()
        if "status" in values:
            _sync_video_project_for_job(conn, row)
        return _decode_video_job(row)


def request_video_generation_cancel(job_id: str, created_by: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM video_generation_jobs WHERE id=? AND created_by=?",
            (job_id, created_by),
        ).fetchone()
        if not row:
            return None
        current = dict(row)
        if current["status"] in _TERMINAL_VIDEO_JOB_STATUSES or current["status"] == "cancel_requested":
            return _decode_video_job(row)
        if current["status"] in ("pending", "needs_review"):
            conn.execute(
                """UPDATE video_generation_jobs
                   SET status='canceled',stage='canceled',cancel_requested_at=datetime('now'),
                       canceled_at=datetime('now'),finished_at=datetime('now'),
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=datetime('now')
                   WHERE id=?""",
                (job_id,),
            )
            next_status = "canceled"
        else:
            conn.execute(
                """UPDATE video_generation_jobs
                   SET status='cancel_requested',cancel_requested_at=datetime('now'),
                       updated_at=datetime('now') WHERE id=?""",
                (job_id,),
            )
            next_status = "cancel_requested"
        conn.execute(
            """INSERT INTO video_generation_events (job_id,event_type,message,payload)
               VALUES (?,'cancel_requested',?,'{}')""",
            (job_id, "任务已取消" if next_status == "canceled" else "正在停止生成"),
        )
        updated = conn.execute("SELECT * FROM video_generation_jobs WHERE id=?", (job_id,)).fetchone()
        _sync_video_project_for_job(conn, updated)
        return _decode_video_job(updated)


def add_video_generation_event(
    job_id: str,
    event_type: str,
    message: str = "",
    payload: dict | None = None,
) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO video_generation_events (job_id,event_type,message,payload)
               VALUES (?,?,?,?)""",
            (job_id, event_type, message, json.dumps(payload or {}, ensure_ascii=False)),
        )
        row = conn.execute(
            "SELECT * FROM video_generation_events WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        item = dict(row)
        item["payload"] = json.loads(item.get("payload") or "{}")
        return item


def list_video_generation_events(job_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM video_generation_events WHERE job_id=? ORDER BY id",
            (job_id,),
        ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.get("payload") or "{}")
            events.append(item)
        return events


def claim_next_video_generation_job(owner: str, lease_seconds: int = 30) -> dict | None:
    modifier = f"+{max(1, int(lease_seconds))} seconds"
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT id FROM video_generation_jobs WHERE status='pending'
               ORDER BY created_at,id LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        job_id = row["id"]
        conn.execute(
            """UPDATE video_generation_jobs
               SET status='running',lease_owner=?,lease_expires_at=datetime('now',?),
                   heartbeat_at=datetime('now'),started_at=COALESCE(started_at,datetime('now')),
                   attempt=attempt+1,updated_at=datetime('now')
               WHERE id=? AND status='pending'""",
            (owner, modifier, job_id),
        )
        claimed = conn.execute(
            "SELECT * FROM video_generation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return _decode_video_job(claimed)


def renew_video_generation_lease(job_id: str, owner: str, lease_seconds: int = 30) -> bool:
    modifier = f"+{max(1, int(lease_seconds))} seconds"
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE video_generation_jobs
               SET heartbeat_at=datetime('now'),lease_expires_at=datetime('now',?),
                   updated_at=datetime('now')
               WHERE id=? AND lease_owner=? AND status IN ('running','cancel_requested')""",
            (modifier, job_id, owner),
        )
        return cur.rowcount == 1


def recover_expired_video_generation_jobs() -> int:
    with get_conn() as conn:
        expired_running = conn.execute(
            """SELECT id FROM video_generation_jobs
               WHERE status='running' AND lease_expires_at IS NOT NULL
                 AND lease_expires_at < datetime('now')"""
        ).fetchall()
        expired_cancel = conn.execute(
            """SELECT id FROM video_generation_jobs
               WHERE status='cancel_requested' AND lease_expires_at IS NOT NULL
                 AND lease_expires_at < datetime('now')"""
        ).fetchall()
        running = conn.execute(
            """UPDATE video_generation_jobs
               SET status='pending',lease_owner=NULL,lease_expires_at=NULL,
                   heartbeat_at=NULL,updated_at=datetime('now')
               WHERE status='running' AND lease_expires_at IS NOT NULL
                 AND lease_expires_at < datetime('now')"""
        )
        canceled = conn.execute(
            """UPDATE video_generation_jobs
               SET status='canceled',stage='canceled',canceled_at=datetime('now'),
                   finished_at=datetime('now'),lease_owner=NULL,lease_expires_at=NULL,
                   heartbeat_at=NULL,updated_at=datetime('now')
               WHERE status='cancel_requested' AND lease_expires_at IS NOT NULL
                 AND lease_expires_at < datetime('now')"""
        )
        for row in [*expired_running, *expired_cancel]:
            job = conn.execute(
                "SELECT * FROM video_generation_jobs WHERE id=?",
                (row["id"],),
            ).fetchone()
            _sync_video_project_for_job(conn, job)
        return running.rowcount + canceled.rowcount


# ==================== Semantic Media Assets ====================

def _normalized_tag(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _fts_query(value: str) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if len(normalized) < 3:
        return ""
    terms = []
    for word in normalized.split():
        if len(word) <= 3:
            terms.append(word)
        else:
            terms.extend(word[index:index + 3] for index in range(len(word) - 2))
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in dict.fromkeys(terms))


def _segment_tags(conn, segment_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT t.dimension,t.display_value AS value,st.confidence,st.source,
                  st.confirmed,st.updated_by
           FROM segment_tags st JOIN tags t ON t.id=st.tag_id
           WHERE st.segment_id=? ORDER BY t.dimension,t.display_value""",
        (segment_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["confirmed"] = bool(item["confirmed"])
        result.append(item)
    return result


def _refresh_segment_fts(conn, segment_id: int):
    row = conn.execute(
        "SELECT transcript,ocr_text,description,primary_category FROM asset_segments WHERE id=?",
        (segment_id,),
    ).fetchone()
    conn.execute("DELETE FROM asset_segment_fts WHERE segment_id=?", (str(segment_id),))
    if not row:
        return
    tags = " ".join(tag["value"] for tag in _segment_tags(conn, segment_id))
    content = " ".join(str(value or "") for value in (*row, tags)).strip()
    conn.execute(
        "INSERT INTO asset_segment_fts (segment_id,content) VALUES (?,?)",
        (str(segment_id), content),
    )


def create_asset_segment(data: dict) -> int:
    fields = (
        "asset_id", "segment_index", "start_ms", "end_ms", "preview_path",
        "thumbnail_path", "transcript", "ocr_text", "description", "primary_category", "primary_category_source",
        "quality_score", "orientation", "status", "processing_version",
    )
    defaults = {
        "start_ms": 0, "end_ms": 0, "transcript": "", "ocr_text": "",
        "description": "", "primary_category_source": "legacy", "quality_score": 0, "orientation": "unknown",
        "status": "active", "processing_version": "v1",
    }
    values = [data.get(field, defaults.get(field)) for field in fields]
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO asset_segments ({','.join(fields)})
                 VALUES ({','.join('?' for _ in fields)})
                 ON CONFLICT(asset_id,segment_index,processing_version) DO UPDATE SET
                    start_ms=excluded.start_ms,end_ms=excluded.end_ms,
                    preview_path=excluded.preview_path,thumbnail_path=excluded.thumbnail_path,
                    transcript=excluded.transcript,ocr_text=excluded.ocr_text,
                    description=excluded.description,primary_category=excluded.primary_category,
                    primary_category_source=excluded.primary_category_source,
                    quality_score=excluded.quality_score,orientation=excluded.orientation,
                    status=excluded.status""",
            values,
        )
        row = conn.execute(
            "SELECT id FROM asset_segments WHERE asset_id=? AND segment_index=? AND processing_version=?",
            (data["asset_id"], data["segment_index"], data.get("processing_version", "v1")),
        ).fetchone()
        segment_id = int(row["id"])
        _refresh_segment_fts(conn, segment_id)
        return segment_id


def get_asset_segment(segment_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM asset_segments WHERE id=?", (segment_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = _segment_tags(conn, segment_id)
        return item


def list_asset_segments(asset_id: int | None = None, status: str = "active", limit: int = 500) -> list[dict]:
    with get_conn() as conn:
        sql = """SELECT s.*,a.name AS asset_name,a.filepath AS asset_filepath,
                        a.file_type AS asset_file_type,a.hotspot_id AS asset_hotspot_id,a.source AS asset_source,
                        a.category AS asset_category,
                        a.rights_status AS asset_rights_status,a.source_url AS asset_source_url,
                        a.attribution AS asset_attribution,a.license AS asset_license,
                        a.event_at,a.created_at AS asset_created_at
                 FROM asset_segments s JOIN assets a ON a.id=s.asset_id WHERE 1=1"""
        params: list = []
        if asset_id is not None:
            sql += " AND s.asset_id=?"
            params.append(asset_id)
        if status:
            sql += " AND s.status=? AND a.status='active'"
            params.append(status)
        sql += " ORDER BY s.asset_id,s.segment_index LIMIT ?"
        params.append(max(1, min(int(limit), 2_000)))
        items = []
        for row in conn.execute(sql, params).fetchall():
            item = dict(row)
            item["tags"] = _segment_tags(conn, item["id"])
            items.append(item)
        return items


def replace_hotspot_event_clips(asset_id: int, hotspot_id: int, events: list[dict]) -> list[dict]:
    with get_conn() as conn:
        asset_row = conn.execute("SELECT duration FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset_row:
            raise ValueError("热点母片不存在")
        mother_duration_ms = round(float(asset_row["duration"] or 0) * 1000)
        old = conn.execute("SELECT id FROM hotspot_event_clips WHERE asset_id=?", (asset_id,)).fetchall()
        conn.executemany("DELETE FROM hotspot_event_segment_links WHERE event_clip_id=?", [(row["id"],) for row in old])
        conn.execute("DELETE FROM hotspot_event_clips WHERE asset_id=?", (asset_id,))
        created = []
        for event in events:
            start_ms = int(event.get("start_ms") or 0)
            end_ms = int(event.get("end_ms") or 0)
            if start_ms < 0 or end_ms <= start_ms:
                raise ValueError("热点事件片段时间范围无效")
            if mother_duration_ms and end_ms > mother_duration_ms:
                raise ValueError("热点事件片段超出母片时长")
            duration_ms = end_ms - start_ms
            first_segment = next((item for item in event.get("segments", []) if item.get("thumbnail_path")), None)
            evidence = {"segments": len(event.get("segments", [])), **dict(event.get("evidence") or {})}
            hook_kind = str(event.get("hook_kind") or "timely_event").strip() or "timely_event"
            if hook_kind not in {"timely_event", "generic_logistics"}:
                hook_kind = "timely_event"
            logistics_scenes = event.get("logistics_scenes") or []
            if not isinstance(logistics_scenes, list):
                logistics_scenes = []
            cur = conn.execute(
                """INSERT INTO hotspot_event_clips
                   (asset_id,hotspot_id,event_index,start_ms,end_ms,title_zh,title_en,location,
                   entities_json,keywords_json,evidence_json,confidence,review_status,duration_ms,
                    thumbnail_path,clip_status,library_origin,hook_kind,logistics_scenes_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (asset_id, hotspot_id, event["event_index"], start_ms, end_ms,
                 event["title_zh"], event["title_en"], event.get("location"),
                 json.dumps(event.get("entities", []), ensure_ascii=False),
                 json.dumps(event.get("keywords", []), ensure_ascii=False),
                 json.dumps(evidence, ensure_ascii=False),
                 event.get("confidence", 0), event.get("review_status", "review_required"),
                 duration_ms, (first_segment or {}).get("thumbnail_path"), "pending", "hotspot_event",
                 hook_kind, json.dumps(logistics_scenes, ensure_ascii=False)),
            )
            event_id = cur.lastrowid
            conn.execute(
                "UPDATE hotspot_event_clips SET virtual_asset_id=? WHERE id=?",
                (f"hotspot-event-{event_id}", event_id),
            )
            for sequence, segment in enumerate(event.get("segments", []), 1):
                segment_id = segment.get("id")
                if segment_id:
                    conn.execute(
                        "INSERT INTO hotspot_event_segment_links(event_clip_id,segment_id,sequence_no) VALUES(?,?,?)",
                        (event_id, segment_id, sequence),
                    )
            row = conn.execute("SELECT * FROM hotspot_event_clips WHERE id=?", (event_id,)).fetchone()
            created.append(dict(row))
        return created


def list_hotspot_event_clips(asset_id: int | None = None, hotspot_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM hotspot_event_clips WHERE 1=1"
    params: list = []
    if asset_id is not None:
        sql += " AND asset_id=?"; params.append(asset_id)
    if hotspot_id is not None:
        sql += " AND hotspot_id=?"; params.append(hotspot_id)
    sql += " ORDER BY asset_id,event_index"
    with get_conn() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            row["virtual_asset_id"] = row.get("virtual_asset_id") or f"hotspot-event-{row['id']}"
            row["duration_ms"] = int(row.get("duration_ms") or (row["end_ms"] - row["start_ms"]))
            row["library_origin"] = row.get("library_origin") or "hotspot_event"
            row["hook_kind"] = row.get("hook_kind") or "timely_event"
            row["entities"] = json.loads(row.pop("entities_json") or "[]")
            row["keywords"] = json.loads(row.pop("keywords_json") or "[]")
            row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
            row["logistics_scenes"] = json.loads(row.pop("logistics_scenes_json") or "[]")
        return rows


def enqueue_hotspot_discovery_request(topic: str, requested_by: int | None = None) -> dict:
    """Persist a targeted collection request when the confirmed Hook library is empty."""
    normalized = " ".join(str(topic or "").split())[:300]
    if not normalized:
        raise ValueError("定向采集主题不能为空")
    topic_key = normalized.casefold()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hotspot_discovery_requests WHERE topic_key=?", (topic_key,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE hotspot_discovery_requests SET status='pending',requested_by=?,error_message=NULL,stage='queued',updated_at=datetime('now') WHERE id=?",
                (requested_by, row["id"]),
            )
            return dict(conn.execute("SELECT * FROM hotspot_discovery_requests WHERE id=?", (row["id"],)).fetchone())
        cur = conn.execute(
            "INSERT INTO hotspot_discovery_requests(topic,topic_key,requested_by,status,stage) VALUES(?,?,?,'pending','queued')",
            (normalized, topic_key, requested_by),
        )
        return dict(conn.execute("SELECT * FROM hotspot_discovery_requests WHERE id=?", (cur.lastrowid,)).fetchone())


def get_hotspot_discovery_request(request_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM hotspot_discovery_requests WHERE id=?", (int(request_id),)).fetchone()
        return dict(row) if row else None


def update_hotspot_discovery_request(
    request_id: int,
    *,
    status: str | None = None,
    stage: str | None = None,
    error_message: str | None = None,
    matched_media_id: int | None = None,
) -> dict | None:
    fields: dict = {}
    if status is not None:
        fields["status"] = status
    if stage is not None:
        fields["stage"] = stage
    if error_message is not None:
        fields["error_message"] = error_message
    if matched_media_id is not None:
        fields["matched_media_id"] = int(matched_media_id)
    if not fields:
        return get_hotspot_discovery_request(request_id)
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            f"UPDATE hotspot_discovery_requests SET {','.join(f'{key}=?' for key in fields)} WHERE id=?",
            (*fields.values(), int(request_id)),
        )
        row = conn.execute("SELECT * FROM hotspot_discovery_requests WHERE id=?", (int(request_id),)).fetchone()
        return dict(row) if row else None


def cancel_misrouted_comparison_discovery_requests(limit: int = 200) -> list[dict]:
    """Archive pending discovery rows whose topic is comparison/evergreen, not hotspot news."""
    import chat_intent

    cancelled = []
    for item in list_hotspot_discovery_requests(status="pending", limit=limit):
        mode = chat_intent.classify_content_mode(item.get("topic") or "")
        if mode != "comparison_research":
            continue
        updated = update_hotspot_discovery_request(
            int(item["id"]),
            status="cancelled_misrouted",
            stage="archived",
            error_message="对比评测类主题不应进入热点补采，已归档误路由请求",
        )
        if updated:
            cancelled.append(updated)
    return cancelled


def list_hotspot_discovery_requests(status: str | None = None, limit: int = 50) -> list[dict]:
    query, params = "SELECT * FROM hotspot_discovery_requests", []
    if status:
        query += " WHERE status=?"; params.append(status)
    query += " ORDER BY updated_at DESC,id DESC LIMIT ?"; params.append(max(1, min(int(limit), 200)))
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def mark_hotspot_discovery_request_matched(request_ids: list[int], media_id: int) -> None:
    ids = []
    for value in request_ids:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in ids:
            ids.append(item)
    ids.sort()
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    with get_conn() as conn:
        conn.execute(
            f"""UPDATE hotspot_discovery_requests
                 SET status='matched',stage='hooks_ready',matched_media_id=?,error_message=NULL,updated_at=datetime('now')
                 WHERE id IN ({marks})""",
            (int(media_id), *ids),
        )


def get_hotspot_event_clip(event_clip_id: int) -> dict | None:
    items = list_hotspot_event_clips()
    return next((item for item in items if int(item["id"]) == int(event_clip_id)), None)


def update_hotspot_event_hook_kind(
    event_clip_id: int,
    *,
    hook_kind: str,
    logistics_scenes: list[str] | None = None,
) -> dict | None:
    """Mark a Hook as timely_event or generic_logistics opener."""
    kind = str(hook_kind or "timely_event").strip() or "timely_event"
    if kind not in {"timely_event", "generic_logistics"}:
        raise ValueError("hook_kind 必须是 timely_event 或 generic_logistics")
    fields = {
        "hook_kind": kind,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if logistics_scenes is not None:
        fields["logistics_scenes_json"] = json.dumps(
            [str(item) for item in logistics_scenes if item],
            ensure_ascii=False,
        )
    with get_conn() as conn:
        conn.execute(
            f"UPDATE hotspot_event_clips SET {','.join(f'{key}=?' for key in fields)} WHERE id=?",
            (*fields.values(), int(event_clip_id)),
        )
    return get_hotspot_event_clip(int(event_clip_id))


def backfill_hotspot_event_logistics_scenes(limit: int = 500) -> int:
    """Fill empty logistics_scenes_json from fact text for older Hooks."""
    import hotspot_lexicon

    updated = 0
    for event in list_hotspot_event_clips():
        if updated >= limit:
            break
        if event.get("logistics_scenes"):
            continue
        fact = " ".join(
            str(value or "")
            for value in (
                event.get("title_zh"),
                event.get("title_en"),
                (event.get("evidence") or {}).get("what_happened"),
            )
        )
        scenes = sorted(hotspot_lexicon.category_profile(fact, mode="event"))
        if not scenes:
            continue
        with get_conn() as conn:
            conn.execute(
                "UPDATE hotspot_event_clips SET logistics_scenes_json=?,updated_at=datetime('now') WHERE id=?",
                (json.dumps(scenes, ensure_ascii=False), int(event["id"])),
            )
        updated += 1
    return updated


def update_hotspot_event_clip_media(event_clip_id: int, clip_path: str | None,
                                    thumbnail_path: str | None, status: str,
                                    error: str | None = None) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE hotspot_event_clips SET clip_path=?,thumbnail_path=?,clip_status=?,clip_error=?,updated_at=datetime('now') WHERE id=?",
            (clip_path, thumbnail_path, status, error, event_clip_id),
        )
        row = conn.execute("SELECT * FROM hotspot_event_clips WHERE id=?", (event_clip_id,)).fetchone()
        return dict(row) if row else None


def update_asset_segment_classification(segment_id: int, primary_category: str, quality_score: float | None = None,
                                        source: str = "manual"):
    with get_conn() as conn:
        if quality_score is None:
            conn.execute("UPDATE asset_segments SET primary_category=?,primary_category_source=? WHERE id=?", (primary_category, source, segment_id))
        else:
            conn.execute(
                "UPDATE asset_segments SET primary_category=?,primary_category_source=?,quality_score=? WHERE id=?",
                (primary_category, source, quality_score, segment_id),
            )
        _refresh_segment_fts(conn, segment_id)


def create_asset_processing_job(asset_id: int, requested_by: int | None = None,
                                processing_version: str = "semantic-v1") -> str:
    job_id = uuid4().hex
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO asset_processing_jobs
               (id,asset_id,status,stage,progress,processing_version,requested_by)
               VALUES (?,?,'pending','waiting',0,?,?)""",
            (job_id, asset_id, processing_version, requested_by),
        )
        conn.execute(
            "UPDATE assets SET processing_status='pending',processing_version=? WHERE id=?",
            (processing_version, asset_id),
        )
    return job_id


def update_asset_semantic_state(asset_id: int, primary_category: str, processing_status: str,
                                rights_status: str | None = None, source: str = "model"):
    with get_conn() as conn:
        if rights_status is None:
            conn.execute(
                "UPDATE assets SET primary_category=?,primary_category_source=?,category=?,processing_status=? WHERE id=?",
                (primary_category, source, primary_category, processing_status, asset_id),
            )
        else:
            conn.execute(
                """UPDATE assets SET primary_category=?,primary_category_source=?,category=?,processing_status=?,rights_status=?
                   WHERE id=?""",
                (primary_category, source, primary_category, processing_status, rights_status, asset_id),
            )


def deactivate_asset_segments_except_version(asset_id: int, processing_version: str):
    """只有新版本完整入库后才隐藏旧分段，避免失败时失去可用素材。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE asset_segments SET status='superseded' WHERE asset_id=? AND processing_version<>? AND status='active'",
            (asset_id, processing_version),
        )


def update_asset_processing_job(job_id: str, **fields):
    allowed = {"status", "stage", "progress", "attempts", "error", "started_at"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            f"UPDATE asset_processing_jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
            (*values.values(), job_id),
        )
        row = conn.execute("SELECT asset_id,status,processing_version FROM asset_processing_jobs WHERE id=?", (job_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE assets SET processing_status=?,processing_version=? WHERE id=?",
                (row["status"], row["processing_version"], row["asset_id"]),
            )


def get_asset_processing_job(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM asset_processing_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_assets_needing_processing(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.* FROM assets a
               WHERE a.status='active' AND COALESCE(a.processing_status,'pending') NOT IN ('ready')
                 AND NOT EXISTS (
                   SELECT 1 FROM asset_processing_jobs j
                   WHERE j.asset_id=a.id AND j.status IN ('pending','running')
                 )
               ORDER BY a.id LIMIT ?""", (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(row) for row in rows]


def list_assets_needing_taxonomy_rebuild(processing_version: str, limit: int = 100) -> list[dict]:
    """选择尚未使用当前 taxonomy 的素材；人工确认素材不自动覆盖。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.* FROM assets a
               WHERE a.status='active'
                 AND COALESCE(a.primary_category_source,'legacy') <> 'manual'
                 AND COALESCE(a.processing_version,'') <> ?
                 AND NOT EXISTS (
                   SELECT 1 FROM asset_processing_jobs j
                   WHERE j.asset_id=a.id AND j.status IN ('pending','running')
                 )
               ORDER BY a.id LIMIT ?""",
            (processing_version, max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(row) for row in rows]


def recover_interrupted_asset_processing_jobs() -> int:
    """进程启动时只标记正在执行的任务为中断；排队中的 pending 由启动逻辑重新派发。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,asset_id FROM asset_processing_jobs WHERE status='running'"
        ).fetchall()
        if rows:
            conn.execute(
                """UPDATE asset_processing_jobs SET status='failed',stage='interrupted',
                   error='服务重启导致任务中断，可批量重新分析',updated_at=datetime('now')
                   WHERE status='running'"""
            )
            conn.execute(
                """UPDATE assets SET processing_status='pending'
                   WHERE id IN (SELECT asset_id FROM asset_processing_jobs WHERE stage='interrupted')"""
            )
        return len(rows)


def list_pending_asset_processing_job_ids(limit: int = 1_000) -> list[str]:
    """返回待派发的素材分析任务，供服务启动或管理员批量续跑。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id FROM asset_processing_jobs
               WHERE status='pending'
               ORDER BY created_at, id
               LIMIT ?""",
            (max(1, min(int(limit), 5_000)),),
        ).fetchall()
        return [str(row["id"]) for row in rows]


_ACTIVE_LOCAL_IMPORT_STATUSES = {
    "pending", "scanning", "importing", "processing", "cancel_requested",
}


def _local_asset_import_row(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["cancel_requested"] = bool(item.get("cancel_requested"))
    try:
        item["errors"] = json.loads(item.get("errors") or "[]")
    except (TypeError, json.JSONDecodeError):
        item["errors"] = []
    return item


def create_or_get_local_asset_import_job(root_path: str, requested_by: int) -> tuple[dict, bool]:
    normalized_root = str(Path(root_path).expanduser().resolve())
    placeholders = ",".join("?" for _ in _ACTIVE_LOCAL_IMPORT_STATUSES)
    active = tuple(sorted(_ACTIVE_LOCAL_IMPORT_STATUSES))
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT * FROM local_asset_import_jobs
                WHERE root_path=? AND requested_by=? AND status IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1""",
            (normalized_root, requested_by, *active),
        ).fetchone()
        if row:
            return _local_asset_import_row(row), False
        job_id = uuid4().hex
        conn.execute(
            """INSERT INTO local_asset_import_jobs (id,root_path,requested_by)
               VALUES (?,?,?)""",
            (job_id, normalized_root, requested_by),
        )
        row = conn.execute(
            "SELECT * FROM local_asset_import_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return _local_asset_import_row(row), True


def get_local_asset_import_job(job_id: str, requested_by: int | None = None) -> dict | None:
    sql = "SELECT * FROM local_asset_import_jobs WHERE id=?"
    params: list = [job_id]
    if requested_by is not None:
        sql += " AND requested_by=?"
        params.append(requested_by)
    with get_conn() as conn:
        return _local_asset_import_row(conn.execute(sql, params).fetchone())


def update_local_asset_import_job(job_id: str, **fields) -> dict | None:
    allowed = {
        "status", "stage", "total", "scanned", "imported", "duplicated",
        "skipped", "failed", "current_file", "cancel_requested", "errors",
        "started_at", "finished_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if "errors" in values and not isinstance(values["errors"], str):
        values["errors"] = json.dumps(values["errors"], ensure_ascii=False)
    if "cancel_requested" in values:
        values["cancel_requested"] = int(bool(values["cancel_requested"]))
    if not values:
        return get_local_asset_import_job(job_id)
    values["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            f"UPDATE local_asset_import_jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
            (*values.values(), job_id),
        )
        row = conn.execute(
            "SELECT * FROM local_asset_import_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return _local_asset_import_row(row)


def request_local_asset_import_cancel(job_id: str, requested_by: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM local_asset_import_jobs WHERE id=? AND requested_by=?",
            (job_id, requested_by),
        ).fetchone()
        if not row:
            return None
        if row["status"] in _ACTIVE_LOCAL_IMPORT_STATUSES:
            conn.execute(
                """UPDATE local_asset_import_jobs
                   SET status='cancel_requested',stage='cancel_requested',cancel_requested=1,
                       updated_at=? WHERE id=?""",
                (datetime.now().isoformat(timespec="seconds"), job_id),
            )
        row = conn.execute(
            "SELECT * FROM local_asset_import_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return _local_asset_import_row(row)


def list_active_local_asset_import_jobs(requested_by: int) -> list[dict]:
    placeholders = ",".join("?" for _ in _ACTIVE_LOCAL_IMPORT_STATUSES)
    active = tuple(sorted(_ACTIVE_LOCAL_IMPORT_STATUSES))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM local_asset_import_jobs
                WHERE requested_by=? AND status IN ({placeholders})
                ORDER BY created_at DESC""",
            (requested_by, *active),
        ).fetchall()
        return [_local_asset_import_row(row) for row in rows]


def recover_interrupted_local_asset_import_jobs() -> int:
    placeholders = ",".join("?" for _ in _ACTIVE_LOCAL_IMPORT_STATUSES)
    active = tuple(sorted(_ACTIVE_LOCAL_IMPORT_STATUSES))
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM local_asset_import_jobs WHERE status IN ({placeholders})", active
        ).fetchall()
        if rows:
            conn.execute(
                f"""UPDATE local_asset_import_jobs
                    SET status='interrupted',stage='interrupted',current_file='',
                        finished_at=?,updated_at=? WHERE status IN ({placeholders})""",
                (now, now, *active),
            )
        return len(rows)


def replace_segment_tags(segment_id: int, tags: list[dict], updated_by: int | None = None):
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM asset_segments WHERE id=?", (segment_id,)).fetchone():
            raise ValueError("镜头不存在")
        conn.execute("DELETE FROM segment_tags WHERE segment_id=?", (segment_id,))
        for raw in tags:
            dimension = str(raw.get("dimension") or "").strip()
            value = str(raw.get("value") or "").strip()
            normalized = _normalized_tag(value)
            if not dimension or not normalized:
                continue
            conn.execute(
                """INSERT INTO tags (dimension,normalized_value,display_value) VALUES (?,?,?)
                   ON CONFLICT(dimension,normalized_value) DO UPDATE SET display_value=excluded.display_value""",
                (dimension, normalized, value),
            )
            tag_id = conn.execute(
                "SELECT id FROM tags WHERE dimension=? AND normalized_value=?",
                (dimension, normalized),
            ).fetchone()["id"]
            source = str(raw.get("source") or "rule")
            confirmed = bool(raw.get("confirmed", source == "manual"))
            conn.execute(
                """INSERT INTO segment_tags
                   (segment_id,tag_id,confidence,source,confirmed,updated_by)
                   VALUES (?,?,?,?,?,?)""",
                (segment_id, tag_id, float(raw.get("confidence") or 0), source, int(confirmed), updated_by),
            )
        _refresh_segment_fts(conn, segment_id)


def search_asset_segments(query: str = "", limit: int = 50, status: str = "active") -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with get_conn() as conn:
        fts_query = _fts_query(query)
        if fts_query:
            rows = conn.execute(
                """SELECT s.* FROM asset_segment_fts f
                   JOIN asset_segments s ON s.id=CAST(f.segment_id AS INTEGER)
                   WHERE asset_segment_fts MATCH ? AND s.status=?
                   ORDER BY bm25(asset_segment_fts),s.quality_score DESC LIMIT ?""",
                (fts_query, status, limit),
            ).fetchall()
        elif query:
            pattern = f"%{query}%"
            rows = conn.execute(
                """SELECT DISTINCT s.* FROM asset_segments s
                   LEFT JOIN segment_tags st ON st.segment_id=s.id
                   LEFT JOIN tags t ON t.id=st.tag_id
                   WHERE s.status=? AND (s.transcript LIKE ? OR s.ocr_text LIKE ?
                     OR s.description LIKE ? OR t.display_value LIKE ?)
                   ORDER BY s.quality_score DESC LIMIT ?""",
                (status, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM asset_segments WHERE status=? ORDER BY quality_score DESC,id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = _segment_tags(conn, item["id"])
            result.append(item)
        return result


def _refresh_inspiration_fts(conn, inspiration_id: int):
    row = conn.execute(
        "SELECT title,summary,author,primary_category,source_type FROM inspiration_items WHERE id=?",
        (inspiration_id,),
    ).fetchone()
    conn.execute("DELETE FROM inspiration_fts WHERE inspiration_id=?", (str(inspiration_id),))
    if row:
        conn.execute(
            "INSERT INTO inspiration_fts (inspiration_id,content) VALUES (?,?)",
            (str(inspiration_id), " ".join(str(value or "") for value in row)),
        )


def upsert_inspiration_item(data: dict) -> tuple[int, bool]:
    canonical_url = str(data.get("canonical_url") or data.get("source_url") or "").strip()
    if not canonical_url:
        raise ValueError("灵感链接不能为空")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM inspiration_items WHERE canonical_url=?", (canonical_url,)
        ).fetchone()
        values = {
            "source_type": data.get("source_type", "other_link"),
            "source_role": data.get("source_role", "creative_reference"),
            "source_url": data.get("source_url", canonical_url),
            "canonical_url": canonical_url,
            "title": str(data.get("title") or canonical_url)[:300],
            "summary": str(data.get("summary") or "")[:4000],
            "author": str(data.get("author") or "")[:300],
            "published_at": data.get("published_at"),
            "thumbnail_url": data.get("thumbnail_url"),
            "media_kind": data.get("media_kind", "link"),
            "primary_category": data.get("primary_category"),
            "rights_status": data.get("rights_status", "unknown"),
            "license_name": data.get("license_name"),
            "attribution": data.get("attribution"),
            "rights_evidence_url": data.get("rights_evidence_url"),
            "materialization_status": data.get("materialization_status", "reference_only"),
            "asset_id": data.get("asset_id"),
            "hotspot_id": data.get("hotspot_id"),
            "created_by": data.get("created_by"),
        }
        if existing:
            inspiration_id = int(existing["id"])
            conn.execute(
                """UPDATE inspiration_items SET source_type=?,source_role=?,source_url=?,title=?,summary=?,
                   author=?,published_at=?,thumbnail_url=?,media_kind=?,primary_category=?,rights_status=?,
                   license_name=COALESCE(?,license_name),attribution=COALESCE(?,attribution),
                   rights_evidence_url=COALESCE(?,rights_evidence_url),materialization_status=?,
                   asset_id=COALESCE(?,asset_id),hotspot_id=COALESCE(?,hotspot_id),
                   updated_at=datetime('now') WHERE id=?""",
                tuple(values[key] for key in (
                    "source_type", "source_role", "source_url", "title", "summary", "author",
                    "published_at", "thumbnail_url", "media_kind", "primary_category", "rights_status",
                    "license_name", "attribution", "rights_evidence_url", "materialization_status",
                    "asset_id", "hotspot_id",
                )) + (inspiration_id,),
            )
            created = False
        else:
            fields = tuple(values)
            cur = conn.execute(
                f"INSERT INTO inspiration_items ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                tuple(values[field] for field in fields),
            )
            inspiration_id = int(cur.lastrowid)
            created = True
        _refresh_inspiration_fts(conn, inspiration_id)
        return inspiration_id, created


def get_inspiration_item(inspiration_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM inspiration_items WHERE id=?", (inspiration_id,)).fetchone()
        return dict(row) if row else None


def list_inspiration_items(query: str = "", source_type: str | None = None,
                           status: str = "active", limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with get_conn() as conn:
        params: list = []
        if query and _fts_query(query):
            sql = """SELECT i.* FROM inspiration_fts f
                     JOIN inspiration_items i ON i.id=CAST(f.inspiration_id AS INTEGER)
                     WHERE inspiration_fts MATCH ?"""
            params.append(_fts_query(query))
        else:
            sql = "SELECT i.* FROM inspiration_items i WHERE 1=1"
            if query:
                sql += " AND (i.title LIKE ? OR i.summary LIKE ? OR i.author LIKE ?)"
                params.extend([f"%{query}%"] * 3)
        if source_type:
            sql += " AND i.source_type=?"
            params.append(source_type)
        if status:
            sql += " AND i.status=?"
            params.append(status)
        sql += " ORDER BY i.published_at DESC,i.created_at DESC,i.id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def update_inspiration_rights(inspiration_id: int, rights_status: str, license_name: str,
                              attribution: str, evidence_url: str, confirmed_by: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE inspiration_items SET rights_status=?,license_name=?,attribution=?,
               rights_evidence_url=?,rights_confirmed_by=?,rights_confirmed_at=datetime('now'),
               updated_at=datetime('now') WHERE id=?""",
            (rights_status, license_name, attribution, evidence_url, confirmed_by, inspiration_id),
        )


def update_inspiration_materialization(inspiration_id: int, status: str, asset_id: int | None = None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE inspiration_items SET materialization_status=?,asset_id=COALESCE(?,asset_id),
               updated_at=datetime('now') WHERE id=?""", (status, asset_id, inspiration_id),
        )


def create_match_session(created_by: int, source_payload: dict) -> str:
    session_id = uuid4().hex
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO match_sessions (id,created_by,source_payload) VALUES (?,?,?)",
            (session_id, created_by, json.dumps(source_payload, ensure_ascii=False)),
        )
    return session_id


def create_semantic_atom(session_id: str, data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO semantic_atoms
               (session_id,position,text,semantics,duration_ms,constraints)
               VALUES (?,?,?,?,?,?)""",
            (session_id, int(data["position"]), str(data.get("text") or ""),
             json.dumps(data.get("semantics") or {}, ensure_ascii=False),
             int(data.get("duration_ms") or 0),
             json.dumps(data.get("constraints") or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def replace_match_candidates(atom_id: int, candidates: list[dict]):
    with get_conn() as conn:
        conn.execute("DELETE FROM match_candidates WHERE atom_id=?", (atom_id,))
        conn.executemany(
            """INSERT INTO match_candidates
               (atom_id,segment_id,rank,match_score,reasons,review_required)
               VALUES (?,?,?,?,?,?)""",
            [(atom_id, item["segment_id"], int(item["rank"]), float(item["match_score"]),
              json.dumps(item.get("reasons") or [], ensure_ascii=False),
              int(bool(item.get("review_required")))) for item in candidates],
        )


def update_semantic_atom_selection(atom_id: int, segment_id: int | None, locked: bool = False, review_confirmed: bool = False):
    with get_conn() as conn:
        conn.execute(
            """UPDATE semantic_atoms SET selected_segment_id=?,locked=?,review_confirmed=?
               WHERE id=?""",
            (segment_id, int(locked), int(review_confirmed), atom_id),
        )


def add_match_feedback(session_id: str, atom_id: int, segment_id: int | None,
                       actor_id: int, action: str, reason: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO match_feedback
               (session_id,atom_id,segment_id,actor_id,action,reason) VALUES (?,?,?,?,?,?)""",
            (session_id, atom_id, segment_id, actor_id, action, reason),
        )
        return int(cur.lastrowid)


def get_match_session(session_id: str, created_by: int | None = None) -> dict | None:
    with get_conn() as conn:
        sql, params = "SELECT * FROM match_sessions WHERE id=?", [session_id]
        if created_by is not None:
            sql += " AND created_by=?"
            params.append(created_by)
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        result = dict(row)
        atoms = []
        for atom_row in conn.execute(
            "SELECT * FROM semantic_atoms WHERE session_id=? ORDER BY position", (session_id,)
        ).fetchall():
            atom = dict(atom_row)
            atom["semantics"] = json.loads(atom["semantics"] or "{}")
            atom["constraints"] = json.loads(atom["constraints"] or "{}")
            atom["locked"] = bool(atom["locked"])
            atom["review_confirmed"] = bool(atom["review_confirmed"])
            candidates = conn.execute(
                "SELECT * FROM match_candidates WHERE atom_id=? ORDER BY rank", (atom["id"],)
            ).fetchall()
            atom["candidates"] = []
            for candidate_row in candidates:
                candidate = dict(candidate_row)
                candidate["reasons"] = json.loads(candidate["reasons"] or "[]")
                candidate["review_required"] = bool(candidate["review_required"])
                atom["candidates"].append(candidate)
            atoms.append(atom)
        result["atoms"] = atoms
        return result
