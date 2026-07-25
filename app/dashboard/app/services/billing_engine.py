"""Atomic credit reserve / settle / refund with append-only ledger."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from dashboard.app.core.config import get_settings
from dashboard.app.core.logging import setup_logging
from dashboard.app.db.models import CreditAccount, CreditLedger

logger = setup_logging("private_api.billing")


class BillingError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class InsufficientCredits(BillingError):
    def __init__(self, message: str = "积分余额不足"):
        super().__init__("insufficient_credits", message, 402)


class IdempotentReplay(Exception):
    """Same idempotency key already posted — return previous result."""

    def __init__(self, entry: CreditLedger):
        self.entry = entry


@dataclass
class AccountSnapshot:
    account_id: uuid.UUID
    balance_microcredits: int
    reserved_microcredits: int
    available_microcredits: int
    version: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_account_for_update(session: AsyncSession, account_id: uuid.UUID) -> CreditAccount:
    result = await session.execute(
        select(CreditAccount).where(CreditAccount.id == account_id).with_for_update()
    )
    acc = result.scalar_one_or_none()
    if acc is None:
        raise BillingError("account_not_found", "积分账户不存在", 404)
    if acc.status != "active":
        raise BillingError("account_inactive", "积分账户不可用", 403)
    return acc


async def find_by_idempotency(session: AsyncSession, key: str) -> Optional[CreditLedger]:
    result = await session.execute(
        select(CreditLedger).where(CreditLedger.idempotency_key == key)
    )
    return result.scalar_one_or_none()


def snapshot(acc: CreditAccount) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=acc.id,
        balance_microcredits=int(acc.balance_microcredits),
        reserved_microcredits=int(acc.reserved_microcredits),
        available_microcredits=int(acc.balance_microcredits) - int(acc.reserved_microcredits),
        version=int(acc.version),
    )


async def _post_ledger(
    session: AsyncSession,
    acc: CreditAccount,
    *,
    transaction_type: str,
    delta: int,
    idempotency_key: str,
    reason: str = "",
    task_id: Optional[uuid.UUID] = None,
    client_request_id: Optional[uuid.UUID] = None,
    attempt_id: Optional[uuid.UUID] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> CreditLedger:
    existing = await find_by_idempotency(session, idempotency_key)
    if existing is not None:
        raise IdempotentReplay(existing)

    acc.version = int(acc.version) + 1
    acc.updated_at = _utcnow()
    entry = CreditLedger(
        id=uuid.uuid4(),
        account_id=acc.id,
        task_id=task_id,
        client_request_id=client_request_id,
        attempt_id=attempt_id,
        transaction_type=transaction_type,
        delta_microcredits=int(delta),
        balance_after_microcredits=int(acc.balance_microcredits),
        reserved_after_microcredits=int(acc.reserved_microcredits),
        idempotency_key=idempotency_key,
        status="posted",
        reason=reason or "",
        metadata_json=metadata,
        created_at=_utcnow(),
    )
    session.add(entry)
    await session.flush()
    return entry


async def reserve_credits(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount_microcredits: int,
    *,
    idempotency_key: str,
    task_id: Optional[uuid.UUID] = None,
    client_request_id: Optional[uuid.UUID] = None,
    reason: str = "pre_reserve",
) -> tuple[Optional[CreditLedger], AccountSnapshot]:
    if amount_microcredits < 0:
        raise BillingError("invalid_amount", "冻结积分不能为负")
    if amount_microcredits == 0:
        acc = await get_account_for_update(session, account_id)
        # 零金额不写流水，避免噪声；调用方必须接受 entry 可能为 None
        return None, snapshot(acc)

    existing = await find_by_idempotency(session, idempotency_key)
    if existing is not None:
        acc = await get_account_for_update(session, account_id)
        return existing, snapshot(acc)

    acc = await get_account_for_update(session, account_id)
    available = int(acc.balance_microcredits) - int(acc.reserved_microcredits)
    if available < amount_microcredits:
        raise InsufficientCredits(
            f"可用积分不足：需要 {amount_microcredits} microcredits，可用 {available}"
        )
    acc.reserved_microcredits = int(acc.reserved_microcredits) + int(amount_microcredits)
    entry = await _post_ledger(
        session,
        acc,
        transaction_type="reserve",
        delta=0,  # balance unchanged; reserved increases
        idempotency_key=idempotency_key,
        reason=reason,
        task_id=task_id,
        client_request_id=client_request_id,
        metadata={"reserved_delta": amount_microcredits},
    )
    # fix reserved_after already set from acc
    return entry, snapshot(acc)


async def release_reservation(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount_microcredits: int,
    *,
    idempotency_key: str,
    task_id: Optional[uuid.UUID] = None,
    client_request_id: Optional[uuid.UUID] = None,
    reason: str = "release_unused",
) -> tuple[CreditLedger, AccountSnapshot]:
    existing = await find_by_idempotency(session, idempotency_key)
    if existing is not None:
        acc = await get_account_for_update(session, account_id)
        return existing, snapshot(acc)

    acc = await get_account_for_update(session, account_id)
    release = min(int(amount_microcredits), int(acc.reserved_microcredits))
    acc.reserved_microcredits = int(acc.reserved_microcredits) - release
    entry = await _post_ledger(
        session,
        acc,
        transaction_type="release",
        delta=0,
        idempotency_key=idempotency_key,
        reason=reason,
        task_id=task_id,
        client_request_id=client_request_id,
        metadata={"released": release},
    )
    return entry, snapshot(acc)


async def settle_from_reservation(
    session: AsyncSession,
    account_id: uuid.UUID,
    reserved_amount: int,
    settle_amount: int,
    *,
    idempotency_key: str,
    task_id: Optional[uuid.UUID] = None,
    client_request_id: Optional[uuid.UUID] = None,
    attempt_id: Optional[uuid.UUID] = None,
    reason: str = "settle",
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[CreditLedger, AccountSnapshot]:
    """Charge settle_amount from balance, release reservation of reserved_amount."""
    existing = await find_by_idempotency(session, idempotency_key)
    if existing is not None:
        acc = await get_account_for_update(session, account_id)
        return existing, snapshot(acc)

    acc = await get_account_for_update(session, account_id)
    settle_amount = max(0, int(settle_amount))
    reserved_amount = max(0, int(reserved_amount))

    # Release reserved first
    release = min(reserved_amount, int(acc.reserved_microcredits))
    acc.reserved_microcredits = int(acc.reserved_microcredits) - release

    if settle_amount > int(acc.balance_microcredits):
        # Should not happen if reserve was correct; clamp to balance (no negative)
        settle_amount = int(acc.balance_microcredits)

    acc.balance_microcredits = int(acc.balance_microcredits) - settle_amount
    if acc.balance_microcredits < 0:
        raise BillingError("negative_balance", "内部错误：余额不能为负", 500)

    meta = dict(metadata or {})
    meta.update({"reserved_released": release, "settled": settle_amount})
    entry = await _post_ledger(
        session,
        acc,
        transaction_type="settle",
        delta=-settle_amount,
        idempotency_key=idempotency_key,
        reason=reason,
        task_id=task_id,
        client_request_id=client_request_id,
        attempt_id=attempt_id,
        metadata=meta,
    )
    return entry, snapshot(acc)


async def grant_or_adjust(
    session: AsyncSession,
    account_id: uuid.UUID,
    delta_microcredits: int,
    *,
    idempotency_key: str,
    transaction_type: str = "adjust",
    reason: str,
    actor: str = "",
) -> tuple[CreditLedger, AccountSnapshot]:
    existing = await find_by_idempotency(session, idempotency_key)
    if existing is not None:
        acc = await get_account_for_update(session, account_id)
        return existing, snapshot(acc)

    acc = await get_account_for_update(session, account_id)
    new_bal = int(acc.balance_microcredits) + int(delta_microcredits)
    if new_bal < 0:
        raise InsufficientCredits("调整后余额不能为负")
    # Also ensure available doesn't go weird — reserved stays
    if new_bal < int(acc.reserved_microcredits):
        raise BillingError("invalid_adjust", "调整后余额不能小于已冻结积分")
    acc.balance_microcredits = new_bal
    entry = await _post_ledger(
        session,
        acc,
        transaction_type=transaction_type,
        delta=int(delta_microcredits),
        idempotency_key=idempotency_key,
        reason=reason,
        metadata={"actor": actor},
    )
    return entry, snapshot(acc)


async def refund(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount_microcredits: int,
    *,
    idempotency_key: str,
    reason: str,
    client_request_id: Optional[uuid.UUID] = None,
    task_id: Optional[uuid.UUID] = None,
) -> tuple[CreditLedger, AccountSnapshot]:
    return await grant_or_adjust(
        session,
        account_id,
        abs(int(amount_microcredits)),
        idempotency_key=idempotency_key,
        transaction_type="refund",
        reason=reason,
    )


async def reverse_entry(
    session: AsyncSession,
    original: CreditLedger,
    *,
    idempotency_key: str,
    reason: str,
) -> tuple[CreditLedger, AccountSnapshot]:
    """Append reverse transaction — never edit history."""
    return await grant_or_adjust(
        session,
        original.account_id,
        -int(original.delta_microcredits),
        idempotency_key=idempotency_key,
        transaction_type="reverse",
        reason=reason or f"reverse:{original.id}",
    )


def billing_fail_open() -> bool:
    return get_settings().billing_fail_mode == "open"
