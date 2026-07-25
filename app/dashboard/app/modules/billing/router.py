from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.security import has_permission
from dashboard.app.db.models import AuditLog, CreditAccount, CreditLedger, User
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user
from dashboard.app.services import billing_engine
from dashboard.app.services.billing_math import microcredits_to_credits

router = APIRouter(prefix="/api/v1", tags=["billing"])


class AdjustBody(BaseModel):
    user_id: uuid.UUID
    delta_microcredits: int
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: Optional[str] = None


@router.get("/credit-accounts/me")
async def my_account(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    assert ctx.user
    r = await db.execute(
        select(CreditAccount).where(CreditAccount.user_id == ctx.user.id).limit(1)
    )
    acc = r.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, detail={"code": "no_account", "message": "无积分账户"})
    return {
        "id": str(acc.id),
        "balance_microcredits": int(acc.balance_microcredits),
        "reserved_microcredits": int(acc.reserved_microcredits),
        "available_microcredits": int(acc.balance_microcredits) - int(acc.reserved_microcredits),
        "balance_credits": microcredits_to_credits(int(acc.balance_microcredits)),
        "status": acc.status,
        "version": acc.version,
    }


@router.get("/credit-ledger")
async def list_ledger(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    account_id: Optional[uuid.UUID] = None,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    q = select(CreditLedger)
    cq = select(func.count()).select_from(CreditLedger)
    if not has_permission(ctx.user.role, "billing:read") and not has_permission(ctx.user.role, "*"):
        # own accounts only
        ar = await db.execute(select(CreditAccount.id).where(CreditAccount.user_id == ctx.user.id))
        ids = [row[0] for row in ar.all()]
        q = q.where(CreditLedger.account_id.in_(ids))
        cq = cq.where(CreditLedger.account_id.in_(ids))
    elif account_id:
        q = q.where(CreditLedger.account_id == account_id)
        cq = cq.where(CreditLedger.account_id == account_id)
    total = int((await db.execute(cq)).scalar() or 0)
    q = q.order_by(CreditLedger.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(e.id),
                "account_id": str(e.account_id),
                "task_id": str(e.task_id) if e.task_id else None,
                "client_request_id": str(e.client_request_id) if e.client_request_id else None,
                "transaction_type": e.transaction_type,
                "delta_microcredits": int(e.delta_microcredits),
                "balance_after_microcredits": int(e.balance_after_microcredits),
                "reserved_after_microcredits": int(e.reserved_after_microcredits),
                "idempotency_key": e.idempotency_key,
                "status": e.status,
                "reason": e.reason,
                "metadata": e.metadata_json,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ],
    }


@router.post("/credit-accounts/adjust")
async def adjust(
    body: AdjustBody,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    if not (
        has_permission(ctx.user.role, "billing:adjust")
        or has_permission(ctx.user.role, "*")
        or ctx.mode == "local"
    ):
        raise HTTPException(403, detail={"code": "forbidden", "message": "需要 billing_admin"})
    ar = await db.execute(
        select(CreditAccount).where(CreditAccount.user_id == body.user_id).limit(1)
    )
    acc = ar.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, detail={"code": "no_account", "message": "用户无账户"})
    key = body.idempotency_key or f"adjust:{body.user_id}:{body.delta_microcredits}:{body.reason}:{uuid.uuid4()}"
    try:
        entry, snap = await billing_engine.grant_or_adjust(
            db,
            acc.id,
            body.delta_microcredits,
            idempotency_key=key,
            transaction_type="adjust" if body.delta_microcredits != 0 else "adjust",
            reason=body.reason,
            actor=ctx.user.username,
        )
    except billing_engine.BillingError as exc:
        raise HTTPException(exc.http_status, detail={"code": exc.code, "message": exc.message})
    db.add(
        AuditLog(
            actor_user_id=ctx.user.id,
            action="credit_adjust",
            resource_type="credit_account",
            resource_id=str(acc.id),
            detail={"delta": body.delta_microcredits, "reason": body.reason},
        )
    )
    await db.commit()
    return {
        "ledger_id": str(entry.id),
        "balance_microcredits": snap.balance_microcredits,
        "reserved_microcredits": snap.reserved_microcredits,
    }
