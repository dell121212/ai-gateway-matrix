"""Password hashing, sessions, CSRF, RBAC."""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .ids import hash_token

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    OPERATOR = "operator"
    BILLING_ADMIN = "billing_admin"
    AUDITOR = "auditor"
    USER = "user"


# permission matrix
_ROLE_PERMS: dict[Role, set[str]] = {
    Role.SUPER_ADMIN: {"*"},
    Role.OPERATOR: {
        "providers:read",
        "providers:write",
        "routing:write",
        "tasks:read",
        "requests:read",
        "system:read",
        "users:read",
        "api_keys:manage_own",
        "billing:read",
    },
    Role.BILLING_ADMIN: {
        "billing:read",
        "billing:adjust",
        "pricing:write",
        "tasks:read",
        "requests:read",
        "users:read",
        "api_keys:manage_own",
        "system:read",
    },
    Role.AUDITOR: {
        "audit:read",
        "billing:read",
        "tasks:read",
        "requests:read",
        "users:read",
        "system:read",
    },
    Role.USER: {
        "billing:read_own",
        "tasks:read_own",
        "requests:read_own",
        "api_keys:manage_own",
        "live:own",
    },
}


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, Exception):
        return False


def password_strong_enough(password: str) -> bool:
    if not password or len(password) < 10:
        return False
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    return classes >= 2


def has_permission(role: str, perm: str) -> bool:
    try:
        r = Role(role)
    except ValueError:
        return False
    perms = _ROLE_PERMS.get(r, set())
    return "*" in perms or perm in perms


def new_csrf_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hash_token(token)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass
class LoginRateLimiter:
    limit: int = 10
    window_sec: int = 60

    def __post_init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self._hits[key]
        while q and now - q[0] > self.window_sec:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


login_rate_limiter = LoginRateLimiter()
