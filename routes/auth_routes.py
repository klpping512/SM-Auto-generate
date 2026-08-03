"""Authentication and user management routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import create_access_token, get_current_user, hash_password, require_role, verify_password
from models import LoginRequest, RegisterRequest, SignupRequest, TokenResponse, UserRole
from repositories import users as user_repo

router = APIRouter()


@router.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request):
    user = user_repo.get_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    if user["status"] != "active":
        raise HTTPException(403, "账号已被禁用")
    user_repo.update_last_login(user["id"])
    user_repo.add_audit_log(user["id"], user["username"], "login", ip=request.client.host)
    token = create_access_token({
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "display_name": user.get("display_name", ""),
    })
    return TokenResponse(
        access_token=token,
        role=UserRole(user["role"]),
        username=user["username"],
        display_name=user.get("display_name", ""),
    )


@router.post("/api/auth/register")
async def register(req: RegisterRequest, user=Depends(require_role(UserRole.ADMIN))):
    if user_repo.get_by_username(req.username):
        raise HTTPException(400, "用户名已存在")
    uid = user_repo.create(req.username, hash_password(req.password), req.role.value, req.display_name)
    user_repo.add_audit_log(user["id"], user["username"], "create_user", target=req.username)
    return {"status": "ok", "user_id": uid}


@router.post("/api/auth/signup", status_code=201)
async def signup(req: SignupRequest, request: Request):
    """创建普通运营账号；角色固定为 editor，防止注册请求越权。"""
    username = req.username.strip().lower()
    if user_repo.get_by_username(username):
        raise HTTPException(400, "用户名已存在")
    uid = user_repo.create(
        username,
        hash_password(req.password),
        UserRole.EDITOR.value,
        req.display_name.strip(),
    )
    user_repo.add_audit_log(
        uid,
        username,
        "self_signup",
        ip=request.client.host if request.client else None,
    )
    return {"status": "ok", "message": "注册成功，请登录"}


@router.get("/api/auth/me")
async def get_me(user=Depends(get_current_user)):
    return user


@router.get("/api/users")
async def list_users(user=Depends(require_role(UserRole.ADMIN))):
    return user_repo.list_all()


@router.put("/api/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    body: dict,
    user=Depends(require_role(UserRole.ADMIN)),
):
    status = body.get("status", "active")
    user_repo.update_status(user_id, status)
    user_repo.add_audit_log(
        user["id"],
        user["username"],
        "update_user_status",
        target=str(user_id),
        detail=status,
    )
    return {"status": "ok"}

