"""Resolve active pricing version for a model."""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.db.models import PricingVersion
from dashboard.app.services.billing_math import PriceQuote


async def resolve_price(
    session: AsyncSession,
    model: str,
    provider: str = "*",
    at: Optional[datetime] = None,
) -> tuple[PriceQuote, Optional[PricingVersion]]:
    at = at or datetime.now(timezone.utc)
    r = await session.execute(
        select(PricingVersion).where(
            (PricingVersion.effective_to.is_(None)) | (PricingVersion.effective_to > at)
        )
    )
    rows = list(r.scalars().all())
    # most specific match first
    candidates: list[PricingVersion] = []
    for row in rows:
        if row.effective_from and row.effective_from > at:
            continue
        if row.provider not in ("*", provider) and provider != "*":
            continue
        if fnmatch.fnmatch(model or "", row.model_pattern) or row.model_pattern == "*":
            candidates.append(row)

    def score(p: PricingVersion) -> tuple:
        return (
            0 if p.provider == "*" else 1,
            0 if p.model_pattern == "*" else len(p.model_pattern),
            p.version,
        )

    candidates.sort(key=score, reverse=True)
    if not candidates:
        return PriceQuote(), None
    best = candidates[0]
    try:
        mult = float(best.credit_multiplier or "1.0")
    except ValueError:
        mult = 1.0
    return (
        PriceQuote(
            input_price=int(best.input_price),
            output_price=int(best.output_price),
            cached_input_price=int(best.cached_input_price or 0),
            reasoning_price=int(best.reasoning_price or 0),
            credit_multiplier=mult,
            minimum_microcredits=int(best.minimum_microcredits or 1),
            billing_basis=best.billing_basis if best.billing_basis in ("actual_cost", "market_value", "custom") else "market_value",  # type: ignore[arg-type]
        ),
        best,
    )
