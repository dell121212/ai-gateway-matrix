from __future__ import annotations

from datetime import datetime, timezone

from dashboard.app.services.observability import (
    CostQuote,
    TokenUsage,
    aggregate_observations,
    build_observation,
    cost_microusd,
    normalize_usage,
)


def test_normalize_usage_keeps_cache_and_reasoning_splits_non_negative() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 40, "cache_creation_tokens": 8},
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    )
    assert usage == TokenUsage(120, 30, 40, 8, 12)
    assert normalize_usage({"prompt_tokens": -2}).prompt_tokens == 0


def test_cost_is_money_not_points_and_is_reproducible() -> None:
    quote = CostQuote(2_000_000, 8_000_000, 500_000, 0, 10_000_000)
    usage = TokenUsage(1_000_000, 500_000, 200_000, 0, 100_000)
    assert cost_microusd(usage, quote) == 5_900_000


def test_observation_contract_contains_route_and_latency_without_credit_ledger() -> None:
    observation = build_observation(
        request_id="req-1",
        requested_model="auto-route",
        actual_model="deepseek-v3",
        provider="siliconflow",
        status="success",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20),
        cost_microusd_value=37,
        latency_ms=850,
        ttft_ms=120,
        route_strategy="headroom",
        route_reason="剩余额度最高",
        timestamp=datetime(2026, 8, 3, 8, tzinfo=timezone.utc),
    )
    assert observation["total_tokens"] == 120
    assert observation["cost_microusd"] == 37
    assert observation["route"]["strategy"] == "headroom"
    assert not any("credit" in key for key in observation)


def test_aggregate_observations_builds_hourly_provider_rollup() -> None:
    rows = [
        build_observation(request_id="r1", provider="a", requested_model="m", actual_model="m", status="success", usage=TokenUsage(10, 5), cost_microusd_value=100, latency_ms=100, timestamp=datetime(2026, 8, 3, 8, 1, tzinfo=timezone.utc)),
        build_observation(request_id="r2", provider="a", requested_model="m", actual_model="m", status="failed", usage=TokenUsage(7), cost_microusd_value=0, latency_ms=300, timestamp=datetime(2026, 8, 3, 8, 30, tzinfo=timezone.utc)),
    ]
    assert aggregate_observations(rows, bucket="hour") == [{
        "bucket": "2026-08-03T08:00:00+00:00", "provider": "a", "requests": 2,
        "successes": 1, "failures": 1, "prompt_tokens": 17, "completion_tokens": 5,
        "total_tokens": 22, "cost_microusd": 100, "average_latency_ms": 200,
    }]
