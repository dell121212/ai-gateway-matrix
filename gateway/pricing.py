#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定价查询模块 (v2 — 规模折扣 + 价格缓存)
————————————————————————————————————————
顺序：
  1. 官方表（GeneralCompute 等已核对）
  2. litellm 内置价格库
  3. 具名估算表 ESTIMATED_PRICES
  4. 按模型名参数量档位的「市场等值」估算（小模型明显打折）
  5. 仍未知 → None

价格解析结果带进程内缓存（默认 24h），避免每次请求重复扫表/调 litellm。
「累计节省」对免费层使用市场等值价（含小模型折扣），不是官方账单。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, NamedTuple, Optional

logger = logging.getLogger("ai_gateway_matrix.pricing")

# 价格缓存 TTL（秒）
_PRICE_CACHE_TTL = float(
    __import__("os").environ.get("PRICING_CACHE_TTL_SECONDS", str(24 * 3600)) or str(24 * 3600)
)
_price_cache: dict[str, tuple["PriceInfo", float]] = {}


class PriceInfo(NamedTuple):
    input_cost_per_token: float
    output_cost_per_token: float
    source: str  # "official" | "litellm" | "estimated" | "size_band"
    label: str = ""  # 可选：7B 档 / 70B 档 等


# General Compute 官方价（2026-07 查询）
GENERALCOMPUTE_PRICES: dict[str, PriceInfo] = {
    "minimax-m2.7": PriceInfo(0.40e-6, 2.34e-6, "official", "GC 官方"),
    "minimax-m2.5": PriceInfo(0.20e-6, 1.17e-6, "official", "GC 官方"),
    "deepseek-v3.2": PriceInfo(3.00e-6, 4.50e-6, "official", "GC 官方"),
    "deepseek-v3.1": PriceInfo(3.00e-6, 4.50e-6, "official", "GC 官方"),
    "deepseek-v3.1-cb": PriceInfo(0.15e-6, 0.75e-6, "official", "GC 官方"),
    "llama-3.3-70b": PriceInfo(0.60e-6, 1.20e-6, "official", "GC 官方"),
    "llama-4-maverick-17b": PriceInfo(0.63e-6, 1.80e-6, "official", "GC 官方"),
    "gpt-oss-120b": PriceInfo(0.21e-6, 0.79e-6, "official", "GC 官方"),
    "gemma-3-12b-it": PriceInfo(0.04e-6, 0.13e-6, "official", "GC 官方"),
}

ESTIMATED_PRICES: dict[str, PriceInfo] = {
    "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo": PriceInfo(0.88e-6, 0.88e-6, "estimated"),
    "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct": PriceInfo(0.90e-6, 0.90e-6, "estimated"),
    "deepinfra/meta-llama/Llama-3.3-70B-Instruct": PriceInfo(0.35e-6, 0.40e-6, "estimated"),
}

# 市场等值：按参数量档位的 roughly USD / token（开源托管中位数量级）
# 小模型折扣刻意拉大，避免 7B 免费调用被算成「省了 GPT 级」的钱。
# (max_params_billions or None, label, input/1e6, output/1e6)
_SIZE_BANDS: list[tuple[Optional[float], str, float, float]] = [
    (3.0, "≤3B", 0.04e-6, 0.08e-6),
    (8.0, "3–8B", 0.08e-6, 0.16e-6),
    (15.0, "8–15B", 0.12e-6, 0.28e-6),
    (34.0, "15–34B", 0.28e-6, 0.55e-6),
    (70.0, "34–70B", 0.45e-6, 0.90e-6),
    (120.0, "70–120B", 0.70e-6, 1.40e-6),
    (None, "120B+", 1.20e-6, 2.40e-6),
]

_SIZE_RE = re.compile(
    r"(?:^|[^0-9])(\d+(?:\.\d+)?)\s*[bB](?:illion)?(?:[^a-z0-9]|$)",
)


def _cache_key(model: str, api_base: Optional[str]) -> str:
    return f"{(api_base or '').rstrip('/')}|{(model or '').strip().lower()}"


def clear_price_cache() -> None:
    _price_cache.clear()


def _lookup_estimated(model: str) -> Optional[PriceInfo]:
    for prefix, price in ESTIMATED_PRICES.items():
        if model == prefix or model.startswith(prefix):
            return price
    return None


