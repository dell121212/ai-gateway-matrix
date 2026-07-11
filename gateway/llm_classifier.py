#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 任务分类器 (v1)
————————————————————————————————————————
按你的要求实现："任务类别先交给指定模型进行判断，再决定路由到哪个池子"。

设计取舍：
  · 指定一个单独、固定、来源明确的"官方直营"模型专职做分类
    （Groq GPT-OSS 20B —— LPU 推理，分类这种几十 token 输出的小任务
    通常能在 300ms 内返回，用户几乎感知不到延迟）。用固定单一模型而不是
    丢给某个"池子"，是因为分类这一步要的是稳定、可预测的延迟，
    不需要 Router 的负载均衡逻辑。
  · 参照 NVIDIA-AI-Blueprints/llm-router 蓝图的"具名类别 + 一句话判据"
    做法：给分类器明确的类别定义和典型场景描述，而不是笼统地问
    "这个任务难不难"——模型对具体判据的判断比对模糊标签的判断稳定得多。
  · 分类器本身直接用 litellm.acompletion() 越过代理的 Router/Pool 体系
    调用，绕开 custom_router_hook 挂载的 async_pre_call_hook，
    避免"分类请求自己也要先被分类"的递归调用。
  · 分类失败（超时/网络错误/返回格式不对/tier 不合法）一律返回 None，
    调用方（gateway/custom_router_hook.py）收到 None 就退回启发式规则兜底——
    分类器只应该让路由"更准"，绝不应该让请求本身变得更脆弱。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import litellm

from . import quota_manager, usage_tracker

logger = logging.getLogger("ai_gateway_matrix.llm_classifier")

# 指定用来做分类的模型：直接写死具体 provider/model（官方直营、超快、免费），
# 不用 "trusted-pool" 这种池子名——分类这一步要的是稳定和快，不需要负载均衡。
CLASSIFIER_MODEL = "groq/openai/gpt-oss-20b"
CLASSIFIER_TIMEOUT_SECONDS = 4
CLASSIFIER_MAX_INPUT_CHARS = 2000  # 只截取前 2000 字符送去分类：够判断任务类型，且够快够省
def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        logger.warning("[ai-gateway-matrix] %s 不是正整数，使用默认值 %d", name, default)
        return default


CLASSIFIER_RPM = _positive_int_env("CLASSIFIER_RPM", 20)

VALID_TIERS = {"弱", "中", "强"}

TIER_TO_POOL = {
    "弱": "fast-pool",
    "中": "free-pool",
    "强": "strong-model-pool",
}

CLASSIFIER_SYSTEM_PROMPT = """你是一个任务复杂度分诊器。你的唯一任务是把用户请求归类到以下三档之一，
不要回答用户问题本身，也不要输出问题之外的任何内容。

【弱】日常问候、闲聊、简短翻译、单行/几行代码片段、简单事实问答
     —— 用最便宜最快的小模型就能出色完成。
【中】常规编程任务、几百字以内的写作/总结、一般性代码调试、中等难度的问答
     —— 大多数正常任务的默认档位。
【强】涉及多文件或整个项目的重构、系统架构设计、安全审计、数据库设计、
     分布式/高并发系统设计、复杂数学推导、需要长上下文或深度多步推理的任务
     —— 需要用最强的模型才能保证质量。

只输出一个 JSON 对象，不要有任何其他文字、不要用 Markdown 代码块包裹：
{"tier": "弱" 或 "中" 或 "强"}"""


async def classify_task(text: str) -> Optional[str]:
    """调用指定的分类模型判断任务档位。

    返回 "fast-pool" / "free-pool" / "strong-model-pool" 之一；
    任何异常（超时、网络错误、返回格式不对、tier 不在合法集合里）都返回 None。
    """
    if not text:
        return None

    api_key = os.environ.get("CLASSIFIER_API_KEY", "").strip()
    if not api_key:
        # 不再隐式复用 GROQ_API_KEY：分类额度必须显式配置、独立记账。
        return None

    reserved = await quota_manager.reserve_limits(
        [("credential:CLASSIFIER_API_KEY:rpm", CLASSIFIER_RPM, 60)]
    )
    if not reserved:
        logger.info("[ai-gateway-matrix] 分类器本分钟额度已满，回退启发式路由")
        return None

    half = CLASSIFIER_MAX_INPUT_CHARS // 2
    snippet = text if len(text) <= CLASSIFIER_MAX_INPUT_CHARS else text[:half] + "\n[...]\n" + text[-half:]
    raw: str = ""
    usage_id = usage_tracker.make_channel_id(CLASSIFIER_MODEL, api_key=api_key)

    try:
        response = await litellm.acompletion(
            model=CLASSIFIER_MODEL,
            api_key=api_key,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": snippet},
            ],
            max_tokens=30,
            temperature=0,
            timeout=CLASSIFIER_TIMEOUT_SECONDS,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # 超时/限流/该渠道恰好也不可用等——一律降级，不让请求失败
        logger.warning(
            "[ai-gateway-matrix] 任务分类器调用失败（%s），回退到启发式规则",
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
        parsed = json.loads(raw)
        tier = parsed.get("tier")
    except Exception as exc:
        logger.warning(
            "[ai-gateway-matrix] 任务分类器返回格式无法解析（%s: %s），回退到启发式规则",
            type(exc).__name__, exc,
        )
        return None

    if tier not in VALID_TIERS:
        logger.warning(
            "[ai-gateway-matrix] 任务分类器返回了不合法的档位（类型: %s），回退到启发式规则",
            type(tier).__name__,
        )
        return None

    pool = TIER_TO_POOL[tier]
    logger.info("[ai-gateway-matrix] 分类器判定档位: %s → %s", tier, pool)
    return pool
