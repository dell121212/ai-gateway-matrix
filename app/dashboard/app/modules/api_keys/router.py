from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.ids import generate_client_key, key_prefix_fingerprint
from dashboard.app.core.security import has_permission
from dashboard.app.db.models import ApiKey, AuditLog, CreditAccount
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user

router = APIRouter(prefix="/api/v1/api-keys", tags=["api_keys"])


class CreateKeyBody(BaseModel):
    alias: str = ""
    default_mode: str = "agent-stream"
    allowed_models: Optional[List[str]] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    request_budget_microcredits: Optional[int] = None
    daily_budget_microcredits: Optional[int] = None


@router.get("")
async def list_keys(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    assert ctx.user
    q = select(ApiKey).where(ApiKey.status == "active")
    if not (has_permission(ctx.user.role, "*") or ctx.mode == "local"):
        q = q.where(ApiKey.user_id == ctx.user.id)
    rows = (await db.execute(q.order_by(ApiKey.created_at.desc()))).scalars().all()
    return {
        "items": [
            {
                "id": str(k.id),
                "alias": k.alias,
                "key_prefix": k.key_prefix,
                "default_mode": k.default_mode,
                "allowed_models": k.allowed_models,
                "rpm_limit": k.rpm_limit,
                "tpm_limit": k.tpm_limit,
                "status": k.status,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in rows
        ]
    }


@router.post("")
async def create_key(
    body: CreateKeyBody,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    if body.default_mode not in ("strict", "agent-stream"):
        raise HTTPException(400, detail={"code": "invalid_mode", "message": "default_mode 非法"})
    ar = await db.execute(
        select(CreditAccount).where(CreditAccount.user_id == ctx.user.id).limit(1)
    )
    acc = ar.scalar_one_or_none()
    if not acc:
        raise HTTPException(400, detail={"code": "no_account", "message": "用户无积分账户"})
    raw = generate_client_key()
    prefix, kh = key_prefix_fingerprint(raw)
    row = ApiKey(
        user_id=ctx.user.id,
        credit_account_id=acc.id,
        key_hash=kh,
        key_prefix=prefix,
        alias=body.alias,
        default_mode=body.default_mode,
        allowed_models=body.allowed_models,
        rpm_limit=body.rpm_limit,
        tpm_limit=body.tpm_limit,
        request_budget_microcredits=body.request_budget_microcredits,
        daily_budget_microcredits=body.daily_budget_microcredits,
        status="active",
    )
    db.add(row)
    db.add(
        AuditLog(
            actor_user_id=ctx.user.id,
            action="api_key_create",
            resource_type="api_key",
            resource_id=str(row.id),
            detail={"alias": body.alias, "prefix": prefix},
        )
    )
    await db.commit()
    await db.refresh(row)
    return {
        "id": str(row.id),
        "alias": row.alias,
        "key_prefix": prefix,
        "default_mode": row.default_mode,
        "api_key": raw,  # only once
        "message": "请立即保存完整密钥，之后无法再次查看",
    }


@router.post("/{key_id}/revoke")
async def revoke_key(
    key_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    r = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = r.scalar_one_or_none()
    if not key:
        raise HTTPException(404, detail={"code": "not_found", "message": "Key 不存在"})
    if key.user_id != ctx.user.id and not (
        has_permission(ctx.user.role, "*") or ctx.mode == "local"
    ):
        raise HTTPException(403, detail={"code": "forbidden", "message": "无权吊销"})
    key.status = "revoked"
    key.revoked_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            actor_user_id=ctx.user.id,
            action="api_key_revoke",
            resource_type="api_key",
            resource_id=str(key.id),
            detail={"prefix": key.key_prefix},
        )
    )
    await db.commit()
    return {"ok": True}
