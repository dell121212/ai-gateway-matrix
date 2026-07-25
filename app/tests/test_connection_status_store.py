from __future__ import annotations

import tempfile
from pathlib import Path

from dashboard import connection_status_store


def test_connection_status_is_isolated_per_account():
    original = connection_status_store.STORE_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            connection_status_store.STORE_PATH = Path(tmp) / "connection-status.json"
            connection_status_store.record(
                company_id="MISTRAL_KEY", env_var="MISTRAL_KEY_1",
                channel_id="one", ok=True, message="账号一正常",
            )
            connection_status_store.record(
                company_id="MISTRAL_KEY", env_var="MISTRAL_KEY_2",
                channel_id="two", ok=False, message="账号二失败",
            )

            first = connection_status_store.get_company("MISTRAL_KEY_1")
            second = connection_status_store.get_company("MISTRAL_KEY_2")
            assert first and first["ok"] is True
            assert second and second["ok"] is False
            assert first["message"] == "账号一正常"
            assert second["message"] == "账号二失败"
    finally:
        connection_status_store.STORE_PATH = original
