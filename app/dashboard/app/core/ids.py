"""ID helpers — UUID primary keys and request/task identifiers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Optional


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def new_id_str() -> str:
    return str(uuid.uuid4())


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def key_prefix_fingerprint(raw: str) -> tuple[str, str]:
    raw = raw or ""
    if len(raw) <= 10:
        return ("sk-…", hash_token(raw))
    return (f"{raw[:8]}…{raw[-4:]}", hash_token(raw))


def generate_client_key() -> str:
    return "sk-" + secrets.token_urlsafe(32)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def optional_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return None
