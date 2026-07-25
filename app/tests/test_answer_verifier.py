#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.answer_verifier import soft_suspect  # noqa: E402
from dashboard.quota_catalog import build_rate_limits, summarize_primary_quota  # noqa: E402


class AnswerVerifierTests(unittest.TestCase):
    def test_soft_suspect_upstream_error(self):
        self.assertEqual(
            soft_suspect("写个函数", "Error: rate limit exceeded, try again later"),
            "upstream_error_text",
        )

    def test_soft_suspect_too_short(self):
        prompt = "请详细解释一下微服务架构的优缺点以及适用场景，并给出示例。" * 3
        self.assertEqual(soft_suspect(prompt, "好的"), "too_short")

    def test_normal_answer_not_suspect(self):
        self.assertIsNone(
            soft_suspect(
                "1+1等于几？",
                "1+1 等于 2。这是基本算术。",
            )
        )


class QuotaSummaryTests(unittest.TestCase):
    def test_agnes_is_not_noisy(self):
        rl = build_rate_limits(
            "AGNES_API_KEY",
            config_rpm=20,
            usage={"available": True, "calls_this_minute": 5, "total_tokens": 2000, "day_tokens": 100},
        )
        ids = [w["id"] for w in rl["windows"]]
        self.assertEqual(ids, ["rpm"])
        self.assertNotIn("img_1k_rpm", ids)
        summary = rl["summary"]
        self.assertIsNotNone(summary["remaining_pct"])
        self.assertAlmostEqual(summary["remaining_pct"], 75.0)
        self.assertEqual(summary["total_tokens"], 2000)

    def test_prefer_daily_over_rpm(self):
        windows = [
            {"id": "rpm", "metric": "requests", "window_sec": 60, "limit": 30, "used": 10,
             "label_zh": "每分钟"},
            {"id": "rpd", "metric": "requests", "window_sec": 86400, "limit": 200, "used": 50,
             "label_zh": "每日请求"},
        ]
        s = summarize_primary_quota(windows, {"available": True, "total_tokens": 9})
        self.assertEqual(s["period_label"], "每日请求")
        self.assertAlmostEqual(s["remaining_pct"], 75.0)


if __name__ == "__main__":
    unittest.main()
