#!/usr/bin/env python3
"""定期对 OpenAI-compatible 上游的 /models 做只读存在性审计。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

from gateway import channel_ids, env_file
from gateway.provider_registry import PRIMARY_POOLS, parse_env_ref

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(os.environ.get("PROVIDER_DISCOVERY_OUTPUT", ROOT / "state/provider-discovery.json"))


def load_env(path: Path) -> None:
    for key, value in env_file.read_env(path).items():
        os.environ.setdefault(key, value)


def _upstream_model_name(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


NATIVE_CATALOGS = {
    "groq": ("https://api.groq.com/openai/v1", "bearer"),
    "cerebras": ("https://api.cerebras.ai/v1", "bearer"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", "google"),
    "mistral": ("https://api.mistral.ai/v1", "bearer"),
    "openrouter": ("https://openrouter.ai/api/v1", "bearer"),
    "deepseek": ("https://api.deepseek.com/v1", "bearer"),
    "together_ai": ("https://api.together.xyz/v1", "bearer"),
    "sambanova": ("https://api.sambanova.ai/v1", "bearer"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "bearer"),
}


def _catalog_for(model: str, api_base: Any) -> tuple[Optional[str], str]:
    if isinstance(api_base, str) and api_base:
        return api_base, "bearer"
    provider = model.split("/", 1)[0] if "/" in model else ""
    return NATIVE_CATALOGS.get(provider, (None, "bearer"))


def audit(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for item in config.get("model_list", []):
        if item.get("model_name") not in PRIMARY_POOLS:
            continue
        params = item.get("litellm_params") or {}
        configured_api_base = params.get("api_base")
        api_base, auth_style = _catalog_for(
            str(params.get("model") or ""), configured_api_base
        )
        env_var = parse_env_ref(params.get("api_key"))
        model = params.get("model")
        if not (api_base and env_var and model):
            continue
        display_id = channel_ids.make_display_id(model, configured_api_base, env_var)
        deployment = {
            "display_id": display_id,
            "model": model,
            "upstream_model": _upstream_model_name(model),
            "api_base": api_base,
            "env_var": env_var,
        }
        groups.setdefault((api_base, env_var, auth_style), []).append(deployment)

    def probe_group(group):
        (api_base, env_var, auth_style), members = group
        group_results: dict[str, dict[str, Any]] = {}
        api_key = os.environ.get(env_var, "").strip()
        if not api_key or api_key.startswith("dummy-"):
            for member in members:
                group_results[member["display_id"]] = {"status": "not_configured"}
            return group_results
        url = api_base.rstrip("/") + "/models"
        try:
            headers = {"Accept": "application/json"}
            if auth_style == "google":
                headers["x-goog-api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                status = "auth_error" if response.status_code in (401, 403) else "endpoint_error"
                for member in members:
                    group_results[member["display_id"]] = {
                        "status": status,
                        "http_status": response.status_code,
                    }
                return group_results
            payload = response.json()
            entries = payload.get("data", payload if isinstance(payload, list) else [])
            available = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                identifier = entry.get("id") or entry.get("name")
                if identifier:
                    identifier = str(identifier)
                    available.add(identifier.split("/", 1)[1] if identifier.startswith("models/") else identifier)
            for member in members:
                group_results[member["display_id"]] = {
                    "status": "available" if member["upstream_model"] in available else "model_missing",
                    "catalog_size": len(available),
                }
        except (requests.RequestException, ValueError) as exc:
            for member in members:
                group_results[member["display_id"]] = {
                    "status": "probe_error",
                    "error_type": type(exc).__name__,
                }
        return group_results

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(groups)))) as executor:
        futures = [executor.submit(probe_group, group) for group in groups.items()]
        for future in as_completed(futures):
            results.update(future.result())

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "note": "覆盖显式 OpenAI-compatible URL 及已知原生 Provider 目录；无标准目录的渠道依赖被动熔断。",
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="上游模型目录只读审计")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args()
    if args.interval < 300:
        parser.error("--interval 不能小于 300 秒，避免频繁请求上游目录")
    load_env(args.env)
    while True:
        report = audit(args.config)
        write_report(args.output, report)
        print(f"[{report['checked_at']}] 上游模型目录审计完成，共 {len(report['results'])} 个 deployment", flush=True)
        if not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
