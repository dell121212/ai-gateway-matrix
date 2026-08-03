from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.db.models import ClientRequest, QuotaSnapshot, UsageAggregate
from dashboard.app.db.session import get_db
from dashboard.app.modules.deps import AuthContext, require_user
from gateway.routing_strategies import STRATEGIES, rank_candidates

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


def _range_start(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    return now - {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}.get(period, timedelta(hours=24))


@router.get("/summary")
async def usage_summary(
    period: Literal["24h", "7d", "30d"] = "24h",
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    start = _range_start(period)
    row = (
        await db.execute(
            select(
                func.count(ClientRequest.id),
                func.coalesce(func.sum(ClientRequest.final_prompt_tokens), 0),
                func.coalesce(func.sum(ClientRequest.final_completion_tokens), 0),
                func.coalesce(func.sum(ClientRequest.cached_tokens), 0),
                func.coalesce(func.sum(ClientRequest.reasoning_tokens), 0),
                func.coalesce(func.sum(ClientRequest.cost_microusd), 0),
                func.coalesce(func.avg(ClientRequest.latency_ms), 0),
                func.coalesce(func.avg(ClientRequest.ttft_ms), 0),
                func.coalesce(func.sum(case((ClientRequest.status == "success", 1), else_=0)), 0),
            ).where(ClientRequest.started_at >= start)
        )
    ).one()
    requests, prompt, completion, cached, reasoning, cost, latency, ttft, successes = row
    return {
        "period": period,
        "requests": int(requests or 0),
        "successes": int(successes or 0),
        "failures": int(requests or 0) - int(successes or 0),
        "success_rate": round(int(successes or 0) / int(requests or 1), 4),
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "cached_tokens": int(cached or 0),
        "reasoning_tokens": int(reasoning or 0),
        "total_tokens": int(prompt or 0) + int(completion or 0),
        "cost_microusd": int(cost or 0),
        "average_latency_ms": round(float(latency or 0)),
        "average_ttft_ms": round(float(ttft or 0)),
    }


@router.get("/timeseries")
async def usage_timeseries(
    period: Literal["24h", "7d", "30d"] = "24h",
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    bucket = "hour" if period == "24h" else "day"
    rows = (
        await db.execute(
            select(UsageAggregate)
            .where(UsageAggregate.bucket == bucket, UsageAggregate.bucket_start >= _range_start(period))
            .order_by(UsageAggregate.bucket_start.asc())
        )
    ).scalars().all()
    return {"bucket": bucket, "items": [_aggregate_json(row) for row in rows]}


@router.get("/providers")
async def provider_breakdown(
    period: Literal["24h", "7d", "30d"] = "24h",
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(
                ClientRequest.provider,
                func.count(ClientRequest.id),
                func.coalesce(func.sum(ClientRequest.final_prompt_tokens + ClientRequest.final_completion_tokens), 0),
                func.coalesce(func.sum(ClientRequest.cost_microusd), 0),
                func.coalesce(func.avg(ClientRequest.latency_ms), 0),
            )
            .where(ClientRequest.started_at >= _range_start(period))
            .group_by(ClientRequest.provider)
            .order_by(func.count(ClientRequest.id).desc())
        )
    ).all()
    return {"items": [{"provider": provider or "unknown", "requests": int(requests), "tokens": int(tokens), "cost_microusd": int(cost), "average_latency_ms": round(float(latency or 0))} for provider, requests, tokens, cost, latency in rows]}


class QuotaSnapshotBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    connection_id: str = ""
    window: str = "unknown"
    remaining_percent: Optional[int] = Field(default=None, ge=0, le=100)
    exhausted: bool = False
    reset_at: Optional[datetime] = None
    window_seconds: Optional[int] = Field(default=None, ge=0)
    raw_data: Optional[dict[str, Any]] = None


@router.post("/quota-snapshots")
async def save_quota_snapshot(body: QuotaSnapshotBody, ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    row = QuotaSnapshot(**body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": str(row.id), "observed_at": row.observed_at.isoformat()}


@router.get("/quota-snapshots/latest")
async def latest_quota_snapshots(ctx: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(QuotaSnapshot).order_by(QuotaSnapshot.observed_at.desc()).limit(200))).scalars().all()
    seen: set[tuple[str, str, str]] = set()
    items = []
    for row in rows:
        key = (row.provider, row.connection_id, row.window)
        if key in seen:
            continue
        seen.add(key)
        items.append({"provider": row.provider, "connection_id": row.connection_id, "window": row.window, "remaining_percent": row.remaining_percent, "exhausted": row.exhausted, "reset_at": row.reset_at.isoformat() if row.reset_at else None, "observed_at": row.observed_at.isoformat()})
    return {"items": items}


class RouteExplainBody(BaseModel):
    strategy: str = "priority"
    candidates: list[dict[str, Any]]


@router.post("/route-explain")
async def route_explain(body: RouteExplainBody, ctx: AuthContext = Depends(require_user)):
    if body.strategy not in STRATEGIES:
        return {"strategy": body.strategy, "error": "unknown_strategy", "supported": STRATEGIES, "ranked": []}
    return {"strategy": body.strategy, "ranked": rank_candidates(body.candidates, body.strategy)}


def _aggregate_json(row: UsageAggregate) -> dict[str, Any]:
    return {
        "bucket": row.bucket_start.isoformat(), "provider": row.provider or "unknown", "model": row.model,
        "requests": int(row.requests), "successes": int(row.successes), "failures": int(row.failures),
        "prompt_tokens": int(row.prompt_tokens), "completion_tokens": int(row.completion_tokens),
        "cached_tokens": int(row.cached_tokens), "reasoning_tokens": int(row.reasoning_tokens),
        "total_tokens": int(row.prompt_tokens) + int(row.completion_tokens), "cost_microusd": int(row.cost_microusd),
        "average_latency_ms": round(int(row.latency_ms_sum or 0) / max(1, int(row.requests))),
        "average_ttft_ms": round(int(row.ttft_ms_sum or 0) / max(1, int(row.requests))),
    }
