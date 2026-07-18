#!/usr/bin/env python3
"""Persist dashboard-set routing priorities outside the editable model catalog.

``config.yaml`` is also maintained by provider/model discovery.  A priority that
the user chose in the dashboard must therefore have an independent source of
truth, otherwise a catalog refresh can silently restore the catalog default.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional


_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = Path(
    os.environ.get(
        "PRIORITY_OVERRIDES_STATE",
        str(_ROOT / "state" / "priority-overrides.json"),
    )
)
_LOCK = threading.RLock()
_POOLS = {"fast-pool", "free-pool", "strong-model-pool", "elite-model-pool"}


def _identity(
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
) -> str:
    raw = json.dumps(
        [pool or "", model or "", (api_base or "").rstrip("/"), env_var or ""],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("overrides"), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"version": 1, "overrides": {}}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def get_priority(
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    *,
    path: Path = STATE_PATH,
) -> Optional[int]:
    with _LOCK:
        entry = _read(path)["overrides"].get(
            _identity(pool, model, api_base, env_var)
        )
    value = entry.get("priority") if isinstance(entry, dict) else None
    return value if isinstance(value, int) else None


def set_priority(
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    priority: int,
    *,
    path: Path = STATE_PATH,
) -> None:
    if pool not in _POOLS:
        raise ValueError(f"不支持的模型池: {pool}")
    if not isinstance(priority, int) or not 0 <= priority <= 1000:
        raise ValueError("priority 必须是 0–1000 的整数")
    key = _identity(pool, model, api_base, env_var)
    with _LOCK:
        data = _read(path)
        data["overrides"][key] = {
            "pool": pool,
            "model": model,
            "api_base": api_base,
            "env_var": env_var,
            "priority": priority,
            "updated_at": int(time.time()),
        }
        _write(path, data)


def rename_model(
    pool: str,
    old_model: str,
    new_model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    *,
    path: Path = STATE_PATH,
) -> None:
    """Carry a manual priority across a model-ID correction."""
    old_key = _identity(pool, old_model, api_base, env_var)
    new_key = _identity(pool, new_model, api_base, env_var)
    with _LOCK:
        data = _read(path)
        entry = data["overrides"].pop(old_key, None)
        if not isinstance(entry, dict):
            return
        entry["model"] = new_model
        entry["updated_at"] = int(time.time())
        data["overrides"][new_key] = entry
        _write(path, data)


def apply_to_source(source: dict[str, Any], *, path: Path = STATE_PATH) -> int:
    """Overlay saved manual priorities on a parsed source config in place."""
    changed = 0
    with _LOCK:
        overrides = _read(path)["overrides"]
    for item in source.get("model_list") or []:
        pool = item.get("model_name")
        if pool not in _POOLS:
            continue
        params = item.get("litellm_params") or {}
        key_ref = params.get("api_key")
        env_var = (
            key_ref.split("/", 1)[1]
            if isinstance(key_ref, str) and key_ref.startswith("os.environ/")
            else None
        )
        key = _identity(pool, params.get("model", ""), params.get("api_base"), env_var)
        entry = overrides.get(key)
        value = entry.get("priority") if isinstance(entry, dict) else None
        if isinstance(value, int) and params.get("priority") != value:
            params["priority"] = value
            changed += 1
    return changed
