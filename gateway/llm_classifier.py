#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 任务强度分诊器
————————————————————————————————————————
智能模式（auto-route / mode-intelligent）的两段式路由：

  1) **强模型快速分诊**：用强档模型只看提问，判断弱/中/强（不回答正文）
  2) **内部选池作答**：把 data["model"] 改写成 fast-pool / free-pool /
     strong-model-pool，再由 LiteLLM Router 在池内挑具体渠道

设计取舍：
  · 分诊走 litellm.acompletion() 直连，越过代理 Router/hook，避免递归。
  · 默认用「强档可用 Key」自动选分诊模型；也可 CLASSIFIER_* 显式指定。
  · 分诊失败（无 Key / 超时 / 解析失败）返回 None，调用方回退启发式——
    分诊只应让路由更准，绝不能让请求本身更脆。
  · 参照 NVIDIA-AI-Blueprints/llm-router：具名档位 + 明确判据。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

# litellm 仅网关进程需要；仪表盘容器无此包，classifier_status() 等必须可无。
# 真正 acompletion 时再 import。

from . import quota_manager, usage_tracker

logger = logging.getLogger("ai_gateway_matrix.llm_classifier")

# 显式指定时的默认分诊模型（强档、JSON 短输出）
DEFAULT_CLASSIFIER_MODEL = os.environ.get(
    "CLASSIFIER_MODEL", "openai/minimax-m2.7"
).strip() or "openai/minimax-m2.7"
CLASSIFIER_TIMEOUT_SECONDS = float(os.environ.get("CLASSIFIER_TIMEOUT_SECONDS", "12") or "12")
CLASSIFIER_MAX_INPUT_CHARS = 2000  # 只截前/后片段：够判强度，又快又省

# 已知渠道 env → 默认分诊模型 / api_base（用户「选用已有 API」时不必手填）
_SOURCE_ENV_DEFAULTS: dict[str, dict[str, Optional[str]]] = {
    "GENERALCOMPUTE_API_KEY": {
        "model": "openai/minimax-m2.7",
        "api_base": "https://api.generalcompute.com/v1",
        "label": "GeneralCompute/minimax",
    },
    "DEEPSEEK_API_KEY": {
        "model": "deepseek/deepseek-reasoner",
        "api_base": None,
        "label": "DeepSeek/reasoner",
    },
    "GEMINI_API_KEY": {
        "model": "gemini/gemini-3.5-flash",
        "api_base": None,
        "label": "Gemini/3.5-flash",
    },
    "DASHSCOPE_API_KEY": {
        "model": "openai/qwen-plus",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "label": "DashScope/qwen-plus",
    },
    "MISTRAL_KEY_1": {
        "model": "mistral/mistral-large-latest",
        "api_base": None,
        "label": "Mistral/large-1",
    },
    "MISTRAL_KEY_2": {
        "model": "mistral/mistral-large-latest",
        "api_base": None,
        "label": "Mistral/large-2",
    },
    "OPENROUTER_API_KEY": {
        "model": "openrouter/openai/gpt-4o-mini",
        "api_base": None,
        "label": "OpenRouter/gpt-4o-mini",
    },
    "MOONSHOT_API_KEY": {
        "model": "openai/moonshot-v1-8k",
        "api_base": "https://api.moonshot.cn/v1",
        "label": "Moonshot/kimi",
    },
    "GROQ_API_KEY": {
        "model": "groq/openai/gpt-oss-120b",
        "api_base": None,
        "label": "Groq/gpt-oss-120b",
    },
}


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        logger.warning("[ai-gateway-matrix] %s 不是正整数，使用默认值 %d", name, default)
        return default


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


CLASSIFIER_RPM = _positive_int_env("CLASSIFIER_RPM", 30)

VALID_TIERS = {"弱", "中", "强", "顶级"}

TIER_TO_POOL = {
    "弱": "fast-pool",           # 0.5B–8B
    "中": "free-pool",           # 9B–30B
    "强": "strong-model-pool",   # 31B–100B
    "顶级": "elite-model-pool",  # 100B+
}

