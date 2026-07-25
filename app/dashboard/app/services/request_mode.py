"""Resolve strict vs agent-stream mode from headers / key / defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

ALLOWED_MODES = frozenset({"strict", "agent-stream"})


class InvalidModeError(ValueError):
    pass


@dataclass(frozen=True)
class ModeResolution:
    mode: str
    source: str  # header | metadata | api_key | system_default


def resolve_mode(
    headers: Optional[Mapping[str, str]] = None,
    metadata: Optional[Mapping[str, object]] = None,
    api_key_default: Optional[str] = None,
    system_default: str = "agent-stream",
) -> ModeResolution:
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    raw = headers.get("x-privateapi-mode") or headers.get("x-private-api-mode")
    if raw:
        mode = raw.strip().lower()
        if mode not in ALLOWED_MODES:
            raise InvalidModeError(f"非法模式: {raw}，允许: strict, agent-stream")
        return ModeResolution(mode=mode, source="header")

    if metadata:
        m = metadata.get("privateapi_mode") or metadata.get("mode")
        if m is not None:
            mode = str(m).strip().lower()
            if mode not in ALLOWED_MODES:
                raise InvalidModeError(f"非法模式: {m}")
            return ModeResolution(mode=mode, source="metadata")

    if api_key_default:
        mode = str(api_key_default).strip().lower()
        if mode in ALLOWED_MODES:
            return ModeResolution(mode=mode, source="api_key")

    mode = (system_default or "agent-stream").strip().lower()
    if mode not in ALLOWED_MODES:
        mode = "agent-stream"
    return ModeResolution(mode=mode, source="system_default")


def should_force_non_stream(mode: str, client_wants_stream: bool) -> bool:
    """strict always non-stream; agent-stream honors client stream flag."""
    if mode == "strict":
        return True
    return False


def extract_tracking_headers(headers: Mapping[str, str]) -> dict[str, Optional[str]]:
    h = {str(k).lower(): str(v).strip() for k, v in headers.items()}
    return {
        "task_id": h.get("x-privateapi-task-id") or h.get("x-private-api-task-id"),
        "session_id": h.get("x-privateapi-session-id") or h.get("x-private-api-session-id"),
        "client_name": h.get("x-privateapi-client") or h.get("x-private-api-client"),
        "workspace_id": h.get("x-privateapi-workspace-id") or h.get("x-private-api-workspace-id"),
        "run_id": h.get("x-privateapi-run-id") or h.get("x-private-api-run-id"),
        "mode": h.get("x-privateapi-mode") or h.get("x-private-api-mode"),
    }
