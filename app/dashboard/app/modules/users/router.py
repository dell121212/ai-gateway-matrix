from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.core.security import has_permission, hash_password, password_strong_enough
from dashboard.app.db.models import AuditLog, CreditAccount, User
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10)
    display_name: str = ""
    role: str = "user"
    initial_microcredits: Optional[int] = None


@router.get("")
async def list_users(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    assert ctx.user
    if not (has_permission(ctx.user.role, "users:read") or has_permission(ctx.user.role, "*") or ctx.mode == "local"):
        raise HTTPException(403, detail={"code": "forbidden", "message": "权限不足"})
    rows = (await db.execute(select(User).order_by(User.created_at.desc()).limit(200))).scalars().all()
    return {
        "items": [
            {
                "id": str(u.id),
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role,
                "status": u.status,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in rows
        ]
    }


@router.post("")
async def create_user(
    body: UserCreate,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    if not (has_permission(ctx.user.role, "*") or ctx.mode == "local"):
        raise HTTPException(403, detail={"code": "forbidden", "message": "需要 super_admin"})
    if not password_strong_enough(body.password):
        raise HTTPException(400, detail={"code": "weak_password", "message": "密码过弱"})
    if body.role not in {"super_admin", "operator", "billing_admin", "auditor", "user"}:
        raise HTTPException(400, detail={"code": "invalid_role", "message": "角色非法"})
    exists = await db.execute(select(User).where(User.username == body.username.strip()))
    if exists.scalar_one_or_none():
        raise HTTPException(400, detail={"code": "exists", "message": "用户名已存在"})
    settings = get_settings()
    user = User(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.username,
        role=body.role,
        status="active",
    )
    db.add(user)
    await db.flush()
    acc = CreditAccount(
        user_id=user.id,
        balance_microcredits=body.initial_microcredits
        if body.initial_microcredits is not None
        else settings.initial_user_microcredits,
        reserved_microcredits=0,
        status="active",
    )
    db.add(acc)
    db.add(
        AuditLog(
            actor_user_id=ctx.user.id,
            action="user_create",
            resource_type="user",
            resource_id=str(user.id),
            detail={"username": user.username, "role": user.role},
        )
    )
    await db.commit()
    return {"id": str(user.id), "account_id": str(acc.id)}


@router.post("/{user_id}/disable")
async def disable_user(
    user_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    if not (has_permission(ctx.user.role, "*") or ctx.mode == "local"):
        raise HTTPException(403, detail={"code": "forbidden", "message": "权限不足"})
    r = await db.execute(select(User).where(User.id == user_id))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(404, detail={"code": "not_found", "message": "用户不存在"})
    user.status = "disabled"
    db.add(
        AuditLog(
            actor_user_id=ctx.user.id,
            action="user_disable",
            resource_type="user",
            resource_id=str(user.id),
        )
    )
    await db.commit()
    return {"ok": True}
