#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机客户端 Key 登记簿：关闭浏览器后仍可列出/再次复制（个人网关场景）。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .safe_files import locked_file, safe_rewrite

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = Path(os.environ.get(
    "CLIENT_KEYS_STORE",
    str(_PROJECT_ROOT / "state" / "client-keys.json"),
))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"version": 3, "portable_secrets": True, "keys": []}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 3, "portable_secrets": True, "keys": []}
        data.setdefault("version", 3)
        data.setdefault("portable_secrets", True)
        data.setdefault("keys", [])
        if not isinstance(data["keys"], list):
            data["keys"] = []
        return data
    except (OSError, ValueError):
        return {"version": 3, "portable_secrets": True, "keys": []}


def _save(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with locked_file(STORE_PATH):
        safe_rewrite(
            STORE_PATH,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )


def mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= 10:
        return "sk-…"
    return f"{key[:6]}…{key[-4:]}"


def remember_key(
    *,
    full_key: str,
    alias: str,
    models: list[str],
    rpm_limit: int,
    tpm_limit: int,
    expires_in: str,
    token_hash: Optional[str] = None,
) -> dict[str, Any]:
    data = _load()
    entry = {
        "id": token_hash or full_key[-16:],
        "alias": alias,
        "key": full_key,  # 个人本机网关：允许再次复制；文件权限 0600
        "key_preview": mask_key(full_key),
        "models": models,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "expires_in": expires_in,
        "created_at": _now(),
        "revoked": False,
    }
    # 去重：同 preview+alias 更新
    keys = [k for k in data["keys"] if k.get("key") != full_key and k.get("id") != entry["id"]]
    keys.insert(0, entry)
    data["keys"] = keys[:100]
    data["version"] = 3
    data["portable_secrets"] = True
    _save(data)
    return entry


def list_local_keys(*, include_secret: bool = False) -> list[dict[str, Any]]:
    data = _load()
    out = []
    for item in data.get("keys") or []:
        if item.get("revoked"):
            continue
        row = {
            "id": item.get("id"),
            "alias": item.get("alias"),
            "key_preview": item.get("key_preview") or mask_key(item.get("key") or ""),
            "models": item.get("models") or ["auto-route"],
            "rpm_limit": item.get("rpm_limit"),
            "tpm_limit": item.get("tpm_limit"),
            "expires_in": item.get("expires_in"),
            "created_at": item.get("created_at"),
            "source": "local_store",
        }
        if include_secret:
            row["key"] = item.get("key") or ""
        out.append(row)
    return out


def reveal_key(key_id: str) -> Optional[str]:
    """按 id 或 alias 取回完整密钥（只读项目登记簿，不依赖浏览器）。"""
    if not key_id:
        return None
    data = _load()
    for item in data.get("keys") or []:
        if item.get("revoked"):
            continue
        if item.get("id") == key_id or item.get("alias") == key_id:
            secret = item.get("key") or None
            if secret:
                return secret
    return None


def store_path() -> str:
    return str(STORE_PATH)


def update_key_meta(key_id: str, **fields: Any) -> bool:
    """更新登记簿中的 rpm/tpm 等元数据。"""
    if not key_id:
        return False
    data = _load()
    found = False
    allowed = {"rpm_limit", "tpm_limit", "models", "alias"}
    for item in data.get("keys") or []:
        if item.get("id") == key_id or item.get("alias") == key_id:
            for k, v in fields.items():
                if k in allowed:
                    item[k] = v
            found = True
            break
    if found:
        _save(data)
    return found


def revoke_local(key_id: str) -> bool:
    """按 id 或 alias 吊销本地登记；密文清空，下次列表不再展示。"""
    if not key_id:
        return False
    data = _load()
    found = False
    for item in data.get("keys") or []:
        if item.get("id") == key_id or item.get("alias") == key_id:
            item["revoked"] = True
            item["key"] = ""
            found = True
    if found:
        # 直接从列表移除，避免堆积作废记录
        data["keys"] = [k for k in data["keys"] if not k.get("revoked")]
        _save(data)
    return found


def is_safe_alias(name: str) -> bool:
    return bool(name) and len(name) <= 80 and re.fullmatch(r"[\w\-.\u4e00-\u9fff ]+", name)
