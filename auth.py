"""JWT Authentication & RBAC for SA-LogiFlow v3.0."""
import hashlib
import os
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from models import UserRole

logger = logging.getLogger(__name__)


def _load_or_create_jwt_secret() -> str:
    """优先用环境变量；否则读取/生成一份本机持久化的随机密钥。

    不用固定字符串兜底：源码里写死的默认密钥等于公开发布签名密钥，
    任何读到代码的人都能伪造登录 token。
    """
    env_secret = os.environ.get("JWT_SECRET")
    if env_secret:
        return env_secret
    secret_path = Path(__file__).parent / "data" / ".jwt_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        return secret_path.read_text().strip()
    generated = secrets.token_hex(32)
    secret_path.write_text(generated)
    secret_path.chmod(0o600)
    return generated


JWT_SECRET = _load_or_create_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 8

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${hashed.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        salt, stored_hash = hashed.split("$", 1)
        computed = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 100000)
        return computed.hex() == stored_hash
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "无效的认证令牌")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """从 Bearer token 解析当前用户。无 token 时拒绝访问。"""
    if not credentials:
        raise HTTPException(401, "未登录，请先登录")
    payload = decode_token(credentials.credentials)
    user_id = int(payload.get("sub", 0))
    username = payload.get("username", "")
    role = payload.get("role", "editor")
    return {"id": user_id, "username": username, "role": role, "display_name": payload.get("display_name", username)}


def require_role(*roles: UserRole) -> Callable:
    """依赖注入：要求用户具有指定角色之一。"""
    async def _check(user=Depends(get_current_user)):
        if user["role"] not in [r.value for r in roles]:
            raise HTTPException(403, f"权限不足，需要角色: {', '.join(r.value for r in roles)}")
        return user
    return _check
