from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.security import has_permission
from dashboard.app.db.models import AuditLog
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user
from fastapi import HTTPException

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("")
async def list_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    if not (
        has_permission(ctx.user.role, "audit:read")
        or has_permission(ctx.user.role, "*")
        or ctx.mode == "local"
    ):
        raise HTTPException(403, detail={"code": "forbidden", "message": "需要 auditor 权限"})
    total = int((await db.execute(select(func.count()).select_from(AuditLog))).scalar() or 0)
    rows = (
        await db.execute(
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(a.id),
                "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None,
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "detail": a.detail,
                "ip": a.ip,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
    }
