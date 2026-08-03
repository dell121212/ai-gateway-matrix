import asyncio

from gateway import quota_manager, usage_tracker


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def eval(self, _script, key_count, *values):
        keys = values[:key_count]
        args = values[key_count:]
        for index, key in enumerate(keys):
            limit = int(args[index * 3])
            amount = int(args[index * 3 + 1])
            if int(self.values.get(key, 0)) + amount > limit:
                return 0
        for index, key in enumerate(keys):
            self.values[key] = int(self.values.get(key, 0)) + int(args[index * 3 + 1])
        return 1


class FakeCooldownRedis:
    def __init__(self):
        self.ttls = {}
        self.values = {}

    async def ttl(self, key):
        return self.ttls.get(key, -2)

    async def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex


def test_credential_limit_is_shared_across_channels():
    fake = FakeRedis()
    old_client, old_aioredis = usage_tracker._client, usage_tracker.aioredis
    usage_tracker._client = fake
    usage_tracker.aioredis = object()
    channel_a = {
        "display_id": "a", "env_var": "SHARED_KEY",
        "rpm_limit": 10, "credential_rpm_limit": 2,
    }
    channel_b = {
        "display_id": "b", "env_var": "SHARED_KEY",
        "rpm_limit": 10, "credential_rpm_limit": 2,
    }
    try:
        assert asyncio.run(quota_manager.reserve_channel(channel_a)) is True
        assert asyncio.run(quota_manager.reserve_channel(channel_b)) is True
        assert asyncio.run(quota_manager.reserve_channel(channel_a)) is False
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis


def test_paid_channel_fails_closed_without_redis(monkeypatch):
    monkeypatch.setattr(quota_manager.usage_tracker, "get_client", lambda: None)
    channel = {
        "display_id": "general-compute",
        "env_var": "GENERALCOMPUTE_API_KEY",
        "billing": "paid",
        "rpm_limit": 20,
        "credential_rpm_limit": 20,
    }

    assert asyncio.run(quota_manager.reserve_channel(channel)) is False


def test_long_cooldown_is_never_shortened_by_later_failure(monkeypatch):
    fake = FakeCooldownRedis()
    monkeypatch.setattr(quota_manager.usage_tracker, "get_client", lambda: fake)

    asyncio.run(quota_manager.mark_failure("gemini-pro", "quota_zero"))
    asyncio.run(quota_manager.mark_failure("gemini-pro", "rate_limit"))

    key = next(iter(fake.ttls))
    assert fake.ttls[key] == 86400
    assert fake.values[key] == "quota_zero"


def test_quality_failure_has_ten_minute_cooldown(monkeypatch):
    fake = FakeCooldownRedis()
    monkeypatch.setattr(quota_manager.usage_tracker, "get_client", lambda: fake)

    asyncio.run(quota_manager.mark_failure("qwen-7b", "quality_error"))

    key = next(iter(fake.ttls))
    assert fake.ttls[key] == 600


def test_candidate_selection_preserves_brain_configured_order(monkeypatch):
    """智脑已经决定档位；档内必须尊重用户配置的顺序。"""
    first = {"display_id": "configured-first"}
    second = {"display_id": "configured-second"}

    async def no_runtime_reranking(_display_id):
        raise AssertionError("档内选择不应再次读取统计并重排智脑候选")

    async def no_cooldown(_display_id):
        return 0

    async def reserve(_channel):
        return True

    monkeypatch.setattr(quota_manager.usage_tracker, "get_usage", no_runtime_reranking)
    monkeypatch.setattr(quota_manager, "cooldown_remaining", no_cooldown)
    monkeypatch.setattr(quota_manager, "reserve_channel", reserve)

    selected = asyncio.run(quota_manager.choose_and_reserve([first, second]))

    assert selected is first
