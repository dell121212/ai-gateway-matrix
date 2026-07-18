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

import litellm

from . import quota_manager, usage_tracker

logger = logging.getLogger("ai_gateway_matrix.llm_classifier")

# 显式指定时的默认分诊模型（强档、JSON 短输出）
DEFAULT_CLASSIFIER_MODEL = os.environ.get(
    "CLASSIFIER_MODEL", "openai/minimax-m2.7"
).strip() or "openai/minimax-m2.7"
CLASSIFIER_TIMEOUT_SECONDS = float(os.environ.get("CLASSIFIER_TIMEOUT_SECONDS", "5") or "5")
CLASSIFIER_MAX_INPUT_CHARS = 2000  # 只截前/后片段：够判强度，又快又省


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        logger.warning("[ai-gateway-matrix] %s 不是正整数，使用默认值 %d", name, default)
        return default


CLASSIFIER_RPM = _positive_int_env("CLASSIFIER_RPM", 30)

VALID_TIERS = {"弱", "中", "强", "顶级"}

TIER_TO_POOL = {
    "弱": "fast-pool",           # 0.5B–8B
    "中": "free-pool",           # 9B–30B
    "强": "strong-model-pool",   # 31B–100B
    "顶级": "elite-model-pool",  # 100B+
}

# 自动分诊候选：优先强模型 + 有 Key 的渠道（按顺序尝试）
# model 为 LiteLLM 路由串；api_base 仅 OpenAI 兼容自定义端点需要
_AUTO_STRONG_CLASSIFIERS: list[dict[str, Any]] = [
    {
        "env": "GENERALCOMPUTE_API_KEY",
        "model": "openai/minimax-m2.7",
        "api_base": "https://api.generalcompute.com/v1",
        "label": "GeneralCompute/minimax",
    },
    {
        "env": "GROQ_API_KEY",
        "model": "groq/openai/gpt-oss-120b",
        "api_base": None,
        "label": "Groq/gpt-oss-120b",
    },
    {
        "env": "DEEPSEEK_API_KEY",
        "model": "deepseek/deepseek-chat",
        "api_base": None,
        "label": "DeepSeek/chat",
    },
    {
        "env": "OPENROUTER_API_KEY",
        "model": "openrouter/deepseek/deepseek-r1:free",
        "api_base": None,
        "label": "OpenRouter/deepseek-r1-free",
    },
    {
        "env": "SAMBANOVA_API_KEY",
        "model": "sambanova/Meta-Llama-3.1-70B-Instruct",
        "api_base": None,
        "label": "SambaNova/70B",
    },
    {
        "env": "GEMINI_API_KEY",
        "model": "gemini/gemini-2.5-pro",
        "api_base": None,
        "label": "Gemini/2.5-pro",
    },
    {
        "env": "SILICONFLOW_API_KEY",
        "model": "openai/deepseek-ai/DeepSeek-R1",
        "api_base": "https://api.siliconflow.cn/v1",
        "label": "SiliconFlow/DeepSeek-R1",
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


def resolve_classifier_backend() -> Optional[dict[str, Any]]:
    """解析分诊用的 model / api_key / api_base。无可用强档 Key 时返回 None。"""
    explicit = (os.environ.get("CLASSIFIER_API_KEY") or "").strip()
    if explicit and _is_usable_key(explicit):
        model = (os.environ.get("CLASSIFIER_MODEL") or "").strip() or DEFAULT_CLASSIFIER_MODEL
        api_base = (os.environ.get("CLASSIFIER_API_BASE") or "").strip() or None
        # 裸模型名补 openai/（自定义 base 场景）
        if api_base and "/" not in model:
            model = f"openai/{model}"
        elif "/" not in model and model:
            # 无 base 时若像 openai 官方，仍加 openai/
            model = f"openai/{model}"
        return {
            "model": model,
            "api_key": explicit,
            "api_base": api_base,
            "cred_name": "CLASSIFIER_API_KEY",
            "label": f"explicit/{model}",
        }

    for cand in _AUTO_STRONG_CLASSIFIERS:
        env_name = cand["env"]
        key = (os.environ.get(env_name) or "").strip()
        if not _is_usable_key(key):
            continue
        return {
            "model": cand["model"],
            "api_key": key,
            "api_base": cand.get("api_base"),
            "cred_name": env_name,
            "label": cand.get("label") or env_name,
        }
    return None


async def classify_task(text: str) -> Optional[str]:
    """用强模型快速判断提问强度，返回目标池名；失败返回 None。"""
    if not text:
        return None

    backend = resolve_classifier_backend()
    if backend is None:
        logger.info(
            "[ai-gateway-matrix] 无可用强档 Key 做智能分诊，回退启发式 "
            "（可配 CLASSIFIER_API_KEY 或任意强档厂商 Key）"
        )
        return None

    cred_name = backend["cred_name"]
    reserved = await quota_manager.reserve_limits(
        [(f"credential:{cred_name}:classifier_rpm", CLASSIFIER_RPM, 60)]
    )
    if not reserved:
        logger.info("[ai-gateway-matrix] 分诊器本分钟额度已满（%s），回退启发式", cred_name)
        return None

    half = CLASSIFIER_MAX_INPUT_CHARS // 2
    snippet = (
        text
        if len(text) <= CLASSIFIER_MAX_INPUT_CHARS
        else text[:half] + "\n[...]\n" + text[-half:]
    )
    usage_id = usage_tracker.make_channel_id(
        backend["model"], backend.get("api_base"), api_key=backend["api_key"]
    )

    kwargs: dict[str, Any] = {
        "model": backend["model"],
        "api_key": backend["api_key"],
        "messages": [
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": snippet},
        ],
        # 推理模型可能先消耗一小段 reasoning token；40 容易只返回
        # {"tier": null} 或截断正文。128 仍是极短分诊，但能留足最终 JSON。
        "max_tokens": 128,
        "temperature": 0,
        "timeout": CLASSIFIER_TIMEOUT_SECONDS,
    }
    if backend.get("api_base"):
        kwargs["api_base"] = backend["api_base"]

    try:
        response = await litellm.acompletion(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except Exception:
        # 部分强模型不支持 response_format，再试一次无约束
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            logger.warning(
                "[ai-gateway-matrix] 强模型分诊失败（%s / %s），回退启发式",
                backend.get("label"),
                type(exc).__name__,
            )
            await usage_tracker.record_call(usage_id, success=False)
            return None

    usage = getattr(response, "usage", None)
    await usage_tracker.record_call(
        usage_id,
        success=True,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )

    try:
        raw = response.choices[0].message.content or ""
        tier = _parse_classifier_tier(raw)
    except Exception as exc:
        logger.warning(
            "[ai-gateway-matrix] 分诊返回无法解析（%s: %s），回退启发式",
            type(exc).__name__,
            exc,
        )
        return None

    if tier not in VALID_TIERS:
        logger.warning(
            "[ai-gateway-matrix] 分诊返回非法档位 %r，回退启发式",
            tier,
        )
        return None

    pool = TIER_TO_POOL[tier]
    logger.info(
        "[ai-gateway-matrix] 智能分诊: 强度=%s → %s（分诊模型=%s）",
        tier,
        pool,
        backend.get("label"),
    )
    return pool
