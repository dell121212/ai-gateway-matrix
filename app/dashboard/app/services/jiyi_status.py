"""Small, non-secret control surface for the existing jiyi-sync service."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATABASE_SNAPSHOT = "private-api-export.json.gz"
REDIS_SNAPSHOT = "gateway-redis-export.json.gz"
SYNC_REQUEST = "jiyi-save-request.json"
CLIENT_KEYS = "client-keys.json"


def _file_status(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {
            "exists": False,
            "size_bytes": 0,
            "updated_at": None,
        }
    return {
        "exists": path.is_file(),
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(
            stat.st_mtime,
            timezone.utc,
        ).isoformat(),
    }


def _client_key_status(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "exists": path.is_file(),
            "version": 0,
            "count": 0,
            "portable_count": 0,
            "complete": False,
        }
    rows = payload.get("keys") if isinstance(payload, dict) else []
    active = [
        row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and not row.get("revoked")
    ]
    portable = sum(bool(row.get("key")) for row in active)
    return {
        "exists": True,
        "version": int(payload.get("version") or 0),
        "count": len(active),
        "portable_count": portable,
        "complete": len(active) == portable and bool(payload.get("portable_secrets", True)),
    }


def read_jiyi_status(state_dir: Path) -> dict[str, Any]:
    """Report logical snapshots without exposing jiyi contents or API keys."""

    database = _file_status(state_dir / DATABASE_SNAPSHOT)
    redis = _file_status(state_dir / REDIS_SNAPSHOT)
    client_keys = _client_key_status(state_dir / CLIENT_KEYS)
    timestamps = [
        value
        for value in (database["updated_at"], redis["updated_at"])
        if value is not None
    ]
    return {
        "enabled": state_dir.is_dir(),
        "mode": "automatic",
        "database_snapshot": database,
        "redis_snapshot": redis,
        "client_keys": client_keys,
        "last_synced_at": max(timestamps) if timestamps else None,
    }


def request_jiyi_sync(state_dir: Path, *, actor: str) -> dict[str, Any]:
    """Wake the existing watcher by atomically changing a tracked state file."""

    state_dir.mkdir(parents=True, exist_ok=True)
    requested_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "requested_at": requested_at,
        "actor": actor[:128],
        "reason": "desktop-request",
        "pid": os.getpid(),
    }
    marker = state_dir / SYNC_REQUEST
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(marker)
    return {
        "accepted": True,
        "requested_at": requested_at,
        "message": "jiyi-sync 已收到同步请求",
    }