# 智能分诊必须由顶级模型完成：最多依次尝试三个不同顶级渠道，全部失败后
# 只允许再试一个强档渠道。Groq / SambaNova / SiliconFlow 被用户明确限制为
# 弱档，因此不能出现在分诊候选里。
_AUTO_ELITE_CLASSIFIERS: list[dict[str, Any]] = [
    {
        "env": "GENERALCOMPUTE_API_KEY",
        "model": "openai/minimax-m2.7",
        "api_base": "https://api.generalcompute.com/v1",
        "label": "GeneralCompute/minimax",
        "tier": "elite",
    },
    {
        "env": "MISTRAL_KEY_1",
        "model": "mistral/mistral-large-latest",
        "api_base": None,
        "label": "Mistral/large-1",
        "tier": "elite",
    },
    {
        "env": "DEEPSEEK_API_KEY",
        "model": "deepseek/deepseek-reasoner",
        "api_base": None,
        "label": "DeepSeek/reasoner",
        "tier": "elite",
    },
    {
        "env": "OPENROUTER_API_KEY",
        "model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        "api_base": None,
        "label": "OpenRouter/Nemotron-550B",
        "tier": "elite",
    },
    {
        "env": "MISTRAL_KEY_2",
        "model": "mistral/mistral-large-latest",
        "api_base": None,
        "label": "Mistral/large-2",
        "tier": "elite",
    },
]

_AUTO_STRONG_CLASSIFIERS: list[dict[str, Any]] = [
    {
        "env": "GEMINI_API_KEY",
        "model": "gemini/gemini-3.5-flash",
        "api_base": None,
        "label": "Gemini/3.5-flash",
        "tier": "strong",
    },
    {
        "env": "DASHSCOPE_API_KEY",
        "model": "openai/qwen-plus",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "label": "DashScope/qwen-plus",
        "tier": "strong",
    },
    {
        "env": "MISTRAL_KEY_1",
        "model": "mistral/mistral-medium-latest",
        "api_base": None,
        "label": "Mistral/medium",
        "tier": "strong",
    },
]

CLASSIFIER_SYSTEM_PROMPT = """你是提问强度分诊器。只判断用户当前提问需要多强的模型，不要回答问题本身。

按参数量能力大致对应（仅供选档，不代表绝对）：
【弱】0.5B–8B：分类、提取、摘要、简单问答；复杂任务易漏条件
【中】9B–30B：日常写作、翻译、普通编程；大型项目与长篇一致性一般
【强】31B–100B：复杂写作、代码分析、多条件任务较稳；70B 通常属强
【顶级】100B+：综合理解、复杂推理、长文本与项目规划更强（不保证必胜优秀小模型）

最低档位示例：
- 寒暄、简单算术、字段提取：弱
- 日常写作、翻译、编写普通函数：至少中
- 调试、死锁、竞态、代码审查、根因分析：至少强
- 多区域架构、大型项目规划、超长多约束任务：强或顶级

只输出一个 JSON 对象，不要 Markdown、不要其它文字：
{"tier":"弱"} 或 {"tier":"中"} 或 {"tier":"强"} 或 {"tier":"顶级"}"""


