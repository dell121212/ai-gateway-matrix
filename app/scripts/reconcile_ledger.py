#!/usr/bin/env python3
"""Reconcile credit_accounts vs credit_ledger. Supports --check and --repair-safe."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

from sqlalchemy import select, text

from dashboard.app.db.models import CreditAccount, CreditLedger
from dashboard.app.db.session import get_session_factory, ensure_schema


async def run(check: bool, repair_safe: bool) -> int:
    await ensure_schema()
    factory = get_session_factory()
    issues = []
    async with factory() as session:
        accounts = (await session.execute(select(CreditAccount))).scalars().all()
        for acc in accounts:
            rows = (
                await session.execute(
                    select(CreditLedger)
                    .where(CreditLedger.account_id == acc.id)
                    .order_by(CreditLedger.created_at.asc())
                )
            ).scalars().all()
            # reconstruct balance from settle/grant/adjust/refund/reverse deltas only
            bal = 0
            # We don't know opening balance without first grant; use first ledger balance_after if present
            if rows:
                # verify last balance_after matches account
                last = rows[-1]
                if int(last.balance_after_microcredits) != int(acc.balance_microcredits):
                    issues.append(
                        {
                            "account_id": str(acc.id),
                            "type": "balance_mismatch",
                            "ledger_last": int(last.balance_after_microcredits),
                            "account": int(acc.balance_microcredits),
                        }
                    )
                if int(last.reserved_after_microcredits) != int(acc.reserved_microcredits):
                    issues.append(
                        {
                            "account_id": str(acc.id),
                            "type": "reserved_mismatch",
                            "ledger_last": int(last.reserved_after_microcredits),
                            "account": int(acc.reserved_microcredits),
                        }
                    )
            if acc.balance_microcredits < 0:
                issues.append({"account_id": str(acc.id), "type": "negative_balance"})
            if acc.reserved_microcredits < 0:
                issues.append({"account_id": str(acc.id), "type": "negative_reserved"})
            if acc.reserved_microcredits > acc.balance_microcredits:
                issues.append({"account_id": str(acc.id), "type": "reserved_gt_balance"})

        # duplicate idempotency (should be impossible with unique constraint)
        dup = await session.execute(
            text(
                "SELECT idempotency_key, COUNT(*) c FROM private_api.credit_ledger "
                "GROUP BY idempotency_key HAVING COUNT(*) > 1"
            )
        )
        for row in dup:
            issues.append({"type": "dup_idempotency", "key": row[0], "count": row[1]})

        if repair_safe:
            for acc in accounts:
                if acc.reserved_microcredits < 0:
                    acc.reserved_microcredits = 0
                # never rewrite ledger history; only clear impossible reserved
            await session.commit()

    print(f"issues={len(issues)}")
    for i in issues[:50]:
        print(i)
    if check and issues:
        return 1
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--repair-safe", action="store_true")
    args = p.parse_args()
    rc = asyncio.run(run(args.check, args.repair_safe))
    sys.exit(rc)


if __name__ == "__main__":
    main()
