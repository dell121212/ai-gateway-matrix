"""Billing-aware helpers for OpenAI-compatible proxy path."""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.core.logging import setup_logging
from dashboard.app.db.models import CreditAccount, User
from dashboard.app.services import billing_engine, billing_math, events, request_lifecycle
from dashboard.app.services.billing_math import TokenUsage
from dashboard.app.services.pricing_service import resolve_price
from dashboard.app.services.request_mode import (
    InvalidModeError,
    extract_tracking_headers,
    resolve_mode,
    should_force_non_stream,
)
from dashboard.app.services.stream_parser import StreamAccumulator, parse_sse_buffer
from sqlalchemy import select

logger = setup_logging("private_api.billing_proxy")


async def prepare_chat_billing(
    session: AsyncSession,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    bearer: str,
    default_user: Optional[User],
) -> dict[str, Any]:
    """
    Returns context dict used after upstream completes.
    May raise billing_engine.InsufficientCredits / InvalidModeError.
    """
    settings = get_settings()
    tracking = extract_tracking_headers(headers)
    api_key = await request_lifecycle.resolve_api_key(session, bearer)

    user = default_user
    account: Optional[CreditAccount] = None
    if api_key:
        ur = await session.execute(select(User).where(User.id == api_key.user_id))
        user = ur.scalar_one_or_none() or user
        ar = await session.execute(
            select(CreditAccount).where(CreditAccount.id == api_key.credit_account_id)
        )
        account = ar.scalar_one_or_none()

    if user and account is None:
        ar = await session.execute(
            select(CreditAccount).where(CreditAccount.user_id == user.id).limit(1)
        )
        account = ar.scalar_one_or_none()

    # Personal open mode without accounts: skip hard billing
    if user is None or account is None:
        if settings.billing_fail_mode == "closed":
            raise billing_engine.BillingError(
                "billing_unavailable", "无法识别积分账户", 402
            )
        return {"billing_enabled": False, "mode": "agent-stream", "force_non_stream": False}

    try:
        mode_res = resolve_mode(
            headers=headers,
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
            api_key_default=api_key.default_mode if api_key else None,
            system_default=settings.default_request_mode,
        )
    except InvalidModeError:
        raise

    client_stream = bool(body.get("stream"))
    force_non_stream = should_force_non_stream(mode_res.mode, client_stream)
    effective_stream = False if force_non_stream else client_stream

    # estimate tokens from messages
    text_parts: list[str] = []
    for m in body.get("messages") or []:
        if isinstance(m, dict):
            c = m.get("content")
            if isinstance(c, str):
                text_parts.append(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(str(part.get("text") or ""))
    est_tokens = billing_math.estimate_tokens_from_text("\n".join(text_parts))

    task = await request_lifecycle.get_or_create_task(
        session,
        user_id=user.id,
        api_key_id=api_key.id if api_key else None,
        external_task_id=tracking.get("task_id"),
        session_id=tracking.get("session_id"),
        client_name=tracking.get("client_name") or "",
        workspace_id=tracking.get("workspace_id"),
    )
    req, reserved, snap = await request_lifecycle.begin_request(
        session,
        user=user,
        account=account,
        api_key=api_key,
        task=task,
        requested_model=str(body.get("model") or ""),
        mode=mode_res.mode,
        stream=effective_stream,
        input_token_estimate=est_tokens,
    )
    await session.commit()

    return {
        "billing_enabled": True,
        "mode": mode_res.mode,
        "mode_source": mode_res.source,
        "force_non_stream": force_non_stream,
        "effective_stream": effective_stream,
        "user_id": user.id,
        "account_id": account.id,
        "task_id": task.id,
        "request_id": req.id,
        "reserved": reserved,
        "available": snap.available_microcredits,
        "input_token_estimate": est_tokens,
    }


async def finalize_chat_billing(
    session: AsyncSession,
    ctx: dict[str, Any],
    *,
    usage: TokenUsage,
    success: bool,
    response_status: int = 200,
    model: str = "",
    error_class: Optional[str] = None,
    actual_cost_microusd: int = 0,
) -> dict[str, Any]:
    if not ctx.get("billing_enabled"):
        return {}
    from dashboard.app.db.models import ClientRequest, Task

    req = (
        await session.execute(
            select(ClientRequest).where(ClientRequest.id == ctx["request_id"])
        )
    ).scalar_one()
    task = (
        await session.execute(select(Task).where(Task.id == ctx["task_id"]))
    ).scalar_one()
    account = (
        await session.execute(
            select(CreditAccount).where(CreditAccount.id == ctx["account_id"])
        )
    ).scalar_one()

    price, pver = await resolve_price(session, model or req.requested_model)
    if pver:
        req.pricing_version_id = pver.id

    result = await request_lifecycle.settle_request(
        session,
        account=account,
        task=task,
        req=req,
        usage=usage,
        actual_cost_microusd=actual_cost_microusd,
        price=price,
        settlement_source="litellm_usage" if usage.prompt_tokens or usage.completion_tokens else "estimated",
        success=success,
        response_status=response_status,
        error_class=error_class,
    )
    await request_lifecycle.record_attempt(
        session,
        req,
        attempt_number=1,
        provider="",
        actual_model=model or req.requested_model,
        status="success" if success else "failed",
        usage=usage,
        actual_cost_microusd=actual_cost_microusd,
        market_value_microusd=result["breakdown"].market_value_microusd,
        charged_microcredits=result["settled_microcredits"] if success else 0,
        is_final_success=success,
        is_platform_loss=False,
        billing_mode="unknown",
        credit_basis=result["breakdown"].billing_basis,
        error_class=error_class,
    )
    await session.commit()
    return {
        "request_id": str(req.id),
        "task_id": str(task.id),
        "settled_microcredits": result["settled_microcredits"],
    }


async def observe_sse_and_estimate(
    byte_iter: AsyncIterator[bytes],
    *,
    ctx: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Yield original bytes; side-channel estimate events."""
    acc = StreamAccumulator()
    buf = ""
    async for chunk in byte_iter:
        try:
            text = chunk.decode("utf-8", errors="ignore")
            buf += text
            # process complete lines
            if "\n" in buf:
                lines, buf = buf.rsplit("\n", 1)
                parse_sse_buffer(lines + "\n", acc)
            rid = str(ctx.get("request_id") or "")
            if rid and events.estimate_throttle.allow(rid) and acc.first_token_seen:
                est_out = acc.estimated_completion_tokens()
                est_in = int(ctx.get("input_token_estimate") or 0)
                # rough credits estimate
                settings = get_settings()
                market = billing_math.market_value_microusd(
                    TokenUsage(prompt_tokens=est_in, completion_tokens=est_out),
                    billing_math.PriceQuote(),
                )
                br = billing_math.compute_credits(
                    actual_cost_microusd=0,
                    market_value_microusd=market,
                    credits_per_usd=settings.credits_per_usd,
                    service_multiplier=settings.service_multiplier,
                )
                await events.publish_event(
                    event="credits.estimated",
                    task_id=str(ctx.get("task_id")),
                    user_id=str(ctx.get("user_id")),
                    payload={
                        "request_id": rid,
                        "request_estimated_microcredits": br.credits_microcredits,
                        "settled": False,
                        "source": "stream_estimate",
                        "completion_tokens_est": est_out,
                    },
                )
        except Exception as exc:
            logger.warning("sse observe error (forward continues): %s", exc)
        yield chunk
    # leftover
    if buf:
        parse_sse_buffer(buf, acc)
    ctx["_stream_acc"] = acc


def usage_from_response_json(data: dict[str, Any]) -> TokenUsage:
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return TokenUsage()
    details = usage.get("prompt_tokens_details") or {}
    cdetails = usage.get("completion_tokens_details") or {}
    return TokenUsage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0,
        reasoning_tokens=int(cdetails.get("reasoning_tokens") or 0)
        if isinstance(cdetails, dict)
        else 0,
    )


def response_headers_from_ctx(ctx: dict[str, Any]) -> dict[str, str]:
    if not ctx.get("billing_enabled"):
        return {}
    return {
        "X-PrivateAPI-Request-ID": str(ctx.get("request_id") or ""),
        "X-PrivateAPI-Task-ID": str(ctx.get("task_id") or ""),
        "X-PrivateAPI-Mode": str(ctx.get("mode") or ""),
    }
