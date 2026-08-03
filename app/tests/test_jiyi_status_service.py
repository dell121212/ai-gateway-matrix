from __future__ import annotations

import json
from pathlib import Path

from dashboard.app.services.jiyi_status import read_jiyi_status, request_jiyi_sync


def test_read_jiyi_status_reports_database_and_redis_snapshots(tmp_path: Path) -> None:
    database = tmp_path / "private-api-export.json.gz"
    redis = tmp_path / "gateway-redis-export.json.gz"
    database.write_bytes(b"database")
    redis.write_bytes(b"redis")
    (tmp_path / "client-keys.json").write_text(
        json.dumps(
            {
                "version": 3,
                "portable_secrets": True,
                "keys": [{"id": "one", "key": "sk-secret"}],
            }
        ),
        encoding="utf-8",
    )

    status = read_jiyi_status(tmp_path)

    assert status["enabled"] is True
    assert status["database_snapshot"]["exists"] is True
    assert status["database_snapshot"]["size_bytes"] == 8
    assert status["redis_snapshot"]["exists"] is True
    assert status["redis_snapshot"]["size_bytes"] == 5
    assert status["client_keys"] == {
        "exists": True,
        "version": 3,
        "count": 1,
        "portable_count": 1,
        "complete": True,
    }
    assert status["last_synced_at"] is not None


def test_request_jiyi_sync_writes_a_non_secret_marker_atomically(
    tmp_path: Path,
) -> None:
    result = request_jiyi_sync(tmp_path, actor="local")
    marker = tmp_path / "jiyi-save-request.json"

    assert result["accepted"] is True
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["actor"] == "local"
    assert payload["reason"] == "desktop-request"
    assert payload["requested_at"] == result["requested_at"]
