"""SQLite database for SA-LogiFlow v2.0."""
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
        """)
    logger.info("数据库初始化完成: %s", DB_PATH)


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


def add_to_queue(title, body, platform, hashtags=None, scheduled_at=None, status="draft", created_by=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO queue (title, body, platform, hashtags, status, scheduled_at, created_by) VALUES (?,?,?,?,?,?,?)",
            (title, body, platform, json.dumps(hashtags or []), status, scheduled_at, created_by),
        )


def update_queue_status(item_id, status, error_msg=None):
    with get_conn() as conn:
        conn.execute("UPDATE queue SET status=?, error_msg=? WHERE id=?", (status, error_msg, item_id))


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
