from __future__ import annotations

import asyncio

from gateway.custom_router_hook import (
    ComplexityRouterHook,
    FAST_POOL,
    FREE_POOL,
    STRONG_POOL,
)


class FakeRegistry:
    def __init__(self, pools: dict[str, list[dict]]):
        self.pools = pools

    def candidates(self, pool: str, requirements: set[str]) -> list[dict]:
        return self.pools.get(pool, [])


def _hook(registry: FakeRegistry) -> ComplexityRouterHook:
    hook = object.__new__(ComplexityRouterHook)
    hook._provider_registry = registry
    return hook


def test_fast_text_can_fall_up_to_configured_strong_channel(monkeypatch):
    monkeypatch.setenv("ONLY_STRONG_KEY", "fixture")
    strong = {"display_id": "only-strong", "env_var": "ONLY_STRONG_KEY", "direct_model_name": "direct-strong"}
    hook = _hook(FakeRegistry({STRONG_POOL: [strong]}))

    target = asyncio.run(hook._resolve_capability_target(FAST_POOL, {"text"}))

    assert target == STRONG_POOL


def test_strong_text_never_falls_to_configured_free_channel(monkeypatch):
    monkeypatch.setenv("ONLY_FREE_KEY", "fixture")
    free = {"env_var": "ONLY_FREE_KEY", "direct_model_name": "direct-free"}
    hook = _hook(FakeRegistry({FREE_POOL: [free]}))

    try:
        asyncio.run(hook._resolve_capability_target(STRONG_POOL, {"text"}))
    except RuntimeError as exc:
        assert "没有已配置且支持" in str(exc)
    else:
        raise AssertionError("强档不应继续降到中档")
