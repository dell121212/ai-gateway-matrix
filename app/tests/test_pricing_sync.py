from __future__ import annotations

from dashboard.app.services.pricing_sync import configured_model_catalog, normalize_litellm_price


def test_litellm_price_is_converted_to_microusd_per_million_tokens() -> None:
    row = normalize_litellm_price(
        "openai/gpt-test",
        {"litellm_provider": "openai", "input_cost_per_token": 2.5e-6, "output_cost_per_token": 1e-5, "cache_read_input_token_cost": 2.5e-7},
    )
    assert row == {
        "provider": "openai", "model_pattern": "openai/gpt-test",
        "input_price": 2_500_000, "output_price": 10_000_000,
        "cached_input_price": 250_000, "reasoning_price": 10_000_000,
    }


def test_litellm_price_skips_non_chat_or_unpriced_rows() -> None:
    assert normalize_litellm_price("embedding", {"mode": "embedding", "input_cost_per_token": 1e-6}) is None
    assert normalize_litellm_price("unknown", {}) is None


def test_configured_catalog_keeps_sync_working_without_dashboard_litellm() -> None:
    catalog = configured_model_catalog(
        {
            "model_list": [
                {
                    "model_name": "fast-pool",
                    "litellm_params": {"model": "openai/test-model-7b"},
                }
            ]
        }
    )

    assert "openai/test-model-7b" in catalog
    assert catalog["openai/test-model-7b"]["input_cost_per_token"] > 0
