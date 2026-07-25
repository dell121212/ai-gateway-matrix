from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.security import has_permission
from dashboard.app.db.models import ClientRequest, LlmAttempt, Task
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user

router = APIRouter(prefix="/api/v1", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str = ""
    external_task_id: Optional[str] = None
    session_id: Optional[str] = None
    client_name: str = ""
    workspace_id: Optional[str] = None


@router.post("/tasks")
async def create_task(
    body: TaskCreate,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    t = Task(
        user_id=ctx.user.id,
        external_task_id=body.external_task_id,
        session_id=body.session_id,
        client_name=body.client_name,
        title=body.title,
        status="running",
        grouping_source="explicit",
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": str(t.id), "status": t.status}


@router.post("/tasks/{task_id}/finish")
async def finish_task(
    task_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    r = await db.execute(select(Task).where(Task.id == task_id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, detail={"code": "not_found", "message": "任务不存在"})
    if t.user_id != ctx.user.id and not (
        has_permission(ctx.user.role, "tasks:read") or has_permission(ctx.user.role, "*")
    ):
        raise HTTPException(403, detail={"code": "forbidden", "message": "无权操作"})
    t.status = "finished"
    t.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(t.id), "status": t.status}


@router.get("/tasks")
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    q = select(Task)
    cq = select(func.count()).select_from(Task)
    admin = has_permission(ctx.user.role, "tasks:read") or has_permission(ctx.user.role, "*") or ctx.mode == "local"
    if not admin:
        q = q.where(Task.user_id == ctx.user.id)
        cq = cq.where(Task.user_id == ctx.user.id)
    if status:
        q = q.where(Task.status == status)
        cq = cq.where(Task.status == status)
    total = int((await db.execute(cq)).scalar() or 0)
    rows = (
        await db.execute(
            q.order_by(Task.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_task_json(t) for t in rows],
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    r = await db.execute(select(Task).where(Task.id == task_id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, detail={"code": "not_found", "message": "任务不存在"})
    admin = has_permission(ctx.user.role, "tasks:read") or has_permission(ctx.user.role, "*") or ctx.mode == "local"
    if t.user_id != ctx.user.id and not admin:
        raise HTTPException(403, detail={"code": "forbidden", "message": "无权查看"})
    reqs = (
        await db.execute(
            select(ClientRequest)
            .where(ClientRequest.task_id == t.id)
            .order_by(ClientRequest.started_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return {"task": _task_json(t), "requests": [_req_json(x) for x in reqs]}


@router.get("/requests")
async def list_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    task_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    q = select(ClientRequest)
    cq = select(func.count()).select_from(ClientRequest)
    admin = has_permission(ctx.user.role, "requests:read") or has_permission(ctx.user.role, "*") or ctx.mode == "local"
    if not admin:
        q = q.where(ClientRequest.user_id == ctx.user.id)
        cq = cq.where(ClientRequest.user_id == ctx.user.id)
    if task_id:
        q = q.where(ClientRequest.task_id == task_id)
        cq = cq.where(ClientRequest.task_id == task_id)
    if status:
        q = q.where(ClientRequest.status == status)
        cq = cq.where(ClientRequest.status == status)
    total = int((await db.execute(cq)).scalar() or 0)
    rows = (
        await db.execute(
            q.order_by(ClientRequest.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_req_json(x) for x in rows],
    }


@router.get("/requests/{request_id}")
async def get_request(
    request_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    assert ctx.user
    r = await db.execute(select(ClientRequest).where(ClientRequest.id == request_id))
    req = r.scalar_one_or_none()
    if not req:
        raise HTTPException(404, detail={"code": "not_found", "message": "请求不存在"})
    admin = has_permission(ctx.user.role, "requests:read") or has_permission(ctx.user.role, "*") or ctx.mode == "local"
    if req.user_id != ctx.user.id and not admin:
        raise HTTPException(403, detail={"code": "forbidden", "message": "无权查看"})
    attempts = (
        await db.execute(
            select(LlmAttempt)
            .where(LlmAttempt.client_request_id == req.id)
            .order_by(LlmAttempt.attempt_number.asc())
        )
    ).scalars().all()
    return {
        "request": _req_json(req),
        "attempts": [
            {
                "id": str(a.id),
                "attempt_number": a.attempt_number,
                "provider": a.provider,
                "actual_model": a.actual_model,
                "status": a.status,
                "prompt_tokens": a.prompt_tokens,
                "completion_tokens": a.completion_tokens,
                "actual_cost_microusd": int(a.actual_cost_microusd),
                "market_value_microusd": int(a.market_value_microusd),
                "charged_microcredits": int(a.charged_microcredits),
                "is_final_success": a.is_final_success,
                "is_platform_loss": a.is_platform_loss,
                "quality_failure_reason": a.quality_failure_reason,
                "billing_mode": a.billing_mode,
                "error_class": a.error_class,
            }
            for a in attempts
        ],
    }


def _task_json(t: Task) -> dict:
    return {
        "id": str(t.id),
        "user_id": str(t.user_id),
        "title": t.title,
        "status": t.status,
        "client_name": t.client_name,
        "grouping_source": t.grouping_source,
        "estimated_microcredits": int(t.estimated_microcredits or 0),
        "settled_microcredits": int(t.settled_microcredits or 0),
        "request_count": int(t.request_count or 0),
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }


def _req_json(r: ClientRequest) -> dict:
    return {
        "id": str(r.id),
        "task_id": str(r.task_id),
        "user_id": str(r.user_id),
        "requested_model": r.requested_model,
        "resolved_pool": r.resolved_pool,
        "mode": r.mode,
        "stream": r.stream,
        "status": r.status,
        "estimated_microcredits": int(r.estimated_microcredits or 0),
        "settled_microcredits": int(r.settled_microcredits or 0),
        "reserved_microcredits": int(r.reserved_microcredits or 0),
        "final_prompt_tokens": r.final_prompt_tokens,
        "final_completion_tokens": r.final_completion_tokens,
        "settlement_source": r.settlement_source,
        "error_class": r.error_class,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "latency_ms": r.latency_ms,
    }
