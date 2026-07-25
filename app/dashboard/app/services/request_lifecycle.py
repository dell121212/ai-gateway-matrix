"""Create tasks/requests, reserve credits, settle after upstream."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.core.ids import hash_token, optional_uuid
from dashboard.app.core.logging import setup_logging
from dashboard.app.db.models import ApiKey, ClientRequest, CreditAccount, LlmAttempt, Task, User
from dashboard.app.services import billing_engine, billing_math, events
from dashboard.app.services.billing_math import PriceQuote, TokenUsage

logger = setup_logging("private_api.lifecycle")


async def resolve_api_key(session: AsyncSession, bearer: str) -> Optional[ApiKey]:
    if not bearer:
        return None
    raw = bearer.removeprefix("Bearer ").strip()
    if not raw:
        return None
    kh = hash_token(raw)
    r = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == kh, ApiKey.status == "active")
    )
    return r.scalar_one_or_none()


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
        eu = optional_uuid(external_task_id)
        if eu:
            r = await session.execute(select(Task).where(Task.id == eu))
            t = r.scalar_one_or_none()
            if t:
                return t
        r = await session.execute(
            select(Task).where(
                Task.external_task_id == external_task_id,
                Task.user_id == user_id,
                Task.status == "running",
            )
        )
        t = r.scalar_one_or_none()
        if t:
            return t

    grouping = "explicit" if external_task_id else "inferred"
    task = Task(
        id=uuid.uuid4(),
        user_id=user_id,
        api_key_id=api_key_id,
        external_task_id=external_task_id,
        session_id=session_id,
        client_name=client_name or "",
        workspace_hash=hash_token(workspace_id)[:16] if workspace_id else None,
        title="",
        status="running",
        grouping_source=grouping,
    )
    session.add(task)
    await session.flush()
    return task


async def begin_request(
    session: AsyncSession,
    *,
    user: User,
    account: CreditAccount,
    api_key: Optional[ApiKey],
    task: Task,
    requested_model: str,
    mode: str,
    stream: bool,
    input_token_estimate: int,
) -> tuple[ClientRequest, int, billing_engine.AccountSnapshot]:
    settings = get_settings()
    reserve = billing_math.estimate_reserve_microcredits(
        input_tokens=input_token_estimate,
        expected_output_tokens=1024,
        credits_per_usd=settings.credits_per_usd,
        service_multiplier=settings.service_multiplier,
    )
    if api_key and api_key.request_budget_microcredits:
        reserve = min(reserve, int(api_key.request_budget_microcredits))

    req = ClientRequest(
        id=uuid.uuid4(),
        task_id=task.id,
        user_id=user.id,
        api_key_id=api_key.id if api_key else None,
        requested_model=requested_model,
        mode=mode,
        stream=stream,
        status="reserved",
        input_token_estimate=input_token_estimate,
        estimated_microcredits=reserve,
        reserved_microcredits=reserve,
        retry_policy=settings.billing_retry_policy,
    )
    session.add(req)
    await session.flush()

    try:
        _entry, snap = await billing_engine.reserve_credits(
            session,
            account.id,
            reserve,
            idempotency_key=f"reserve:{req.id}",
            task_id=task.id,
            client_request_id=req.id,
        )
    except billing_engine.InsufficientCredits:
        req.status = "rejected_insufficient"
        await session.flush()
        raise

    task.request_count = int(task.request_count or 0) + 1
    task.estimated_microcredits = int(task.estimated_microcredits or 0) + reserve
    await session.flush()

    await events.publish_event(
        event="credits.reserved",
        task_id=str(task.id),
        user_id=str(user.id),
        payload={
            "request_id": str(req.id),
            "reserved_microcredits": reserve,
            "task_estimated_microcredits": task.estimated_microcredits,
            "available_balance_microcredits": snap.available_microcredits,
            "mode": mode,
            "model": requested_model,
        },
    )
    return req, reserve, snap


async def record_attempt(
    session: AsyncSession,
    req: ClientRequest,
    *,
    attempt_number: int,
    provider: str,
    actual_model: str,
    status: str,
    usage: TokenUsage,
    actual_cost_microusd: int,
    market_value_microusd: int,
    charged_microcredits: int,
    is_final_success: bool = False,
    is_platform_loss: bool = False,
    quality_failure_reason: Optional[str] = None,
    litellm_call_id: Optional[str] = None,
    billing_mode: str = "unknown",
    credit_basis: str = "market_value",
    error_class: Optional[str] = None,
) -> LlmAttempt:
    idem = f"attempt:{req.id}:{attempt_number}:{litellm_call_id or status}"
    existing = await session.execute(
        select(LlmAttempt).where(LlmAttempt.idempotency_key == idem)
    )
    row = existing.scalar_one_or_none()
    if row:
        return row
    att = LlmAttempt(
        client_request_id=req.id,
        attempt_number=attempt_number,
        provider=provider,
        actual_model=actual_model,
        status=status,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        actual_cost_microusd=actual_cost_microusd,
        market_value_microusd=market_value_microusd,
        charged_microcredits=charged_microcredits,
        cost_source="usage" if usage.prompt_tokens or usage.completion_tokens else "estimate",
        is_final_success=is_final_success,
        is_platform_loss=is_platform_loss,
        quality_failure_reason=quality_failure_reason,
        litellm_call_id=litellm_call_id,
        idempotency_key=idem,
        billing_mode=billing_mode,
        credit_basis=credit_basis,
        error_class=error_class,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(att)
    await session.flush()
    return att


async def settle_request(
    session: AsyncSession,
    *,
    account: CreditAccount,
    task: Task,
    req: ClientRequest,
    usage: TokenUsage,
    actual_cost_microusd: int = 0,
    price: Optional[PriceQuote] = None,
    settlement_source: str = "litellm_usage",
    success: bool = True,
    response_status: int = 200,
    error_class: Optional[str] = None,
) -> dict[str, Any]:
    settings = get_settings()
    price = price or PriceQuote()
    market = billing_math.market_value_microusd(usage, price)
    breakdown = billing_math.compute_credits(
        actual_cost_microusd=actual_cost_microusd,
        market_value_microusd=market,
        credits_per_usd=settings.credits_per_usd,
        service_multiplier=settings.service_multiplier,
        price=price,
    )
    charge = breakdown.credits_microcredits if success else 0
    reserved = int(req.reserved_microcredits or 0)

    _entry, snap = await billing_engine.settle_from_reservation(
        session,
        account.id,
        reserved_amount=reserved,
        settle_amount=charge,
        idempotency_key=f"settle:{req.id}",
        task_id=task.id,
        client_request_id=req.id,
        reason="request_settle",
        metadata={
            "settlement_source": settlement_source,
            "actual_cost_microusd": breakdown.actual_cost_microusd,
            "market_value_microusd": breakdown.market_value_microusd,
            "basis": breakdown.billing_basis,
        },
    )

    req.final_prompt_tokens = usage.prompt_tokens
    req.final_completion_tokens = usage.completion_tokens
    req.cached_tokens = usage.cached_tokens
    req.reasoning_tokens = usage.reasoning_tokens
    req.settled_microcredits = charge
    req.estimated_microcredits = max(int(req.estimated_microcredits or 0), charge)
    req.status = "settled" if success else "failed"
    req.settlement_source = settlement_source
    req.response_status_code = response_status
    req.error_class = error_class
    req.finished_at = datetime.now(timezone.utc)
    if req.started_at:
        req.latency_ms = int((req.finished_at - req.started_at).total_seconds() * 1000)

    task.settled_microcredits = int(task.settled_microcredits or 0) + charge
    # reduce estimated by reserved for this request, add settled (approx)
    task.estimated_microcredits = max(
        int(task.settled_microcredits or 0),
        int(task.estimated_microcredits or 0) - reserved + charge,
    )
    await session.flush()

    await events.publish_event(
        event="credits.settled",
        task_id=str(task.id),
        user_id=str(req.user_id),
        payload={
            "request_id": str(req.id),
            "request_settled_microcredits": charge,
            "task_settled_microcredits": task.settled_microcredits,
            "balance_microcredits": snap.balance_microcredits,
            "reserved_microcredits": snap.reserved_microcredits,
            "settled": True,
            "cost_source": settlement_source,
            "success": success,
        },
    )
    return {
        "settled_microcredits": charge,
        "snapshot": snap,
        "breakdown": breakdown,
    }


async def fail_and_release(
    session: AsyncSession,
    *,
    account: CreditAccount,
    task: Task,
    req: ClientRequest,
    error_class: str,
    response_status: int = 500,
) -> None:
    reserved = int(req.reserved_microcredits or 0)
    await billing_engine.release_reservation(
        session,
        account.id,
        reserved,
        idempotency_key=f"release:{req.id}",
        task_id=task.id,
        client_request_id=req.id,
        reason=f"fail:{error_class}",
    )
    req.status = "failed"
    req.error_class = error_class
    req.response_status_code = response_status
    req.finished_at = datetime.now(timezone.utc)
    req.settled_microcredits = 0
    await session.flush()
    await events.publish_event(
        event="credits.released",
        task_id=str(task.id),
        user_id=str(req.user_id),
        payload={
            "request_id": str(req.id),
            "released_microcredits": reserved,
            "error_class": error_class,
        },
    )
