"""FastAPI dependencies: DB session, auth context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.core.security import has_permission, hash_session_token
from dashboard.app.db.models import Session as DbSession
from dashboard.app.db.models import User
from dashboard.app.db.session import get_db


@dataclass
class AuthContext:
    mode: str  # local | token | accounts | none
    user: Optional[User] = None
    session: Optional[DbSession] = None
    is_admin: bool = False


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None, alias="privateapi_session"),
    authorization: Optional[str] = Header(default=None),
    x_dashboard_token: Optional[str] = Header(default=None, alias="X-Dashboard-Token"),
) -> AuthContext:
    settings = get_settings()
    auth_mode = settings.dashboard_auth

    if auth_mode == "local":
        # single-user local: synthetic super_admin if no accounts yet
        r = await db.execute(select(User).where(User.role == "super_admin").limit(1))
        user = r.scalar_one_or_none()
        return AuthContext(mode="local", user=user, is_admin=True)

    if auth_mode == "token":
        token = settings.dashboard_token
        provided = x_dashboard_token or ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        if not token or provided != token:
            # allow static page; API routes check require_user
            return AuthContext(mode="token", user=None, is_admin=False)
        r = await db.execute(select(User).where(User.role == "super_admin").limit(1))
        user = r.scalar_one_or_none()
        return AuthContext(mode="token", user=user, is_admin=True)

    # accounts mode
    raw = session_token
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw:
        return AuthContext(mode="accounts", user=None)
    th = hash_session_token(raw)
    r = await db.execute(
        select(DbSession).where(DbSession.token_hash == th, DbSession.revoked_at.is_(None))
    )
    sess = r.scalar_one_or_none()
    if not sess:
        return AuthContext(mode="accounts", user=None)
    from datetime import datetime, timezone

    # 过期判断：统一成 aware UTC，避免 naive/aware 混比抛错或逻辑恒真
    exp = sess.expires_at
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return AuthContext(mode="accounts", user=None)
    ur = await db.execute(select(User).where(User.id == sess.user_id))
    user = ur.scalar_one_or_none()
    if not user or user.status != "active":
        return AuthContext(mode="accounts", user=None)
    return AuthContext(
        mode="accounts",
        user=user,
        session=sess,
        is_admin=user.role in ("super_admin", "operator", "billing_admin"),
    )


def require_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if ctx.user is None and ctx.mode != "local":
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "需要登录"})
    if ctx.mode == "local" and ctx.user is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "not_bootstrapped", "message": "数据库未初始化管理员"},
        )
    return ctx


def require_perm(perm: str):
    async def _inner(ctx: AuthContext = Depends(require_user)) -> AuthContext:
        if ctx.mode == "local":
            return ctx
        if ctx.user and has_permission(ctx.user.role, perm):
            return ctx
        if ctx.user and has_permission(ctx.user.role, "*"):
            return ctx
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "权限不足"})

    return _inner
