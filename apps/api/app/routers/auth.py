"""账号鉴权路由（M4 W1）：注册 / 登录 / 登出 / 当前用户。

安全要点：
- 密码 argon2id 哈希（core.auth），永不回显；
- 会话 JWT 写入 httpOnly SameSite cookie（同源/BFF 友好，JS 读不到）；
- 现有匿名 token 流（resumes/interviews）不受影响，本路由为可选叠加层；
- get_current_user 依赖供后续账号域端点（/api/account/*）复用。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_session_token,
    decode_session_token,
    hash_password,
    verify_password,
)
from app.core.audit import audit_event, user_subject
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.models import User
from app.schemas.schemas import LoginRequest, RegisterRequest, UserView

router = APIRouter(tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_settings = get_settings()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_settings.AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_settings.is_production,
        samesite=cast(Literal["lax", "strict", "none"], _settings.AUTH_COOKIE_SAMESITE),
        max_age=_settings.AUTH_JWT_TTL_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(_settings.AUTH_COOKIE_NAME, path="/")


def _to_view(user: User) -> UserView:
    return UserView(
        id=str(user.id),
        email=user.email,
        created_at=user.created_at.isoformat(),
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


async def get_current_user(request: Request) -> User:
    """账号域端点依赖：从 cookie 解析会话，返回已加载的 User。"""
    token = request.cookies.get(_settings.AUTH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user_id = decode_session_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话无效或已过期")
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


@router.post("/auth/register", response_model=UserView, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request, response: Response) -> UserView:
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="邮箱格式无效")
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已注册")
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password(body.password),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    _set_session_cookie(response, create_session_token(user.id))
    audit_event(
        "auth.register",
        subject=user_subject(user.id),
        resource="user",
        resource_id=str(user.id),
        request_id=getattr(request.state, "request_id", None),
    )
    return _to_view(user)


@router.post("/auth/login", response_model=UserView)
async def login(body: LoginRequest, request: Request, response: Response) -> UserView:
    email = body.email.strip().lower()
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
    _set_session_cookie(response, create_session_token(user.id))
    audit_event(
        "auth.login",
        subject=user_subject(user.id),
        resource="user",
        resource_id=str(user.id),
        request_id=getattr(request.state, "request_id", None),
    )
    return _to_view(user)


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(_settings.AUTH_COOKIE_NAME)
    uid = decode_session_token(token) if token else None
    subject = user_subject(uid) if uid else "anon:none"
    _clear_session_cookie(response)
    audit_event(
        "auth.logout",
        subject=subject,
        resource="session",
        request_id=getattr(request.state, "request_id", None),
    )
    return {"detail": "已登出"}


@router.get("/auth/me", response_model=UserView)
async def me(user: User = Depends(get_current_user)) -> UserView:
    return _to_view(user)
