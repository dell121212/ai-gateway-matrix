#!/usr/bin/env python3
"""用户指定的渠道档位（弱/中/强/顶级），独立于 config 目录默认值。

档位由 config.yaml 的 model_name 池决定；目录刷新可能把默认写回去，
因此把用户选择另存到 state/tier-overrides.json，并在运行时覆盖。
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
        "TIER_OVERRIDES_STATE",
        str(_ROOT / "state" / "tier-overrides.json"),
    )
)
_LOCK = threading.RLock()

POOLS = {
    "fast-pool",
    "free-pool",
    "strong-model-pool",
    "elite-model-pool",
}
LABEL_TO_POOL = {
    "弱": "fast-pool",
    "中": "free-pool",
    "强": "strong-model-pool",
    "顶级": "elite-model-pool",
    "weak": "fast-pool",
    "mid": "free-pool",
    "medium": "free-pool",
    "strong": "strong-model-pool",
    "elite": "elite-model-pool",
    "fast-pool": "fast-pool",
    "free-pool": "free-pool",
    "strong-model-pool": "strong-model-pool",
    "elite-model-pool": "elite-model-pool",
}
POOL_TO_LABEL = {
    "fast-pool": "弱",
    "free-pool": "中",
    "strong-model-pool": "强",
    "elite-model-pool": "顶级",
}


def normalize_pool(value: str) -> str:
    key = (value or "").strip()
    pool = LABEL_TO_POOL.get(key) or LABEL_TO_POOL.get(key.lower())
    if not pool:
        raise ValueError("档位必须是 弱/中/强/顶级（或对应 pool 名）")
    return pool


def _identity(model: str, api_base: Optional[str], env_var: Optional[str]) -> str:
    raw = json.dumps(
        [model or "", (api_base or "").rstrip("/"), env_var or ""],
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


def get_pool(
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    *,
    path: Path = STATE_PATH,
) -> Optional[str]:
    with _LOCK:
        entry = _read(path)["overrides"].get(_identity(model, api_base, env_var))
    pool = entry.get("pool") if isinstance(entry, dict) else None
    return pool if pool in POOLS else None


def set_pool(
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    pool: str,
    *,
    path: Path = STATE_PATH,
) -> str:
    pool = normalize_pool(pool)
    key = _identity(model, api_base, env_var)
    with _LOCK:
        data = _read(path)
        data["overrides"][key] = {
            "model": model,
            "api_base": api_base,
            "env_var": env_var,
            "pool": pool,
            "updated_at": int(time.time()),
        }
        _write(path, data)
    return pool


def rename_model(
    old_model: str,
    new_model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    *,
    path: Path = STATE_PATH,
) -> None:
    old_key = _identity(old_model, api_base, env_var)
    new_key = _identity(new_model, api_base, env_var)
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
    """把用户档位覆盖写到 parsed config 的 model_name 上。"""
    changed = 0
    with _LOCK:
        overrides = _read(path)["overrides"]
    if not overrides:
        return 0
    for item in source.get("model_list") or []:
        pool = item.get("model_name")
        if pool not in POOLS:
            continue
        params = item.get("litellm_params") or {}
        key_ref = params.get("api_key")
        env_var = (
            key_ref.split("/", 1)[1]
            if isinstance(key_ref, str) and key_ref.startswith("os.environ/")
            else None
        )
        key = _identity(str(params.get("model") or ""), params.get("api_base"), env_var)
        entry = overrides.get(key)
        target = entry.get("pool") if isinstance(entry, dict) else None
        if target in POOLS and target != pool:
            item["model_name"] = target
            changed += 1
    return changed
