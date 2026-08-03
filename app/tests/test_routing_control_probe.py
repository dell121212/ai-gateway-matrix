from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from dashboard import backend
from gateway import llm_classifier


class RoutingControlProbeTests(unittest.TestCase):
    def test_classifier_probe_channel_never_contains_secret(self):
        channel = backend._classifier_probe_channel(
            {
                "model": "openai/minimax-m2.7",
                "api_base": "https://api.generalcompute.com/v1",
                "api_key": "must-not-leak",
                "cred_name": "GENERALCOMPUTE_API_KEY",
                "label": "GeneralCompute/minimax",
            }
        )

        self.assertEqual(channel, {
            "channel_id": "classifier",
            "model": "openai/minimax-m2.7",
            "api_base": "https://api.generalcompute.com/v1",
            "env_var": "GENERALCOMPUTE_API_KEY",
            "provider_name": "GeneralCompute/minimax",
        })
        self.assertNotIn("api_key", channel)

    def test_routing_probe_uses_fresh_jiyi_env_key_without_returning_it(self):
        candidate = {
            "model": "openai/minimax-m2.7",
            "api_base": "https://api.generalcompute.com/v1",
            "api_key": "stale-key",
            "cred_name": "GENERALCOMPUTE_API_KEY",
            "label": "GeneralCompute/minimax",
        }
        probe = mock.AsyncMock(return_value={
            "ok": True,
            "label": "GeneralCompute/minimax",
            "model": "minimax-m2.7",
            "latency_ms": 18,
            "message": "chat/completions 成功",
            "prompt_tokens": 1,
            "completion_tokens": 1,
        })
        with (
            mock.patch.object(llm_classifier, "resolve_classifier_backend", return_value=candidate),
            mock.patch.object(
                backend.channel_loader,
                "read_env_file",
                return_value={"GENERALCOMPUTE_API_KEY": "fresh-portable-key"},
            ),
            mock.patch.object(backend.channel_loader, "load_channels", return_value=[]),
            mock.patch.object(backend, "_probe_chat", probe),
        ):
            result = asyncio.run(backend.probe_routing_control())

        self.assertEqual(probe.await_args.kwargs["api_key"], "fresh-portable-key")
        self.assertTrue(result["connection_ok"])
        self.assertTrue(result["answer_verify_mode"])
        self.assertNotIn("fresh-portable-key", str(result))


if __name__ == "__main__":
    unittest.main()
