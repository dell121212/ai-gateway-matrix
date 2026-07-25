"""Pure billing math — microcredits / microusd, no I/O.

Units:
  microcredits = 1e-6 credit
  microusd     = 1e-6 USD
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

CreditBasis = Literal["actual_cost", "market_value", "custom"]
Rounding = Literal["ceil", "floor", "round"]


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class PriceQuote:
    # microusd per 1_000_000 tokens
    input_price: int = 0
    output_price: int = 0
    cached_input_price: int = 0
    reasoning_price: int = 0
    credit_multiplier: float = 1.0
    minimum_microcredits: int = 1
    billing_basis: CreditBasis = "market_value"


@dataclass(frozen=True)
class CostBreakdown:
    actual_cost_microusd: int
    market_value_microusd: int
    selected_basis_microusd: int
    credits_microcredits: int
    billing_basis: str
    pricing_note: str = ""


def usd_to_microusd(usd: float) -> int:
    if usd is None or usd != usd:  # NaN
        return 0
    return int(round(float(usd) * 1_000_000))


def microusd_to_usd(microusd: int) -> float:
    return microusd / 1_000_000.0


def credits_to_microcredits(credits: float) -> int:
    return int(round(float(credits) * 1_000_000))


def microcredits_to_credits(micro: int) -> float:
    return micro / 1_000_000.0


def apply_rounding(value: float, mode: Rounding = "ceil") -> int:
    if mode == "floor":
        return int(math.floor(value))
    if mode == "round":
        return int(round(value))
    return int(math.ceil(value))


def market_value_microusd(usage: TokenUsage, price: PriceQuote) -> int:
    """Compute market value from token counts and per-1M-token prices."""
    pt = max(0, int(usage.prompt_tokens))
    ct = max(0, int(usage.completion_tokens))
    cached = max(0, int(usage.cached_tokens))
    reasoning = max(0, int(usage.reasoning_tokens))
    uncached_prompt = max(0, pt - cached)

    total = 0
    total += uncached_prompt * price.input_price
    total += cached * (price.cached_input_price or price.input_price)
    total += ct * price.output_price
    total += reasoning * (price.reasoning_price or price.output_price)
    # prices are per 1M tokens → divide by 1e6, stay in microusd integers
    return int(total // 1_000_000)


def compute_credits(
    *,
    actual_cost_microusd: int,
    market_value_microusd: int,
    credits_per_usd: int = 1000,
    service_multiplier: float = 1.0,
    price: Optional[PriceQuote] = None,
    billing_mode: str = "unknown",
    custom_basis_microusd: Optional[int] = None,
    rounding: Rounding = "ceil",
) -> CostBreakdown:
    """
    credits = selected_cost_basis_usd * credits_per_usd * multipliers
    stored as microcredits (integer).
    """
    price = price or PriceQuote()
    basis = price.billing_basis
    if billing_mode in ("free", "trial") and basis == "actual_cost":
        # free APIs: actual cost may be 0; still may charge market-value credits if configured
        pass

    if basis == "actual_cost":
        selected = max(0, int(actual_cost_microusd))
    elif basis == "custom" and custom_basis_microusd is not None:
        selected = max(0, int(custom_basis_microusd))
    else:
        selected = max(0, int(market_value_microusd))
        basis = "market_value"

    # microusd * credits_per_usd / 1e6 * multiplier → microcredits
    # 1 USD = credits_per_usd credits = credits_per_usd * 1e6 microcredits
    # selected_usd = selected/1e6
    # credits = selected_usd * credits_per_usd * mult
    # microcredits = credits * 1e6 = selected * credits_per_usd * mult
    mult = float(price.credit_multiplier) * float(service_multiplier)
    raw = selected * float(credits_per_usd) * mult
    micro = apply_rounding(raw, rounding)
    if micro > 0:
        micro = max(micro, int(price.minimum_microcredits or 1))
    elif selected > 0:
        micro = max(1, int(price.minimum_microcredits or 1))

    return CostBreakdown(
        actual_cost_microusd=max(0, int(actual_cost_microusd)),
        market_value_microusd=max(0, int(market_value_microusd)),
        selected_basis_microusd=selected,
        credits_microcredits=max(0, micro),
        billing_basis=basis,
    )


def estimate_reserve_microcredits(
    *,
    input_tokens: int,
    expected_output_tokens: int = 1024,
    price: Optional[PriceQuote] = None,
    credits_per_usd: int = 1000,
    service_multiplier: float = 1.0,
    safety_factor: float = 1.5,
) -> int:
    """Conservative reserve for pre-flight freeze."""
    price = price or PriceQuote(
        input_price=500_000,  # $0.50 / 1M as default market
        output_price=1_500_000,
    )
    usage = TokenUsage(
        prompt_tokens=max(0, input_tokens),
        completion_tokens=max(0, expected_output_tokens),
    )
    market = market_value_microusd(usage, price)
    br = compute_credits(
        actual_cost_microusd=0,
        market_value_microusd=market,
        credits_per_usd=credits_per_usd,
        service_multiplier=service_multiplier * safety_factor,
        price=price,
    )
    return max(br.credits_microcredits, price.minimum_microcredits or 1)


def estimate_tokens_from_text(text: str) -> int:
    """Rough token estimate: ~4 chars/token for mixed CJK/EN."""
    if not text:
        return 0
    # CJK denser
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))
