#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用量追踪模块 (v2 — 新增 token/金额统计)
————————————————————————————————————————
给"仪表盘要看到各渠道余量进度、重置倒计时"这个需求提供数据来源。
v2 新增：累计 token 消耗量和预计花费金额（今天 + 从有记录以来的总量），
对应"仪表盘应该显示总消耗 token、预计的金额"这个需求。金额本身怎么算
（先查 litellm 内置价格库，查不到再估算，都查不到就是查不到）在
gateway/pricing.py 里，这个模块只负责把算好的数字存起来、按窗口累加。

设计取舍：
  · 复用 docker-compose.yml 里已经有的 redis 服务，而不是另起一个 SQLite
    文件——gateway/custom_router_hook.py（跑在 ai-gateway-matrix 容器里）和
    dashboard/backend.py（跑在单独的 dashboard 容器里）需要读写同一份
    用量数据，Redis 本来就是两个容器之间现成的共享存储，没必要再造一个。
  · 分钟计数使用 60 秒固定窗口；日统计按 USAGE_TIMEZONE 的自然日分桶，
    到当地次日零点自动切换。Redis TTL 直接提供“还有多久重置”。
    分钟窗口仍不是"滑动窗口"——跟 LiteLLM Router 内部
    Router 内部限流/冷却窗口不是同一套账本，仪表盘上看到的
    "这分钟用了几次"是一个近似值，用来给你一个大致的感觉，不代表
    Router 内部限流判断的精确依据。
  · 任何 Redis 操作失败（网络问题/没配 Redis/密码错）都静默吞掉、返回
    "不可用"的默认值——统计功能绝不能因为自己出错而影响真实请求的处理。
  · redis 包在当前 litellm 官方镜像里是否一定存在没有 100% 把握，
    所以 import 也包了一层容错，import 失败时整个模块退化成"什么都不做"。
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("ai_gateway_matrix.usage_tracker")

try:
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover - 镜像里没有 redis 包时的兜底
    aioredis = None
    logger.warning("[ai-gateway-matrix] 未找到 redis 包，用量追踪功能将被禁用（不影响正常路由）")

MINUTE_TTL_SECONDS = 60
DAY_TTL_SECONDS = 86400  # 兼容旧测试/外部引用；实际自然日 TTL 由 _day_window 计算。
# 累计统计不是按调用次数增长的明细表，每个渠道只占固定数量的 key；仍给
# 已删除/长期停用渠道设置一个可配置的闲置保留期，避免注册表长期变更后留下
# 永久孤儿键。活跃渠道每次写入都会续期，不改变其“从开始记录以来累计”的语义。
def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        logger.warning("[ai-gateway-matrix] %s 不是正整数，使用默认值 %d", name, default)
        return default


TOTAL_RETENTION_SECONDS = _positive_int_env("USAGE_TOTAL_RETENTION_DAYS", 400) * 86400
try:
    USAGE_TZ = ZoneInfo(os.environ.get("USAGE_TIMEZONE", "Asia/Shanghai"))
except ZoneInfoNotFoundError:
    logger.warning("[ai-gateway-matrix] USAGE_TIMEZONE 无效，回退 UTC")
    USAGE_TZ = ZoneInfo("UTC")
KEY_PREFIX = "gwmatrix:usage"

# 顺畅时段：按「本地小时」累计成功/失败，跨多天合并到 0–23 点位
SMOOTH_HOD_TTL_SECONDS = _positive_int_env("USAGE_SMOOTH_HOD_DAYS", 21) * 86400
SMOOTH_RECENT_TTL_SECONDS = 3 * 86400  # 近 3 天滚动样本
SMOOTH_MIN_SAMPLES_HOUR = _positive_int_env("USAGE_SMOOTH_MIN_SAMPLES_HOUR", 5)
SMOOTH_MIN_SAMPLES_RECENT = _positive_int_env("USAGE_SMOOTH_MIN_SAMPLES_RECENT", 6)
SMOOTH_GOOD_RATE = float(os.environ.get("USAGE_SMOOTH_GOOD_RATE", "0.75") or "0.75")
SMOOTH_BUSY_RATE = float(os.environ.get("USAGE_SMOOTH_BUSY_RATE", "0.45") or "0.45")

_client: Optional["aioredis.Redis"] = None


