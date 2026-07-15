#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定价查询模块 (v1)
————————————————————————————————————————
按你的要求："模型的单价先通过 API 查询，若没有结果则再进行预估"。

三层查询顺序：
  1. litellm.completion_cost() —— litellm 自带、社区维护、定期更新的价格库
     （model_prices_and_context_window.json），覆盖大部分主流模型。这是
     "先通过 API 查询"里说的"API"：不是每次都真的发一次网络请求去问
     供应商，而是查询 litellm 库内置、定期从各家官方定价页同步过来的
     价格表——这是目前能覆盖最多渠道、最省事也最不容易出错的数据源。
  2. 查不到（比如 Together/Fireworks/DeepInfra 这几个渠道用的具体部署名
     可能没有精确匹配上 litellm 价格表里的条目）：退回下面 ESTIMATED_PRICES
     这个小表。这些数字是从几个第三方定价聚合站交叉核对后取的中位数，
     标注了大致时间，**不是实时数据**，只用来给你一个大概的数量级参考，
     真金白银决策请以对应渠道官网当前价格为准。
  3. 两层都查不到（大部分免费官方渠道确实没有一个"正常价格"——它们的
     免费层不是"打折"，是压根不计费）：返回 (None, "unknown")，仪表盘
     应该显示"免费 / 暂无定价数据"，而不是编一个数字出来显得"看起来很
     科学"。伪造金额比不显示金额更容易误导你做决策。
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional

import litellm

logger = logging.getLogger("ai_gateway_matrix.pricing")


class PriceInfo(NamedTuple):
    input_cost_per_token: float
    output_cost_per_token: float
    source: str  # "official" | "litellm" | "estimated"


# General Compute 官方价（2026-07-14 查询 docs.generalcompute.com/models）。
# 该渠道是用户已充值的按量付费 API，必须优先于通用模型价格库，避免同名
# 开源模型被误套成另一家托管商的价格。键为 General Compute 的原始 model ID。
GENERALCOMPUTE_PRICES: dict[str, PriceInfo] = {
    "minimax-m2.7": PriceInfo(0.40e-6, 2.34e-6, "official"),
    "minimax-m2.5": PriceInfo(0.20e-6, 1.17e-6, "official"),
    "deepseek-v3.2": PriceInfo(3.00e-6, 4.50e-6, "official"),
    "deepseek-v3.1": PriceInfo(3.00e-6, 4.50e-6, "official"),
    "deepseek-v3.1-cb": PriceInfo(0.15e-6, 0.75e-6, "official"),
    "llama-3.3-70b": PriceInfo(0.60e-6, 1.20e-6, "official"),
    "llama-4-maverick-17b": PriceInfo(0.63e-6, 1.80e-6, "official"),
    "gpt-oss-120b": PriceInfo(0.21e-6, 0.79e-6, "official"),
    "gemma-3-12b-it": PriceInfo(0.04e-6, 0.13e-6, "official"),
}


# 补充估算表：只收录调研过、能在第三方定价聚合站交叉核对到数字的渠道，
# 均为 2026 年年中前后的近似值（不同来源报的价格本身就能差 20%-40%，
# 这里取的是交叉核对后的中位数），仅供数量级参考。
# key 用 litellm 实际调用的 model 字符串做前缀匹配。
# 完全免费的官方渠道（Groq/Cerebras/SambaNova/GLM/Gemini 免费层等）故意不
# 在这里瞎编一个"这本来要收费"的数字——它们的免费层是真免费，不是折扣价。
ESTIMATED_PRICES: dict[str, PriceInfo] = {
    "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo": PriceInfo(0.88e-6, 0.88e-6, "estimated"),
    "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct": PriceInfo(0.90e-6, 0.90e-6, "estimated"),
    "deepinfra/meta-llama/Llama-3.3-70B-Instruct": PriceInfo(0.35e-6, 0.40e-6, "estimated"),
}


def _lookup_estimated(model: str) -> Optional[PriceInfo]:
    for prefix, price in ESTIMATED_PRICES.items():
        if model == prefix or model.startswith(prefix):
            return price
    return None


def compute_cost(
    model: str,
    response_obj: object,
    prompt_tokens: int,
    completion_tokens: int,
    api_base: Optional[str] = None,
) -> tuple[Optional[float], str]:
    """计算这次调用的花费。

    返回 (金额, 数据来源)：
      - 数据来源 "litellm"：用 litellm 内置价格库精确算出来的
      - 数据来源 "estimated"：用上面那个小表估算的，仪表盘应该标注"约"
      - 数据来源 "unknown"：两边都查不到，金额是 None，仪表盘应该显示
        "免费 / 暂无定价数据"，绝不能显示成 $0.00（那看起来像是"查过了、
        确实不要钱"，跟"根本没查到数据"是两回事，不能混为一谈）
    """
    if (api_base or "").rstrip("/") == "https://api.generalcompute.com/v1":
        upstream_model = model.removeprefix("openai/")
        official_price = GENERALCOMPUTE_PRICES.get(upstream_model)
        if official_price is not None:
            cost = (
                prompt_tokens * official_price.input_cost_per_token
                + completion_tokens * official_price.output_cost_per_token
            )
            return cost, official_price.source
        # 按量付费渠道不能套用同名模型在另一家托管商的价格。官方表尚未
        # 收录的新模型明确显示未知，避免给出看似精确但错误的金额。
        return None, "unknown"

    try:
        cost = litellm.completion_cost(completion_response=response_obj, model=model)
        if cost is not None and cost > 0:
            return float(cost), "litellm"
    except Exception as exc:
        logger.debug("[ai-gateway-matrix] litellm.completion_cost 查询失败（%s），尝试估算表", exc)

    price = _lookup_estimated(model)
    if price is not None:
        cost = prompt_tokens * price.input_cost_per_token + completion_tokens * price.output_cost_per_token
        return cost, "estimated"

    return None, "unknown"
