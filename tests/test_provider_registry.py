from pathlib import Path
import json

from gateway import provider_registry


ROOT = Path(__file__).resolve().parents[1]


def registry():
    return provider_registry.ProviderRegistry(
        ROOT / "config.yaml", ROOT / "provider_manifest.yaml"
    )


def test_all_primary_deployments_are_registered():
    assert len(registry().channels) == 43


def test_sensitive_pool_only_contains_explicitly_allowed_providers():
    reg = registry()
    trusted = [channel for channel in reg.channels.values() if channel["in_trusted_pool"]]
    assert trusted
    assert all(channel["sensitive_allowed"] for channel in trusted)
    assert all(channel["env_var"] not in {"GEMINI_API_KEY", "MISTRAL_KEY_1", "MISTRAL_KEY_2"} for channel in trusted)


def test_request_capabilities_are_detected_and_filter_candidates():
    reg = registry()
    data = {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
        ]}],
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        "response_format": {"type": "json_object"},
    }
    requirements = reg.request_requirements(data)
    assert requirements == {"text", "vision", "tools", "json_object"}
    candidates = reg.candidates("fast-pool", requirements)
    assert candidates
    assert all(all(channel["capabilities"][key] for key in requirements) for channel in candidates)


def test_security_text_includes_tool_arguments_but_not_base64_payloads():
    reg = registry()
    data = {"messages": [{
        "role": "assistant",
        "tool_calls": [{"function": {"arguments": '{"password":"hunter2"}'}}],
        "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRET"}}],
    }]}
    text = reg.security_text(data)
    assert "hunter2" in text
    assert "base64,SECRET" not in text


def test_security_text_covers_structured_keys_prompt_and_long_tail():
    reg = registry()
    secret = "sk-" + "A" * 24
    data = {
        "prompt": "x" * 100_001 + secret,
        "messages": [{"role": "user", "content": {"api_key": secret}}],
    }
    text = reg.security_text(data)
    assert secret in text
    assert "api_key" in text


def test_plain_text_response_format_does_not_require_json_schema():
    reg = registry()
    assert reg.request_requirements({"response_format": {"type": "text"}}) == {"text"}
    assert reg.request_requirements({"tool_choice": "none"}) == {"text"}


def test_model_missing_discovery_result_excludes_candidate(tmp_path: Path):
    reg = registry()
    candidate = reg.candidates("free-pool", {"text"})[0]
    report = tmp_path / "discovery.json"
    report.write_text(json.dumps({
        "results": {candidate["display_id"]: {"status": "model_missing"}}
    }), encoding="utf-8")
    reg.discovery_path = report
    remaining = reg.candidates("free-pool", {"text"})
    assert candidate["display_id"] not in {item["display_id"] for item in remaining}
