#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分诊后端解析：智能模式应优先用强档 Key。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.llm_classifier import (  # noqa: E402
    _parse_classifier_tier,
    resolve_classifier_backend,
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
            },
            clear=False,
        ):
            b = resolve_classifier_backend()
            self.assertIsNotNone(b)
            assert b is not None
            self.assertEqual(b["cred_name"], "CLASSIFIER_API_KEY")
            self.assertEqual(b["model"], "openai/minimax-m2.7")
            self.assertEqual(b["api_base"], "https://api.generalcompute.com/v1")

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

    def test_skips_placeholder_keys(self):
        with mock.patch.dict(
            os.environ,
            {
                "CLASSIFIER_API_KEY": "",
                "GENERALCOMPUTE_API_KEY": "",
                "GROQ_API_KEY": "sk-test-not-real",
                "DEEPSEEK_API_KEY": "",
                "OPENROUTER_API_KEY": "",
                "SAMBANOVA_API_KEY": "已写入（假）",
                "GEMINI_API_KEY": "sk-tes000",
                "SILICONFLOW_API_KEY": "",
            },
            clear=False,
        ):
            b = resolve_classifier_backend()
            self.assertIsNone(b)


if __name__ == "__main__":
    unittest.main()
