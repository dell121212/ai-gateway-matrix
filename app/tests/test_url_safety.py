from __future__ import annotations

import pytest

from dashboard.url_safety import validate_api_base


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://169.254.169.254/latest", "http://[::1]/v1"])
def test_custom_provider_blocks_private_and_metadata_destinations(url: str) -> None:
    with pytest.raises(ValueError, match="内网|本机|公共"):
        validate_api_base(url, resolve_dns=False)


def test_custom_provider_accepts_public_https() -> None:
    assert validate_api_base("https://api.example.com/v1", resolve_dns=False) == "https://api.example.com/v1"
