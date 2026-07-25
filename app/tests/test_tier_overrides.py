#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from dashboard.config_editor import update_tier  # noqa: E402
from gateway import tier_overrides  # noqa: E402


class TierOverrideTests(unittest.TestCase):
    def test_update_tier_moves_aihubmix_style_entry(self):
        source = ROOT / "config.yaml"
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "config.yaml"
            shutil.copy2(source, target)
            model = "openai/gpt-4o-mini"
            api_base = "https://aihubmix.com/v1"
            env_var = "AIHUBMIX_API_KEY"

            # 当前默认可能已在 free/strong；从实际池挪到 elite 验证
            config0 = yaml.safe_load(target.read_text(encoding="utf-8"))
            current_pool = next(
                item["model_name"]
                for item in config0["model_list"]
                if item.get("litellm_params", {}).get("model") == model
                and item.get("litellm_params", {}).get("api_base") == api_base
                and item["model_name"] in tier_overrides.POOLS
            )
            dest = "elite-model-pool" if current_pool != "elite-model-pool" else "free-pool"
            self.assertTrue(
                update_tier(target, current_pool, model, api_base, env_var, dest)
            )
            config = yaml.safe_load(target.read_text(encoding="utf-8"))
            main = next(
                item for item in config["model_list"]
                if item.get("litellm_params", {}).get("model") == model
                and item.get("litellm_params", {}).get("api_base") == api_base
                and item["model_name"] in tier_overrides.POOLS
            )
            self.assertEqual(main["model_name"], dest)

    def test_tier_override_apply_to_source(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tier-overrides.json"
            model = "openai/foo"
            api_base = "https://example.com/v1"
            env_var = "FOO_API_KEY"
            tier_overrides.set_pool(
                model, api_base, env_var, "elite-model-pool", path=state
            )
            source = {
                "model_list": [
                    {
                        "model_name": "fast-pool",
                        "litellm_params": {
                            "model": model,
                            "api_base": api_base,
                            "api_key": f"os.environ/{env_var}",
                        },
                    }
                ]
            }
            n = tier_overrides.apply_to_source(source, path=state)
            self.assertEqual(n, 1)
            self.assertEqual(source["model_list"][0]["model_name"], "elite-model-pool")

    def test_normalize_pool_labels(self):
        self.assertEqual(tier_overrides.normalize_pool("强"), "strong-model-pool")
        self.assertEqual(tier_overrides.normalize_pool("elite"), "elite-model-pool")
        with self.assertRaises(ValueError):
            tier_overrides.normalize_pool("超级")


if __name__ == "__main__":
    unittest.main()
