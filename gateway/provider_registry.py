#!/usr/bin/env python3
"""将 LiteLLM deployment 与机器可读的供应商政策合并成运行时注册表。"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from . import channel_ids

PRIMARY_POOLS = ("fast-pool", "free-pool", "strong-model-pool", "elite-model-pool")
CAPABILITY_KEYS = (
    "text", "vision", "tools", "json_object", "json_schema", "audio",
)


def default_config_path() -> Path:
    """Return the complete catalog used by the routing hook.

    Docker's ``GATEWAY_CONFIG_PATH`` points at a credential-filtered runtime
    file.  Keys added later through the dashboard can hot-reload LiteLLM's
    Router, but they will never appear in a registry cached from that filtered
    file.  Prefer the unfiltered source catalog and retain the runtime path only
    as a compatibility fallback for deployments without runtime_launcher.
    """
    for env_name in (
        "PROVIDER_REGISTRY_CONFIG_PATH",
        "SOURCE_GATEWAY_CONFIG_PATH",
        "GATEWAY_CONFIG_PATH",
    ):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return Path(value)
    return Path("config.yaml")


def parse_env_ref(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.startswith("os.environ/"):
        return value.split("/", 1)[1]
    return None


def _merge_capabilities(base: dict[str, bool], extra: Any) -> dict[str, bool]:
    result = dict(base)
    if isinstance(extra, dict):
        for key in CAPABILITY_KEYS:
            if key in extra:
                result[key] = bool(extra[key])
    return result


class ProviderRegistry:
    def __init__(self, config_path: Path, manifest_path: Path):
        self.config_path = config_path
        self.manifest_path = manifest_path
        self.manifest = self._load_yaml(manifest_path)
        self.config = self._load_yaml(config_path)
        self.channels: dict[str, dict[str, Any]] = {}
        self.discovery_path = Path(
            os.environ.get("PROVIDER_DISCOVERY_OUTPUT", "state/provider-discovery.json")
        )
        self._discovery_mtime_ns: Optional[int] = None
        self._discovery_results: dict[str, dict[str, Any]] = {}
        self._load_channels()

    def _refresh_discovery(self) -> None:
        """按 mtime 热加载目录审计结果；报告不存在时不影响 provider-native 渠道。"""
        try:
            stat = self.discovery_path.stat()
            if stat.st_mtime_ns == self._discovery_mtime_ns:
                return
            payload = json.loads(self.discovery_path.read_text(encoding="utf-8"))
            results = payload.get("results", {}) if isinstance(payload, dict) else {}
            self._discovery_results = results if isinstance(results, dict) else {}
            self._discovery_mtime_ns = stat.st_mtime_ns
        except (OSError, ValueError):
            self._discovery_results = {}
            self._discovery_mtime_ns = None

    def _blocked_by_discovery(self, display_id: str) -> bool:
        self._refresh_discovery()
        status = (self._discovery_results.get(display_id) or {}).get("status")
        return status in {"model_missing", "auth_error"}

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        with path.open(encoding="utf-8") as f:
            value = yaml.safe_load(f)
        if not isinstance(value, dict):
            raise ValueError(f"{path} 顶层必须是 mapping")
        return value

    def _provider_policy(self, env_var: Optional[str]) -> dict[str, Any]:
        defaults = self.manifest.get("defaults") or {}
        provider = (self.manifest.get("providers") or {}).get(env_var or "", {}) or {}
        capabilities = _merge_capabilities(
            {key: bool((defaults.get("capabilities") or {}).get(key, False)) for key in CAPABILITY_KEYS},
            provider.get("capabilities"),
        )
        return {
            "trust": provider.get("trust", "unknown"),
            "billing": provider.get("billing", "free_or_trial"),
            "sensitive_allowed": bool(provider.get("sensitive_allowed", defaults.get("sensitive_allowed", False))),
            "data_policy": provider.get("data_policy", defaults.get("data_policy", "unverified")),
            "capabilities": capabilities,
        }

    def _model_capabilities(self, model: str, initial: dict[str, bool]) -> dict[str, bool]:
        capabilities = dict(initial)
        for rule in self.manifest.get("model_rules") or []:
            if isinstance(rule, dict) and fnmatch.fnmatchcase(model, str(rule.get("match", ""))):
                capabilities = _merge_capabilities(capabilities, rule.get("capabilities"))
        return capabilities

    def _load_channels(self) -> None:
        model_list = self.config.get("model_list") or []
        trusted_fingerprints = set()
        for item in model_list:
            if item.get("model_name") != "trusted-pool":
                continue
            params = item.get("litellm_params") or {}
            trusted_fingerprints.add(
                (params.get("model"), params.get("api_base"), parse_env_ref(params.get("api_key")))
            )

        credential_rpm: dict[str, int] = {}
        for item in model_list:
            if item.get("model_name") not in PRIMARY_POOLS:
                continue
            params = item.get("litellm_params") or {}
            env_var = parse_env_ref(params.get("api_key"))
            rpm = params.get("rpm")
            if env_var and isinstance(rpm, int) and rpm > 0:
                credential_rpm[env_var] = max(credential_rpm.get(env_var, 0), rpm)

        for item in model_list:
            pool = item.get("model_name")
            if pool not in PRIMARY_POOLS:
                continue
            params = item.get("litellm_params") or {}
            model = params.get("model")
            if not isinstance(model, str) or not model:
                continue
            api_base = params.get("api_base")
            env_var = parse_env_ref(params.get("api_key"))
            display_id = channel_ids.make_display_id(model, api_base, env_var)
            policy = self._provider_policy(env_var)
            policy["capabilities"] = self._model_capabilities(model, policy["capabilities"])
            fingerprint = (model, api_base, env_var)
            self.channels[display_id] = {
                "display_id": display_id,
                "pool": pool,
                "model": model,
                "api_base": api_base,
                "env_var": env_var,
                "direct_model_name": channel_ids.make_direct_model_name(model, api_base, env_var),
                "priority": int(params.get("priority") or 0),
                "rpm_limit": params.get("rpm"),
                "credential_rpm_limit": credential_rpm.get(env_var or ""),
                "additional_limits": (
                    (self.manifest.get("limits") or {}).get(env_var or "", []) or []
                ),
                "max_input_tokens": params.get("max_input_tokens"),
                "in_trusted_pool": fingerprint in trusted_fingerprints,
                **policy,
            }

    def candidates(
        self,
        pool: str,
        requirements: set[str],
        *,
        sensitive: bool = False,
    ) -> list[dict[str, Any]]:
        result = []
        for channel in self.channels.values():
            if channel["pool"] != pool:
                continue
            if self._blocked_by_discovery(channel["display_id"]):
                continue
            if sensitive and not (channel["in_trusted_pool"] and channel["sensitive_allowed"]):
                continue
            capabilities = channel["capabilities"]
            if any(not capabilities.get(requirement, False) for requirement in requirements):
                continue
            result.append(channel)
        return sorted(result, key=lambda item: item["priority"], reverse=True)

    def sensitive_candidates(self, requirements: set[str]) -> list[dict[str, Any]]:
        result = []
        for channel in self.channels.values():
            if not (channel["in_trusted_pool"] and channel["sensitive_allowed"]):
                continue
            if self._blocked_by_discovery(channel["display_id"]):
                continue
            if any(not channel["capabilities"].get(requirement, False) for requirement in requirements):
                continue
            result.append(channel)
        return sorted(result, key=lambda item: item["priority"], reverse=True)

    @staticmethod
    def request_requirements(data: dict) -> set[str]:
        requirements = {"text"}
        tool_choice = data.get("tool_choice")
        if data.get("tools") or data.get("functions") or (
            tool_choice and tool_choice != "none"
        ):
            requirements.add("tools")
        response_format = data.get("response_format")
        if isinstance(response_format, dict):
            response_type = response_format.get("type")
            # json_object 只要求模型输出合法 JSON，调用方仍可本地校验；把它
            # 当硬能力会让 hook 提前改写成单个 direct-*，从而丢失 Router
            # 的池内重试与跨池 fallback。json_schema 有严格结构约束，仍需
            # 精确选择声明支持它的渠道。
            if response_type == "json_schema":
                requirements.add("json_schema")
        if data.get("audio") or "audio" in (data.get("modalities") or []):
            requirements.add("audio")
        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in {
                        "image_url", "input_image", "image", "input_audio",
                    }:
                        requirements.add("audio" if block.get("type") == "input_audio" else "vision")
        return requirements

    @staticmethod
    def security_text(data: dict, max_chars: Optional[int] = None) -> str:
        """线性收集所有用户可控文本；仅跳过不具备文本语义的 base64 数据体。"""
        parts: list[str] = []
        total = 0

        def visit(value: Any) -> None:
            nonlocal total
            if max_chars is not None and total >= max_chars:
                return
            if isinstance(value, str):
                if value.startswith("data:") and ";base64," in value[:100]:
                    return
                fragment = value if max_chars is None else value[: max_chars - total]
                parts.append(fragment)
                total += len(fragment)
            elif isinstance(value, dict):
                for key, child in value.items():
                    # 键名也可能表达敏感语义（例如 tool 参数 api_key）。
                    visit(str(key))
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for field in ("messages", "tools", "functions", "input", "prompt"):
            visit(data.get(field))
        text = "\n".join(parts)
        return text if max_chars is None else text[:max_chars]

    def as_public_dict(self, channel: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in channel.items() if key not in {"api_key"}}


def load_registry(
    config_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> ProviderRegistry:
    return ProviderRegistry(
        Path(config_path) if config_path else default_config_path(),
        Path(manifest_path or os.environ.get("PROVIDER_MANIFEST_PATH", "provider_manifest.yaml")),
    )
