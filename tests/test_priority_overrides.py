from gateway import priority_overrides


def test_manual_priority_survives_catalog_default_change(tmp_path):
    state = tmp_path / "priority-overrides.json"
    priority_overrides.set_priority(
        "free-pool",
        "openai/example-mid",
        "https://example.test/v1",
        "EXAMPLE_KEY",
        777,
        path=state,
    )
    source = {
        "model_list": [
            {
                "model_name": "free-pool",
                "litellm_params": {
                    "model": "openai/example-mid",
                    "api_base": "https://example.test/v1/",
                    "api_key": "os.environ/EXAMPLE_KEY",
                    "priority": 40,
                },
            }
        ]
    }

    assert priority_overrides.apply_to_source(source, path=state) == 1
    assert source["model_list"][0]["litellm_params"]["priority"] == 777


def test_manual_priority_follows_model_rename(tmp_path):
    state = tmp_path / "priority-overrides.json"
    args = ("fast-pool", "openai/old-model", None, "EXAMPLE_KEY")
    priority_overrides.set_priority(*args, 321, path=state)

    priority_overrides.rename_model(
        "fast-pool",
        "openai/old-model",
        "openai/new-model",
        None,
        "EXAMPLE_KEY",
        path=state,
    )

    assert priority_overrides.get_priority(*args, path=state) is None
    assert priority_overrides.get_priority(
        "fast-pool", "openai/new-model", None, "EXAMPLE_KEY", path=state
    ) == 321
