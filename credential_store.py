"""Encrypt account credentials at rest with transparent decrypt on read.

Legacy plaintext JSON rows remain readable until rewritten; new writes always
use the ``enc:v1:`` Fernet prefix.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"


def _key_path() -> Path:
    return Path(__file__).resolve().parent / "data" / ".credential_key"


def load_or_create_key() -> bytes:
    """Prefer CREDENTIAL_ENCRYPTION_KEY; otherwise persist a local Fernet key."""
    env_key = str(os.environ.get("CREDENTIAL_ENCRYPTION_KEY") or "").strip()
    if env_key:
        return env_key.encode("ascii")
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="utf-8").strip().encode("ascii")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - dependency should be installed
        raise RuntimeError("凭据加密需要 cryptography 包") from exc
    generated = Fernet.generate_key()
    path.write_text(generated.decode("ascii"), encoding="utf-8")
    path.chmod(0o600)
    return generated


def encrypt_credentials(plaintext: str) -> str:
    """Encrypt a credentials JSON string for SQLite storage."""
    raw = str(plaintext or "{}")
    if raw.startswith(PREFIX):
        return raw
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("凭据加密需要 cryptography 包") from exc
    token = Fernet(load_or_create_key()).encrypt(raw.encode("utf-8")).decode("ascii")
    return f"{PREFIX}{token}"


def decrypt_credentials(stored: str) -> str:
    """Decrypt stored credentials; plaintext rows pass through unchanged."""
    raw = str(stored or "{}")
    if not raw.startswith(PREFIX):
        return raw
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("凭据解密需要 cryptography 包") from exc
    token = raw[len(PREFIX):].encode("ascii")
    try:
        return Fernet(load_or_create_key()).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        logger.error("凭据解密失败：密钥不匹配或数据损坏")
        raise RuntimeError("账号凭据无法解密，请检查 CREDENTIAL_ENCRYPTION_KEY") from exc


def looks_encrypted(stored: str) -> bool:
    return str(stored or "").startswith(PREFIX)
