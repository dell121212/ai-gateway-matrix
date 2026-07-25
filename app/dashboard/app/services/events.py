"""Redis Streams publisher / reader for real-time credit events."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dashboard.app.core.config import get_settings
from dashboard.app.core.logging import setup_logging

logger = setup_logging("private_api.events")

_redis = None
_redis_failed = False


async def get_redis():
    global _redis, _redis_failed
    if _redis_failed:
        return None
    if _redis is not None:
        return _redis
    try:
        import redis.asyncio as aioredis

        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await _redis.ping()
        return _redis
    except Exception as exc:
        logger.warning("Redis unavailable for events: %s", exc)
        _redis_failed = True
        _redis = None
        return None


def reset_redis_state() -> None:
    global _redis, _redis_failed
    _redis = None
    _redis_failed = False


def task_stream_key(task_id: str) -> str:
    return f"privateapi:task:{task_id}:events"


def user_stream_key(user_id: str) -> str:
    return f"privateapi:user:{user_id}:events"


async def publish_event(
    *,
    event: str,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Publish to Redis Streams. Never raises to caller critical path."""
    body = dict(payload or {})
    body["event"] = event
    body["timestamp"] = datetime.now(timezone.utc).isoformat()
    if task_id:
        body.setdefault("task_id", task_id)
    if user_id:
        body.setdefault("user_id", user_id)

    r = await get_redis()
    if r is None:
        return None
    settings = get_settings()
    fields = {"data": json.dumps(body, ensure_ascii=False)}
    event_id = None
    try:
        if task_id:
            event_id = await r.xadd(
                task_stream_key(task_id),
                fields,
                maxlen=settings.redis_stream_maxlen,
                approximate=True,
            )
        if user_id:
            await r.xadd(
                user_stream_key(user_id),
                fields,
                maxlen=settings.redis_stream_maxlen,
                approximate=True,
            )
        return event_id
    except Exception as exc:
        logger.warning("publish_event failed: %s", exc)
        return None


async def read_stream(
    stream_key: str,
    *,
    last_id: str = "0-0",
    count: int = 50,
    block_ms: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    r = await get_redis()
    if r is None:
        return []
    try:
        if block_ms > 0:
            result = await r.xread({stream_key: last_id}, count=count, block=block_ms)
        else:
            result = await r.xread({stream_key: last_id}, count=count)
        out: list[tuple[str, dict[str, Any]]] = []
        if not result:
            return out
        for _key, messages in result:
            for msg_id, fields in messages:
                raw = fields.get("data") or "{}"
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"raw": raw}
                out.append((msg_id, data))
        return out
    except Exception as exc:
        logger.warning("read_stream failed: %s", exc)
        return []


class EstimateThrottle:
    """Limit estimate event frequency per request."""

    def __init__(self, interval_ms: int = 300):
        self.interval_ms = interval_ms
        self._last: dict[str, float] = {}

    def allow(self, request_id: str) -> bool:
        now = time.time() * 1000
        last = self._last.get(request_id, 0)
        if now - last < self.interval_ms:
            return False
        self._last[request_id] = now
        return True


estimate_throttle = EstimateThrottle()
