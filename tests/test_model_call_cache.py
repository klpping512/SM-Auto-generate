def test_model_call_cache_expires_by_ttl_and_enforces_max_rows(tmp_db):
    tmp_db.create_model_budget("cache-job", 10, 1_000, 1_000)
    for index in range(4):
        tmp_db.record_model_call(
            "cache-job",
            "text",
            "demo-model",
            f"cache-key-{index}",
            1,
            1,
            0.01,
            {"content": f"response-{index}"},
        )
    with tmp_db.get_conn() as conn:
        conn.execute(
            """UPDATE model_call_cache
               SET created_at=datetime('now','-40 days'),
                   last_accessed_at=datetime('now','-40 days')
               WHERE cache_key='cache-key-0'"""
        )
        conn.execute(
            """UPDATE model_call_cache
               SET last_accessed_at=datetime('now','-1 day')
               WHERE cache_key='cache-key-1'"""
        )

    report = tmp_db.cleanup_model_call_cache(ttl_days=30, max_rows=2)

    assert report["expired"] == 1
    assert report["remaining"] == 2
    assert tmp_db.get_model_cache("cache-key-0") is None
    assert tmp_db.get_model_cache("cache-key-1") is None
    assert tmp_db.get_model_cache("cache-key-2") is not None
    assert tmp_db.get_model_cache("cache-key-3") is not None


def test_model_cache_hit_refreshes_last_accessed_at(tmp_db):
    tmp_db.create_model_budget("hit-job", 10, 1_000, 1_000)
    tmp_db.record_model_call(
        "hit-job", "text", "demo-model", "hit-key", 1, 1, 0.01, {"content": "ok"},
    )
    with tmp_db.get_conn() as conn:
        conn.execute(
            """UPDATE model_call_cache
               SET last_accessed_at=datetime('now','-2 days') WHERE cache_key='hit-key'"""
        )
        before = conn.execute(
            "SELECT last_accessed_at FROM model_call_cache WHERE cache_key='hit-key'"
        ).fetchone()[0]
    hit = tmp_db.record_model_call(
        "hit-job", "text", "demo-model", "hit-key", 1, 1, 0.01, {"content": "ignored"},
    )
    assert hit["cache_hit"] is True
    with tmp_db.get_conn() as conn:
        after = conn.execute(
            "SELECT last_accessed_at FROM model_call_cache WHERE cache_key='hit-key'"
        ).fetchone()[0]
    assert after > before
