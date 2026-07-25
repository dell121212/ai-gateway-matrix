from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.security import has_permission
from dashboard.app.db.models import Task
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user
from dashboard.app.services import events

router = APIRouter(prefix="/api/v1/live", tags=["live"])


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: uuid.UUID,
    request: Request,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
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
        raise HTTPException(403, detail={"code": "forbidden", "message": "无权订阅"})

    header_last = request.headers.get("last-event-id") or last_event_id or "0-0"
    stream_key = events.task_stream_key(str(task_id))

    async def gen():
        cursor = header_last
        # snapshot first
        yield f"event: snapshot\ndata: {json.dumps({'task_id': str(task_id), 'status': t.status, 'estimated_microcredits': int(t.estimated_microcredits or 0), 'settled_microcredits': int(t.settled_microcredits or 0)}, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected():
                break
            msgs = await events.read_stream(stream_key, last_id=cursor, count=20, block_ms=15000)
            if not msgs:
                yield ": heartbeat\n\n"
                continue
            for mid, data in msgs:
                cursor = mid
                yield f"id: {mid}\nevent: {data.get('event', 'message')}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if t.status in ("finished", "cancelled"):
                # one more short poll then exit
                await asyncio.sleep(0.5)
                break

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.get("/users/me/events")
async def user_events(
    request: Request,
    last_event_id: Optional[str] = Query(default=None),
    ctx: AuthContext = Depends(require_user),
):
    assert ctx.user
    header_last = request.headers.get("last-event-id") or last_event_id or "0-0"
    stream_key = events.user_stream_key(str(ctx.user.id))

    async def gen():
        cursor = header_last
        yield f"event: hello\ndata: {json.dumps({'user_id': str(ctx.user.id)})}\n\n"
        while True:
            if await request.is_disconnected():
                break
            msgs = await events.read_stream(stream_key, last_id=cursor, count=20, block_ms=15000)
            if not msgs:
                yield ": heartbeat\n\n"
                continue
            for mid, data in msgs:
                cursor = mid
                yield f"id: {mid}\nevent: {data.get('event', 'message')}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
