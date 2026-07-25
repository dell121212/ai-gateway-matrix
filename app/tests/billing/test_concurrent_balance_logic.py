"""Concurrency safety properties on pure balance math (no DB flake)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class Acc:
    balance: int
    reserved: int
    lock: asyncio.Lock

    @property
    def available(self) -> int:
        return self.balance - self.reserved


async def reserve(acc: Acc, amount: int) -> bool:
    async with acc.lock:
        if acc.available < amount:
            return False
        acc.reserved += amount
        return True


async def settle(acc: Acc, reserved: int, charge: int) -> None:
    async with acc.lock:
        release = min(reserved, acc.reserved)
        acc.reserved -= release
        charge = min(charge, acc.balance)
        acc.balance -= charge
        assert acc.balance >= 0
        assert acc.reserved >= 0


async def test_concurrent_reserves_never_negative():
    acc = Acc(balance=1_000_000, reserved=0, lock=asyncio.Lock())
    results = await asyncio.gather(*[reserve(acc, 300_000) for _ in range(10)])
    assert sum(1 for r in results if r) <= 3
    assert acc.available >= 0
    assert acc.reserved <= acc.balance


async def test_settle_idempotent_pattern():
    acc = Acc(balance=5_000_000, reserved=0, lock=asyncio.Lock())
    assert await reserve(acc, 1_000_000)
    await settle(acc, 1_000_000, 400_000)
    # second settle with same reserved should not go negative if reserved already 0
    await settle(acc, 1_000_000, 0)
    assert acc.balance == 4_600_000
    assert acc.reserved == 0
