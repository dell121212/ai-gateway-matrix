#!/usr/bin/env python3
"""PostgreSQL 用户操作快照：供 jiyi.txt 自动导出与新环境一次性恢复。"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path
from typing import Any

SNAPSHOT_FORMAT = "AGM-PRIVATE-API-V2"
LEGACY_SNAPSHOT_FORMAT = "AGM-PRIVATE-API-V1"
SCHEMA = "private_api"

# 按外键依赖排序；V2 保存身份、调用事实、成本、聚合与配额快照。
TABLES = (
    "users",
    "sessions",
    "api_keys",
    "pricing_versions",
    "tasks",
    "client_requests",
    "llm_attempts",
    "usage_aggregates",
    "quota_snapshots",
    "audit_logs",
)

# LiteLLM keeps client-key authorization outside the private_api schema.  It is
# part of the same portable user state: without it, a copied plaintext key can
# be shown in the UI but will not authenticate on a freshly restored gateway.
PUBLIC_TABLES = (
    ("litellm_verification_tokens", "public", "LiteLLM_VerificationToken", "token"),
)

LEGACY_TABLES = ("credit_accounts", "credit_ledger")
LEGACY_RESTORE_TABLES = (
    "users", "credit_accounts", "sessions", "api_keys", "pricing_versions",
    "tasks", "client_requests", "llm_attempts", "credit_ledger",
    "usage_aggregates", "quota_snapshots", "audit_logs",
)


def _portable_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if table == "api_keys":
        # V2 no longer carries the account table, so legacy foreign keys and
        # point budgets must not survive into a clean restore.
        result["credit_account_id"] = None
        result["daily_budget_microcredits"] = None
        result["request_budget_microcredits"] = None
    if table == "litellm_verification_tokens":
        # These optional references point to commercial/team entities that this
        # personal gateway does not use. Clearing them makes the auth row
        # independently restorable while preserving its token, limits and model
        # policy.
        for field in (
            "budget_id",
            "organization_id",
            "object_permission_id",
            "project_id",
        ):
            result[field] = None
    return result


def normalize_database_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgres+asyncpg://",
        "postgresql://",
        1,
    )


def _encode_snapshot(
    tables: dict[str, list[dict[str, Any]]], *, snapshot_format: str = SNAPSHOT_FORMAT
) -> bytes:
    raw = json.dumps(
        {"format": snapshot_format, "tables": tables},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _decode_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    if payload.get("format") not in {SNAPSHOT_FORMAT, LEGACY_SNAPSHOT_FORMAT} or not isinstance(
        payload.get("tables"),
        dict,
    ):
        raise ValueError(f"未识别的数据库快照格式: {path}")
    return payload


def _write_if_changed(path: Path, content: bytes) -> bool:
    try:
        if path.is_file() and path.read_bytes() == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return True


async def export_snapshot(database_url: str, destination: Path) -> bool:
    """在一个可重复读事务中导出全部 private_api 表；无变化时不碰 mtime。"""

    import asyncpg

    connection = await asyncpg.connect(normalize_database_url(database_url))
    try:
        tables: dict[str, list[dict[str, Any]]] = {}
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            schema_exists = await connection.fetchval(
                "SELECT to_regnamespace($1) IS NOT NULL",
                SCHEMA,
            )
            for table in TABLES:
                if not schema_exists:
                    tables[table] = []
                    continue
                exists = await connection.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL",
                    f"{SCHEMA}.{table}",
                )
                if not exists:
                    tables[table] = []
                    continue
                rows = await connection.fetch(
                    f'SELECT row_to_json(t)::text AS row '
                    f'FROM "{SCHEMA}"."{table}" AS t ORDER BY id'
                )
                tables[table] = [
                    _portable_row(table, json.loads(row["row"])) for row in rows
                ]
            for snapshot_key, schema, table, primary_key in PUBLIC_TABLES:
                exists = await connection.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL",
                    f'{schema}."{table}"',
                )
                if not exists:
                    tables[snapshot_key] = []
                    continue
                rows = await connection.fetch(
                    f'SELECT row_to_json(t)::text AS row '
                    f'FROM "{schema}"."{table}" AS t ORDER BY "{primary_key}"'
                )
                tables[snapshot_key] = [
                    _portable_row(snapshot_key, json.loads(row["row"])) for row in rows
                ]
        return _write_if_changed(destination, _encode_snapshot(tables))
    finally:
        await connection.close()


async def restore_snapshot_if_empty(database_url: str, source: Path) -> str:
    """仅向全新的 private_api schema 恢复，绝不覆盖或合并已有数据。"""

    if not source.is_file():
        return "missing"
    payload = _decode_snapshot(source)

    import asyncpg

    connection = await asyncpg.connect(normalize_database_url(database_url))
    try:
        async with connection.transaction():
            existing = 0
            for table in TABLES:
                exists = await connection.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL",
                    f"{SCHEMA}.{table}",
                )
                if exists:
                    existing += int(
                        await connection.fetchval(
                            f'SELECT count(*) FROM "{SCHEMA}"."{table}"'
                        )
                    )
            if existing:
                return "skipped_non_empty"

            restored = 0
            snapshot_tables = payload["tables"]
            restore_tables = TABLES
            if payload.get("format") == LEGACY_SNAPSHOT_FORMAT:
                restore_tables = LEGACY_RESTORE_TABLES
            for table in restore_tables:
                exists = await connection.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL", f"{SCHEMA}.{table}"
                )
                if not exists:
                    continue
                rows = snapshot_tables.get(table, [])
                if not isinstance(rows, list):
                    raise ValueError(f"数据库快照表格式错误: {table}")
                for row in rows:
                    row_json = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    await connection.execute(
                        f'INSERT INTO "{SCHEMA}"."{table}" '
                        f'SELECT * FROM json_populate_record('
                        f'NULL::"{SCHEMA}"."{table}", $1::json) '
                        "ON CONFLICT (id) DO NOTHING",
                        row_json,
                    )
                    restored += 1
            if payload.get("format") == SNAPSHOT_FORMAT:
                for snapshot_key, schema, table, primary_key in PUBLIC_TABLES:
                    exists = await connection.fetchval(
                        "SELECT to_regclass($1) IS NOT NULL",
                        f'{schema}."{table}"',
                    )
                    if not exists:
                        continue
                    rows = snapshot_tables.get(snapshot_key, [])
                    if not isinstance(rows, list):
                        raise ValueError(f"数据库快照表格式错误: {snapshot_key}")
                    for row in rows:
                        row_json = json.dumps(
                            row,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        await connection.execute(
                            f'INSERT INTO "{schema}"."{table}" '
                            f'SELECT * FROM json_populate_record('
                            f'NULL::"{schema}"."{table}", $1::json) '
                            f'ON CONFLICT ("{primary_key}") DO NOTHING',
                            row_json,
                        )
                        restored += 1
            return f"restored:{restored}"
    finally:
        await connection.close()
