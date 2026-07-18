#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
渠道加载/持久化逻辑 (v1)
————————————————————————————————————————
从 dashboard/backend.py 里拆出来的纯业务逻辑，不依赖 FastAPI，方便离线
单元测试（不需要装 fastapi 也能验证"config.yaml 解析对不对"这件事）。
"""

from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Optional

import yaml

from gateway import channel_ids, env_file, priority_overrides, provider_registry, usage_tracker
from .provider_catalog import (
    account_index_from_env,
    company_id_from_env,
    free_quota_kind,
    free_quota_label_zh,
    get_provider_info,
)
from .quota_catalog import build_rate_limits
from .safe_files import locked_file, safe_rewrite

_DASHBOARD_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent

CONFIG_PATH = Path(os.environ.get("GATEWAY_CONFIG_PATH", str(_PROJECT_ROOT / "config.yaml")))
ENV_PATH = Path(os.environ.get("GATEWAY_ENV_PATH", str(_PROJECT_ROOT / ".env")))
DISCOVERY_PATH = Path(os.environ.get("PROVIDER_DISCOVERY_OUTPUT", str(_PROJECT_ROOT / "state/provider-discovery.json")))

# config.yaml 里的 model_name → 前端展示用的档位标签
TIER_LABELS = {
    "fast-pool": "弱",              # 0.5B–8B
    "free-pool": "中",              # 9B–30B
    "strong-model-pool": "强",      # 31B–100B
    "elite-model-pool": "顶级",     # 100B+
}


def parse_env_var_ref(value: Optional[str]) -> Optional[str]:
    """从 "os.environ/XXX" 里提取出 XXX；不是这个格式就返回 None。"""
    if not isinstance(value, str) or not value.startswith("os.environ/"):
        return None
    return value.split("/", 1)[1]


def read_env_file(env_path: Path = ENV_PATH) -> dict[str, str]:
    """读取 .env 文件，返回 {KEY: VALUE}。文件不存在就返回空字典（不报错）。"""
    return env_file.read_env(env_path)


def find_channel(channel_id: str, config_path: Path = CONFIG_PATH, env_path: Path = ENV_PATH) -> Optional[dict]:
    """按 channel_id 查找渠道；兼容旧人类可读 id 与被 # 截断的 URL。

    浏览器在未 encode 时会丢掉 ``#env`` 后缀，导致 OpenRouter 等 404「渠道不存在」。
    """
    from urllib.parse import unquote

    cid = unquote((channel_id or "").strip())
    if not cid:
        return None
    channels = load_channels(config_path, env_path)
    by_id = {c["channel_id"]: c for c in channels}
    if cid in by_id:
        return by_id[cid]
    for c in channels:
        if c.get("legacy_channel_id") == cid:
            return c
        if c.get("direct_model_name") == cid:
            return c
    # 被 # 截断：openrouter/foo@default  ← 缺 #OPENROUTER_API_KEY
    if "#" not in cid and "@" in cid:
        prefix = cid + "#"
        hits = [c for c in channels if str(c.get("legacy_channel_id") or "").startswith(prefix)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            # 同 model@base 多账号时取第一个已配置，否则第一个
            configured = [c for c in hits if c.get("is_configured")]
            return (configured or hits)[0]
    # 仅 env 名（公司级误传）
    env_hits = [c for c in channels if c.get("env_var") == cid]
    if len(env_hits) == 1:
        return env_hits[0]
    return None


def write_env_var(key: str, value: str, env_path: Path = ENV_PATH) -> None:
    """更新（或新增）.env 里的一行 KEY=VALUE，保留其余行/注释不变。"""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise ValueError("环境变量名不合法")
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise ValueError("环境变量值不能包含换行或 NUL")
    with locked_file(env_path):
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        pattern = re.compile(rf"^{re.escape(key)}\s*=")
        found = False
        new_lines = []
        for line in lines:
            if pattern.match(line):
                new_lines.append(f"{key}={env_file.encode_value(value)}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={env_file.encode_value(value)}")
        safe_rewrite(env_path, "\n".join(new_lines) + "\n", mode=0o600)


def load_channels(config_path: Path = CONFIG_PATH, env_path: Path = ENV_PATH) -> list[dict]:
    """解析 config.yaml，把每个 deployment 转成前端需要的渠道结构。

    trusted-pool 里的条目全部是别的池子（fast/free/strong）已经出现过的
    deployment 通过 YAML 锚点复用过来的，所以这里直接跳过 trusted-pool 本身，
    避免同一个官方渠道在前端重复出现一次；trusted-pool 成员身份改用
    "is_trusted_pool_member" 字段体现在它所属的那条主记录上。

    MVP 限制：如果未来有 deployment 同时出现在两个非 trusted 池子里
    （目前配置里没有这种情况），这里会保留后处理到的那一条，
    不做多档位合并展示。
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    env_values = read_env_file(env_path)
    try:
        discovery = json.loads(DISCOVERY_PATH.read_text(encoding="utf-8")).get("results", {})
    except (OSError, ValueError, AttributeError):
        discovery = {}
    try:
        runtime_registry = provider_registry.load_registry(str(config_path))
    except Exception:
        runtime_registry = None

    trusted_keys: set[str] = set()
    for m in cfg["model_list"]:
        if m["model_name"] != "trusted-pool":
            continue
        params = m.get("litellm_params", {}) or {}
        trusted_keys.add(usage_tracker.make_channel_id(params.get("model", ""), params.get("api_base")))

    channels: dict[str, dict] = {}
    for m in cfg["model_list"]:
        pool = m["model_name"]
        if pool not in TIER_LABELS:  # 跳过 auto-route 和 trusted-pool
            continue
        params = m.get("litellm_params", {}) or {}
        model = params.get("model", "unknown")
        api_base = params.get("api_base")
        env_var = parse_env_var_ref(params.get("api_key"))

        # 展示/API 用的稳定主键：用 model+api_base+env_var 三元组，
        # 不依赖是否已经配置了真实 key、也不会在重启前后发生变化。
        # 之前只用 model+api_base 算 id 时，Mistral 的两个账号
        # （相同 model、都没设 api_base，只有 env_var 不同）会撞车成
        # 同一个 id，导致仪表盘上只显示得出一张卡片——这是实测发现的
        # 真实 bug，这里用三元组修掉。用共享的 gateway/channel_ids.py 生成，
        # 保证跟 gateway/custom_router_hook.py 启动时构建的渠道注册表用的是
        # 同一套算法，"限时优先"标记才能两边对上号。
        display_id = channel_ids.make_display_id(model, api_base, env_var)
        legacy_channel_id = channel_ids.make_legacy_display_id(model, api_base, env_var)
        direct_model_name = channel_ids.make_direct_model_name(model, api_base, env_var)

        info = get_provider_info(env_var) if env_var else {
            "name": model, "signup_url": "", "trust": "third_party", "note": "",
        }
        configured_value = env_values.get(env_var, "") if env_var else ""
        try:
            from gateway.runtime_launcher import _is_placeholder_credential
            is_configured = not _is_placeholder_credential(configured_value)
        except Exception:
            is_configured = bool(configured_value) and not configured_value.startswith("dummy-")
        masked = f"****{configured_value[-4:]}" if is_configured and len(configured_value) >= 4 else ""

        # 用量查询专用的 key：跟 gateway/custom_router_hook.py 在请求时用同样的算法
        # （model + api_base + 真实 api_key 的哈希）算出来，两边才能对上号；
        # 在没配置真实 key 之前这个值退化成没有哈希后缀的版本，此时反正也
        # 没有真实用量可查，不影响展示（配置完并重启后，两边会自动同步成
        # 带哈希后缀的一致值）。
        usage_key = usage_tracker.make_channel_id(model, api_base, configured_value or None)

        # model：LiteLLM 路由串（含 gemini/ openai/ 等 provider 前缀）
        # model_display：厂商真实 model id，给用户编辑用（无需手写前缀）
        try:
            from .config_editor import strip_litellm_provider
            model_display = strip_litellm_provider(model)
        except Exception:
            model_display = model

        company_id = company_id_from_env(env_var or "")
        account_index = account_index_from_env(env_var or "")
        company_name = info["name"]
        manual_priority = priority_overrides.get_priority(
            pool, model, api_base, env_var
        )

        channels[display_id] = {
            "channel_id": display_id,
            "legacy_channel_id": legacy_channel_id,
            "usage_key": usage_key,
            "direct_model_name": direct_model_name,
            "model": model,
            "model_display": model_display,
            "api_base": api_base,
            "env_var": env_var,
            "company_id": company_id,
            "company_name": company_name,
            "account_index": account_index,
            "account_label": f"账号 {account_index}",
            "provider_name": company_name,
            "signup_url": info["signup_url"],
            "trust": info["trust"],
            "note": info["note"],
            "billing": info.get("billing", "free_or_trial"),
            "pricing_label_zh": info.get("pricing_label_zh") or "",
            "how_free_zh": info.get("how_free_zh") or "",
            "pricing_detail_zh": info.get("pricing_detail_zh") or "",
            # 模型级：可重置免费优先；一次性免费往后排（如硅基 V3/R1）
            "free_quota_kind": free_quota_kind(env_var or "", model),
            "free_quota_label_zh": free_quota_label_zh(free_quota_kind(env_var or "", model)),
            "tier": TIER_LABELS[pool],
            "tier_pool": pool,
            "is_trusted_pool_member": usage_tracker.make_channel_id(model, api_base) in trusted_keys,
            "rpm_limit": params.get("rpm"),
            "priority": manual_priority if manual_priority is not None else params.get("priority"),
            "max_input_tokens": params.get("max_input_tokens"),
            "is_configured": is_configured,
            "masked_key": masked,
            # 先挂文档/config 限额骨架；后端合并 usage 后再注入 used
            "rate_limits": build_rate_limits(env_var, params.get("rpm"), usage=None),
            "capabilities": (
                runtime_registry.channels.get(display_id, {}).get("capabilities", {})
                if runtime_registry else {}
            ),
            "data_policy": (
                runtime_registry.channels.get(display_id, {}).get("data_policy", "unverified")
                if runtime_registry else "unverified"
            ),
            "sensitive_allowed": (
                bool(runtime_registry.channels.get(display_id, {}).get("sensitive_allowed", False))
                if runtime_registry else False
            ),
            "discovery": discovery.get(display_id),
        }
    return list(channels.values())
