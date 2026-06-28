"""JWT Authentication & RBAC for SA-LogiFlow v2.0."""
import hashlib
import os
import logging
import secrets
from datetime import datetime, timedelta
from typing import Callable

import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from models import UserRole

logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET", "sa-logiflow-secret-key-change-in-production")
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
    """从 Bearer token 解析当前用户。无 token 时返回匿名用户（兼容旧版）。"""
    if not credentials:
        # 兼容模式：无 token 时返回 admin（方便迁移）
        return {"id": 0, "username": "anonymous", "role": "admin", "display_name": "匿名用户"}
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
