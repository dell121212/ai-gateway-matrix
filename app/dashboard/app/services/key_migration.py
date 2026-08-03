"""Keep the portable client-key registry in sync with private API records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.ids import hash_token, key_prefix_fingerprint
from dashboard.app.core.logging import setup_logging
from dashboard.app.db.models import ApiKey, User
from dashboard.safe_files import locked_file, safe_rewrite

logger = setup_logging("private_api.key_migration")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_store(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 3, "keys": []}
    if not isinstance(data, dict):
        return {"version": 3, "keys": []}
    keys = data.get("keys")
    data["keys"] = keys if isinstance(keys, list) else []
    return data


def _write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_file(path):
        safe_rewrite(
            path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )


def recover_plaintext_keys(store_path: Path, *, valid_key_hashes: set[str]) -> int:
    """Recover trusted old plaintext keys into the current portable registry.

    Older builds scrubbed the current registry after importing hashes. Recovery is
    intentionally conservative: a backup secret is accepted only when its hash is
    already present in the private API database and it matches a current row by id
    or alias. The secret itself is never logged.
    """

    if not store_path.is_file() or not valid_key_hashes:
        return 0
    current = _read_store(store_path)
    current_keys = [item for item in current["keys"] if isinstance(item, dict)]
    if not current_keys:
        return 0

    candidates: list[dict[str, Any]] = []
    for backup in sorted(store_path.parent.glob("client-keys.pre-migration-*.bak")):
        candidates.extend(
            item
            for item in _read_store(backup).get("keys", [])
            if isinstance(item, dict) and item.get("key")
        )

    recovered = 0
    for item in current_keys:
        if item.get("key"):
            continue
        match = next(
            (
                candidate
                for candidate in candidates
                if (
                    (item.get("id") and candidate.get("id") == item.get("id"))
                    or (
                        item.get("alias")
                        and candidate.get("alias") == item.get("alias")
                    )
                )
                and hash_token(str(candidate["key"])) in valid_key_hashes
            ),
            None,
        )
        if match:
            item["key"] = str(match["key"])
            item["key_preview"] = item.get("key_preview") or _mask_key(item["key"])
            recovered += 1

    if recovered:
        current["keys"] = current_keys
        current["version"] = 3
        current["portable_secrets"] = True
        current["updated_at"] = _now()
        _write_store(store_path, current)
    return recovered


def litellm_token_record(item: dict[str, Any]) -> dict[str, Any]:
    """Create the minimal non-plaintext LiteLLM authorization representation."""

    full = str(item.get("key") or "")
    token = hash_token(full)
    models = item.get("models") if isinstance(item.get("models"), list) else ["auto-route"]
    return {
        "token": token,
        "key_name": str(item.get("key_preview") or _mask_key(full)),
        "key_alias": f"portable-{token[:16]}",
        "models": [str(model) for model in models if model],
        "rpm_limit": item.get("rpm_limit"),
        "tpm_limit": item.get("tpm_limit"),
    }


async def ensure_litellm_token_records(
    session: AsyncSession,
    keys: list[dict[str, Any]],
) -> int:
    """Ensure every portable plaintext client key can authenticate after restore."""

    exists = await session.scalar(
        text("SELECT to_regclass('public.\"LiteLLM_VerificationToken\"')")
    )
    if not exists:
        return 0
    inserted = 0
    statement = text(
        'INSERT INTO public."LiteLLM_VerificationToken" '
        '(token, key_name, key_alias, models, rpm_limit, tpm_limit, metadata, created_by, updated_by) '
        "VALUES (:token, :key_name, :key_alias, "
        "ARRAY(SELECT jsonb_array_elements_text(CAST(:models AS jsonb))), "
        ":rpm_limit, :tpm_limit, CAST(:metadata AS jsonb), :actor, :actor) "
        "ON CONFLICT (token) DO NOTHING RETURNING token"
    )
    for item in keys:
        if not isinstance(item, dict) or not item.get("key") or item.get("revoked"):
            continue
        record = litellm_token_record(item)
        result = await session.execute(
            statement,
            {
                **record,
                "models": json.dumps(record["models"], ensure_ascii=False),
                "metadata": json.dumps({"portable": True, "managed_by": "jiyi"}),
                "actor": "ai-gateway-matrix",
            },
        )
        inserted += int(result.scalar_one_or_none() is not None)
    return inserted


async def migrate_client_keys_file(
    session: AsyncSession,
    store_path: Path,
    *,
    default_user: User,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": _now(),
        "path": str(store_path),
        "found": 0,
        "imported": 0,
        "skipped": 0,
        "recovered": 0,
        "litellm_rehydrated": 0,
        "portable": False,
        "errors": [],
    }
    if not store_path.exists():
        report["message"] = "no client-keys.json"
        return report

    existing_hash_result = await session.execute(select(ApiKey.key_hash))
    existing_hashes = {value for value in existing_hash_result.scalars() if value}
    try:
        report["recovered"] = recover_plaintext_keys(
            store_path,
            valid_key_hashes=existing_hashes,
        )
    except OSError as exc:
        report["errors"].append(f"secret recovery failed: {exc}")

    data = _read_store(store_path)
    keys = data.get("keys", [])
    report["found"] = len(keys)

    portable_keys: list[dict[str, Any]] = []
    for item in keys:
        if not isinstance(item, dict):
            report["skipped"] += 1
            continue
        full = str(item.get("key") or "")
        if not full or item.get("revoked"):
            portable_keys.append(_portable_entry(item))
            report["skipped"] += 1
            continue
        prefix, key_hash = key_prefix_fingerprint(full)
        existing = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        if existing.scalar_one_or_none():
            portable_keys.append(_portable_entry(item, prefix=prefix))
            report["skipped"] += 1
            continue
        row = ApiKey(
            user_id=default_user.id,
            credit_account_id=None,
            litellm_token_hash=(
                item.get("id")
                if isinstance(item.get("id"), str) and len(str(item.get("id"))) == 64
                else hash_token(full)
            ),
            key_hash=key_hash,
            key_prefix=prefix,
            alias=str(item.get("alias") or ""),
            status="active",
            default_mode="agent-stream",
            allowed_models=item.get("models"),
            rpm_limit=item.get("rpm_limit"),
            tpm_limit=item.get("tpm_limit"),
        )
        session.add(row)
        portable_keys.append(_portable_entry(item, prefix=prefix))
        report["imported"] += 1
        logger.info("migrated key prefix=%s alias=%s", prefix, item.get("alias"))

    await session.commit()

    try:
        report["litellm_rehydrated"] = await ensure_litellm_token_records(
            session,
            portable_keys,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        report["errors"].append(f"LiteLLM key rehydration failed: {type(exc).__name__}")

    try:
        out = {
            **{key: value for key, value in data.items() if key != "keys"},
            "version": 3,
            "keys": portable_keys,
            "portable_secrets": True,
            "migrated_at": _now(),
        }
        _write_store(store_path, out)
        report["portable"] = True
    except OSError as exc:
        report["errors"].append(f"portable store write failed: {exc}")

    report["finished_at"] = _now()
    return report


def _mask_key(key: str) -> str:
    return "sk-…" if len(key) <= 10 else f"{key[:6]}…{key[-4:]}"


def _portable_entry(item: dict[str, Any], prefix: Optional[str] = None) -> dict[str, Any]:
    full = str(item.get("key") or "")
    return {
        "id": item.get("id"),
        "alias": item.get("alias"),
        "key": full,
        "key_preview": prefix or item.get("key_preview") or _mask_key(full),
        "models": item.get("models"),
        "rpm_limit": item.get("rpm_limit"),
        "tpm_limit": item.get("tpm_limit"),
        "expires_in": item.get("expires_in"),
        "created_at": item.get("created_at"),
        "revoked": item.get("revoked", False),
        "migrated": True,
    }
