#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公司/渠道最近一次连通性探测结果（本机持久化，供顶栏「正常连接/已配置」）。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from .safe_files import locked_file, safe_rewrite

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = Path(
    os.environ.get(
        "CONNECTION_STATUS_STORE",
        str(_PROJECT_ROOT / "state" / "connection-status.json"),
    )
)


def _load() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"version": 1, "by_company": {}, "by_channel": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "by_company": {}, "by_channel": {}}
        data.setdefault("version", 1)
        data.setdefault("by_company", {})
        data.setdefault("by_channel", {})
        return data
    except (OSError, ValueError):
        return {"version": 1, "by_company": {}, "by_channel": {}}


def _save(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with locked_file(STORE_PATH):
        safe_rewrite(
            STORE_PATH,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )


def record(
    *,
    company_id: str,
    channel_id: str = "",
    env_var: str = "",
    ok: bool,
    message: str = "",
    latency_ms: Optional[float] = None,
) -> None:
    if not company_id and not env_var:
        return
    cid = company_id or env_var
    data = _load()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {
        "ok": bool(ok),
        "checked_at": now,
        "message": (message or "")[:240],
        "latency_ms": latency_ms,
        "channel_id": channel_id or "",
        "env_var": env_var or "",
    }
    # 公司级：只要有一次成功就记 ok；失败仅在尚无成功记录时覆盖，
    # 或总是更新最近一次探测（用户要实时状态 → 最近一次为准）
    data["by_company"][cid] = entry
    if channel_id:
        data["by_channel"][channel_id] = entry
    if env_var:
        # 同一公司的每个账号必须保留自己的最近探测结果，不能只写入第一个账号。
        data["by_company"][env_var] = entry
    _save(data)


def get_company(company_id: str) -> Optional[dict[str, Any]]:
    if not company_id:
        return None
    data = _load()
    hit = (data.get("by_company") or {}).get(company_id)
    return hit if isinstance(hit, dict) else None


def get_all_companies() -> dict[str, dict[str, Any]]:
    data = _load()
    raw = data.get("by_company") or {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}
