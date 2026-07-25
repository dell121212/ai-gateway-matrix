#!/usr/bin/env python3
"""基于 Redis 的凭据级/渠道级原子额度预占与被动熔断。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable, Optional

from . import usage_tracker

logger = logging.getLogger("ai_gateway_matrix.quota")
KEY_PREFIX = "gwmatrix:routing"

_RESERVE_SCRIPT = """
for i = 1, #KEYS do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  local limit = tonumber(ARGV[(i - 1) * 3 + 1])
  local amount = tonumber(ARGV[(i - 1) * 3 + 2])
  if limit > 0 and current + amount > limit then
    return 0
  end
end
for i = 1, #KEYS do
  local amount = tonumber(ARGV[(i - 1) * 3 + 2])
  local window = tonumber(ARGV[(i - 1) * 3 + 3])
  redis.call('INCRBY', KEYS[i], amount)
  if redis.call('TTL', KEYS[i]) < 0 then
    redis.call('EXPIRE', KEYS[i], window)
  end
end
return 1
"""


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


async def reserve_limits(
    items: list[tuple[str, int, int]], amount: int = 1, *, fail_closed: bool = False
) -> bool:
    """原子预占共享窗口；付费渠道在 Redis 故障时拒绝放行。"""
    client = usage_tracker.get_client()
    if client is None:
        return not fail_closed
    usable = [
        (key, int(limit), int(window))
        for key, limit, window in items
        if isinstance(limit, int) and limit > 0 and isinstance(window, int) and window > 0
    ]
    if not usable:
        return True
    keys = [f"{KEY_PREFIX}:quota:{_safe_id(key)}:{window}" for key, _, window in usable]
    args: list[int] = []
    for _, limit, window in usable:
        args.extend((limit, amount, window))
    try:
        result = await client.eval(_RESERVE_SCRIPT, len(keys), *keys, *args)
        return bool(result)
    except Exception as exc:
        behavior = "拒绝付费渠道调用" if fail_closed else "免费渠道降级为不拦截"
        logger.warning("额度原子预占失败，%s: %s", behavior, type(exc).__name__)
        return not fail_closed


async def reserve_channel(channel: dict[str, Any]) -> bool:
    display_id = str(channel.get("display_id", ""))
    env_var = str(channel.get("env_var") or "no-credential")
    # 同一 Key 挂多模型/多档时：只按凭据级限额预占，避免弱/中/强各自
    # 一套 channel RPM 把同一 Mistral Key 拆成多份假额度。
    shared = bool(channel.get("env_var")) and channel.get("shared_credential_quota", True)
    limits: list[tuple[str, int, int]] = []
    if not shared:
        limits.append(
            (f"channel:{display_id}:rpm", channel.get("rpm_limit") or 0, 60),
        )
    cred_rpm = channel.get("credential_rpm_limit") or channel.get("rpm_limit") or 0
    limits.append((f"credential:{env_var}:rpm", cred_rpm, 60))
    for item in channel.get("additional_limits") or []:
        if not isinstance(item, dict):
            continue
        scope = item.get("scope", "credential")
        identity = display_id if scope == "channel" else env_var
        limits.append((
            f"{scope}:{identity}:{item.get('type', 'requests')}",
            item.get("limit") or 0,
            item.get("window_seconds") or 0,
        ))
    return await reserve_limits(
        limits,
        fail_closed=channel.get("billing") == "paid",
    )


async def cooldown_remaining(display_id: str) -> int:
    client = usage_tracker.get_client()
    if client is None:
        return 0
    try:
        ttl = await client.ttl(f"{KEY_PREFIX}:cooldown:{_safe_id(display_id)}")
        return ttl if isinstance(ttl, int) and ttl > 0 else 0
    except Exception:
        return 0


async def mark_failure(display_id: str, error_class: str) -> None:
    client = usage_tracker.get_client()
    if client is None:
        return
    # rate_limit / 拥挤（含智谱「访问量过大」）：只短回避，稍后必须再试。
    # 切勿用小时级 TTL，否则免费层一抖就「再也用不上」。
    ttl_by_class = {
        "auth_error": 86400,       # 真·坏 Key：长冷却
        "quota_zero": 86400,       # 明确 limit=0：至少等到下一个日窗口再探
        "quota_probe": 86400,      # 已知零额度模型：先熔断，成功回调再自动清除
        "quota_error": 21600,      # 日/月额度类：6 小时后再探
        "quality_error": 600,      # 模板泄漏/乱码：临时隔离该推理端点
        "rate_limit": 8,           # 临时拥挤：约 8 秒后再进候选
        "timeout": 15,
        "router_exhausted": 10,
        "unknown": 12,
    }
    ttl = ttl_by_class.get(error_class, 20)
    key = f"{KEY_PREFIX}:cooldown:{_safe_id(display_id)}"
    try:
        # 后续的短错误不能覆盖已存在的长熔断（例如 quota_zero 后又收到
        # cooldown_active）。只延长，不缩短。
        current_ttl = await client.ttl(key)
        if isinstance(current_ttl, int) and current_ttl >= ttl:
            return
        await client.set(key, error_class, ex=ttl)
    except Exception:
        pass


async def mark_success(display_id: str) -> None:
    client = usage_tracker.get_client()
    if client is None:
        return
    try:
        await client.delete(f"{KEY_PREFIX}:cooldown:{_safe_id(display_id)}")
    except Exception:
        pass


async def choose_and_reserve(candidates: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for channel in candidates:
        if await cooldown_remaining(str(channel["display_id"])) > 0:
            continue
        if await reserve_channel(channel):
            return channel
    return None
