#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway import pricing  # noqa: E402


class PricingTests(unittest.TestCase):
    def setUp(self):
        pricing.clear_price_cache()

    def test_infer_size(self):
        self.assertEqual(pricing.infer_model_size_billions("Qwen2.5-7B-Instruct"), 7.0)
        self.assertEqual(pricing.infer_model_size_billions("llama-3.3-70b"), 70.0)
        self.assertLessEqual(pricing.infer_model_size_billions("ministral-8b-latest"), 8.0)

    def test_small_model_cheaper_than_large(self):
        small = pricing.price_for_size_band(7.0)
        large = pricing.price_for_size_band(70.0)
        self.assertLess(small.input_cost_per_token, large.input_cost_per_token)

    def test_cache(self):
        a = pricing.resolve_price("openai/Foo-7B")
        b = pricing.resolve_price("openai/Foo-7B")
        self.assertIs(a, b)  # same cached object

    def test_savings_discount(self):
        s7, _ = pricing.market_value_for_savings("model-7b", 1_000_000)
        s70, _ = pricing.market_value_for_savings("model-70b", 1_000_000)
        self.assertIsNotNone(s7)
        self.assertIsNotNone(s70)
        self.assertLess(s7, s70)

    def test_generalcompute_official(self):
        cost, src = pricing.compute_cost(
            "openai/minimax-m2.7",
            object(),
            1000,
            500,
            api_base="https://api.generalcompute.com/v1",
        )
        self.assertEqual(src, "official")
        self.assertAlmostEqual(cost, 1000 * 0.40e-6 + 500 * 2.34e-6)


if __name__ == "__main__":
    unittest.main()
