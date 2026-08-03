from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.db.models import ClientRequest, Task
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user
from dashboard.app.services import events
from dashboard.app.services.jiyi_status import read_jiyi_status, request_jiyi_sync

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
        "observation_mode": "usage-and-cost",
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
    token_totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(ClientRequest.final_prompt_tokens), 0),
                func.coalesce(func.sum(ClientRequest.final_completion_tokens), 0),
                func.coalesce(func.sum(ClientRequest.cost_microusd), 0),
            ).where(ClientRequest.started_at >= start)
        )
    ).one()
    all_tokens = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(ClientRequest.final_prompt_tokens + ClientRequest.final_completion_tokens), 0))
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
    in_flight = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ClientRequest)
                .where(ClientRequest.status.in_(["running", "pending"]))
            )
        ).scalar()
        or 0
    )
    return {
        "today_requests": req_count,
        "today_cost_microusd": int(token_totals[2] or 0),
        "active_tasks": active_tasks,
        "in_flight_requests": in_flight,
        "today_prompt_tokens": int(token_totals[0] or 0),
        "today_completion_tokens": int(token_totals[1] or 0),
        "today_tokens": int(token_totals[0] or 0) + int(token_totals[1] or 0),
        "total_tokens": all_tokens,
    }


def _jiyi_state_dir() -> Path:
    return Path(os.environ.get("JIYI_STATE_DIR", "/app/state")).resolve()


@router.get("/jiyi")
async def jiyi_status(ctx: AuthContext = Depends(require_user)):
    return read_jiyi_status(_jiyi_state_dir())


@router.post("/jiyi/save")
async def save_jiyi(ctx: AuthContext = Depends(require_user)):
    actor = ctx.user.username if ctx.user else "local"
    return request_jiyi_sync(_jiyi_state_dir(), actor=actor)
