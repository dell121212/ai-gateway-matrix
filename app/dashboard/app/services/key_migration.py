"""Migrate plaintext client-keys.json to hashed DB records + scrub file."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.ids import hash_token, key_prefix_fingerprint
from dashboard.app.core.logging import setup_logging
from dashboard.app.db.models import ApiKey, CreditAccount, User

logger = setup_logging("private_api.key_migration")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def migrate_client_keys_file(
    session: AsyncSession,
    store_path: Path,
    *,
    default_user: User,
    default_account: CreditAccount,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": _now(),
        "path": str(store_path),
        "found": 0,
        "imported": 0,
        "skipped": 0,
        "scrubbed": False,
        "backup": None,
        "errors": [],
    }
    if not store_path.exists():
        report["message"] = "no client-keys.json"
        return report

    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report

    keys = data.get("keys") if isinstance(data, dict) else []
    if not isinstance(keys, list):
        keys = []
    report["found"] = len(keys)

    # backup with secrets before scrub
    backup = store_path.with_suffix(f".pre-migration-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.bak")
    try:
        shutil.copy2(store_path, backup)
        backup.chmod(0o600)
        report["backup"] = str(backup)
    except OSError as exc:
        report["errors"].append(f"backup failed: {exc}")

    scrubbed_keys: list[dict[str, Any]] = []
    for item in keys:
        if not isinstance(item, dict):
            report["skipped"] += 1
            continue
        full = item.get("key") or ""
        if not full or item.get("revoked"):
            scrubbed_keys.append(_scrub_entry(item))
            report["skipped"] += 1
            continue
        prefix, kh = key_prefix_fingerprint(full)
        existing = await session.execute(select(ApiKey).where(ApiKey.key_hash == kh))
        if existing.scalar_one_or_none():
            scrubbed_keys.append(_scrub_entry(item, prefix=prefix))
            report["skipped"] += 1
            continue
        row = ApiKey(
            user_id=default_user.id,
            credit_account_id=default_account.id,
            litellm_token_hash=item.get("id") if isinstance(item.get("id"), str) and len(str(item.get("id"))) == 64 else hash_token(full),
            key_hash=kh,
            key_prefix=prefix,
            alias=str(item.get("alias") or ""),
            status="active",
            default_mode="agent-stream",
            allowed_models=item.get("models"),
            rpm_limit=item.get("rpm_limit"),
            tpm_limit=item.get("tpm_limit"),
        )
        session.add(row)
        scrubbed_keys.append(_scrub_entry(item, prefix=prefix))
        report["imported"] += 1
        logger.info("migrated key prefix=%s alias=%s", prefix, item.get("alias"))

    await session.commit()

    # write scrubbed file (no full keys)
    try:
        out = {"version": 2, "keys": scrubbed_keys, "migrated_at": _now()}
        store_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        store_path.chmod(0o600)
        report["scrubbed"] = True
    except OSError as exc:
        report["errors"].append(f"scrub write failed: {exc}")

    report["finished_at"] = _now()
    return report


def _scrub_entry(item: dict[str, Any], prefix: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "alias": item.get("alias"),
        "key_preview": prefix or item.get("key_preview") or "sk-…",
        "models": item.get("models"),
        "rpm_limit": item.get("rpm_limit"),
        "tpm_limit": item.get("tpm_limit"),
        "expires_in": item.get("expires_in"),
        "created_at": item.get("created_at"),
        "revoked": item.get("revoked", False),
        "migrated": True,
        # deliberately no "key" field
    }
