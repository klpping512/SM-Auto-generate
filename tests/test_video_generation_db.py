import json


def _user(db, name="video-owner"):
    return db.create_user(name, "hashed", role="admin", display_name="Video Owner")


def _project_and_revision(db, user_id):
    project = db.create_video_project(
        created_by=user_id,
        source_type="chat",
        source_snapshot={"topic": "南非海外仓"},
        title="海外仓入库",
        target_orientation="portrait",
    )
    revision = db.create_video_project_revision(
        project["id"],
        {"title": "海外仓入库", "voice": "冰糖", "scenes": []},
        created_by=user_id,
    )
    return project, revision


def test_video_generation_schema_is_created_idempotently(tmp_db):
    tmp_db.init_db()
    with tmp_db.get_conn() as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert {
        "video_projects",
        "video_project_revisions",
        "video_generation_jobs",
        "video_generation_events",
    } <= tables


def test_project_revision_is_versioned_and_returned_as_json(tmp_db):
    user_id = _user(tmp_db)
    project, first = _project_and_revision(tmp_db, user_id)
    second = tmp_db.create_video_project_revision(
        project["id"],
        {"title": "修改后", "voice": "茉莉", "scenes": [{"scene": 1}]},
        created_by=user_id,
    )

    loaded = tmp_db.get_video_project(project["id"], created_by=user_id)

    assert first["revision_no"] == 1
    assert second["revision_no"] == 2
    assert loaded["current_revision_id"] == second["id"]
    assert loaded["current_revision"]["payload"]["voice"] == "茉莉"
    assert json.loads(loaded["source_snapshot"])["topic"] == "南非海外仓"
    assert tmp_db.get_video_project(project["id"], created_by=user_id + 99) is None


def test_generation_job_is_idempotent_for_same_revision(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)

    first, created = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "same-key"
    )
    second, created_again = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "same-key"
    )

    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]
    assert len(tmp_db.list_active_video_generation_jobs(user_id)) == 1


def test_idempotency_key_is_isolated_between_users(tmp_db):
    first_user = _user(tmp_db, "first-video-owner")
    second_user = _user(tmp_db, "second-video-owner")
    first_project, first_revision = _project_and_revision(tmp_db, first_user)
    second_project, second_revision = _project_and_revision(tmp_db, second_user)

    first, _ = tmp_db.create_or_get_video_generation_job(
        first_project["id"], first_revision["id"], first_user, "shared-client-key"
    )
    second, created = tmp_db.create_or_get_video_generation_job(
        second_project["id"], second_revision["id"], second_user, "shared-client-key"
    )

    assert created is True
    assert first["id"] != second["id"]


def test_terminal_job_allows_new_job_with_same_idempotency_key(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    first, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "repeat-key"
    )
    tmp_db.update_video_generation_job(first["id"], status="failed", stage="planning", error_code="boom")

    second, created = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "repeat-key"
    )

    assert created is True
    assert second["id"] != first["id"]


def test_cancel_is_immediate_for_pending_and_requested_for_running(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    pending, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "pending-key"
    )
    canceled = tmp_db.request_video_generation_cancel(pending["id"], user_id)

    running, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "running-key"
    )
    tmp_db.update_video_generation_job(running["id"], status="running", stage="final_rendering")
    requested = tmp_db.request_video_generation_cancel(running["id"], user_id)
    repeated = tmp_db.request_video_generation_cancel(running["id"], user_id)

    assert canceled["status"] == "canceled"
    assert canceled["canceled_at"]
    assert requested["status"] == "cancel_requested"
    assert requested["cancel_requested_at"]
    assert repeated["status"] == "cancel_requested"


def test_job_events_and_quality_report_are_decoded(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "event-key"
    )
    tmp_db.update_video_generation_job(
        job["id"],
        stage="match_quality_check",
        progress=45,
        quality_report={"score": 72, "issues": ["scene-2"]},
    )
    tmp_db.add_video_generation_event(
        job["id"], "stage_changed", "素材匹配检查", {"progress": 45}
    )

    loaded = tmp_db.get_video_generation_job(job["id"], created_by=user_id)
    events = tmp_db.list_video_generation_events(job["id"])

    assert loaded["quality_report"]["issues"] == ["scene-2"]
    assert events[0]["payload"] == {"progress": 45}


def test_worker_lease_prevents_double_claim_and_expired_job_recovers(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "lease-key"
    )

    claimed = tmp_db.claim_next_video_generation_job("worker-a", lease_seconds=30)
    blocked = tmp_db.claim_next_video_generation_job("worker-b", lease_seconds=30)
    assert claimed["id"] == job["id"]
    assert claimed["status"] == "running"
    assert blocked is None

    with tmp_db.get_conn() as conn:
        conn.execute(
            "UPDATE video_generation_jobs SET lease_expires_at=datetime('now','-1 second') WHERE id=?",
            (job["id"],),
        )
    assert tmp_db.recover_expired_video_generation_jobs() == 1
    reclaimed = tmp_db.claim_next_video_generation_job("worker-b", lease_seconds=30)
    assert reclaimed["id"] == job["id"]
    assert reclaimed["lease_owner"] == "worker-b"


def test_expired_cancel_requested_job_recovers_as_canceled(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "cancel-recovery-key"
    )
    tmp_db.update_video_generation_job(
        job["id"], status="cancel_requested", lease_owner="dead-worker",
        lease_expires_at="2000-01-01 00:00:00",
    )

    assert tmp_db.recover_expired_video_generation_jobs() == 1
    recovered = tmp_db.get_video_generation_job(job["id"])
    assert recovered["status"] == "canceled"
    assert recovered["canceled_at"]


def test_project_status_tracks_job_terminal_and_review_states(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    job, created = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "sync-key"
    )
    assert created is True
    loaded = tmp_db.get_video_project(project["id"], created_by=user_id)
    assert loaded["status"] == "generating"
    assert loaded["active_job_id"] == job["id"]

    tmp_db.update_video_generation_job(job["id"], status="needs_review", stage="preview_quality_check")
    loaded = tmp_db.get_video_project(project["id"], created_by=user_id)
    assert loaded["status"] == "needs_review"
    assert loaded["active_job_id"] == job["id"]

    for terminal in ("succeeded", "failed", "canceled"):
        tmp_db.update_video_generation_job(job["id"], status=terminal, stage=terminal)
        loaded = tmp_db.get_video_project(project["id"], created_by=user_id)
        assert loaded["status"] == "ready"
        assert loaded["active_job_id"] == job["id"]


def test_cancel_pending_job_sets_project_ready(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "cancel-sync-key"
    )
    tmp_db.request_video_generation_cancel(job["id"], user_id)
    loaded = tmp_db.get_video_project(project["id"], created_by=user_id)
    assert loaded["status"] == "ready"
    assert loaded["active_job_id"] == job["id"]


def test_revision_payload_update_does_not_force_generating_for_terminal_job(tmp_db):
    user_id = _user(tmp_db)
    project, revision = _project_and_revision(tmp_db, user_id)
    job, _ = tmp_db.create_or_get_video_generation_job(
        project["id"], revision["id"], user_id, "revision-sync-key"
    )
    tmp_db.update_video_generation_job(job["id"], status="succeeded", stage="succeeded", progress=100)
    tmp_db.update_video_project_revision_payload(
        revision["id"],
        {"title": "改标题", "voice": "冰糖", "scenes": []},
        created_by=user_id,
        title="改标题",
    )
    loaded = tmp_db.get_video_project(project["id"], created_by=user_id)
    assert loaded["status"] == "ready"
    assert loaded["active_job_id"] == job["id"]