def _parse_classifier_tier(raw: str) -> Any:
    """从模型输出中解出第一个 JSON 结果，并返回 ``tier``。

    部分 OpenAI 兼容端点即使收到 ``response_format``，仍可能输出 Markdown、
    多个连续 JSON，或把唯一对象包成数组。``raw_decode`` 只消费第一个完整
    JSON 值，避免用“第一个 { 到最后一个 }”拼出非法 JSON。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        closing = text.rfind("```")
        if closing >= 0:
            text = text[:closing]
        text = text.strip()

    starts = [index for token in ("{", "[") if (index := text.find(token)) >= 0]
    if not starts:
        raise ValueError("没有 JSON 对象或数组")
    parsed, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    if isinstance(parsed, list):
        parsed = next((item for item in parsed if isinstance(item, dict)), None)
    if not isinstance(parsed, dict):
        raise TypeError("分诊 JSON 顶层不是对象")
    return parsed.get("tier")


def _is_usable_key(value: str) -> bool:
    try:
        from gateway.runtime_launcher import _is_placeholder_credential
        return not _is_placeholder_credential(value)
    except Exception:
        v = (value or "").strip()
        if not v or not v.isascii():
            return False
        low = v.lower()
        if low.startswith("dummy-") or low.startswith("sk-test") or low.startswith("sk-tes"):
            return False
        return True


def _normalize_model_name(model: str, api_base: Optional[str]) -> str:
    model = (model or "").strip()
    if not model:
        return DEFAULT_CLASSIFIER_MODEL
    if api_base and "/" not in model:
        return f"openai/{model}"
    if "/" not in model:
        return f"openai/{model}"
    return model


def _backend_from_source_env(env_name: str) -> Optional[dict[str, Any]]:
    """从已有渠道凭据构造专用分诊后端。"""
    env_name = (env_name or "").strip().upper()
    if not env_name or env_name == "AUTO":
        return None
    key = (os.environ.get(env_name) or "").strip()
    if not _is_usable_key(key):
        return None
    defaults = _SOURCE_ENV_DEFAULTS.get(env_name, {})
    model = (os.environ.get("CLASSIFIER_MODEL") or "").strip() or defaults.get("model") or DEFAULT_CLASSIFIER_MODEL
    api_base_env = (os.environ.get("CLASSIFIER_API_BASE") or "").strip()
    api_base = api_base_env or defaults.get("api_base") or None
    model = _normalize_model_name(model, api_base)
    label = defaults.get("label") or f"source/{env_name}"
    return {
        "model": model,
        "api_key": key,
        "api_base": api_base,
        "cred_name": env_name,
        "label": label,
        "tier": "elite",
        "dedicated": True,
    }


def resolve_dedicated_classifier() -> Optional[dict[str, Any]]:
    """解析用户指定的「专用分诊/答检」后端（不含自动免费链）。

    优先级：
      1. CLASSIFIER_API_KEY（独立专用 Key）
      2. CLASSIFIER_SOURCE_ENV=某渠道 env（复用已填 Key，推荐）
    """
    explicit = (os.environ.get("CLASSIFIER_API_KEY") or "").strip()
    if explicit and _is_usable_key(explicit):
        model = (os.environ.get("CLASSIFIER_MODEL") or "").strip() or DEFAULT_CLASSIFIER_MODEL
        api_base = (os.environ.get("CLASSIFIER_API_BASE") or "").strip() or None
        model = _normalize_model_name(model, api_base)
        return {
            "model": model,
            "api_key": explicit,
            "api_base": api_base,
            "cred_name": "CLASSIFIER_API_KEY",
            "label": f"dedicated/{model}",
            "tier": "elite",
            "dedicated": True,
        }

    source = (os.environ.get("CLASSIFIER_SOURCE_ENV") or "").strip().upper()
    if source and source not in {"", "AUTO", "NONE", "OFF"}:
        return _backend_from_source_env(source)
    return None


def resolve_classifier_backends() -> list[dict[str, Any]]:
    """返回分诊链。

    · 已配置专用分诊时：默认**独占**该后端（CLASSIFIER_EXCLUSIVE 默认 true），
      保证分配任务走你指定的稳定 API，不被免费链拖垮。
    · 未配置时：自动最多三个顶级 + 一个强档（历史行为）。
    """
    dedicated = resolve_dedicated_classifier()
    exclusive = _truthy_env("CLASSIFIER_EXCLUSIVE", default=True)
    if dedicated and exclusive:
        return [dedicated]

    resolved: list[dict[str, Any]] = []
    if dedicated:
        resolved.append(dedicated)

    for cand in _AUTO_ELITE_CLASSIFIERS:
        if len([item for item in resolved if item["tier"] == "elite"]) >= 3:
            break
        env_name = cand["env"]
        key = (os.environ.get(env_name) or "").strip()
        if not _is_usable_key(key):
            continue
        identity = (cand["model"], env_name)
        if any((item["model"], item["cred_name"]) == identity for item in resolved):
            continue
        resolved.append({
            "model": cand["model"],
            "api_key": key,
            "api_base": cand.get("api_base"),
            "cred_name": env_name,
            "label": cand.get("label") or env_name,
            "tier": "elite",
            "dedicated": False,
        })

    for cand in _AUTO_STRONG_CLASSIFIERS:
        env_name = cand["env"]
        key = (os.environ.get(env_name) or "").strip()
        if not _is_usable_key(key):
            continue
        if any(item["cred_name"] == env_name and item["model"] == cand["model"] for item in resolved):
            continue
        resolved.append({
            "model": cand["model"],
            "api_key": key,
            "api_base": cand.get("api_base"),
            "cred_name": env_name,
            "label": cand.get("label") or env_name,
            "tier": "strong",
            "dedicated": False,
        })
        break
    return resolved


def classifier_status() -> dict[str, Any]:
    """供仪表盘展示当前分诊配置（不泄露完整 Key）。"""
    dedicated = resolve_dedicated_classifier()
    backends = resolve_classifier_backends()
    source = (os.environ.get("CLASSIFIER_SOURCE_ENV") or "").strip().upper()
    has_explicit = bool((os.environ.get("CLASSIFIER_API_KEY") or "").strip())
    mode = "auto"
    if has_explicit:
        mode = "dedicated_key"
    elif source and source not in {"", "AUTO", "NONE", "OFF"}:
        mode = "source_env"
    return {
        "mode": mode,
        "source_env": source if mode == "source_env" else "",
        "model": (os.environ.get("CLASSIFIER_MODEL") or "").strip()
            or (dedicated or {}).get("model")
            or "",
        "api_base": (os.environ.get("CLASSIFIER_API_BASE") or "").strip()
            or (dedicated or {}).get("api_base")
            or "",
        "exclusive": _truthy_env("CLASSIFIER_EXCLUSIVE", default=True),
        "has_dedicated": dedicated is not None,
        "active_label": (backends[0].get("label") if backends else None),
        "active_model": (backends[0].get("model") if backends else None),
        "chain_len": len(backends),
        "answer_verify_mode": (os.environ.get("ANSWER_VERIFY_MODE") or "hybrid").strip().lower(),
        "known_source_envs": sorted(_SOURCE_ENV_DEFAULTS.keys()),
    }


def resolve_classifier_backend() -> Optional[dict[str, Any]]:
    """兼容旧调用方：返回分诊链的第一个可用后端。"""
    backends = resolve_classifier_backends()
    return backends[0] if backends else None


async def classify_task(text: str) -> Optional[str]:
    """用强模型快速判断提问强度，返回目标池名；失败返回 None。"""
    if not text:
        return None

    backends = resolve_classifier_backends()
    if not backends:
        logger.info(
            "[ai-gateway-matrix] 无可用顶级/强档 Key 做智能分诊，回退启发式 "
            "（可配 CLASSIFIER_API_KEY 或任意强档厂商 Key）"
        )
        return None

    half = CLASSIFIER_MAX_INPUT_CHARS // 2
    snippet = (
        text
        if len(text) <= CLASSIFIER_MAX_INPUT_CHARS
        else text[:half] + "\n[...]\n" + text[-half:]
    )
    for attempt, backend in enumerate(backends, 1):
        cred_name = backend["cred_name"]
        reserved = await quota_manager.reserve_limits(
            [(f"credential:{cred_name}:classifier_rpm", CLASSIFIER_RPM, 60)]
        )
        if not reserved:
            logger.warning(
                "[ai-gateway-matrix] 分诊候选 %s 本分钟额度已满，立即换下一个",
                backend.get("label"),
            )
            continue

        usage_id = usage_tracker.make_usage_key(
            backend["model"],
            backend.get("api_base"),
            backend["api_key"],
            env_var=backend.get("cred_name"),
        )
        kwargs: dict[str, Any] = {
            "model": backend["model"],
            "api_key": backend["api_key"],
            "messages": [
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": snippet},
            ],
            "max_tokens": 128,
            "temperature": 0,
            "timeout": CLASSIFIER_TIMEOUT_SECONDS,
            "response_format": {"type": "json_object"},
        }
        if backend.get("api_base"):
            kwargs["api_base"] = backend["api_base"]

        try:
            import litellm
            response = await litellm.acompletion(**kwargs)
            raw = response.choices[0].message.content or ""
            tier = _parse_classifier_tier(raw)
            if tier not in VALID_TIERS:
                raise ValueError(f"非法档位 {tier!r}")
        except Exception as exc:
            await usage_tracker.record_call(usage_id, success=False)
            next_label = (
                backends[attempt].get("label") if attempt < len(backends) else "启发式"
            )
            logger.warning(
                "[ai-gateway-matrix] 分诊失败（%s / %s），立即换 %s",
                backend.get("label"), type(exc).__name__, next_label,
            )
            continue

        usage = getattr(response, "usage", None)
        await usage_tracker.record_call(
            usage_id,
            success=True,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        pool = TIER_TO_POOL[tier]
        logger.info(
            "[ai-gateway-matrix] 智能分诊: 强度=%s → %s（分诊模型=%s，候选 %d/%d）",
            tier, pool, backend.get("label"), attempt, len(backends),
        )
        return pool

    return None
