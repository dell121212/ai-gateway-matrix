from __future__ import annotations

import stat
from pathlib import Path

import pytest

from scripts.jiyi_database import (
    LEGACY_SNAPSHOT_FORMAT,
    SNAPSHOT_FORMAT,
    PUBLIC_TABLES,
    TABLES,
    _decode_snapshot,
    _encode_snapshot,
    _portable_row,
    normalize_database_url,
)
from scripts.jiyi_store import AutoSync, collect_files, pack, unpack
from scripts.jiyi_redis import (
    SNAPSHOT_FORMAT as REDIS_SNAPSHOT_FORMAT,
    _decode_snapshot as decode_redis_snapshot,
    export_snapshot as export_redis_snapshot,
    restore_snapshot_missing,
)


class FakeRedisSnapshotClient:
    def __init__(self, rows=None) -> None:
        self.rows = dict(rows or {})

    async def scan_iter(self, match: str, count: int = 10):
        assert match == "gwmatrix:*"
        for key in sorted(self.rows):
            if key.startswith(b"gwmatrix:"):
                yield key

    async def dump(self, key: bytes):
        row = self.rows.get(key)
        return row["payload"] if row else None

    async def exists(self, key: bytes) -> int:
        return int(key in self.rows)

    async def execute_command(self, command: str, *args):
        command = command.upper()
        if command == "PEXPIRETIME":
            row = self.rows.get(args[0])
            return row.get("expire_at_ms", -1) if row else -2
        if command == "RESTORE":
            key, ttl, payload, *options = args
            self.rows[key] = {
                "payload": payload,
                "expire_at_ms": int(ttl) if "ABSTTL" in options else -1,
            }
            return b"OK"
        raise AssertionError(f"unexpected redis command: {command}")


def _seed_data(data_dir: Path) -> None:
    (data_dir / "state").mkdir(parents=True)
    (data_dir / ".env").write_text("API_KEY=secret-one\n", encoding="utf-8")
    (data_dir / "config.yaml").write_text("model: auto-route\n", encoding="utf-8")
    (data_dir / "state" / "settings.json").write_text(
        '{"theme":"dark"}\n',
        encoding="utf-8",
    )


def test_pack_is_single_file_round_trip_and_keeps_inode(tmp_path: Path) -> None:
    data_dir = tmp_path / "home"
    restored = tmp_path / "restored"
    jiyi = tmp_path / "jiyi.txt"
    _seed_data(data_dir)

    assert pack(data_dir, jiyi, quiet=True) == 0
    first_inode = jiyi.stat().st_ino
    assert stat.S_IMODE(jiyi.stat().st_mode) == 0o600

    (data_dir / ".env").write_text("API_KEY=secret-two\n", encoding="utf-8")
    assert pack(data_dir, jiyi, quiet=True) == 0
    assert jiyi.stat().st_ino == first_inode
    assert "secret-two" in jiyi.read_text(encoding="utf-8")

    assert unpack(jiyi, restored) == 0
    assert (restored / ".env").read_text(encoding="utf-8") == "API_KEY=secret-two\n"
    assert (restored / "state" / "settings.json").is_file()


def test_auto_sync_generates_then_updates_after_debounce(tmp_path: Path) -> None:
    data_dir = tmp_path / "home"
    jiyi = tmp_path / "jiyi.txt"
    _seed_data(data_dir)
    sync = AutoSync(data_dir, jiyi, debounce=1.0)

    assert sync.initialize() is True
    before = jiyi.read_text(encoding="utf-8")
    assert "secret-one" in before

    (data_dir / ".env").write_text("API_KEY=secret-after-change\n", encoding="utf-8")
    assert sync.tick(now=10.0) is False
    assert sync.tick(now=10.5) is False
    assert sync.tick(now=11.0) is True
    assert "secret-after-change" in jiyi.read_text(encoding="utf-8")


