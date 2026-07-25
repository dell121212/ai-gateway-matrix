from dashboard.app.core.logging import redact, safe_meta
from dashboard.app.core.security import (
    has_permission,
    hash_password,
    password_strong_enough,
    verify_password,
)


def test_password_hash_and_verify():
    h = hash_password("CorrectHorseBattery1!")
    assert verify_password(h, "CorrectHorseBattery1!")
    assert not verify_password(h, "wrong")


def test_password_strength():
    assert not password_strong_enough("short")
    assert password_strong_enough("longenough1")


def test_rbac():
    assert has_permission("super_admin", "anything")
    assert has_permission("user", "billing:read_own")
    assert not has_permission("user", "billing:adjust")
    assert has_permission("billing_admin", "billing:adjust")
    assert has_permission("auditor", "audit:read")


def test_redact_secrets():
    s = "Authorization: Bearer sk-abc1234567890secret"
    out = redact(s)
    assert "sk-abc" not in out or "***" in out
    assert "Bearer" in out or "***" in out


def test_safe_meta():
    m = safe_meta({"authorization": "secret", "model": "gpt", "content": "x" * 1000})
    assert m["authorization"] == "***"
    assert m["model"] == "gpt"
