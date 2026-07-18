#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按量付费 / 余额查询（官方接口）
————————————————————————————————————————
部分厂商提供余额/积分 API，可用 API Key 直接查询；
无官方余额接口的，回退展示本机 usage_tracker 估算消耗。

官方接口（需 Bearer API Key）：
  · DeepSeek    GET https://api.deepseek.com/user/balance
  · OpenRouter  GET https://openrouter.ai/api/v1/key
                GET https://openrouter.ai/api/v1/credits（部分账号）
  · SiliconFlow GET https://api.siliconflow.cn/v1/user/info
  · Moonshot    GET https://api.moonshot.cn/v1/users/me/balance
                （国际站 api.moonshot.ai）

缓存：按 env_var 缓存 45s，避免 /api/channels 轮询打爆上游。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("ai_gateway_matrix.balance_probe")

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 45.0

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=12.0, trust_env=False)
    return _client


def _cache_get(key: str) -> Optional[dict[str, Any]]:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.monotonic() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return dict(val)


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE[key] = (time.monotonic(), dict(val))


def _money(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _get_json(url: str, api_key: str) -> tuple[Optional[dict], Optional[str], int]:
    try:
        resp = await _get_client().get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        return None, f"网络错误：{exc}", 0
    try:
        body = resp.json()
    except Exception:
        body = None
    if resp.status_code >= 400:
        msg = ""
        if isinstance(body, dict):
            msg = str(body.get("error") or body.get("message") or body.get("detail") or body)[:200]
        else:
            msg = (resp.text or "")[:200]
        return body if isinstance(body, dict) else None, msg or f"HTTP {resp.status_code}", resp.status_code
    return body if isinstance(body, dict) else None, None, resp.status_code


async def fetch_deepseek(api_key: str) -> dict[str, Any]:
    body, err, status = await _get_json("https://api.deepseek.com/user/balance", api_key)
    if err and not body:
        return {"ok": False, "provider": "DeepSeek", "message": err, "http_status": status}
    infos = (body or {}).get("balance_infos") or []
    lines = []
    primary = None
    for info in infos:
        if not isinstance(info, dict):
            continue
        cur = info.get("currency") or "CNY"
        total = info.get("total_balance")
        granted = info.get("granted_balance")
        topped = info.get("topped_up_balance")
        lines.append({
            "currency": cur,
            "total_balance": total,
            "granted_balance": granted,
            "topped_up_balance": topped,
        })
        if primary is None:
            primary = _money(total)
    return {
        "ok": True,
        "provider": "DeepSeek",
        "source": "official",
        "endpoint": "GET /user/balance",
        "docs_url": "https://api-docs.deepseek.com/api/get-user-balance/",
        "is_available": (body or {}).get("is_available"),
        "currency": (lines[0]["currency"] if lines else "CNY"),
        "balance": primary,
        "details": lines,
        "summary_zh": (
            f"余额 {lines[0]['currency']} {lines[0]['total_balance']}"
            if lines else "已查询，无明细"
        ),
        "http_status": status,
    }


async def fetch_openrouter(api_key: str) -> dict[str, Any]:
    # Key 维度：limit_remaining / usage
    body, err, status = await _get_json("https://openrouter.ai/api/v1/key", api_key)
    if err and not body:
        return {"ok": False, "provider": "OpenRouter", "message": err, "http_status": status}
    data = (body or {}).get("data") if isinstance((body or {}).get("data"), dict) else (body or {})
    limit_remaining = _money(data.get("limit_remaining"))
    usage = _money(data.get("usage"))
    limit = _money(data.get("limit"))

    # 账户积分（部分 key 可用）
    credits_body, _, _ = await _get_json("https://openrouter.ai/api/v1/credits", api_key)
    credits_data = {}
    if isinstance(credits_body, dict):
        credits_data = credits_body.get("data") if isinstance(credits_body.get("data"), dict) else credits_body
    total_credits = _money(credits_data.get("total_credits"))
    total_usage = _money(credits_data.get("total_usage"))
    account_balance = None
    if total_credits is not None and total_usage is not None:
        account_balance = total_credits - total_usage

    balance = limit_remaining if limit_remaining is not None else account_balance
    parts = []
    if balance is not None:
        parts.append(f"剩余 ${balance:.4f}")
    if usage is not None:
        parts.append(f"本 Key 已用 ${usage:.4f}")
    if limit is not None:
        parts.append(f"Key 上限 ${limit:.4f}")
    if account_balance is not None and limit_remaining is None:
        parts.append(f"账户余额 ${account_balance:.4f}")

    return {
        "ok": True,
        "provider": "OpenRouter",
        "source": "official",
        "endpoint": "GET /api/v1/key (+ /credits)",
        "docs_url": "https://openrouter.ai/docs/api-reference/limits",
        "currency": "USD",
        "balance": balance,
        "usage": usage,
        "limit": limit,
        "total_credits": total_credits,
        "total_usage": total_usage,
        "account_balance": account_balance,
        "summary_zh": " · ".join(parts) if parts else "已查询",
        "http_status": status,
    }


async def fetch_siliconflow(api_key: str) -> dict[str, Any]:
    body, err, status = await _get_json("https://api.siliconflow.cn/v1/user/info", api_key)
    if err and not body:
        return {"ok": False, "provider": "SiliconFlow", "message": err, "http_status": status}
    data = (body or {}).get("data") if isinstance((body or {}).get("data"), dict) else (body or {})
    balance = _money(data.get("balance") or data.get("totalBalance"))
    charge = _money(data.get("chargeBalance"))
    total = _money(data.get("totalBalance"))
    return {
        "ok": True,
        "provider": "SiliconFlow",
        "source": "official",
        "endpoint": "GET /v1/user/info",
        "docs_url": "https://docs.siliconflow.com/en/api-reference/userinfo/get-user-info",
        "currency": "CNY",
        "balance": balance,
        "charge_balance": charge,
        "total_balance": total,
        "status": data.get("status"),
        "summary_zh": (
            f"可用 ¥{data.get('balance', '—')} · 充值 ¥{data.get('chargeBalance', '—')} · 总计 ¥{data.get('totalBalance', '—')}"
        ),
        "http_status": status,
    }


async def fetch_moonshot(api_key: str) -> dict[str, Any]:
    # 国内站优先，失败再试国际站
    last_err = None
    for base in ("https://api.moonshot.cn", "https://api.moonshot.ai"):
        body, err, status = await _get_json(f"{base}/v1/users/me/balance", api_key)
        if err and not body:
            last_err = err
            continue
        data = (body or {}).get("data") if isinstance((body or {}).get("data"), dict) else (body or {})
        # 兼容多种字段名
        available = _money(
            data.get("available_balance")
            or data.get("balance")
            or data.get("available")
        )
        voucher = _money(data.get("voucher_balance") or data.get("voucher"))
        cash = _money(data.get("cash_balance") or data.get("cash"))
        return {
            "ok": True,
            "provider": "Moonshot",
            "source": "official",
            "endpoint": f"GET {base}/v1/users/me/balance",
            "docs_url": "https://platform.kimi.ai/docs/api/balance",
            "currency": data.get("currency") or "CNY",
            "balance": available,
            "voucher_balance": voucher,
            "cash_balance": cash,
            "raw": {k: data.get(k) for k in list(data.keys())[:12]} if isinstance(data, dict) else {},
            "summary_zh": (
                f"可用 {available if available is not None else '—'} · "
                f"代金券 {voucher if voucher is not None else '—'} · "
                f"现金 {cash if cash is not None else '—'}"
            ),
            "http_status": status,
        }
    return {
        "ok": False,
        "provider": "Moonshot",
        "message": last_err or "余额接口不可用",
        "http_status": 0,
    }


# env_var → fetcher
_FETCHERS = {
    "DEEPSEEK_API_KEY": fetch_deepseek,
    "OPENROUTER_API_KEY": fetch_openrouter,
    "SILICONFLOW_API_KEY": fetch_siliconflow,
    "MOONSHOT_API_KEY": fetch_moonshot,
}


def supports_official_balance(env_var: Optional[str]) -> bool:
    return bool(env_var and env_var in _FETCHERS)


async def fetch_balance_for_channel(channel: dict) -> dict[str, Any]:
    """
    为渠道附加 billing 信息：
      · official: 官方余额/积分
      · local:    本机 usage 估算消耗（若有）
    """
    env_var = channel.get("env_var") or ""
    usage = channel.get("usage") or {}
    local_spend = {
        "day_cost": usage.get("day_cost"),
        "total_cost": usage.get("total_cost"),
        "cost_source": usage.get("cost_source"),
        "day_tokens": usage.get("day_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "available": bool(usage.get("available")),
    }

    base: dict[str, Any] = {
        "billing": channel.get("billing") or "free_or_trial",
        "supports_official_balance": supports_official_balance(env_var),
        "local_spend": local_spend,
        "official": None,
    }

    if not channel.get("is_configured"):
        base["message"] = "未配置 Key，无法查询余额"
        return base

    # 本机消耗摘要（所有渠道）
    if local_spend.get("available") and (
        local_spend.get("day_cost") is not None or local_spend.get("total_cost") is not None
    ):
        approx = "~" if local_spend.get("cost_source") == "estimated" else ""
        day = local_spend.get("day_cost")
        tot = local_spend.get("total_cost")
        base["local_spend_summary_zh"] = (
            f"本机统计：今日 {approx}${day if day is not None else '—'} · "
            f"累计 {approx}${tot if tot is not None else '—'}"
        )

    fetcher = _FETCHERS.get(env_var)
    if not fetcher:
        if channel.get("billing") == "paid":
            base["message"] = (
                "该按量渠道暂无对接官方余额接口；请看本机消耗统计或厂商控制台。"
            )
        else:
            base["message"] = "免费/试用层以 RPM·RPD 等限额表为主；本机有消耗时显示估算花费。"
        return base

    cache_key = f"{env_var}:{(channel.get('masked_key') or '')}"
    cached = _cache_get(cache_key)
    if cached is not None:
        base["official"] = cached
        base["cached"] = True
        return base

    # 读真实 key
    from . import channel_loader

    env_values = channel_loader.read_env_file()
    api_key = (env_values.get(env_var) or "").strip()
    if not api_key or api_key.startswith("dummy-"):
        base["message"] = "Key 无效"
        return base

    try:
        official = await fetcher(api_key)
    except Exception as exc:
        logger.exception("余额查询异常 %s", env_var)
        official = {
            "ok": False,
            "provider": env_var,
            "message": f"{type(exc).__name__}: {exc}",
        }

    _cache_set(cache_key, official)
    base["official"] = official
    base["cached"] = False
    return base
