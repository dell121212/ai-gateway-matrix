import asyncio
import fnmatch
from datetime import datetime

from dashboard.quota_catalog import MONTH, build_rate_limits
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

    async def get(self, key):
        return self.values.get(key)

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def scan_iter(self, match="*", count=10):
        for key in sorted(self.values):
            if fnmatch.fnmatch(key, match):
                yield key


def test_same_env_shares_usage_key_across_models():
    """Mistral 等同 Key 多模型必须共用账本，不能按模型拆开。"""
    k1 = usage_tracker.make_usage_key(
        "mistral/ministral-8b-latest", None, "secret-a", env_var="MISTRAL_KEY_1",
    )
    k2 = usage_tracker.make_usage_key(
        "mistral/mistral-large-latest", None, "secret-a", env_var="MISTRAL_KEY_1",
    )
    k3 = usage_tracker.make_usage_key(
        "mistral/ministral-8b-latest", None, "secret-b", env_var="MISTRAL_KEY_3",
    )
    assert k1 == k2 == "cred:MISTRAL_KEY_1"
    assert k3 == "cred:MISTRAL_KEY_3"
    assert k1 != k3


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
        month_bucket, _month_ttl = usage_tracker._month_window()
        assert fake.values[f"{prefix}:minute:tokens"] == 10
        assert fake.values[f"{prefix}:month:{month_bucket}:tokens"] == 10
        token_ttl = fake.ttls[f"{prefix}:day:{bucket}:tokens"]
        assert 1 <= token_ttl <= usage_tracker.DAY_TTL_SECONDS + 60
        assert fake.ttls[f"{prefix}:day:{bucket}:cost"] == token_ttl
        assert fake.ttls[f"{prefix}:total:tokens"] == usage_tracker.TOTAL_RETENTION_SECONDS
        assert fake.ttls[f"{prefix}:total:cost"] == usage_tracker.TOTAL_RETENTION_SECONDS
        assert fake.ttls[f"{prefix}:last_cost_source"] == usage_tracker.TOTAL_RETENTION_SECONDS
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis


def test_first_month_write_migrates_existing_total_once():
    fake = FakeRedis()
    prefix = f"{usage_tracker.KEY_PREFIX}:channel"
    fake.values[f"{prefix}:total:tokens"] = 100
    old_client, old_aioredis = usage_tracker._client, usage_tracker.aioredis
    usage_tracker._client = fake
    usage_tracker.aioredis = object()
    try:
        asyncio.run(usage_tracker.record_call(
            "channel", prompt_tokens=7, completion_tokens=3,
        ))
        month_bucket, _month_ttl = usage_tracker._month_window()
        assert fake.values[f"{prefix}:month:{month_bucket}:tokens"] == 110
        assert fake.values[f"{prefix}:total:tokens"] == 110
        assert fake.values[f"{prefix}:migration:month_tokens_v1"] == "1"
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis


def test_duplicate_success_event_is_only_counted_once():
    fake = FakeRedis()
    old_client, old_aioredis = usage_tracker._client, usage_tracker.aioredis
    usage_tracker._client = fake
    usage_tracker.aioredis = object()
    try:
        for _ in range(2):
            asyncio.run(usage_tracker.record_call(
                "channel", prompt_tokens=7, completion_tokens=3,
                event_id="response-123",
            ))
        prefix = f"{usage_tracker.KEY_PREFIX}:channel"
        assert fake.values[f"{prefix}:total:tokens"] == 10
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis


def test_global_usage_includes_legacy_and_removed_channel_ledgers():
    fake = FakeRedis()
    bucket, _ttl = usage_tracker._day_window()
    prefix = usage_tracker.KEY_PREFIX
    fake.values.update(
        {
            f"{prefix}:cred:CURRENT:total:tokens": 100,
            f"{prefix}:legacy-model@base#hash:total:tokens": 200,
            f"{prefix}:removed-model@base#hash:total:tokens": 300,
            f"{prefix}:cred:CURRENT:day:{bucket}:tokens": 10,
            f"{prefix}:legacy-model@base#hash:day:{bucket}:tokens": 20,
            f"{prefix}:cred:CURRENT:total:cost": "0.25",
            f"{prefix}:removed-model@base#hash:total:cost": "0.75",
        }
    )
    old_client, old_aioredis = usage_tracker._client, usage_tracker.aioredis
    usage_tracker._client = fake
    usage_tracker.aioredis = object()
    try:
        result = asyncio.run(usage_tracker.get_global_usage())
        assert result["available"] is True
        assert result["total_tokens"] == 600
        assert result["day_tokens"] == 30
        assert result["total_cost"] == 1.0
        assert result["ledger_count"] == 3
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis


def test_day_window_uses_local_midnight():
    now = datetime(2026, 7, 11, 23, 59, 30, tzinfo=usage_tracker.USAGE_TZ)
    bucket, ttl = usage_tracker._day_window(now)
    assert bucket == "20260711"
    assert 30 <= ttl <= 90


def test_month_window_uses_local_calendar_month():
    now = datetime(2026, 7, 31, 23, 59, 30, tzinfo=usage_tracker.USAGE_TZ)
    bucket, ttl = usage_tracker._month_window(now)
    assert bucket == "202607"
    assert 30 <= ttl <= 90


def test_token_windows_use_matching_usage_buckets():
    usage = {
        "available": True,
        "minute_tokens": 120,
        "day_tokens": 340,
        "month_tokens": 560,
        "month_tokens_estimated": True,
        "total_tokens": 780,
        "seconds_until_minute_reset": 12,
        "seconds_until_day_reset": 34,
        "seconds_until_month_reset": 56,
    }
    resettable = build_rate_limits("MISTRAL_KEY_1", usage=usage, quota_kind="resettable")
    by_id = {item["id"]: item for item in resettable["windows"]}
    assert by_id["tpm"]["used"] == 120
    assert by_id["tpm"]["seconds_until_reset"] == 12
    assert by_id["month_tokens"]["used"] == 560
    assert by_id["month_tokens"]["usage_estimated"] is True
    assert by_id["month_tokens"]["seconds_until_reset"] == 56

    once = build_rate_limits("SAMBANOVA_API_KEY", usage=usage, quota_kind="once")
    token_windows = [item for item in once["windows"] if item["metric"] == "tokens"]
    assert token_windows
    assert all(item["used"] == 780 for item in token_windows if item["window_sec"] == MONTH)
    assert all(item["window_label"] == "累计" for item in token_windows if item["window_sec"] == MONTH)
