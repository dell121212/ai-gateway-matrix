#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"限时优先"渠道管理 (v1)
————————————————————————————————————————
对应你的需求："若里面存在[被标记的]api则都走它，用于处理快过期的api和活动api"。

设计：
  · 通过仪表盘把某个渠道标记为"限时优先"，可以选填一个过期时间。
  · gateway/custom_router_hook.py 在处理【非敏感内容】的请求时，如果存在至少一个
    仍然有效（没过期、这分钟还有 RPM 余量）的限时优先渠道，就把请求
    无条件路由过去——不管这个请求本来该分类到弱/中/强哪一档，
    这是你要的"都走它"。
  · 敏感内容检测依然是最高优先级，不会因为某个渠道被标记为限时优先，
    就把密钥/密码这类内容发给它——哪怕它是官方直营渠道也一样，
    "限时优先"只是一个成本/额度优化，不应该影响隐私保护的判断。
  · 多个渠道同时被标记时，按"最快过期的先烧"排序；没设过期时间的
    （比如"这就是个活动送的额度，用完为止，没有硬性过期日"）排在最后，
    因为没有紧迫性。
  · 用 Redis key 的 TTL 直接实现"过期自动失效"——不需要额外写一个
    定时任务去清理过期标记，Redis 到期自己就把 key 删了。

跟 gateway/usage_tracker.py 的关系：这里只负责"哪些渠道被标记了、还有多久过期"，
不管"这个渠道这分钟还有没有 RPM 余量"——那部分复用 gateway/usage_tracker.py
已有的用量数据，两个模块各管一段，职责不重叠。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from . import usage_tracker  # 复用同一个 Redis 客户端获取逻辑

logger = logging.getLogger("ai_gateway_matrix.optimal_channels")

KEY_PREFIX = "gwmatrix:optimal"


async def set_optimal(display_id: str, reason: str = "", expires_in_seconds: Optional[int] = None) -> bool:
    """标记一个渠道为"限时优先"。

    display_id 用 channel_ids.make_display_id() 算出来的稳定标识
    （不是 usage_key，那是另一套给用量查询用的哈希标识）。
    expires_in_seconds 为 None 表示"没有已知的硬性过期时间，手动取消为止"。
    """
    client = usage_tracker.get_client()
    if client is None:
        return False
    key = f"{KEY_PREFIX}:{display_id}"
    payload = json.dumps({
        "display_id": display_id,
        "reason": reason,
        "flagged_at": int(time.time()),
        "expires_at": (int(time.time()) + expires_in_seconds) if expires_in_seconds else None,
    })
    try:
        # SET EX 是单条 Redis 命令，避免 SET 成功、EXPIRE 失败后留下永久标记。
        if expires_in_seconds:
            await client.set(key, payload, ex=expires_in_seconds)
        else:
            await client.set(key, payload)
        return True
    except Exception as exc:
        logger.warning("[ai-gateway-matrix] 标记限时优先渠道失败: %s", exc)
        return False


async def clear_optimal(display_id: str) -> bool:
    """取消一个渠道的"限时优先"标记。"""
    client = usage_tracker.get_client()
    if client is None:
        return False
    try:
        await client.delete(f"{KEY_PREFIX}:{display_id}")
        return True
    except Exception as exc:
        logger.warning("[ai-gateway-matrix] 取消限时优先标记失败: %s", exc)
        return False


async def list_optimal() -> list[dict]:
    """列出所有当前有效的"限时优先"标记，按最快过期排序（没有过期时间的排最后）。"""
    client = usage_tracker.get_client()
    if client is None:
        return []
    results = []
    try:
        async for key in client.scan_iter(match=f"{KEY_PREFIX}:*"):
            try:
                raw = await client.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                ttl = await client.ttl(key)
                data["seconds_until_expiry"] = ttl if ttl and ttl > 0 else None
                results.append(data)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("[ai-gateway-matrix] 列出限时优先渠道失败: %s", exc)
        return []

    results.sort(key=lambda d: (d["seconds_until_expiry"] is None, d["seconds_until_expiry"] or 0))
    return results