def infer_model_size_billions(model: str) -> Optional[float]:
    """从模型名推断参数量（B）。无法判断返回 None。"""
    text = (model or "").lower()
    # 显式 Nb / N.Nb
    hits = [float(m.group(1)) for m in _SIZE_RE.finditer(text)]
    if hits:
        # 取最大合理值（避免匹配到版本号里的小数）
        candidates = [h for h in hits if 0.1 <= h <= 2000]
        if candidates:
            return max(candidates)
    # 语义别名（非严格参数量，作档位代理）
    if any(t in text for t in ("nano", "tiny", "0.5b")):
        return 0.5
    if any(t in text for t in ("mini", "small", "lite", "flash-lite", "ministral")):
        return 7.0
    if "flash" in text and "pro" not in text:
        return 20.0  # flash 类按中档偏下
    if any(t in text for t in ("medium", "plus")):
        return 32.0
    if any(t in text for t in ("large", "70b", "72b", "sonnet")):
        return 70.0
    if any(t in text for t in ("405b", "120b", "235b", "550b", "ultra", "opus", "reasoner", "r1")):
        return 200.0
    return None


def price_for_size_band(size_b: Optional[float]) -> PriceInfo:
    for max_b, label, inp, out in _SIZE_BANDS:
        if max_b is None or (size_b is not None and size_b <= max_b):
            if size_b is None and max_b is not None:
                continue
            if size_b is None and max_b is None:
                return PriceInfo(inp, out, "size_band", "未知规模·保守")
            return PriceInfo(inp, out, "size_band", f"{label} 市场等值")
    # size_b 很大
    inp, out = _SIZE_BANDS[-1][2], _SIZE_BANDS[-1][3]
    return PriceInfo(inp, out, "size_band", "120B+ 市场等值")


def resolve_price(model: str, api_base: Optional[str] = None) -> Optional[PriceInfo]:
    """解析单价（带缓存）。不依赖具体一次 completion 对象。"""
    model = (model or "").strip()
    if not model:
        return None
    key = _cache_key(model, api_base)
    now = time.monotonic()
    hit = _price_cache.get(key)
    if hit is not None:
        price, ts = hit
        if now - ts < _PRICE_CACHE_TTL:
            return price

    price: Optional[PriceInfo] = None

    if (api_base or "").rstrip("/") == "https://api.generalcompute.com/v1":
        upstream = model.removeprefix("openai/")
        price = GENERALCOMPUTE_PRICES.get(upstream)
        if price is None:
            # 付费渠道未收录：仍可按规模估，避免完全空白
            size = infer_model_size_billions(upstream)
            price = price_for_size_band(size)
            price = PriceInfo(price.input_cost_per_token, price.output_cost_per_token, "size_band", price.label)
    else:
        # litellm 模型价目（若环境有 litellm）
        try:
            import litellm  # type: ignore

            info = None
            if hasattr(litellm, "model_cost"):
                info = litellm.model_cost.get(model) or litellm.model_cost.get(
                    model.split("/", 1)[-1]
                )
            if isinstance(info, dict):
                inp = info.get("input_cost_per_token")
                out = info.get("output_cost_per_token")
                if inp is not None and out is not None:
                    price = PriceInfo(float(inp), float(out), "litellm", "litellm 价目")
        except Exception as exc:
            logger.debug("litellm 价目查询失败: %s", type(exc).__name__)

        if price is None:
            price = _lookup_estimated(model)

        if price is None:
            size = infer_model_size_billions(model)
            price = price_for_size_band(size)

    if price is not None:
        _price_cache[key] = (price, now)
    return price


def cost_from_tokens(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    api_base: Optional[str] = None,
) -> tuple[Optional[float], str, str]:
    """token 数 × 单价。返回 (金额, source, label)。"""
    price = resolve_price(model, api_base)
    if price is None:
        return None, "unknown", ""
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    cost = pt * price.input_cost_per_token + ct * price.output_cost_per_token
    return cost, price.source, price.label


