import asyncio
import importlib
import json

import pytest


pytest.importorskip("fastapi")


async def _asgi_get(app, path: str, headers: dict[str, str] | None = None):
    """Drive the ASGI app directly.

    Starlette 1.x's legacy TestClient can deadlock with versions of its optional
    HTTP client dependencies even though the same application works under an
    ASGI server.  Authentication middleware does not need an HTTP client, so a
    direct request keeps this regression test deterministic.
    """
    incoming = [{"type": "http.request", "body": b"", "more_body": False}]
    outgoing: list[dict] = []

    async def receive():
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        outgoing.append(message)

    encoded_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": encoded_headers,
        "client": ("test", 123),
        "server": ("test", 80),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in outgoing if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in outgoing
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in start.get("headers", [])
    }
    return start["status"], response_headers, json.loads(body)


def test_dashboard_api_requires_separate_token(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "dash-test-secret")
    import dashboard.backend as backend
    importlib.reload(backend)

    health_status, _, _ = asyncio.run(_asgi_get(backend.app, "/healthz"))
    denied_status, _, _ = asyncio.run(_asgi_get(backend.app, "/api/auth/verify"))
    status, headers, body = asyncio.run(
        _asgi_get(
            backend.app,
            "/api/auth/verify",
            headers={"X-Dashboard-Token": "dash-test-secret"},
        )
    )
    assert health_status == 200
    assert denied_status == 401
    assert status == 200
    assert body == {"authenticated": True}
    assert "access-control-allow-origin" not in headers
