from pathlib import Path
import pytest
import database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """每个测试用独立临时 SQLite，避免污染 data/logiflow.db。"""
    monkeypatch.setattr(db, "DB_PATH", Path(tmp_path / "test.db"))
    db.init_db()
    return db
