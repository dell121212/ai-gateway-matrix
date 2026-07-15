from __future__ import annotations

from gateway.runtime_launcher import build_runtime_config


def test_runtime_config_removes_empty_keys_and_maps_priority_to_order():
    source = {
        "model_list": [
            {
                "model_name": "auto-route",
                "litellm_params": {"model": "openai/fallback", "api_key": "os.environ/A_KEY"},
            },
            {
                "model_name": "fast-pool",
                "litellm_params": {
                    "model": "groq/usable",
                    "api_key": "os.environ/A_KEY",
                    "priority": 90,
                },
            },
            {
                "model_name": "fast-pool",
                "litellm_params": {
                    "model": "other/empty",
                    "api_key": "os.environ/B_KEY",
                    "priority": 100,
                },
            },
        ],
        "router_settings": {"routing_strategy": "usage-based-routing-v2"},
    }

    runtime, stats = build_runtime_config(source, {"A_KEY": "fixture", "B_KEY": ""})

    assert [item["model_name"] for item in runtime["model_list"]] == [
        "auto-route",
        "fast-pool",
    ]
    assert runtime["model_list"][1]["litellm_params"]["order"] == 910
    assert "order" not in source["model_list"][1]["litellm_params"]
    assert stats == {"source": 3, "runtime": 2, "configured_primary": 1}
