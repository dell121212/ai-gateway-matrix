"""Create and finalize durable call observations without a credit ledger."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.ids import hash_token, optional_uuid
from dashboard.app.db.models import ApiKey, ClientRequest, LlmAttempt, Task, UsageAggregate, User
from dashboard.app.services import events
from dashboard.app.services.observability import TokenUsage


async def resolve_api_key(session: AsyncSession, bearer: str) -> Optional[ApiKey]:
    raw = bearer.removeprefix("Bearer ").strip() if bearer else ""
    if not raw:
        return None
    result = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_token(raw), ApiKey.status == "active")
    )
    return result.scalar_one_or_none()


async def get_or_create_task(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    api_key_id: Optional[uuid.UUID],
    external_task_id: Optional[str],
    session_id: Optional[str],
    client_name: str,
    workspace_id: Optional[str],
) -> Task:
    if external_task_id:
        parsed = optional_uuid(external_task_id)
        if parsed:
            existing = (await session.execute(select(Task).where(Task.id == parsed))).scalar_one_or_none()
            if existing:
                return existing
        existing = (
            await session.execute(
                select(Task).where(
                    Task.external_task_id == external_task_id,
                    Task.user_id == user_id,
                    Task.status == "running",
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing
    task = Task(
        user_id=user_id,
        api_key_id=api_key_id,
        external_task_id=external_task_id,
        session_id=session_id,
        client_name=client_name or "",
        workspace_hash=hash_token(workspace_id)[:16] if workspace_id else None,
        status="running",
        grouping_source="explicit" if external_task_id else "inferred",
    )
    session.add(task)
    await session.flush()
    return task


async def begin_request(
    session: AsyncSession,
    *,
    user: User,
    api_key: Optional[ApiKey],
    task: Task,
    requested_model: str,
    mode: str,
    stream: bool,
    input_token_estimate: int,
    request_summary: Optional[dict[str, Any]] = None,
    route_strategy: str = "brain-tier",
) -> ClientRequest:
    row = ClientRequest(
        task_id=task.id,
        user_id=user.id,
        api_key_id=api_key.id if api_key else None,
        requested_model=requested_model,
        mode=mode,
        stream=stream,
        status="running",
        input_token_estimate=max(0, input_token_estimate),
        request_summary=request_summary,
        route_strategy=route_strategy,
    )
    session.add(row)
    task.request_count = int(task.request_count or 0) + 1
    await session.flush()
    await events.publish_event(
        event="usage.started",
        task_id=str(task.id),
        user_id=str(user.id),
        payload={"request_id": str(row.id), "model": requested_model, "mode": mode},
    )
    return row


async def record_attempt(
    session: AsyncSession,
    req: ClientRequest,
    *,
    attempt_number: int,
    provider: str,
    actual_model: str,
    status: str,
    usage: TokenUsage,
    cost_microusd: int,
    latency_ms: Optional[int] = None,
    ttft_ms: Optional[int] = None,
    service_tier: str = "",
    is_final_success: bool = False,
    error_class: Optional[str] = None,
    litellm_call_id: Optional[str] = None,
) -> LlmAttempt:
    idem = f"attempt:{req.id}:{attempt_number}:{litellm_call_id or status}"
    existing = (
        await session.execute(select(LlmAttempt).where(LlmAttempt.idempotency_key == idem))
    ).scalar_one_or_none()
    if existing:
        return existing
    row = LlmAttempt(
        client_request_id=req.id,
        attempt_number=attempt_number,
        provider=provider,
        actual_model=actual_model,
        status=status,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        actual_cost_microusd=max(0, cost_microusd),
        market_value_microusd=max(0, cost_microusd),
        charged_microcredits=0,
        cost_source="usage" if usage.total_tokens else "estimate",
        is_final_success=is_final_success,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        service_tier=service_tier,
        error_class=error_class,
        litellm_call_id=litellm_call_id,
        idempotency_key=idem,
        billing_mode="observation",
        credit_basis="none",
        finished_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


async def finalize_request(
    session: AsyncSession,
    *,
    task: Task,
    req: ClientRequest,
    usage: TokenUsage,
    cost_microusd: int,
    success: bool,
    response_status: int,
    provider: str = "",
    actual_model: str = "",
    error_class: Optional[str] = None,
    ttft_ms: Optional[int] = None,
    service_tier: str = "",
) -> dict[str, Any]:
    finished = datetime.now(timezone.utc)
    req.final_prompt_tokens = usage.prompt_tokens
    req.final_completion_tokens = usage.completion_tokens
    req.cached_tokens = usage.cached_tokens
    req.cache_creation_tokens = usage.cache_creation_tokens
    req.reasoning_tokens = usage.reasoning_tokens
    req.cost_microusd = max(0, cost_microusd)
    req.provider = provider
    req.actual_model = actual_model or req.requested_model
    if not req.provider and "/" in req.actual_model:
        req.provider = req.actual_model.split("/", 1)[0]
    if not req.route_reason:
        req.route_reason = f"{req.provider or 'upstream'} 返回 {req.actual_model}"
    req.service_tier = service_tier
    req.ttft_ms = ttft_ms
    req.status = "success" if success else "failed"
    req.response_status_code = response_status
    req.error_class = error_class
    req.finished_at = finished
    req.latency_ms = int((finished - req.started_at).total_seconds() * 1000) if req.started_at else None
    req.settlement_source = "usage"
    req.estimated_microcredits = req.settled_microcredits = req.reserved_microcredits = 0

    task.prompt_tokens = int(task.prompt_tokens or 0) + usage.prompt_tokens
    task.completion_tokens = int(task.completion_tokens or 0) + usage.completion_tokens
    task.cost_microusd = int(task.cost_microusd or 0) + req.cost_microusd
    await _update_aggregates(session, req=req, usage=usage, success=success)
    await record_attempt(
        session,
        req,
        attempt_number=1,
        provider=provider,
        actual_model=req.actual_model,
        status=req.status,
        usage=usage,
        cost_microusd=req.cost_microusd,
        latency_ms=req.latency_ms,
        ttft_ms=ttft_ms,
        service_tier=service_tier,
        is_final_success=success,
        error_class=error_class,
    )
    await session.flush()
    await events.publish_event(
        event="usage.observed",
        task_id=str(task.id),
        user_id=str(req.user_id),
        payload={
            "request_id": str(req.id),
            "status": req.status,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cost_microusd": req.cost_microusd,
            "latency_ms": req.latency_ms,
        },
    )
    return {"request_id": str(req.id), "task_id": str(task.id), "cost_microusd": req.cost_microusd}


async def _update_aggregates(
    session: AsyncSession, *, req: ClientRequest, usage: TokenUsage, success: bool
) -> None:
    moment = req.finished_at or datetime.now(timezone.utc)
    for bucket, start in (
        ("hour", moment.replace(minute=0, second=0, microsecond=0)),
        ("day", moment.replace(hour=0, minute=0, second=0, microsecond=0)),
    ):
        values = {
            "bucket": bucket, "bucket_start": start, "provider": req.provider,
            "model": req.actual_model, "requests": 1,
            "successes": int(success), "failures": int(not success),
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cached_tokens": usage.cached_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_microusd": req.cost_microusd,
            "latency_ms_sum": int(req.latency_ms or 0),
            "ttft_ms_sum": int(req.ttft_ms or 0),
        }
        statement = insert(UsageAggregate).values(**values)
        excluded = statement.excluded
        counters = (
            "requests", "successes", "failures", "prompt_tokens",
            "completion_tokens", "cached_tokens", "reasoning_tokens",
            "cost_microusd", "latency_ms_sum", "ttft_ms_sum",
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_usage_aggregate_slice",
            set_={field: getattr(UsageAggregate, field) + getattr(excluded, field) for field in counters},
        )
        await session.execute(statement)