def _day_window(now: Optional[datetime] = None) -> tuple[str, int]:
    current = now.astimezone(USAGE_TZ) if now is not None else datetime.now(USAGE_TZ)
    tomorrow = (current + timedelta(days=1)).date()
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=USAGE_TZ)
    ttl = max(1, int((midnight - current).total_seconds()) + 60)
    return current.strftime("%Y%m%d"), ttl


def make_channel_id(model: str, api_base: Optional[str] = None, api_key: Optional[str] = None) -> str:
    """构造一个用于"用量统计查找"的稳定标识。

    仅用 model+api_base 不够——config.yaml 里 Mistral 的两个账号
    （MISTRAL_KEY_1 / MISTRAL_KEY_2）用的是完全相同的 model 字符串、
    都没设 api_base，只有 api_key 不同，这种情况下如果不把 api_key
    也纳入标识，两个账号的用量会被统计成同一份，仪表盘上也会显示成
    同一张卡片（这是实测中发现的真实 bug，不是假设）。

    这里不直接拼接真实的 api_key 值（那是密钥，不该出现在 Redis 的 key
    名字里，万一 Redis 被谁 dump 出来查看就是一次额外的泄漏面），而是
    取一个不可逆的短哈希（sha256 前 8 位）。gateway/custom_router_hook.py 在
    请求时能拿到解析后的真实 api_key，dashboard/channel_loader.py 读
    .env 文件时也能拿到同一个真实值，两边各自算出的哈希后缀是一致的，
    足够互相对上号；但没办法从哈希反推出真实的 key 是什么。

    注意：这个函数算出来的 id 只用于"用量统计查找"，不是仪表盘 UI 上
    每一行的持久化标识（那个用 channel_loader.py 里稳定的
    "model@api_base#env_var" 格式，不依赖账号是否已经配置了真实 key）。
    """
    base = api_base or "default"
    channel_id = f"{model}@{base}"
    if api_key:
        suffix = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
        channel_id = f"{channel_id}#{suffix}"
    return channel_id


def get_client():
    """公开的 Redis 客户端获取入口，供 gateway/optimal_channels.py 等其他模块复用同一个连接，
    不用各自另外维护一份连接逻辑。返回 None 表示 Redis 不可用（未安装 redis 包
    或连接信息没配置），调用方需要自行处理这种情况。"""
    return _get_client()


