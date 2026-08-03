"""Privacy-preserving request observation for the OpenAI-compatible proxy."""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.db.models import ClientRequest, Task, User
from dashboard.app.services import events, request_lifecycle
from dashboard.app.services.observability import CostQuote, TokenUsage, cost_microusd, normalize_usage
from dashboard.app.services.pricing_service import resolve_price
from dashboard.app.services.request_mode import extract_tracking_headers, resolve_mode, should_force_non_stream
from dashboard.app.services.stream_parser import StreamAccumulator, parse_sse_buffer


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return max(1, int(cjk / 1.5 + (len(text) - cjk) / 4))


def _request_summary(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    roles: dict[str, int] = {}
    characters = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        roles[role] = roles.get(role, 0) + 1
        content = message.get("content")
        if isinstance(content, str):
            characters += len(content)
        elif isinstance(content, list):
            characters += sum(len(str(part.get("text") or "")) for part in content if isinstance(part, dict))
    return {
        "message_count": len(messages),
        "roles": roles,
        "character_count": characters,
        "tool_count": len(body.get("tools") or []) if isinstance(body.get("tools"), list) else 0,
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
    }


async def prepare_chat_observation(
    session: AsyncSession,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    bearer: str,
    default_user: Optional[User],
) -> dict[str, Any]:
    tracking = extract_tracking_headers(headers)
    api_key = await request_lifecycle.resolve_api_key(session, bearer)
    user = default_user
    if api_key:
        user = (await session.execute(select(User).where(User.id == api_key.user_id))).scalar_one_or_none() or user
    mode_result = resolve_mode(
        headers=headers,
        metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
        api_key_default=api_key.default_mode if api_key else None,
        system_default="agent-stream",
    )
    client_stream = bool(body.get("stream"))
    force_non_stream = should_force_non_stream(mode_result.mode, client_stream)
    if not user:
        return {"observation_enabled": False, "mode": mode_result.mode, "force_non_stream": force_non_stream}

    summary = _request_summary(body)
    estimate = _estimate_tokens("x" * int(summary["character_count"]))
    task = await request_lifecycle.get_or_create_task(
        session,
        user_id=user.id,
        api_key_id=api_key.id if api_key else None,
        external_task_id=tracking.get("task_id"),
        session_id=tracking.get("session_id"),
        client_name=tracking.get("client_name") or "",
        workspace_id=tracking.get("workspace_id"),
    )
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    strategy = str(metadata.get("routing_strategy") or "brain-tier")
    request = await request_lifecycle.begin_request(
        session,
        user=user,
        api_key=api_key,
        task=task,
        requested_model=str(body.get("model") or ""),
        mode=mode_result.mode,
        stream=False if force_non_stream else client_stream,
        input_token_estimate=estimate,
        request_summary=summary,
        route_strategy=strategy,
    )
    await session.commit()
    return {
        "observation_enabled": True,
        "mode": mode_result.mode,
        "mode_source": mode_result.source,
        "force_non_stream": force_non_stream,
        "effective_stream": False if force_non_stream else client_stream,
        "user_id": user.id,
        "task_id": task.id,
        "request_id": request.id,
        "input_token_estimate": estimate,
    }


async def finalize_chat_observation(
    session: AsyncSession,
    context: dict[str, Any],
    *,
    usage: TokenUsage,
    success: bool,
    response_status: int = 200,
    model: str = "",
    provider: str = "",
    error_class: Optional[str] = None,
    actual_cost_microusd: int = 0,
    ttft_ms: Optional[int] = None,
    service_tier: str = "",
) -> dict[str, Any]:
    if not context.get("observation_enabled"):
        return {}
    request = (
        await session.execute(select(ClientRequest).where(ClientRequest.id == context["request_id"]))
    ).scalar_one()
    task = (await session.execute(select(Task).where(Task.id == context["task_id"]))).scalar_one()
    price, version = await resolve_price(session, model or request.requested_model, provider or "*")
    if version:
        request.pricing_version_id = version.id
    estimated_cost = cost_microusd(
        usage,
        CostQuote(
            input_microusd_per_million=price.input_price,
            output_microusd_per_million=price.output_price,
            cached_input_microusd_per_million=price.cached_input_price or price.input_price,
            reasoning_microusd_per_million=price.reasoning_price or price.output_price,
        ),
    )
    result = await request_lifecycle.finalize_request(
        session,
        task=task,
        req=request,
        usage=usage,
        cost_microusd=actual_cost_microusd or estimated_cost,
        success=success,
        response_status=response_status,
        provider=provider,
        actual_model=model,
        error_class=error_class,
        ttft_ms=ttft_ms,
        service_tier=service_tier,
    )
    await session.commit()
    return result


async def observe_sse(byte_iter: AsyncIterator[bytes], *, context: dict[str, Any]) -> AsyncIterator[bytes]:
    accumulator = StreamAccumulator()
    buffer = ""
    started = time.monotonic()
    ttft_recorded = False
    async for chunk in byte_iter:
        buffer += chunk.decode("utf-8", errors="ignore")
        if "\n" in buffer:
            complete, buffer = buffer.rsplit("\n", 1)
            parse_sse_buffer(complete + "\n", accumulator)
        if accumulator.first_token_seen and not ttft_recorded:
            context["_ttft_ms"] = round((time.monotonic() - started) * 1000)
            ttft_recorded = True
        request_id = str(context.get("request_id") or "")
        if request_id and events.estimate_throttle.allow(request_id) and accumulator.first_token_seen:
            await events.publish_event(
                event="usage.estimated",
                task_id=str(context.get("task_id") or ""),
                user_id=str(context.get("user_id") or ""),
                payload={
                    "request_id": request_id,
                    "prompt_tokens": int(context.get("input_token_estimate") or 0),
                    "completion_tokens": accumulator.estimated_completion_tokens(),
                },
            )
        yield chunk
    if buffer:
        parse_sse_buffer(buffer, accumulator)
    context["_stream_acc"] = accumulator


def usage_from_response_json(data: dict[str, Any]) -> TokenUsage:
    usage = data.get("usage")
    return normalize_usage(usage if isinstance(usage, dict) else {})


def response_headers_from_context(context: dict[str, Any]) -> dict[str, str]:
    if not context.get("observation_enabled"):
        return {}
    return {
        "X-PrivateAPI-Request-ID": str(context.get("request_id") or ""),
        "X-PrivateAPI-Task-ID": str(context.get("task_id") or ""),
        "X-PrivateAPI-Mode": str(context.get("mode") or ""),
    }
