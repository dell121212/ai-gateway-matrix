#!/usr/bin/env python3
"""通过 LiteLLM 管理 API 创建可限额、限模型、限 IP 的客户端虚拟 Key。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests


def load_env() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="创建 LiteLLM 虚拟客户端 Key")
    parser.add_argument("--name", required=True, help="Key 别名，例如 codex-laptop")
    parser.add_argument("--models", nargs="+", default=["auto-route"])
    parser.add_argument("--rpm", type=int, default=30)
    parser.add_argument("--tpm", type=int, default=100000)
    parser.add_argument("--duration", default="30d", help="例如 24h / 30d")
    parser.add_argument("--max-budget", type=float, default=None)
    parser.add_argument("--allowed-ip", action="append", default=[])
    args = parser.parse_args()
    if args.rpm <= 0 or args.tpm <= 0:
        parser.error("--rpm/--tpm 必须大于 0")
    if args.max_budget is not None and args.max_budget < 0:
        parser.error("--max-budget 不能小于 0")

    master_key = os.environ.get("GATEWAY_MASTER_KEY", "")
    base_url = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000").rstrip("/")
    if not master_key:
        parser.error(".env 中缺少 GATEWAY_MASTER_KEY")
    payload = {
        "key_alias": args.name,
        "models": args.models,
        "rpm_limit": args.rpm,
        "tpm_limit": args.tpm,
        "duration": args.duration,
    }
    if args.max_budget is not None:
        payload["max_budget"] = args.max_budget
    if args.allowed_ip:
        payload["allowed_ips"] = args.allowed_ip

    response = requests.post(
        f"{base_url}/key/generate",
        headers={"Authorization": f"Bearer {master_key}"},
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        print(f"创建失败: HTTP {response.status_code} {response.text[:300]}")
        return 1
    body = response.json()
    key = body.get("key")
    if not key:
        print("创建失败: 响应中没有 key")
        return 1
    print("已创建虚拟 Key（只显示这一次，请立即保存）:")
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