def _get_client():
    global _client
    if aioredis is None:
        return None
    if _client is None:
        host = os.environ.get("REDIS_HOST", "localhost")
        port = int(os.environ.get("REDIS_PORT", "6379") or "6379")
        _client = aioredis.Redis(
            host=host,
            port=port,
            password=os.environ.get("REDIS_PASSWORD") or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


async def record_call(
    channel_id: str,
    success: bool = True,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost: Optional[float] = None,
    cost_source: str = "unknown",
    latency_ms: Optional[float] = None,
    error_class: Optional[str] = None,
) -> None:
    """记录一次调用。只做统计用途，任何异常都静默忽略。

    v7 新增 token/金额参数：
      · day_tokens / day_cost：当前自然日累计，到当地次日零点切换
      · total_tokens / total_cost：从有记录以来的累计总量；活跃渠道写入时
        续期，停用超过 USAGE_TOTAL_RETENTION_DAYS 后清理孤儿键
      · last_cost_source：最近一次这个渠道的花费是用什么数据算出来的
        （"litellm" 精确 / "estimated" 估算 / "unknown" 查不到），
        仪表盘据此决定要不要在金额前面加"约"、或者干脆显示"暂无定价"
    """
    client = _get_client()
    if client is None:
        return

    status = "ok" if success else "fail"
    day_bucket, day_ttl = _day_window()
    minute_key = f"{KEY_PREFIX}:{channel_id}:minute"
    day_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}"
    day_status_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:{status}"
    day_tokens_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:tokens"
    total_tokens_key = f"{KEY_PREFIX}:{channel_id}:total:tokens"
    day_cost_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:cost"
    total_cost_key = f"{KEY_PREFIX}:{channel_id}:total:cost"
    cost_source_key = f"{KEY_PREFIX}:{channel_id}:last_cost_source"
    latency_total_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:latency_total_ms"
    latency_samples_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:latency_samples"
    last_error_key = f"{KEY_PREFIX}:{channel_id}:last_error_class"

    total_tokens = prompt_tokens + completion_tokens

    try:
        # 固定窗口计数：第一次 INCR 命中 1 时才设置 TTL，避免每次调用都把
        # 窗口往后推（那样就变成"只要一直有请求，永远不重置"了）。
        for key, ttl in ((minute_key, MINUTE_TTL_SECONDS), (day_key, day_ttl), (day_status_key, day_ttl)):
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, ttl)

        if total_tokens > 0:
            await client.incrby(day_tokens_key, total_tokens)
            # 不通过“累计值 == 本次增量”推断是否首次写入。如果之前
            # INCRBY 已成功但 EXPIRE 失败，key 会存在却没有 TTL；后续调用
            # 应能自动修复这种状态。这也与 day_cost_key 的处理保持一致。
            ttl = await client.ttl(day_tokens_key)
            if ttl is None or ttl < 0:
                await client.expire(day_tokens_key, day_ttl)
            await client.incrby(total_tokens_key, total_tokens)
            await client.expire(total_tokens_key, TOTAL_RETENTION_SECONDS)

        if cost is not None and cost > 0:
            await client.incrbyfloat(day_cost_key, cost)
            # incrbyfloat 不会自动告诉你这是不是第一次写入，这里单独查一下 TTL，
            # 没设过才补一个——多一次 Redis 调用，但避免"金额窗口"和"次数窗口"
            # 因为写入顺序不同而不同步重置。
            ttl = await client.ttl(day_cost_key)
            if ttl is None or ttl < 0:
                await client.expire(day_cost_key, day_ttl)
            await client.incrbyfloat(total_cost_key, cost)
            await client.expire(total_cost_key, TOTAL_RETENTION_SECONDS)

        if cost_source != "unknown":
            await client.set(cost_source_key, cost_source, ex=TOTAL_RETENTION_SECONDS)

        if latency_ms is not None and latency_ms >= 0:
            await client.incrbyfloat(latency_total_key, latency_ms)
            await client.incr(latency_samples_key)
            for key in (latency_total_key, latency_samples_key):
                ttl = await client.ttl(key)
                if ttl is None or ttl < 0:
                    await client.expire(key, day_ttl)

        if error_class:
            await client.set(last_error_key, error_class, ex=day_ttl)

        # 顺畅时段学习：按本地小时累加成功/失败（跨日合并到 hod 0–23）
        try:
            hour = datetime.now(USAGE_TZ).hour
            hod_ok = f"{KEY_PREFIX}:{channel_id}:smooth:hod:{hour}:ok"
            hod_fail = f"{KEY_PREFIX}:{channel_id}:smooth:hod:{hour}:fail"
            hod_key = hod_ok if success else hod_fail
            n = await client.incr(hod_key)
            if n == 1:
                await client.expire(hod_key, SMOOTH_HOD_TTL_SECONDS)
            else:
                ttl = await client.ttl(hod_key)
                if ttl is None or ttl < 0:
                    await client.expire(hod_key, SMOOTH_HOD_TTL_SECONDS)
            # 最近滚动窗口（约 3 天）：用于「当前是否顺畅」
            recent_ok = f"{KEY_PREFIX}:{channel_id}:smooth:recent:ok"
            recent_fail = f"{KEY_PREFIX}:{channel_id}:smooth:recent:fail"
            rkey = recent_ok if success else recent_fail
            rn = await client.incr(rkey)
            if rn == 1:
                await client.expire(rkey, SMOOTH_RECENT_TTL_SECONDS)
            else:
                rttl = await client.ttl(rkey)
                if rttl is None or rttl < 0:
                    await client.expire(rkey, SMOOTH_RECENT_TTL_SECONDS)
        except Exception:
            pass
    except Exception as exc:
        logger.debug("[ai-gateway-matrix] 用量记录失败（不影响真实请求）: %s", exc)


