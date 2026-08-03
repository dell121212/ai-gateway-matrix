#!/usr/bin/env python3
"""Redis 运行状态快照：把 gwmatrix:* 逻辑数据纳入 jiyi.txt。

快照使用 Redis DUMP/RESTORE 保存原始类型，并记录绝对过期时间。恢复时只补
当前 Redis 中不存在的键，因此不会覆盖新环境已经产生的用量或路由状态。
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

SNAPSHOT_FORMAT = "AGM-REDIS-V1"
KEY_PATTERNS = ("gwmatrix:*",)
KEY_PREFIX = b"gwmatrix:"


def _encode_snapshot(records: list[dict[str, Any]]) -> bytes:
    raw = json.dumps(
        {"format": SNAPSHOT_FORMAT, "records": records},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _decode_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    if payload.get("format") != SNAPSHOT_FORMAT or not isinstance(
        payload.get("records"),
        list,
    ):
        raise ValueError(f"未识别的 Redis 快照格式: {path}")
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


def _redis_client(host: str, port: int, password: str):
    import redis.asyncio as redis

    return redis.Redis(
        host=host,
        port=port,
        password=password or None,
        decode_responses=False,
        socket_connect_timeout=3,
        socket_timeout=5,
    )


async def export_snapshot(
    host: str,
    port: int,
    password: str,
    destination: Path,
    *,
    client=None,
) -> bool:
    """导出全部 gwmatrix:* 键；无变化时不更新目标文件时间。"""

    own_client = client is None
    redis_client = client or _redis_client(host, port, password)
    try:
        keys: set[bytes] = set()
        for pattern in KEY_PATTERNS:
            async for key in redis_client.scan_iter(match=pattern, count=1000):
                key_bytes = key.encode("utf-8") if isinstance(key, str) else bytes(key)
                if key_bytes.startswith(KEY_PREFIX):
                    keys.add(key_bytes)

        records: list[dict[str, Any]] = []
        for key in sorted(keys):
            dumped = await redis_client.dump(key)
            if dumped is None:
                continue
            expire_at_ms = int(
                await redis_client.execute_command("PEXPIRETIME", key)
            )
            records.append(
                {
                    "key_b64": base64.b64encode(key).decode("ascii"),
                    "payload_b64": base64.b64encode(dumped).decode("ascii"),
                    "expire_at_ms": expire_at_ms if expire_at_ms >= 0 else None,
                }
            )
        return _write_if_changed(destination, _encode_snapshot(records))
    finally:
        if own_client:
            await redis_client.aclose()


async def restore_snapshot_missing(
    host: str,
    port: int,
    password: str,
    source: Path,
    *,
    client=None,
    now_ms: int | None = None,
) -> str:
    """只恢复缺失且尚未过期的键，绝不覆盖当前 Redis 数据。"""

    if not source.is_file():
        return "missing"
    payload = _decode_snapshot(source)
    own_client = client is None
    redis_client = client or _redis_client(host, port, password)
    restored = 0
    skipped_existing = 0
    skipped_expired = 0
    clock_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        for record in payload["records"]:
            if not isinstance(record, dict):
                raise ValueError("Redis 快照记录格式错误")
            key = base64.b64decode(record["key_b64"], validate=True)
            dumped = base64.b64decode(record["payload_b64"], validate=True)
            if not key.startswith(KEY_PREFIX):
                raise ValueError("Redis 快照包含非 gwmatrix 命名空间键")
            expire_at_ms = record.get("expire_at_ms")
            if expire_at_ms is not None:
                expire_at_ms = int(expire_at_ms)
                if expire_at_ms <= clock_ms:
                    skipped_expired += 1
                    continue
            if await redis_client.exists(key):
                skipped_existing += 1
                continue
            if expire_at_ms is None:
                await redis_client.execute_command("RESTORE", key, 0, dumped)
            else:
                await redis_client.execute_command(
                    "RESTORE",
                    key,
                    expire_at_ms,
                    dumped,
                    "ABSTTL",
                )
            restored += 1
        return (
            f"restored:{restored},existing:{skipped_existing},"
            f"expired:{skipped_expired}"
        )
    finally:
        if own_client:
            await redis_client.aclose()
