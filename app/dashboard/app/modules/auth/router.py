from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.core.ids import generate_session_token
from dashboard.app.core.security import (
    hash_password,
    hash_session_token,
    login_rate_limiter,
    new_csrf_secret,
    password_strong_enough,
    verify_password,
)
from dashboard.app.db.models import AuditLog, Session as DbSession, User
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, get_auth_context, require_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


class BootstrapBody(BaseModel):
    username: str = "admin"
    password: str = Field(min_length=10)
    display_name: str = "Administrator"


@router.get("/status")
async def auth_status(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    r = await db.execute(select(User).limit(1))
    has_users = r.scalar_one_or_none() is not None
    return {
        "auth_mode": settings.dashboard_auth,
        "has_users": has_users,
        "authenticated": ctx.user is not None,
        "user": (
            {
                "id": str(ctx.user.id),
                "username": ctx.user.username,
                "display_name": ctx.user.display_name,
                "role": ctx.user.role,
            }
            if ctx.user
            else None
        ),
        "csrf_token": ctx.session.csrf_secret if ctx.session else None,
    }


@router.post("/bootstrap")
async def bootstrap_admin(body: BootstrapBody, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).limit(1))
    if r.scalar_one_or_none():
        raise HTTPException(400, detail={"code": "already_bootstrapped", "message": "已有用户"})
    if not password_strong_enough(body.password):
        raise HTTPException(400, detail={"code": "weak_password", "message": "密码至少10位且含两类字符"})
    user = User(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role="super_admin",
        status="active",
    )
    db.add(user)
    await db.flush()
    db.add(
        AuditLog(
            actor_user_id=user.id,
            action="bootstrap_admin",
            resource_type="user",
            resource_id=str(user.id),
            detail={"username": user.username},
        )
    )
    await db.commit()
    return {"ok": True, "username": user.username}


@router.post("/login")
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
    if not login_rate_limiter.allow(f"{ip}:{body.username}"):
        raise HTTPException(429, detail={"code": "rate_limited", "message": "登录过于频繁"})
    r = await db.execute(select(User).where(User.username == body.username.strip()))
    user = r.scalar_one_or_none()
    if not user or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, detail={"code": "invalid_credentials", "message": "用户名或密码错误"})
    if user.status != "active":
        raise HTTPException(403, detail={"code": "disabled", "message": "用户已禁用"})

    raw = generate_session_token()
    csrf = new_csrf_secret()
    sess = DbSession(
        user_id=user.id,
        token_hash=hash_session_token(raw),
        csrf_secret=csrf,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(sess)
    user.last_login_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            actor_user_id=user.id,
            action="login",
            resource_type="session",
            resource_id=str(sess.id),
            ip=ip,
        )
    )
    await db.commit()
    response.set_cookie(
        key="privateapi_session",
        value=raw,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return {
        "ok": True,
        "csrf_token": csrf,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        },
    }


@router.post("/logout")
async def logout(
    response: Response,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if ctx.session:
        ctx.session.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    response.delete_cookie("privateapi_session", path="/")
    return {"ok": True}


@router.get("/me")
async def me(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    assert ctx.user
    return {
        "user": {
            "id": str(ctx.user.id),
            "username": ctx.user.username,
            "display_name": ctx.user.display_name,
            "role": ctx.user.role,
            "timezone": ctx.user.timezone,
        },
        "csrf_token": ctx.session.csrf_secret if ctx.session else None,
    }
