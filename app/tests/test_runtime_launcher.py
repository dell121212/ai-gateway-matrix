from __future__ import annotations

from gateway.runtime_launcher import build_runtime_config, _is_placeholder_credential


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
                    "max_input_tokens": 128000,
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
        "router_settings": {"routing_strategy": "simple-shuffle"},
    }

    runtime, stats = build_runtime_config(source, {"A_KEY": "fixture", "B_KEY": ""})

    assert [item["model_name"] for item in runtime["model_list"]] == [
        "auto-route",
        "fast-pool",
    ]
    assert runtime["model_list"][1]["litellm_params"]["order"] == 910
    assert "priority" not in runtime["model_list"][1]["litellm_params"]
    assert "max_input_tokens" not in runtime["model_list"][1]["litellm_params"]
    assert "order" not in source["model_list"][1]["litellm_params"]
    assert stats == {"source": 3, "runtime": 2, "configured_primary": 1}


def test_placeholder_keys_are_not_configured():
    assert _is_placeholder_credential("")
    assert _is_placeholder_credential("dummy-key")
    assert _is_placeholder_credential("sk-test-abc")
    assert _is_placeholder_credential("sk-tes000000000000000000")
    assert _is_placeholder_credential("已写入（仪表盘）")
    assert not _is_placeholder_credential("gsk_real_looking_key_value")


def test_runtime_drops_sk_test_placeholders():
    source = {
        "model_list": [
            {
                "model_name": "strong-model-pool",
                "litellm_params": {
                    "model": "groq/bad",
                    "api_key": "os.environ/GROQ_API_KEY",
                    "priority": 80,
                },
            },
            {
                "model_name": "strong-model-pool",
                "litellm_params": {
                    "model": "sambanova/good",
                    "api_key": "os.environ/SAMBANOVA_API_KEY",
                    "priority": 90,
                },
            },
        ]
    }
    runtime, stats = build_runtime_config(
        source,
        {
            "GROQ_API_KEY": "sk-test-not-real",
            "SAMBANOVA_API_KEY": "real-sambanova-token-xyz",
        },
    )
    assert stats["configured_primary"] == 1
    assert runtime["model_list"][0]["litellm_params"]["model"] == "sambanova/good"
