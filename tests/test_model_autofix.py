#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型改名自愈：相似度与错误识别（不依赖 litellm 网络）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.model_autofix import (  # noqa: E402
    is_model_name_error,
    join_litellm_model,
    rank_candidates,
    similarity,
    split_litellm_model,
)


class ModelAutofixUnitTests(unittest.TestCase):
    def test_split_join_openrouter(self):
        p, u = split_litellm_model("openrouter/meta-llama/llama-3.3-70b-instruct:free")
        self.assertEqual(p, "openrouter")
        self.assertEqual(u, "meta-llama/llama-3.3-70b-instruct:free")
        self.assertEqual(join_litellm_model(p, "meta-llama/llama-3.3-70b-instruct:free"),
                         "openrouter/meta-llama/llama-3.3-70b-instruct:free")

    def test_similarity_small_rename(self):
        a = "meta-llama/llama-3.3-70b-instruct:free"
        b = "meta-llama/llama-3.3-70b-instruct-v2:free"
        self.assertGreater(similarity(a, b), 0.7)

    def test_rank_picks_close_name(self):
        old = "google/gemma-2-9b-it:free"
        catalog = [
            "google/gemma-2-9b-it:free",  # exact ignored
            "google/gemma-2-9b-it-v2:free",
            "openai/gpt-4o",
            "meta-llama/llama-3.3-70b-instruct:free",
        ]
        ranked = rank_candidates(old, catalog)
        self.assertTrue(ranked)
        self.assertIn("gemma", ranked[0][0])

    def test_rank_never_drops_free_suffix_or_changes_modality(self):
        self.assertEqual(
            rank_candidates("mistralai/mistral-nemo:free", ["mistralai/mistral-nemo"]),
            [],
        )
        self.assertEqual(
            rank_candidates("Qwen/Qwen2.5-7B-Instruct", ["Qwen/Qwen3-VL-8B-Instruct"]),
            [],
        )

    def test_rank_rejects_large_size_jump(self):
        self.assertEqual(
            rank_candidates("google/gemma-2-9b-it:free", ["google/gemma-2-27b-it:free"]),
            [],
        )

    def test_error_markers(self):
        self.assertTrue(is_model_name_error("Error: model_not_found"))
        self.assertTrue(is_model_name_error("模型不存在：glm-4.5-flash"))
        self.assertTrue(is_model_name_error('{"error":{"code":"invalid_request_error","message":"model invalid"}}'))
        self.assertFalse(is_model_name_error("该模型当前访问量过大，请您稍后再试。"))


if __name__ == "__main__":
    unittest.main()
