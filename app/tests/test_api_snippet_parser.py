#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.api_snippet_parser import parse_provider_snippet  # noqa: E402


class SnippetParserTests(unittest.TestCase):
    def test_curl_bearer(self):
        text = """
        curl https://api.foo-llm.com/v1/chat/completions \\
          -H "Authorization: Bearer sk-abc1234567890xyz" \\
          -H "Content-Type: application/json" \\
          -d '{"model":"foo-flash-7b","messages":[]}'
        """
        p = parse_provider_snippet(text)
        self.assertIn("api.foo-llm.com", p["api_base"])
        self.assertEqual(p["api_key"], "sk-abc1234567890xyz")
        self.assertEqual(p["model"], "foo-flash-7b")
        self.assertGreaterEqual(p["confidence"], 0.7)

    def test_env_style(self):
        text = """
        OPENAI_BASE_URL=https://gateway.example.org/v1
        OPENAI_API_KEY=tok_live_12345678
        # model: bar-32b
        """
        p = parse_provider_snippet(text)
        self.assertTrue(p["api_base"].endswith("/v1"))
        self.assertEqual(p["api_key"], "tok_live_12345678")
        self.assertIn("bar-32b", p["models_mentioned"] or [p.get("model")])

    def test_json_blob(self):
        text = '{"name":"Acme AI","base_url":"https://acme.test/v1","api_key":"key_abcdefgh","model":"acme-large"}'
        p = parse_provider_snippet(text)
        self.assertEqual(p["provider_name"], "Acme AI")
        self.assertEqual(p["api_base"], "https://acme.test/v1")
        self.assertEqual(p["model"], "acme-large")


if __name__ == "__main__":
    unittest.main()
