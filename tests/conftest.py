from pathlib import Path
import pytest
import database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """每个测试用独立临时 SQLite，避免污染 data/logiflow.db。"""
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path / "test.db"))
    db.init_db()
    # 预插入中性 queue 种子行，仅为满足 publish_log.queue_id 外键约束；
    # 用 __seed__ 平台 + draft 状态，避免与任何按 platform/status 过滤的测试查询相撞。
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO queue (id, title, body, platform, status) VALUES (?,?,?,?,?)",
            [(i, f"seed-{i}", "", "__seed__", "draft") for i in range(1, 6)],
        )
    return db
