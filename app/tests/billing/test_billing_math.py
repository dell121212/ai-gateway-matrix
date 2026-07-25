"""Unit tests: credit math, rounding, minimum charge."""

from dashboard.app.services.billing_math import (
    PriceQuote,
    TokenUsage,
    compute_credits,
    estimate_reserve_microcredits,
    estimate_tokens_from_text,
    market_value_microusd,
    microcredits_to_credits,
    usd_to_microusd,
)


def test_usd_to_microusd():
    assert usd_to_microusd(1.0) == 1_000_000
    assert usd_to_microusd(0.001) == 1000


def test_market_value_basic():
    price = PriceQuote(input_price=1_000_000, output_price=2_000_000)  # $1 / $2 per 1M
    usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=500_000)
    # 1*1 + 0.5*2 = 2 USD = 2_000_000 microusd
    assert market_value_microusd(usage, price) == 2_000_000


def test_compute_credits_default_formula():
    # 0.001 USD market * 1000 credits/USD = 1 credit = 1_000_000 microcredits
    br = compute_credits(
        actual_cost_microusd=0,
        market_value_microusd=1_000,  # $0.001
        credits_per_usd=1000,
        service_multiplier=1.0,
        price=PriceQuote(minimum_microcredits=1),
    )
    assert br.credits_microcredits == 1_000_000
    assert br.billing_basis == "market_value"
    assert br.actual_cost_microusd == 0


def test_minimum_microcredits():
    br = compute_credits(
        actual_cost_microusd=0,
        market_value_microusd=1,  # tiny
        credits_per_usd=1000,
        price=PriceQuote(minimum_microcredits=100),
    )
    assert br.credits_microcredits >= 100


def test_free_api_actual_zero_still_market_credits():
    br = compute_credits(
        actual_cost_microusd=0,
        market_value_microusd=500_000,
        credits_per_usd=1000,
        price=PriceQuote(billing_basis="market_value"),
    )
    assert br.actual_cost_microusd == 0
    assert br.credits_microcredits > 0


def test_actual_cost_basis():
    br = compute_credits(
        actual_cost_microusd=2_000_000,
        market_value_microusd=9_000_000,
        credits_per_usd=1000,
        price=PriceQuote(billing_basis="actual_cost"),
    )
    assert br.selected_basis_microusd == 2_000_000
    assert br.credits_microcredits == 2_000_000_000  # 2 USD * 1000 * 1e6


def test_estimate_tokens_and_reserve():
    n = estimate_tokens_from_text("你好世界 hello world")
    assert n >= 1
    r = estimate_reserve_microcredits(input_tokens=100, expected_output_tokens=200)
    assert r >= 1


def test_microcredits_to_credits():
    assert microcredits_to_credits(1_500_000) == 1.5
