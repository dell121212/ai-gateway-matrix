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
