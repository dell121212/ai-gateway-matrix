from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.security import has_permission
from dashboard.app.db.models import AuditLog, PricingVersion
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user
from dashboard.app.services.pricing_sync import sync_litellm_catalog

router = APIRouter(prefix="/api/v1/pricing", tags=["pricing"])


class PricingCreate(BaseModel):
    provider: str = "*"
    model_pattern: str
    input_price: int = 0
    output_price: int = 0
    cached_input_price: int = 0
    reasoning_price: int = 0
    source: str = "manual"


@router.get("")
async def list_pricing(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(PricingVersion).order_by(PricingVersion.created_at.desc()).limit(200))
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(p.id),
                "provider": p.provider,
                "model_pattern": p.model_pattern,
                "input_price": int(p.input_price),
                "output_price": int(p.output_price),
                "cached_input_price": int(p.cached_input_price),
                "reasoning_price": int(p.reasoning_price),
                "source": p.source,
                "version": p.version,
                "effective_from": p.effective_from.isoformat() if p.effective_from else None,
                "effective_to": p.effective_to.isoformat() if p.effective_to else None,
            }
            for p in rows
        ]
    }


@router.post("")
async def create_pricing(
    body: PricingCreate,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    if not (
        has_permission(ctx.user.role, "pricing:write")
        or has_permission(ctx.user.role, "*")
        or ctx.mode == "local"
    ):
        raise HTTPException(403, detail={"code": "forbidden", "message": "权限不足"})
    row = PricingVersion(
        provider=body.provider,
        model_pattern=body.model_pattern,
        input_price=body.input_price,
        output_price=body.output_price,
        cached_input_price=body.cached_input_price,
        reasoning_price=body.reasoning_price,
        billing_basis="market_value",
        credit_multiplier="1.0",
        minimum_microcredits=0,
        source=body.source,
        version=1,
        created_by=ctx.user.username,
    )
    db.add(row)
    db.add(
        AuditLog(
            actor_user_id=ctx.user.id,
            action="pricing_create",
            resource_type="pricing",
            detail={"model_pattern": body.model_pattern},
        )
    )
    await db.commit()
    await db.refresh(row)
    return {"id": str(row.id)}


@router.post("/sync-litellm")
async def sync_pricing(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    assert ctx.user
    if not (
        has_permission(ctx.user.role, "pricing:write")
        or has_permission(ctx.user.role, "*")
        or ctx.mode == "local"
    ):
        raise HTTPException(403, detail={"code": "forbidden", "message": "权限不足"})
    result = await sync_litellm_catalog(db, actor=ctx.user.username)
    db.add(AuditLog(actor_user_id=ctx.user.id, action="pricing_sync_litellm", resource_type="pricing", detail=result))
    await db.commit()
    return result