def test_auto_sync_does_not_overwrite_existing_migration_file_on_start(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "home"
    jiyi = tmp_path / "jiyi.txt"
    _seed_data(data_dir)
    jiyi.write_text("# AGM-JIYI-V1\n# carried-from-old-machine\n", encoding="utf-8")
    original = jiyi.read_text(encoding="utf-8")

    sync = AutoSync(data_dir, jiyi)

    assert sync.initialize() is False
    assert jiyi.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(jiyi.stat().st_mode) == 0o600


def test_database_snapshot_is_deterministic_compressed_json(tmp_path: Path) -> None:
    tables = {"users": [{"id": "user-1", "username": "alice"}]}
    first = _encode_snapshot(tables)
    second = _encode_snapshot(tables)
    snapshot = tmp_path / "private-api-export.json.gz"
    snapshot.write_bytes(first)

    assert first == second
    assert _decode_snapshot(snapshot) == {
        "format": SNAPSHOT_FORMAT,
        "tables": tables,
    }
    assert normalize_database_url(
        "postgresql+asyncpg://user:pass@postgres/db"
    ) == "postgresql://user:pass@postgres/db"


def test_database_snapshot_v2_carries_observability_and_reads_v1(tmp_path: Path) -> None:
    assert SNAPSHOT_FORMAT == "AGM-PRIVATE-API-V2"
    assert {"usage_aggregates", "quota_snapshots"}.issubset(TABLES)
    assert "credit_ledger" not in TABLES
    assert PUBLIC_TABLES == (
        ("litellm_verification_tokens", "public", "LiteLLM_VerificationToken", "token"),
    )

    legacy_payload = _encode_snapshot(
        {"users": [{"id": "legacy-user"}]}, snapshot_format=LEGACY_SNAPSHOT_FORMAT
    )
    path = tmp_path / "legacy.json.gz"
    path.write_bytes(legacy_payload)
    decoded = _decode_snapshot(path)
    assert decoded["format"] == LEGACY_SNAPSHOT_FORMAT
    assert decoded["tables"]["users"][0]["id"] == "legacy-user"
    assert _portable_row("api_keys", {"id": "k", "credit_account_id": "legacy"})["credit_account_id"] is None
    portable_token = _portable_row(
        "litellm_verification_tokens",
        {
            "token": "hashed-token",
            "budget_id": "old-budget",
            "organization_id": "old-org",
            "object_permission_id": "old-object-policy",
            "project_id": "old-project",
            "rpm_limit": 1200,
        },
    )
    assert portable_token["token"] == "hashed-token"
    assert portable_token["rpm_limit"] == 1200
    assert all(
        portable_token[field] is None
        for field in ("budget_id", "organization_id", "object_permission_id", "project_id")
    )


@pytest.mark.asyncio
async def test_redis_snapshot_round_trip_only_restores_missing_live_keys(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "gateway-redis-export.json.gz"
    source = FakeRedisSnapshotClient(
        {
            b"gwmatrix:usage:a:total:tokens": {
                "payload": b"dump-total",
                "expire_at_ms": 5_000,
            },
            b"gwmatrix:optimal:a": {
                "payload": b"dump-optimal",
                "expire_at_ms": -1,
            },
            b"gwmatrix:routing:expired": {
                "payload": b"dump-expired",
                "expire_at_ms": 1_000,
            },
            b"unrelated:key": {
                "payload": b"must-not-export",
                "expire_at_ms": -1,
            },
        }
    )

    assert await export_redis_snapshot(
        "redis",
        6379,
        "",
        snapshot,
        client=source,
    )
    assert decode_redis_snapshot(snapshot)["format"] == REDIS_SNAPSHOT_FORMAT
    first = snapshot.read_bytes()
    assert not await export_redis_snapshot(
        "redis",
        6379,
        "",
        snapshot,
        client=source,
    )
    assert snapshot.read_bytes() == first

    target = FakeRedisSnapshotClient(
        {
            b"gwmatrix:optimal:a": {
                "payload": b"newer-current-value",
                "expire_at_ms": -1,
            }
        }
    )
    result = await restore_snapshot_missing(
        "redis",
        6379,
        "",
        snapshot,
        client=target,
        now_ms=2_000,
    )

    assert result == "restored:1,existing:1,expired:1"
    assert target.rows[b"gwmatrix:usage:a:total:tokens"]["payload"] == b"dump-total"
    assert target.rows[b"gwmatrix:optimal:a"]["payload"] == b"newer-current-value"
    assert b"gwmatrix:routing:expired" not in target.rows
    assert b"unrelated:key" not in target.rows


def test_unpack_rejects_path_traversal(tmp_path: Path) -> None:
    jiyi = tmp_path / "jiyi.txt"
    jiyi.write_text(
        "# AGM-JIYI-V1\n"
        "===== BEGIN FILE: ../outside.txt =====\n"
        "blocked\n"
        "===== END FILE: ../outside.txt =====\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="越过用户数据目录"):
        unpack(jiyi, tmp_path / "home")

    assert not (tmp_path / "outside.txt").exists()


def test_jiyi_excludes_plaintext_pre_migration_key_backups(tmp_path: Path) -> None:
    data_dir = tmp_path / "home"
    _seed_data(data_dir)
    stale = data_dir / "state" / "client-keys.pre-migration-20260803000000.bak"
    stale.write_text('{"key":"must-not-enter-jiyi"}', encoding="utf-8")

    relative = {path.relative_to(data_dir).as_posix() for path in collect_files(data_dir)}

    assert stale.relative_to(data_dir).as_posix() not in relative
