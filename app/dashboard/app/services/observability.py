"""Pure usage, cost and aggregation helpers for the call-observation pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class CostQuote:
    """Prices in micro-US-dollars per one million tokens."""

    input_microusd_per_million: int = 0
    output_microusd_per_million: int = 0
    cached_input_microusd_per_million: int = 0
    cache_creation_microusd_per_million: int = 0
    reasoning_microusd_per_million: int = 0


def _non_negative(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_usage(raw: Mapping[str, Any] | None) -> TokenUsage:
    raw = raw or {}
    prompt_details = raw.get("prompt_tokens_details")
    completion_details = raw.get("completion_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    completion_details = completion_details if isinstance(completion_details, Mapping) else {}
    return TokenUsage(
        prompt_tokens=_non_negative(raw.get("prompt_tokens")),
        completion_tokens=_non_negative(raw.get("completion_tokens")),
        cached_tokens=_non_negative(prompt_details.get("cached_tokens")),
        cache_creation_tokens=_non_negative(
            prompt_details.get("cache_creation_tokens")
            or prompt_details.get("cache_write_tokens")
        ),
        reasoning_tokens=_non_negative(completion_details.get("reasoning_tokens")),
    )


def cost_microusd(usage: TokenUsage, quote: CostQuote) -> int:
    cached = min(usage.cached_tokens, usage.prompt_tokens)
    uncached = usage.prompt_tokens - cached
    reasoning = min(usage.reasoning_tokens, usage.completion_tokens)
    normal_output = usage.completion_tokens - reasoning
    weighted = (
        uncached * quote.input_microusd_per_million
        + cached * quote.cached_input_microusd_per_million
        + usage.cache_creation_tokens * quote.cache_creation_microusd_per_million
        + normal_output * quote.output_microusd_per_million
        + reasoning * quote.reasoning_microusd_per_million
    )
    return max(0, int(round(weighted / 1_000_000)))


def build_observation(
    *,
    request_id: str,
    requested_model: str,
    actual_model: str,
    provider: str,
    status: str,
    usage: TokenUsage,
    cost_microusd_value: int,
    latency_ms: int | None = None,
    ttft_ms: int | None = None,
    route_strategy: str = "",
    route_reason: str = "",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    observed_at = timestamp or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return {
        "request_id": request_id,
        "requested_model": requested_model,
        "actual_model": actual_model,
        "provider": provider,
        "status": status,
        **asdict(usage),
        "total_tokens": usage.total_tokens,
        "cost_microusd": _non_negative(cost_microusd_value),
        "latency_ms": _non_negative(latency_ms) if latency_ms is not None else None,
        "ttft_ms": _non_negative(ttft_ms) if ttft_ms is not None else None,
        "route": {"strategy": route_strategy, "reason": route_reason},
        "timestamp": observed_at.isoformat(),
    }


def aggregate_observations(
    rows: Iterable[Mapping[str, Any]], *, bucket: Literal["hour", "day"] = "hour"
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        moment = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
        start = moment.replace(
            hour=0 if bucket == "day" else moment.hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        key = (start.isoformat(), str(row.get("provider") or "unknown"))
        item = grouped.setdefault(
            key,
            {
                "bucket": key[0], "provider": key[1], "requests": 0,
                "successes": 0, "failures": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0,
                "cost_microusd": 0, "average_latency_ms": 0, "_latency": 0,
            },
        )
        item["requests"] += 1
        if row.get("status") == "success":
            item["successes"] += 1
        else:
            item["failures"] += 1
        for field in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_microusd"):
            item[field] += _non_negative(row.get(field))
        item["_latency"] += _non_negative(row.get("latency_ms"))
    result = []
    for item in grouped.values():
        item["average_latency_ms"] = round(item.pop("_latency") / item["requests"])
        result.append(item)
    return sorted(result, key=lambda item: (item["bucket"], item["provider"]))
