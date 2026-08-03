"""SQLite write queue and schema migrations."""


def test_write_queue_serializes_tasks(tmp_db):
    import sqlite_write_queue

    results = []

    def task(value):
        results.append(value)
        return value * 2

    assert sqlite_write_queue.submit_write(lambda: task(1)) == 2
    assert sqlite_write_queue.submit_write(lambda: task(2)) == 4
    assert results == [1, 2]
    stats = sqlite_write_queue.get_sqlite_health()
    assert stats["completed"] >= 2
    assert "wal_size_bytes" in stats


def test_schema_migrations_recorded(tmp_db):
    versions = {item["version"] for item in tmp_db.list_schema_migrations()}
    assert "2026-08-03-delivery-loop" in versions
