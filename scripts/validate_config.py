#!/usr/bin/env python3
"""不依赖 LiteLLM/真实 API Key 的严格配置与派生数据校验。"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
import yaml

from gateway import channel_ids
from gateway.provider_registry import PRIMARY_POOLS, parse_env_ref

ROOT = Path(__file__).resolve().parents[1]


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping_without_duplicates(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"重复 YAML key {key!r}（第 {key_node.start_mark.line + 1} 行）")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _mapping_without_duplicates,
)


def load_strict(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        result = yaml.load(f, Loader=UniqueKeyLoader)
    if not isinstance(result, dict):
        raise ValueError(f"{path.name} 顶层必须是 mapping")
    return result


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visited: set[str] = set()
    active: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in active:
            index = active.index(node)
            return active[index:] + [node]
        if node in visited:
            return None
        active.append(node)
        for target in graph.get(node, []):
            cycle = visit(target)
            if cycle:
                return cycle
        active.pop()
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def validate() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        config = load_strict(ROOT / "config.yaml")
        manifest = load_strict(ROOT / "provider_manifest.yaml")
        compose = load_strict(ROOT / "docker-compose.yml")
    except Exception as exc:
        return [str(exc)], warnings

    model_list = config.get("model_list")
    if not isinstance(model_list, list):
        return ["config.yaml 缺少 model_list"], warnings

    # 根目录只放入口配置；运行时、运维和管理面必须保持分层。
    root_python_files = sorted(path.name for path in ROOT.glob("*.py"))
    if root_python_files:
        errors.append(f"根目录不应堆放 Python 文件: {root_python_files}")
    required_paths = (
        ROOT / "gateway" / "__init__.py",
        ROOT / "gateway" / "custom_router_hook.py",
        ROOT / "scripts" / "__init__.py",
        ROOT / "scripts" / "test_gateway.py",
        ROOT / "dashboard" / "__init__.py",
    )
    missing_paths = [str(path.relative_to(ROOT)) for path in required_paths if not path.is_file()]
    if missing_paths:
        errors.append(f"项目分层缺少必需文件: {missing_paths}")

    callbacks = (config.get("litellm_settings") or {}).get("callbacks") or []
    if "gateway.custom_router_hook.proxy_handler_instance" not in callbacks:
        errors.append("LiteLLM 未指向 gateway 包中的自定义路由 Hook")

    for service_name, service in (compose.get("services") or {}).items():
        for mount in (service or {}).get("volumes") or []:
            if not isinstance(mount, str) or not mount.startswith("./"):
                continue
            source = mount.split(":", 1)[0]
            if source == "./.env" and (ROOT / ".env.example").is_file():
                continue  # run.sh 首次启动时会从模板安全创建
            if not (ROOT / source).exists():
                errors.append(f"Docker 服务 {service_name} 挂载了不存在的路径: {source}")

    primary = [item for item in model_list if item.get("model_name") in PRIMARY_POOLS]
    direct_items = [
        item for item in model_list
        if str(item.get("model_name", "")).startswith("direct-")
    ]
    direct = {item.get("model_name"): item for item in direct_items}
    env_refs: set[str] = set()
    fingerprints: set[tuple] = set()
    retired_models = {
        "gemini/gemini-2.0-flash",
        "groq/llama-3.3-70b-versatile",
        "groq/llama-4-scout-17b-16e-instruct",
        "groq/kimi-k2-instruct",
        "cerebras/llama3.1-8b",
        "cerebras/llama3.1-70b",
    }

    for item in primary:
        params = item.get("litellm_params") or {}
        model = params.get("model")
        env_var = parse_env_ref(params.get("api_key"))
        if env_var:
            env_refs.add(env_var)
        fingerprint = (model, params.get("api_base"), env_var)
        if fingerprint in fingerprints:
            errors.append(f"主 deployment 重复: {fingerprint}")
        fingerprints.add(fingerprint)
        if model in retired_models:
            errors.append(f"仍在使用已退役模型: {model}")
        expected = channel_ids.make_direct_model_name(model, params.get("api_base"), env_var)
        derived = direct.get(expected)
        if derived is None:
            errors.append(f"{item.get('model_name')}/{model} 缺少派生直连分组 {expected}")
        elif (derived.get("litellm_params") or {}) != params:
            errors.append(f"{expected} 与主 deployment 参数漂移")

    if len(direct) != len(direct_items):
        errors.append("存在重复的 direct model_name")
    if len(direct_items) != len(primary):
        errors.append(f"direct 分组 {len(direct_items)} 个，主 deployment {len(primary)} 个")

    provider_policies = manifest.get("providers") or {}
    missing_manifest = sorted(env_refs - set(provider_policies))
    if missing_manifest:
        errors.append(f"provider_manifest.yaml 缺少凭据: {missing_manifest}")

    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    env_example = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_text, re.M))
    missing_env = sorted(env_refs - env_example)
    if missing_env:
        errors.append(f".env.example 缺少: {missing_env}")

    for item in model_list:
        if item.get("model_name") != "trusted-pool":
            continue
        env_var = parse_env_ref((item.get("litellm_params") or {}).get("api_key"))
        if not (provider_policies.get(env_var, {}) or {}).get("sensitive_allowed", False):
            errors.append(f"trusted-pool 包含未显式允许敏感数据的凭据 {env_var}")

    graph: dict[str, list[str]] = {}
    for mapping in (config.get("litellm_settings") or {}).get("fallbacks") or []:
        if isinstance(mapping, dict):
            for source, targets in mapping.items():
                graph[str(source)] = [str(target) for target in targets or []]
    cycle = _find_cycle(graph)
    if cycle:
        errors.append("fallback 存在环: " + " -> ".join(cycle))

    image = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    if "litellm:main-latest" in image:
        errors.append("LiteLLM Docker 镜像仍使用 main-latest")

    dockerignore_path = ROOT / ".dockerignore"
    if not dockerignore_path.is_file():
        errors.append("缺少 .dockerignore，Docker 构建可能上传 .env 密钥")
    else:
        dockerignore_patterns = {
            line.strip()
            for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if ".env" not in dockerignore_patterns:
            errors.append(".dockerignore 未排除 .env 密钥文件")

    pool_counts = Counter(item.get("model_name") for item in primary)
    warnings.append(
        "主 deployment: " + ", ".join(f"{pool}={pool_counts[pool]}" for pool in PRIMARY_POOLS)
    )
    return errors, warnings


def main() -> int:
    errors, warnings = validate()
    for warning in warnings:
        print(f"INFO: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("严格配置校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
