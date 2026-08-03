"""Import the installed LiteLLM catalog into the versioned cost registry."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.db.models import PricingVersion


def _microusd_per_million(value: Any) -> int:
    try:
        return max(0, round(float(value or 0) * 1_000_000_000_000))
    except (TypeError, ValueError):
        return 0


def normalize_litellm_price(model: str, raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    if raw.get("mode") in {"embedding", "image_generation", "audio_speech", "audio_transcription", "rerank"}:
        return None
    input_price = _microusd_per_million(raw.get("input_cost_per_token"))
    output_price = _microusd_per_million(raw.get("output_cost_per_token"))
    if not input_price and not output_price:
        return None
    cached = _microusd_per_million(raw.get("cache_read_input_token_cost"))
    reasoning = _microusd_per_million(raw.get("output_cost_per_reasoning_token")) or output_price
    return {
        "provider": str(raw.get("litellm_provider") or model.split("/", 1)[0] or "*"),
        "model_pattern": model,
        "input_price": input_price,
        "output_price": output_price,
        "cached_input_price": cached,
        "reasoning_price": reasoning,
    }


def configured_model_catalog(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a local price catalog when LiteLLM is not in the dashboard image."""

    from gateway.pricing import resolve_price

    catalog: dict[str, dict[str, Any]] = {}
    model_list = config.get("model_list")
    if not isinstance(model_list, list):
        return catalog
    for deployment in model_list:
        params = deployment.get("litellm_params") if isinstance(deployment, Mapping) else None
        if not isinstance(params, Mapping):
            continue
        model = str(params.get("model") or "").strip()
        if not model or model in catalog:
            continue
        price = resolve_price(model, str(params.get("api_base") or "") or None)
        if not price:
            continue
        catalog[model] = {
            "litellm_provider": model.split("/", 1)[0] if "/" in model else "*",
            "input_cost_per_token": price.input_cost_per_token,
            "output_cost_per_token": price.output_cost_per_token,
        }
    return catalog


def _available_catalog() -> Mapping[str, Any]:
    try:
        import litellm

        catalog = getattr(litellm, "model_cost", {}) or {}
        if catalog:
            return catalog
    except ModuleNotFoundError:
        pass

    import yaml

    path = Path(os.environ.get("SOURCE_GATEWAY_CONFIG_PATH", "/app/config.yaml"))
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    return configured_model_catalog(config) if isinstance(config, Mapping) else {}


async def sync_litellm_catalog(session: AsyncSession, *, actor: str) -> dict[str, int]:
    catalog = _available_catalog()
    imported = updated = unchanged = skipped = 0
    now = datetime.now(timezone.utc)
    for model, raw in catalog.items():
        if not isinstance(raw, Mapping):
            skipped += 1
            continue
        normalized = normalize_litellm_price(str(model), raw)
        if not normalized:
            skipped += 1
            continue
        existing = (
            await session.execute(
                select(PricingVersion)
                .where(
                    PricingVersion.provider == normalized["provider"],
                    PricingVersion.model_pattern == normalized["model_pattern"],
                    PricingVersion.effective_to.is_(None),
                )
                .order_by(PricingVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        comparable = ("input_price", "output_price", "cached_input_price", "reasoning_price")
        if existing and all(int(getattr(existing, key) or 0) == int(normalized[key]) for key in comparable):
            unchanged += 1
            continue
        version = 1
        if existing:
            existing.effective_to = now
            version = int(existing.version or 0) + 1
            updated += 1
        else:
            imported += 1
        session.add(
            PricingVersion(
                **normalized,
                billing_basis="market_value",
                credit_multiplier="1.0",
                minimum_microcredits=0,
                source="litellm",
                version=version,
                effective_from=now,
                created_by=actor,
            )
        )
    await session.flush()
    return {"imported": imported, "updated": updated, "unchanged": unchanged, "skipped": skipped}