def estimate_usage_spend(
    model: str,
    usage: dict[str, Any],
    api_base: Optional[str] = None,
) -> dict[str, Any]:
    """根据 usage 快照估算今日/累计花费（无官方余额时的本机计价）。

    若 usage 已有 day_cost/total_cost 则优先采用；否则用 total_tokens 按
    输入:输出 ≈ 1:1 拆分后乘单价（粗估，仪表盘标 ~）。
    """
    usage = usage or {}
    day_cost = usage.get("day_cost")
    total_cost = usage.get("total_cost")
    cost_source = str(usage.get("cost_source") or "unknown")
    price = resolve_price(model, api_base)
    label = price.label if price else ""

    def _from_tokens(n: int) -> Optional[float]:
        if not price or n <= 0:
            return None
        # 无拆分时：一半输入一半输出
        half = n / 2.0
        return half * price.input_cost_per_token + half * price.output_cost_per_token

    day_tokens = int(usage.get("day_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)

    if day_cost is None and day_tokens > 0:
        day_cost = _from_tokens(day_tokens)
        if day_cost is not None:
            cost_source = price.source if price else "estimated"
    if total_cost is None and total_tokens > 0:
        total_cost = _from_tokens(total_tokens)
        if total_cost is not None and cost_source == "unknown":
            cost_source = price.source if price else "estimated"

    approx = cost_source in {"estimated", "size_band"}
    return {
        "day_cost": day_cost,
        "total_cost": total_cost,
        "cost_source": cost_source,
        "price_label": label,
        "approx": approx,
        "input_per_mtok": (
            price.input_cost_per_token * 1_000_000 if price else None
        ),
        "output_per_mtok": (
            price.output_cost_per_token * 1_000_000 if price else None
        ),
        "size_billions": infer_model_size_billions(model),
    }


def market_value_for_savings(
    model: str,
    tokens: int,
    api_base: Optional[str] = None,
) -> tuple[Optional[float], str]:
    """免费层「市场等值节省」：强制走规模折扣档，避免小模型高估节省。"""
    if tokens <= 0:
        return 0.0, "size_band"
    size = infer_model_size_billions(model)
    # 先看是否有更准的 litellm/官方价，但小模型（≤15B）仍 cap 到规模档
    price = resolve_price(model, api_base)
    band = price_for_size_band(size)
    if price is not None and price.source in {"official", "litellm"}:
        if size is not None and size <= 15:
            # 取「官方/litellm」与「规模档」中更低者，防止小模型被标成大模型价
            inp = min(price.input_cost_per_token, band.input_cost_per_token)
            out = min(price.output_cost_per_token, band.output_cost_per_token)
            half = tokens / 2.0
            return half * inp + half * out, "size_band"
        half = tokens / 2.0
        return (
            half * price.input_cost_per_token + half * price.output_cost_per_token,
            price.source,
        )
    half = tokens / 2.0
    return (
        half * band.input_cost_per_token + half * band.output_cost_per_token,
        "size_band",
    )


def compute_cost(
    model: str,
    response_obj: object,
    prompt_tokens: int,
    completion_tokens: int,
    api_base: Optional[str] = None,
) -> tuple[Optional[float], str]:
    """计算这次调用的花费。

    返回 (金额, 数据来源)：
      official | litellm | estimated | size_band | unknown
    """
    if (api_base or "").rstrip("/") == "https://api.generalcompute.com/v1":
        upstream_model = model.removeprefix("openai/")
        official_price = GENERALCOMPUTE_PRICES.get(upstream_model)
        if official_price is not None:
            cost = (
                prompt_tokens * official_price.input_cost_per_token
                + completion_tokens * official_price.output_cost_per_token
            )
            _price_cache[_cache_key(model, api_base)] = (official_price, time.monotonic())
            return cost, official_price.source
        # 未收录官方价：按规模估
        cost, source, _ = cost_from_tokens(
            model, prompt_tokens, completion_tokens, api_base=api_base
        )
        return cost, source

    try:
        import litellm  # 延迟导入：dashboard 镜像可能无 litellm

        cost = litellm.completion_cost(completion_response=response_obj, model=model)
        if cost is not None and cost > 0:
            # 回填单价缓存（粗分摊）
            total = max(1, int(prompt_tokens) + int(completion_tokens))
            approx = PriceInfo(
                float(cost) / total,
                float(cost) / total,
                "litellm",
                "litellm completion_cost",
            )
            _price_cache[_cache_key(model, api_base)] = (approx, time.monotonic())
            return float(cost), "litellm"
    except Exception as exc:
        logger.debug(
            "[ai-gateway-matrix] litellm.completion_cost 查询失败（%s），尝试估算",
            type(exc).__name__,
        )

    cost, source, _ = cost_from_tokens(
        model, prompt_tokens, completion_tokens, api_base=api_base
    )
    return cost, source
