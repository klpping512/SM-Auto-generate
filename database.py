"""SQLite database for SA-LogiFlow v3.0."""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "logiflow.db"


@contextmanager
def get_conn():
    """Context manager: auto-commit on success, rollback on error."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
                created_at TEXT DEFAULT (datetime('now'))
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
                error TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );
        """)
        # 迁移：为旧数据库添加缺失的列
        _ensure_column(conn, "queue", "created_by", "INTEGER")
        _ensure_column(conn, "queue", "reviewer_id", "INTEGER")
        _ensure_column(conn, "queue", "review_note", "TEXT")
        _ensure_column(conn, "queue", "reviewed_at", "TEXT")
        _ensure_column(conn, "queue", "retry_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "accounts", "credentials", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "queue", "attachments", "TEXT DEFAULT '[]'")
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

def get_accounts(platform: str = None):
    with get_conn() as conn:
        if platform:
            rows = conn.execute("SELECT * FROM accounts WHERE platform=? ORDER BY id", (platform,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def create_account(platform, name, account_id, config_summary="", credentials="{}"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (platform, name, account_id, status, config_summary, credentials, last_sync) VALUES (?,?,?,?,?,?,?)",
            (platform, name, account_id, "active", config_summary, credentials, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )


def delete_account(account_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))


def update_account_status(account_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET status=? WHERE id=?", (status, account_id))


def update_account_credentials(account_id: str, credentials: str):
    """按业务 account_id（非自增主键）更新凭据 JSON，并刷新 last_sync。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET credentials=?, last_sync=? WHERE account_id=?",
            (credentials, datetime.now().strftime("%Y-%m-%d %H:%M"), account_id),
        )


# ==================== Queue ====================

def get_queue(status: str = None, platform: str = None):
    with get_conn() as conn:
        query = "SELECT * FROM queue WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if platform:
            query += " AND platform=?"
            params.append(platform)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_parse_queue_row(r) for r in rows]


def get_queue_item_by_id(item_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM queue WHERE id=?", (item_id,)).fetchone()
        return _parse_queue_row(row) if row else None


def add_to_queue(title, body, platform, hashtags=None, scheduled_at=None, status="draft", created_by=None, attachments=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO queue (title, body, platform, hashtags, status, scheduled_at, created_by, attachments) VALUES (?,?,?,?,?,?,?,?)",
            (title, body, platform, json.dumps(hashtags or []), status, scheduled_at, created_by, json.dumps(attachments or [], ensure_ascii=False)),
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
        conn.execute("DELETE FROM queue WHERE id=?", (item_id,))


def get_queue_stats():
    with get_conn() as conn:
        stats = {}
        for status in ["draft", "pending_review", "approved", "queued", "published", "failed"]:
            stats[status] = conn.execute("SELECT COUNT(*) FROM queue WHERE status=?", (status,)).fetchone()[0]
        stats["total"] = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        return stats


def get_recent_activity(limit=10):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM queue ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
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
    return d


# ==================== Publish Log ====================

def add_publish_log(queue_id: int, platform: str, title: str, status: str, error_msg: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO publish_log (queue_id, platform, title, status, error_msg) VALUES (?,?,?,?,?)",
            (queue_id, platform, title, status, error_msg),
        )
    logger.info("发布日志: queue_id=%d, platform=%s, status=%s", queue_id, platform, status)


def get_publish_logs(limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM publish_log ORDER BY published_at DESC LIMIT ?", (limit,)).fetchall()
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

def get_weekly_stats() -> dict:
    """Get stats for the current week (Monday to now)."""
    from datetime import timedelta
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d 00:00")
    with get_conn() as conn:
        published = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status='published' AND created_at >= ?", (monday,)
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status='failed' AND created_at >= ?", (monday,)
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE created_at >= ?", (monday,)
        ).fetchone()[0]
        # Most active platform
        row = conn.execute(
            "SELECT platform, COUNT(*) as cnt FROM queue WHERE created_at >= ? GROUP BY platform ORDER BY cnt DESC LIMIT 1",
            (monday,),
        ).fetchone()
        top_platform = row["platform"] if row else "-"
        return {"published": published, "failed": failed, "total": total, "top_platform": top_platform}


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
            sql += " AND category=?"; params.append(category)
        if status:
            sql += " AND status=?"; params.append(status)
        if query:
            sql += " AND name LIKE ?"; params.append(f"%{query}%")
        sql += " ORDER BY created_at DESC, id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


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


def update_asset(asset_id: int, name: str, category: str, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE assets SET name=?, category=?, status=? WHERE id=?", (name, category, status, asset_id))


def asset_is_referenced(asset_id: int) -> bool:
    needle = f'"asset_id": {asset_id}'
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM video_render_jobs WHERE script LIKE ? LIMIT 1", (f"%{needle}%",)).fetchone() is not None


def delete_asset(asset_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))


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
        return result


def update_render_job(job_id: str, **fields):
    allowed = {"status", "stage", "progress", "output_path", "error"}
    values = {key: value for key, value in fields.items() if key in allowed}
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
