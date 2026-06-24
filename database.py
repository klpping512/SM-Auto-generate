"""SQLite database for SA-LogiFlow MVP."""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from models import Platform, TaskStatus, AccountStatus

DB_PATH = Path(__file__).parent / "data" / "logiflow.db"


def get_conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            name TEXT NOT NULL,
            account_id TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'active',
            config_summary TEXT DEFAULT '',
            last_sync TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            platform TEXT NOT NULL,
            hashtags TEXT DEFAULT '[]',
            status TEXT DEFAULT 'queued',
            scheduled_at TEXT,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS publish_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER,
            platform TEXT,
            title TEXT,
            status TEXT,
            published_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (queue_id) REFERENCES queue(id)
        );
    """)
    conn.commit()
    conn.close()


# --- Accounts CRUD ---
def get_accounts(platform: str = None):
    conn = get_conn()
    if platform:
        rows = conn.execute("SELECT * FROM accounts WHERE platform=? ORDER BY id", (platform,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_account(platform, name, account_id, config_summary=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO accounts (platform, name, account_id, status, config_summary, last_sync) VALUES (?,?,?,?,?,?)",
        (platform, name, account_id, "active", config_summary, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


def delete_account(account_id):
    conn = get_conn()
    conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()


# --- Queue CRUD ---
def get_queue(status: str = None, platform: str = None):
    conn = get_conn()
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
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["hashtags"] = json.loads(d.get("hashtags", "[]"))
        result.append(d)
    return result


def add_to_queue(title, body, platform, hashtags=None, scheduled_at=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO queue (title, body, platform, hashtags, status, scheduled_at) VALUES (?,?,?,?,?,?)",
        (title, body, platform, json.dumps(hashtags or []), "queued", scheduled_at)
    )
    conn.commit()
    conn.close()


def update_queue_status(item_id, status, error_msg=None):
    conn = get_conn()
    conn.execute("UPDATE queue SET status=?, error_msg=? WHERE id=?", (status, error_msg, item_id))
    conn.commit()
    conn.close()


def delete_queue_item(item_id):
    conn = get_conn()
    conn.execute("DELETE FROM queue WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def get_queue_stats():
    conn = get_conn()
    stats = {}
    for status in ["queued", "reviewing", "published", "failed"]:
        stats[status] = conn.execute("SELECT COUNT(*) FROM queue WHERE status=?", (status,)).fetchone()[0]
    stats["total"] = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
    conn.close()
    return stats


def get_recent_activity(limit=10):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM queue ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["hashtags"] = json.loads(d.get("hashtags", "[]"))
        result.append(d)
    return result
