import asyncio

from gateway import optimal_channels, usage_tracker


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def set(self, key, value, ex=None):
        self.calls.append((key, value, ex))
        return True


def test_expiring_optimal_flag_uses_atomic_set_ex():
    fake = FakeRedis()
    old_client, old_aioredis = usage_tracker._client, usage_tracker.aioredis
    usage_tracker._client = fake
    usage_tracker.aioredis = object()
    try:
        assert asyncio.run(
            optimal_channels.set_optimal("channel", expires_in_seconds=60)
        ) is True
        assert len(fake.calls) == 1
        assert fake.calls[0][2] == 60
    finally:
        usage_tracker._client = old_client
        usage_tracker.aioredis = old_aioredis
