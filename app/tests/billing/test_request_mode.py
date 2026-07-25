import pytest

from dashboard.app.services.request_mode import (
    InvalidModeError,
    extract_tracking_headers,
    resolve_mode,
    should_force_non_stream,
)


def test_header_wins():
    r = resolve_mode(
        headers={"X-PrivateAPI-Mode": "strict"},
        metadata={"mode": "agent-stream"},
        api_key_default="agent-stream",
        system_default="agent-stream",
    )
    assert r.mode == "strict"
    assert r.source == "header"


def test_metadata_then_key_then_default():
    r = resolve_mode(metadata={"privateapi_mode": "strict"})
    assert r.mode == "strict" and r.source == "metadata"
    r2 = resolve_mode(api_key_default="strict")
    assert r2.mode == "strict" and r2.source == "api_key"
    r3 = resolve_mode(system_default="agent-stream")
    assert r3.mode == "agent-stream" and r3.source == "system_default"


def test_invalid_mode():
    with pytest.raises(InvalidModeError):
        resolve_mode(headers={"X-PrivateAPI-Mode": "turbo"})


def test_force_non_stream():
    assert should_force_non_stream("strict", True) is True
    assert should_force_non_stream("agent-stream", True) is False
    assert should_force_non_stream("agent-stream", False) is False


def test_tracking_headers():
    h = extract_tracking_headers(
        {
            "X-PrivateAPI-Task-ID": "t1",
            "X-PrivateAPI-Client": "cline",
            "X-PrivateAPI-Mode": "agent-stream",
        }
    )
    assert h["task_id"] == "t1"
    assert h["client_name"] == "cline"
