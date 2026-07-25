#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分诊后端解析：智能模式应优先用强档 Key。"""

from __future__ import annotations

import os
import sys
import unittest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.llm_classifier import (  # noqa: E402
    _parse_classifier_tier,
    classify_task,
    resolve_classifier_backend,
    resolve_classifier_backends,
)


class ClassifierResolveTests(unittest.TestCase):
    def test_parser_accepts_first_of_multiple_json_objects(self):
        self.assertEqual(
            _parse_classifier_tier('{"tier":"强"}{"tier":"弱"}'),
            "强",
        )

    def test_parser_accepts_markdown_wrapped_object_array(self):
        self.assertEqual(
            _parse_classifier_tier('说明：```json\n[{"tier":"中"}]\n```'),
            "中",
        )

    def test_explicit_classifier_key_wins(self):
        with mock.patch.dict(
            os.environ,
            {
                "CLASSIFIER_API_KEY": "real-classifier-key-xyz",
                "CLASSIFIER_MODEL": "openai/minimax-m2.7",
                "CLASSIFIER_API_BASE": "https://api.generalcompute.com/v1",
                "GENERALCOMPUTE_API_KEY": "other-key-should-not-win",
                "CLASSIFIER_EXCLUSIVE": "true",
            },
            clear=False,
        ):
            b = resolve_classifier_backend()
            self.assertIsNotNone(b)
            assert b is not None
            self.assertEqual(b["cred_name"], "CLASSIFIER_API_KEY")
            self.assertEqual(b["model"], "openai/minimax-m2.7")
            self.assertEqual(b["api_base"], "https://api.generalcompute.com/v1")
            # 独占：链长应为 1
            chain = resolve_classifier_backends()
            self.assertEqual(len(chain), 1)

    def test_source_env_exclusive(self):
        with mock.patch.dict(
            os.environ,
            {
                "CLASSIFIER_API_KEY": "",
                "CLASSIFIER_SOURCE_ENV": "DEEPSEEK_API_KEY",
                "CLASSIFIER_EXCLUSIVE": "true",
                "DEEPSEEK_API_KEY": "deepseek-stable-paid-key-here",
                "GENERALCOMPUTE_API_KEY": "gc-should-not-be-used",
            },
            clear=False,
        ):
            os.environ["CLASSIFIER_API_KEY"] = ""
            chain = resolve_classifier_backends()
            self.assertEqual(len(chain), 1)
            self.assertEqual(chain[0]["cred_name"], "DEEPSEEK_API_KEY")
            self.assertIn("deepseek", chain[0]["model"])

    def test_auto_picks_generalcompute_when_present(self):
        with mock.patch.dict(
            os.environ,
            {
                "CLASSIFIER_API_KEY": "",
                "GENERALCOMPUTE_API_KEY": "gc_real_looking_key_value_here",
                "GROQ_API_KEY": "sk-test-fake",
            },
            clear=False,
        ):
            # clear empty may still leave other env; force CLASSIFIER empty
            os.environ["CLASSIFIER_API_KEY"] = ""
            b = resolve_classifier_backend()
            self.assertIsNotNone(b)
            assert b is not None
            self.assertEqual(b["cred_name"], "GENERALCOMPUTE_API_KEY")
            self.assertIn("minimax", b["model"])

    def test_classifier_chain_is_three_elite_then_one_strong(self):
        with mock.patch.dict(os.environ, {
            "GENERALCOMPUTE_API_KEY": "gc-real-key",
            "MISTRAL_KEY_1": "mistral-real-key",
            "DEEPSEEK_API_KEY": "deepseek-real-key",
            "OPENROUTER_API_KEY": "openrouter-real-key",
            "GEMINI_API_KEY": "gemini-real-key",
        }, clear=True):
            backends = resolve_classifier_backends()
        self.assertEqual([item["tier"] for item in backends], ["elite", "elite", "elite", "strong"])
        self.assertEqual(backends[-1]["model"], "gemini/gemini-3.5-flash")
        self.assertFalse(any(
            item["model"].startswith(("groq/", "sambanova/"))
            or "siliconflow" in str(item.get("api_base") or "")
            for item in backends
        ))

    def test_classifier_switches_models_and_stops_after_strong(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"tier":"顶级"}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        )
        fake_litellm = mock.MagicMock()
        fake_litellm.acompletion = mock.AsyncMock(
            side_effect=[
                RuntimeError("elite-1"),
                RuntimeError("elite-2"),
                RuntimeError("elite-3"),
                response,
            ]
        )
        with mock.patch.dict(os.environ, {
            "GENERALCOMPUTE_API_KEY": "gc-real-key",
            "MISTRAL_KEY_1": "mistral-real-key",
            "DEEPSEEK_API_KEY": "deepseek-real-key",
            "GEMINI_API_KEY": "gemini-real-key",
        }, clear=True), mock.patch(
            "gateway.llm_classifier.quota_manager.reserve_limits",
            mock.AsyncMock(return_value=True),
        ), mock.patch(
            "gateway.llm_classifier.usage_tracker.record_call",
            mock.AsyncMock(),
        ), mock.patch.dict(sys.modules, {"litellm": fake_litellm}):
            pool = asyncio.run(classify_task("请分析复杂项目架构"))
            completion = fake_litellm.acompletion

        self.assertEqual(pool, "elite-model-pool")
        self.assertEqual(completion.await_count, 4)
        self.assertEqual(
            [call.kwargs["model"] for call in completion.await_args_list],
            [
                "openai/minimax-m2.7",
                "mistral/mistral-large-latest",
                "deepseek/deepseek-reasoner",
                "gemini/gemini-3.5-flash",
            ],
        )

    def test_skips_placeholder_keys(self):
        with mock.patch.dict(os.environ, {
                "CLASSIFIER_API_KEY": "",
                "GENERALCOMPUTE_API_KEY": "",
                "MISTRAL_KEY_1": "sk-test-not-real",
                "DEEPSEEK_API_KEY": "",
                "OPENROUTER_API_KEY": "",
                "GEMINI_API_KEY": "sk-tes000",
                "DASHSCOPE_API_KEY": "",
            }, clear=True):
            b = resolve_classifier_backend()
            self.assertIsNone(b)


if __name__ == "__main__":
    unittest.main()
