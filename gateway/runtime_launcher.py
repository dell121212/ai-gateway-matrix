#!/usr/bin/env python3
"""Build a LiteLLM config containing only usable credentials, then exec LiteLLM.

The source config remains the editable catalog shown by the dashboard.  LiteLLM,
however, must not receive deployments whose ``os.environ/KEY`` is empty: its
Router considers those deployments selectable and only discovers the missing
credential after a request has already been routed to them.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
from typing import Any

import yaml


SOURCE_CONFIG = Path(os.environ.get("SOURCE_GATEWAY_CONFIG_PATH", "/app/config.yaml"))
# LiteLLM resolves custom callbacks relative to the config directory. Keep the
# generated file beside /app/gateway; placing it in /tmp would make LiteLLM look
# for the hook below /tmp/gateway instead.
RUNTIME_CONFIG = Path(os.environ.get("GATEWAY_CONFIG_PATH", "/app/runtime-config.yaml"))


def _env_reference(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("os.environ/"):
        return value.split("/", 1)[1]
    return None


def _deployment_is_configured(item: dict[str, Any], environment: dict[str, str]) -> bool:
    params = item.get("litellm_params") or {}
    api_key = params.get("api_key")
    env_var = _env_reference(api_key)
    if env_var:
        value = environment.get(env_var, "").strip()
        return bool(value) and not value.startswith("dummy-")
    # Keyless local endpoints and explicitly configured literal credentials are
    # valid LiteLLM configurations.  Literal secrets are discouraged but must
    # not be silently removed by this launcher.
    return api_key is None or bool(str(api_key).strip())


def build_runtime_config(
    source: dict[str, Any], environment: dict[str, str]
) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a deep-copied, filtered config and build statistics.

    ``priority`` is the dashboard's human-facing convention (larger is better).
    LiteLLM's supported deployment field is ``order`` (smaller is better), so
    the runtime-only config translates one into the other without rewriting the
    user's source configuration.
    """
    runtime = copy.deepcopy(source)
    source_models = source.get("model_list") or []
    kept: list[dict[str, Any]] = []
    configured_primary = 0

    for original in source_models:
        if not isinstance(original, dict):
            continue
        model_name = str(original.get("model_name") or "")
        # auto-route must remain visible to clients.  The pre-call hook rewrites
        # it before an upstream request is made and emits a clear error if no
        # configured target exists.
        if model_name != "auto-route" and not _deployment_is_configured(original, environment):
            continue

        item = copy.deepcopy(original)
        params = item.get("litellm_params") or {}
        priority = params.get("priority")
        if isinstance(priority, int):
            params["order"] = 1000 - priority
            item["litellm_params"] = params
        kept.append(item)
        if model_name in {"fast-pool", "free-pool", "strong-model-pool"}:
            configured_primary += 1

    runtime["model_list"] = kept
    return runtime, {
        "source": len(source_models),
        "runtime": len(kept),
        "configured_primary": configured_primary,
    }


def write_runtime_config(source_path: Path, output_path: Path) -> dict[str, int]:
    with source_path.open(encoding="utf-8") as handle:
        source = yaml.safe_load(handle)
    if not isinstance(source, dict) or not isinstance(source.get("model_list"), list):
        raise ValueError(f"{source_path} 缺少 model_list")

    runtime, stats = build_runtime_config(source, dict(os.environ))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("# Auto-generated at container startup; edit /app/config.yaml instead.\n")
        yaml.safe_dump(runtime, handle, allow_unicode=True, sort_keys=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(output_path, 0o600)
    return stats


def main() -> int:
    stats = write_runtime_config(SOURCE_CONFIG, RUNTIME_CONFIG)
    print(
        "运行时配置已生成: "
        f"主渠道 {stats['configured_primary']}，"
        f"deployment {stats['runtime']}/{stats['source']}",
        flush=True,
    )
    argv = ["litellm", "--config", str(RUNTIME_CONFIG), *sys.argv[1:]]
    os.execvp(argv[0], argv)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
