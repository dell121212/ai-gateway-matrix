from __future__ import annotations

import json
from pathlib import Path

from dashboard.app.core.ids import hash_token
from dashboard.app.services.key_migration import (
    litellm_token_record,
    recover_plaintext_keys,
)


def test_recover_plaintext_client_key_into_current_portable_store(tmp_path: Path) -> None:
    store = tmp_path / "client-keys.json"
    raw = "sk-portable-secret-123456789"
    store.write_text(json.dumps({"version": 2, "keys": [{"id": "token-hash", "alias": "client", "key_preview": "sk-por…6789", "migrated": True}]}), encoding="utf-8")
    backup = tmp_path / "client-keys.pre-migration-20260803000000.bak"
    backup.write_text(json.dumps({"version": 1, "keys": [{"id": "token-hash", "alias": "client", "key": raw}]}), encoding="utf-8")

    recovered = recover_plaintext_keys(store, valid_key_hashes={hash_token(raw)})

    current = json.loads(store.read_text(encoding="utf-8"))
    assert recovered == 1
    assert current["version"] == 3
    assert current["keys"][0]["key"] == raw


def test_recovery_rejects_backup_key_not_present_in_database(tmp_path: Path) -> None:
    store = tmp_path / "client-keys.json"
    store.write_text(json.dumps({"version": 2, "keys": [{"id": "known", "alias": "client"}]}), encoding="utf-8")
    (tmp_path / "client-keys.pre-migration-old.bak").write_text(json.dumps({"keys": [{"id": "known", "alias": "client", "key": "sk-untrusted-secret"}]}), encoding="utf-8")

    assert recover_plaintext_keys(store, valid_key_hashes={"different"}) == 0
    assert not json.loads(store.read_text(encoding="utf-8"))["keys"][0].get("key")


def test_litellm_auth_record_is_rebuilt_without_copying_plaintext() -> None:
    raw = "sk-portable-current-version"
    record = litellm_token_record(
        {
            "key": raw,
            "key_preview": "sk-por…sion",
            "models": ["auto-route"],
            "rpm_limit": 1200,
            "tpm_limit": 2_000_000,
        }
    )

    assert record["token"] == hash_token(raw)
    assert record["key_alias"].startswith("portable-")
    assert "key" not in record
