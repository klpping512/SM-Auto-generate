"""Bootstrap admin must replace hardcoded admin/admin123."""
from __future__ import annotations

import pytest


def test_empty_db_without_bootstrap_env_rejects_startup(tmp_db, monkeypatch):
    import app

    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN"):
        app._ensure_bootstrap_admin()


def test_empty_db_with_bootstrap_env_creates_admin(tmp_db, monkeypatch):
    import app
    import auth

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "ops_admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "secure-pass-99")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Ops")
    user = app._ensure_bootstrap_admin()
    assert user["username"] == "ops_admin"
    assert user["role"] == "admin"
    assert auth.verify_password("secure-pass-99", user["password_hash"])
    # Second call must not recreate or fail when users already exist.
    again = app._ensure_bootstrap_admin()
    assert again["username"] == "ops_admin"
    assert len(tmp_db.get_users()) == 1


def test_existing_users_skip_bootstrap_even_without_env(tmp_db, monkeypatch):
    import app

    tmp_db.create_user("seed", "hash", "admin", "Seed")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    user = app._ensure_bootstrap_admin()
    assert user["username"] == "seed"


def test_short_bootstrap_password_rejected(tmp_db, monkeypatch):
    import app

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "ops_admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "short")
    with pytest.raises(RuntimeError, match="至少 8"):
        app._ensure_bootstrap_admin()
