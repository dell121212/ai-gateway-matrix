"""Bootstrap schema, admin user, model prices and key migration."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.app.core.config import get_settings
from dashboard.app.core.logging import setup_logging
from dashboard.app.core.security import hash_password
from dashboard.app.db.models import PricingVersion, User
from dashboard.app.db.session import ensure_schema, get_session_factory
from dashboard.app.services.key_migration import migrate_client_keys_file

logger = setup_logging("private_api.bootstrap")


async def bootstrap_all() -> dict:
    report: dict = {
        "schema": False,
        "database_restore": None,
        "redis_restore": None,
        "admin": None,
        "pricing": False,
        "key_migration": None,
    }
    try:
        await ensure_schema()
        report["schema"] = True
    except Exception as exc:
        logger.error("schema bootstrap failed: %s", exc)
        report["schema_error"] = str(exc)
        return report

    snapshot = Path(
        os.environ.get("JIYI_DATABASE_SNAPSHOT")
        or Path(__file__).resolve().parents[3]
        / "state"
        / "private-api-export.json.gz"
    )
    try:
        from scripts.jiyi_database import restore_snapshot_if_empty

        report["database_restore"] = await restore_snapshot_if_empty(
            get_settings().async_database_url(),
            snapshot,
        )
    except Exception as exc:
        logger.error("jiyi database restore skipped/failed: %s", exc)
        report["database_restore"] = f"error:{exc}"

    redis_snapshot = Path(
        os.environ.get("JIYI_REDIS_SNAPSHOT")
        or Path(__file__).resolve().parents[3]
        / "state"
        / "gateway-redis-export.json.gz"
    )
    try:
        from scripts.jiyi_redis import restore_snapshot_missing

        settings = get_settings()
        report["redis_restore"] = await restore_snapshot_missing(
            settings.redis_host,
            settings.redis_port,
            settings.redis_password,
            redis_snapshot,
        )
    except Exception as exc:
        logger.error("jiyi redis restore skipped/failed: %s", exc)
        report["redis_restore"] = f"error:{exc}"

    factory = get_session_factory()
    async with factory() as session:
        report["admin"] = await ensure_admin(session)
        report["pricing"] = await ensure_default_pricing(session)
        settings = get_settings()
        store = settings.client_keys_store or str(
            Path(__file__).resolve().parents[3] / "state" / "client-keys.json"
        )
        admin = await _get_admin(session)
        if admin:
            report["key_migration"] = await migrate_client_keys_file(
                session, Path(store), default_user=admin
            )
        await session.commit()
    return report


async def _get_admin(session: AsyncSession) -> User | None:
    r = await session.execute(select(User).where(User.role == "super_admin").limit(1))
    return r.scalar_one_or_none()


async def ensure_admin(session: AsyncSession) -> dict:
    settings = get_settings()
    existing = await session.execute(select(User).where(User.username == settings.bootstrap_admin_username))
    user = existing.scalar_one_or_none()
    if user:
        return {"created": False, "username": user.username}

    password = settings.bootstrap_admin_password or os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(16)
        generated = True
        logger.warning(
            "BOOTSTRAP_ADMIN_PASSWORD not set; generated temporary password for %s (check logs once)",
            settings.bootstrap_admin_username,
        )
        # Do not log the password in production — write to state file once
        try:
            p = Path(__file__).resolve().parents[3] / "state" / "bootstrap-admin.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                f"username={settings.bootstrap_admin_username}\npassword={password}\n"
                f"CHANGE_IMMEDIATELY=1\n",
                encoding="utf-8",
            )
            p.chmod(0o600)
        except OSError:
            pass

    user = User(
        username=settings.bootstrap_admin_username,
        password_hash=hash_password(password),
        display_name="Administrator",
        role="super_admin",
        status="active",
    )
    session.add(user)
    await session.flush()
    return {
        "created": True,
        "username": user.username,
        "password_generated": generated,
        "password_file": "state/bootstrap-admin.txt" if generated else None,
    }


async def ensure_default_pricing(session: AsyncSession) -> bool:
    r = await session.execute(select(PricingVersion).limit(1))
    if r.scalar_one_or_none():
        return False
    defaults = [
        PricingVersion(
            provider="*",
            model_pattern="*",
            input_price=500_000,  # $0.50 / 1M
            output_price=1_500_000,
            cached_input_price=50_000,
            reasoning_price=1_500_000,
            billing_basis="market_value",
            credit_multiplier="1.0",
            minimum_microcredits=1,
            source="bootstrap",
            version=1,
            created_by="system",
        ),
        PricingVersion(
            provider="*",
            model_pattern="*gpt-4o*",
            input_price=2_500_000,
            output_price=10_000_000,
            billing_basis="market_value",
            credit_multiplier="1.0",
            minimum_microcredits=1,
            source="bootstrap",
            version=1,
            created_by="system",
        ),
    ]
    for row in defaults:
        session.add(row)
    await session.flush()
    return True