async def get_usage(channel_id: str) -> dict:
    """返回某个渠道当前的用量快照，供仪表盘展示。

    Redis 不可用/未配置时返回 available=False，前端应显示"暂无数据"
    而不是把 0 当成"确实是 0 次调用"来展示（那样会误导用户）。
    """
    client = _get_client()
    default = {
        "calls_this_minute": 0,
        "seconds_until_minute_reset": 0,
        "calls_today": 0,
        "seconds_until_day_reset": 0,
        "day_tokens": 0,
        "total_tokens": 0,
        "day_cost": None,
        "total_cost": None,
        "cost_source": "unknown",
        "successful_calls_today": 0,
        "failed_calls_today": 0,
        "average_latency_ms_today": None,
        "last_error_class": None,
        "available": False,
    }
    if client is None:
        return default

    day_bucket, _day_ttl = _day_window()
    minute_key = f"{KEY_PREFIX}:{channel_id}:minute"
    day_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}"
    day_tokens_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:tokens"
    total_tokens_key = f"{KEY_PREFIX}:{channel_id}:total:tokens"
    day_cost_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:cost"
    total_cost_key = f"{KEY_PREFIX}:{channel_id}:total:cost"
    cost_source_key = f"{KEY_PREFIX}:{channel_id}:last_cost_source"
    day_ok_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:ok"
    day_fail_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:fail"
    latency_total_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:latency_total_ms"
    latency_samples_key = f"{KEY_PREFIX}:{channel_id}:day:{day_bucket}:latency_samples"
    last_error_key = f"{KEY_PREFIX}:{channel_id}:last_error_class"

    try:
        pipe = client.pipeline()
        pipe.get(minute_key)
        pipe.ttl(minute_key)
        pipe.get(day_key)
        pipe.ttl(day_key)
        pipe.get(day_tokens_key)
        pipe.get(total_tokens_key)
        pipe.get(day_cost_key)
        pipe.get(total_cost_key)
        pipe.get(cost_source_key)
        pipe.get(day_ok_key)
        pipe.get(day_fail_key)
        pipe.get(latency_total_key)
        pipe.get(latency_samples_key)
        pipe.get(last_error_key)
        (
            minute_count, minute_ttl, day_count, day_ttl,
            day_tokens, total_tokens, day_cost, total_cost, cost_source,
            day_ok, day_fail, latency_total, latency_samples, last_error,
        ) = await pipe.execute()
        latency_count = int(latency_samples) if latency_samples else 0
        return {
            "calls_this_minute": int(minute_count) if minute_count else 0,
            "seconds_until_minute_reset": minute_ttl if minute_ttl and minute_ttl > 0 else 0,
            "calls_today": int(day_count) if day_count else 0,
            "seconds_until_day_reset": day_ttl if day_ttl and day_ttl > 0 else 0,
            "day_tokens": int(day_tokens) if day_tokens else 0,
            "total_tokens": int(total_tokens) if total_tokens else 0,
            "day_cost": float(day_cost) if day_cost else None,
            "total_cost": float(total_cost) if total_cost else None,
            "cost_source": cost_source or "unknown",
            "successful_calls_today": int(day_ok) if day_ok else 0,
            "failed_calls_today": int(day_fail) if day_fail else 0,
            "average_latency_ms_today": (
                float(latency_total) / latency_count if latency_count else None
            ),
            "last_error_class": last_error or None,
            "available": True,
        }
    except Exception as exc:
        logger.debug("[ai-gateway-matrix] 用量查询失败: %s", exc)
        return default


def _rate(ok: int, fail: int) -> Optional[float]:
    total = ok + fail
    if total <= 0:
        return None
    return ok / total


def _format_hour_ranges(hours: list[int]) -> str:
    """[2,3,4,14,15] → 「2–4 点、14–15 点」"""
    if not hours:
        return ""
    hours = sorted(set(h % 24 for h in hours))
    ranges: list[tuple[int, int]] = []
    start = prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        ranges.append((start, prev))
        start = prev = h
    ranges.append((start, prev))
    parts = []
    for a, b in ranges:
        if a == b:
            parts.append(f"{a} 点")
        else:
            parts.append(f"{a}–{b} 点")
    return "、".join(parts)


