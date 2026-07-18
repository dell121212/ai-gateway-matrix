#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由模式别名与强制档位解析（不依赖 pytest）。"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.custom_router_hook import (  # noqa: E402
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

    def request_requirements(self, data: dict) -> set[str]:
        return {"text"}

    def security_text(self, data: dict) -> str:
        return ""

    def sensitive_candidates(self, requirements: set[str]) -> list[dict]:
        return []


def _hook(registry: FakeRegistry) -> ComplexityRouterHook:
    hook = object.__new__(ComplexityRouterHook)
    hook._provider_registry = registry
    hook._stats = {
        "total_requests": 0,
        "non_completion_skipped": 0,
        "routed_to_trusted_sensitive": 0,
        "escalated_to_strong": 0,
        "routed_to_fast": 0,
        "routed_to_free": 0,
        "routed_to_optimal": 0,
        "classifier_used": 0,
        "classifier_fallback_to_heuristic": 0,
        "classifier_skipped_trivial": 0,
    }
    hook._channel_registry = {}
    return hook


class RouteModeTests(unittest.TestCase):
    def test_resolve_aliases(self):
        r = ComplexityRouterHook._resolve_client_mode
        self.assertEqual(r("auto-route"), "intelligent")
        self.assertEqual(r("mode-intelligent"), "intelligent")
        self.assertEqual(r("mode-weak"), "weak")
        self.assertEqual(r("fast-pool"), "weak")
        self.assertEqual(r("mode-mid"), "mid")
        self.assertEqual(r("free-pool"), "mid")
        self.assertEqual(r("mode-strong"), "strong")
        self.assertEqual(r("strong-model-pool"), "strong")
        self.assertEqual(r("mode-elite"), "elite")
        self.assertEqual(r("elite-model-pool"), "elite")
        self.assertIsNone(r("gpt-4o"))

    def test_forced_weak_falls_up_when_empty(self):
        os.environ["ONLY_STRONG"] = "fixture-key"
        strong = {"env_var": "ONLY_STRONG", "direct_model_name": "direct-strong"}
        hook = _hook(FakeRegistry({STRONG_POOL: [strong]}))

        async def run():
            data = {"model": "mode-weak", "messages": [{"role": "user", "content": "hi"}]}
            out = await hook.async_pre_call_hook(None, None, data, "acompletion")
            return out["model"]

        target = asyncio.run(run())
        self.assertEqual(target, STRONG_POOL)

    def test_forced_strong_falls_to_free_when_no_strong_key(self):
        """可用性优先：强档无 Key 时允许落到已配置的免费档，不整次失败。"""
        os.environ["ONLY_FREE"] = "fixture-key"
        free = {"env_var": "ONLY_FREE", "direct_model_name": "direct-free"}
        hook = _hook(FakeRegistry({FREE_POOL: [free]}))

        async def run():
            data = {"model": "mode-strong", "messages": [{"role": "user", "content": "hi"}]}
            out = await hook.async_pre_call_hook(None, None, data, "acompletion")
            return out["model"]

        target = asyncio.run(run())
        self.assertEqual(target, FREE_POOL)

    def test_no_keys_anywhere_still_errors(self):
        hook = _hook(FakeRegistry({}))

        async def run():
            data = {"model": "auto-route", "messages": [{"role": "user", "content": "hello world task"}]}
            return await hook.async_pre_call_hook(None, None, data, "acompletion")

        with self.assertRaises(RuntimeError):
            asyncio.run(run())

    def test_marked_mid_channel_covers_weak_and_mid_but_not_strong(self):
        os.environ["FLAGGED_MID_KEY"] = "fixture-key"
        channel = {
            "display_id": "flagged-mid",
            "env_var": "FLAGGED_MID_KEY",
            "pool": FREE_POOL,
            "direct_model_name": "direct-flagged-mid",
            "capabilities": {"text": True},
        }
        hook = _hook(FakeRegistry({}))
        hook._channel_registry = {"flagged-mid": channel}
        flags = AsyncMock(return_value=[{"display_id": "flagged-mid"}])
        reserve = AsyncMock(return_value=True)

        async def run():
            weak = await hook._pick_optimal_channel({"text"}, FAST_POOL)
            mid = await hook._pick_optimal_channel({"text"}, FREE_POOL)
            strong = await hook._pick_optimal_channel({"text"}, STRONG_POOL)
            return weak, mid, strong

        with patch("gateway.custom_router_hook.optimal_channels.list_optimal", flags), patch(
            "gateway.custom_router_hook.quota_manager.reserve_channel", reserve
        ):
            weak, mid, strong = asyncio.run(run())

        self.assertEqual(weak, "direct-flagged-mid")
        self.assertEqual(mid, "direct-flagged-mid")
        self.assertIsNone(strong)

    def test_marked_channel_without_quota_falls_back(self):
        os.environ["FLAGGED_NO_QUOTA_KEY"] = "fixture-key"
        channel = {
            "display_id": "flagged-no-quota",
            "env_var": "FLAGGED_NO_QUOTA_KEY",
            "pool": FREE_POOL,
            "direct_model_name": "direct-flagged-no-quota",
            "capabilities": {"text": True},
        }
        hook = _hook(FakeRegistry({}))
        hook._channel_registry = {"flagged-no-quota": channel}

        with patch(
            "gateway.custom_router_hook.optimal_channels.list_optimal",
            AsyncMock(return_value=[{"display_id": "flagged-no-quota"}]),
        ), patch(
            "gateway.custom_router_hook.quota_manager.reserve_channel",
            AsyncMock(return_value=False),
        ):
            target = asyncio.run(hook._pick_optimal_channel({"text"}, FAST_POOL))

        self.assertIsNone(target)


if __name__ == "__main__":
    unittest.main()
