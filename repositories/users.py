"""User repository facade.

This keeps route modules from importing the monolithic database module directly,
while the underlying storage implementation is still being split gradually.
"""
from __future__ import annotations

import database as db


def get_by_username(username: str) -> dict | None:
    return db.get_user_by_username(username)


def create(username: str, password_hash: str, role: str = "editor", display_name: str = "") -> int:
    return db.create_user(username, password_hash, role, display_name)


def list_all() -> list[dict]:
    return db.get_users()


def update_last_login(user_id: int) -> None:
    db.update_user_last_login(user_id)


def update_status(user_id: int, status: str) -> None:
    db.update_user_status(user_id, status)


def add_audit_log(
    user_id: int,
    username: str,
    action: str,
    *,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> int:
    return db.add_audit_log(user_id, username, action, target=target, detail=detail, ip=ip)

