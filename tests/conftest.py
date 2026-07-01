from pathlib import Path
import pytest
import database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """每个测试用独立临时 SQLite，避免污染 data/logiflow.db。"""
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path / "test.db"))
    db.init_db()
    # 预插入 queue 种子行，满足 publish_log.queue_id 外键约束
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO queue (id, title, body, platform, status) VALUES (?,?,?,?,?)",
            [
                (1, "seed-1", "", "reddit", "published"),
                (2, "seed-2", "", "reddit", "published"),
            ],
        )
    return db