async def get_smoothness(channel_id: str) -> dict:
    """根据历史成功/失败，标注免费模型的顺畅/拥挤时段。

    使用一段时间后，各本地小时的成功率会显现；仪表盘据此打标，
    不改变路由硬逻辑（仍靠 cooldown/fallback），仅作观察与排序提示。
    """
    client = _get_client()
    default = {
        "available": False,
        "label": "学习中",
        "label_level": "unknown",
        "recent_success_rate": None,
        "recent_samples": 0,
        "best_hours": [],
        "worst_hours": [],
        "hint_zh": "使用一段时间后，将根据成功/失败标注顺畅时段",
        "by_hour": [],
        "current_hour": datetime.now(USAGE_TZ).hour,
        "timezone": str(USAGE_TZ),
    }
    if client is None or not channel_id:
        return default

    try:
        now_h = datetime.now(USAGE_TZ).hour
        pipe = client.pipeline()
        for h in range(24):
            pipe.get(f"{KEY_PREFIX}:{channel_id}:smooth:hod:{h}:ok")
            pipe.get(f"{KEY_PREFIX}:{channel_id}:smooth:hod:{h}:fail")
        pipe.get(f"{KEY_PREFIX}:{channel_id}:smooth:recent:ok")
        pipe.get(f"{KEY_PREFIX}:{channel_id}:smooth:recent:fail")
        vals = await pipe.execute()

        by_hour: list[dict] = []
        best: list[tuple[int, float, int]] = []
        worst: list[tuple[int, float, int]] = []
        for h in range(24):
            ok = int(vals[h * 2] or 0)
            fail = int(vals[h * 2 + 1] or 0)
            total = ok + fail
            rate = _rate(ok, fail)
            entry = {
                "hour": h,
                "ok": ok,
                "fail": fail,
                "samples": total,
                "success_rate": rate,
            }
            by_hour.append(entry)
            if total >= SMOOTH_MIN_SAMPLES_HOUR and rate is not None:
                best.append((h, rate, total))
                worst.append((h, rate, total))

        best_hours = [h for h, r, _ in sorted(best, key=lambda x: (-x[1], -x[2])) if r >= SMOOTH_GOOD_RATE][:8]
        worst_hours = [h for h, r, _ in sorted(worst, key=lambda x: (x[1], -x[2])) if r <= SMOOTH_BUSY_RATE][:8]

        recent_ok = int(vals[48] or 0)
        recent_fail = int(vals[49] or 0)
        recent_samples = recent_ok + recent_fail
        recent_rate = _rate(recent_ok, recent_fail)

        # 当前小时标签
        cur = by_hour[now_h]
        cur_rate = cur["success_rate"]
        cur_n = cur["samples"]

        if recent_samples < SMOOTH_MIN_SAMPLES_RECENT and cur_n < SMOOTH_MIN_SAMPLES_HOUR:
            label, level = "学习中", "unknown"
            hint = "样本还少，多用不一样的时段后会标注顺畅/拥挤"
        elif recent_rate is not None and recent_samples >= SMOOTH_MIN_SAMPLES_RECENT:
            if recent_rate >= SMOOTH_GOOD_RATE:
                label, level = "近期顺畅", "smooth"
            elif recent_rate <= SMOOTH_BUSY_RATE:
                label, level = "近期易拥挤", "busy"
            else:
                label, level = "一般", "mixed"
            hint_parts = [f"近 3 天成功率 {recent_rate * 100:.0f}%（{recent_samples} 次）"]
            if best_hours:
                hint_parts.append(f"较顺：{_format_hour_ranges(best_hours)}")
            if worst_hours:
                hint_parts.append(f"易堵：{_format_hour_ranges(worst_hours)}")
            if cur_n >= SMOOTH_MIN_SAMPLES_HOUR and cur_rate is not None:
                hint_parts.append(f"此刻（{now_h} 点）历史成功率 {cur_rate * 100:.0f}%")
            hint = " · ".join(hint_parts)
        elif cur_n >= SMOOTH_MIN_SAMPLES_HOUR and cur_rate is not None:
            if cur_rate >= SMOOTH_GOOD_RATE:
                label, level = f"{now_h} 点较顺", "smooth"
            elif cur_rate <= SMOOTH_BUSY_RATE:
                label, level = f"{now_h} 点易堵", "busy"
            else:
                label, level = f"{now_h} 点一般", "mixed"
            hint = f"该小时历史成功率 {cur_rate * 100:.0f}%（{cur_n} 次）"
            if best_hours:
                hint += f" · 全天较顺：{_format_hour_ranges(best_hours)}"
        else:
            label, level = "学习中", "unknown"
            hint = "继续使用后会按小时汇总顺畅时段"

        return {
            "available": True,
            "label": label,
            "label_level": level,
            "recent_success_rate": recent_rate,
            "recent_samples": recent_samples,
            "best_hours": best_hours,
            "worst_hours": worst_hours,
            "hint_zh": hint,
            "by_hour": by_hour,
            "current_hour": now_h,
            "timezone": str(USAGE_TZ),
        }
    except Exception as exc:
        logger.debug("[ai-gateway-matrix] 顺畅时段查询失败: %s", exc)
        return default


async def close() -> None:
    """关闭 Redis 连接（进程退出时调用，非必需但干净）。"""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
