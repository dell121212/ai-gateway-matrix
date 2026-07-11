import asyncio
from datetime import datetime

from gateway import usage_tracker


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def incrby(self, key, amount):
        self.values[key] = int(self.values.get(key, 0)) + amount
        return self.values[key]

    async def incrbyfloat(self, key, amount):
        self.values[key] = float(self.values.get(key, 0)) + amount
        return self.values[key]

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True


def test_day_ttl_is_repaired_and_total_keys_have_retention():
    fake = FakeRedis()
    old_client, old_aioredis = usage_tracker._client, usage_tracker.aioredis
    usage_tracker._client = fake
    usage_tracker.aioredis = object()
    try:
        asyncio.run(usage_tracker.record_call(
            "channel", prompt_tokens=7, completion_tokens=3,
            cost=0.01, cost_source="litellm",
        ))
        prefix = f"{usage_tracker.KEY_PREFIX}:channel"
        bucket, _expected_ttl = usage_tracker._day_window()
        token_ttl = fake.ttls[f"{prefix}:day:{bucket}:tokens"]
        assert 1 <= token_ttl <= usage_tracker.DAY_TTL_SECONDS + 60
        assert fake.ttls[f"{prefix}:day:{bucket}:cost"] == token_ttl
        assert fake.ttls[f"{prefix}:total:tokens"] == usage_tracker.TOTAL_RETENTION_SECONDS
        assert fake.ttls[f"{prefix}:total:cost"] == usage_tracker.TOTAL_RETENTION_SECONDS
        assert fake.ttls[f"{prefix}:last_cost_source"] == usage_tracker.TOTAL_RETENTION_SECONDS
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis


def test_day_window_uses_local_midnight():
    now = datetime(2026, 7, 11, 23, 59, 30, tzinfo=usage_tracker.USAGE_TZ)
    bucket, ttl = usage_tracker._day_window(now)
    assert bucket == "20260711"
    assert 30 <= ttl <= 90
