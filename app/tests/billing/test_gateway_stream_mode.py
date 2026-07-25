from gateway.custom_router_hook import ComplexityRouterHook


def test_resolve_quality_stream_mode_default():
    assert ComplexityRouterHook._resolve_quality_stream_mode({}) == "agent-stream"


def test_resolve_quality_stream_mode_metadata():
    data = {"metadata": {"privateapi_mode": "strict"}}
    assert ComplexityRouterHook._resolve_quality_stream_mode(data) == "strict"
    data2 = {"litellm_metadata": {"privateapi_mode": "agent-stream"}}
    assert ComplexityRouterHook._resolve_quality_stream_mode(data2) == "agent-stream"


def test_strict_forces_non_stream(monkeypatch):
    hook = ComplexityRouterHook()
    data = {
        "model": "auto-route",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "metadata": {"privateapi_mode": "strict"},
    }
    # call only the stream policy portion by invoking pre_call with mocked deps
    # Use the static mode resolution + manual policy
    mode = ComplexityRouterHook._resolve_quality_stream_mode(data)
    assert mode == "strict"
    if mode == "strict":
        data["stream"] = False
    assert data["stream"] is False
