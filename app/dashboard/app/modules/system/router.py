from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.db.models import ClientRequest, CreditLedger, Task
from dashboard.app.db.session import get_db, get_engine
from dashboard.app.modules.deps import AuthContext, require_user
from dashboard.app.services import events

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    pg_ok = False
    redis_ok = False
    try:
        await db.execute(text("SELECT 1"))
        pg_ok = True
    except Exception:
        pg_ok = False
    r = await events.get_redis()
    redis_ok = r is not None
    version = "1.0.0"
    try:
        version = (Path(__file__).resolve().parents[4] / "VERSION").read_text().strip()
    except OSError:
        pass
    return {
        "ok": pg_ok,
        "postgres": pg_ok,
        "redis": redis_ok,
        "billing_fail_mode": settings.billing_fail_mode,
        "auth_mode": settings.dashboard_auth,
        "version": version,
        "schema": settings.db_schema,
    }


@router.get("/stats")
async def stats(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    from datetime import datetime, timezone

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    req_count = int(
        (
            await db.execute(
                select(func.count()).select_from(ClientRequest).where(ClientRequest.started_at >= start)
            )
        ).scalar()
        or 0
    )
    settled = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(ClientRequest.settled_microcredits), 0)).where(
                    ClientRequest.started_at >= start
                )
            )
        ).scalar()
        or 0
    )
    active_tasks = int(
        (
            await db.execute(select(func.count()).select_from(Task).where(Task.status == "running"))
        ).scalar()
        or 0
    )
    unsettle = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ClientRequest)
                .where(ClientRequest.status.in_(["reserved", "running", "pending"]))
            )
        ).scalar()
        or 0
    )
    return {
        "today_requests": req_count,
        "today_settled_microcredits": settled,
        "active_tasks": active_tasks,
        "unsettled_requests": unsettle,
    }
