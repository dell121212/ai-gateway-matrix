"""ORM models for private_api schema — professional ledger + auth."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dashboard.app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user")
    credit_accounts: Mapped[list["CreditAccount"]] = relationship(back_populates="user")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("private_api.users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class CreditAccount(Base):
    __tablename__ = "credit_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("private_api.users.id"), nullable=False, index=True)
    balance_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship(back_populates="credit_accounts")
    ledger_entries: Mapped[list["CreditLedger"]] = relationship(back_populates="account")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("private_api.users.id"), nullable=False, index=True)
    credit_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("private_api.credit_accounts.id"), nullable=False
    )
    litellm_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    alias: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    default_mode: Mapped[str] = mapped_column(String(32), default="agent-stream", nullable=False)
    allowed_models: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    rpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tpm_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_budget_microcredits: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    request_budget_microcredits: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="api_keys")


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_credit_ledger_idempotency"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("private_api.credit_accounts.id"), nullable=False, index=True
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    client_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # reserve | release | settle | refund | grant | adjust | reverse
    delta_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_after_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="posted", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[Optional[Any]] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    account: Mapped["CreditAccount"] = relationship(back_populates="ledger_entries")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("private_api.users.id"), nullable=False, index=True)
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    external_task_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    client_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    workspace_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)
    grouping_source: Mapped[str] = mapped_column(String(32), default="explicit", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    settled_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[Optional[Any]] = mapped_column("metadata", JSONB, nullable=True)


class ClientRequest(Base):
    __tablename__ = "client_requests"
    __table_args__ = (Index("ix_client_requests_task_started", "task_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("private_api.tasks.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("private_api.users.id"), nullable=False, index=True)
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    litellm_call_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    requested_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    resolved_pool: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="agent-stream", nullable=False)
    stream: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    input_token_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    final_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    final_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    settled_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    response_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_class: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    settlement_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    pricing_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    retry_policy: Mapped[str] = mapped_column(String(32), default="successful_only", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    first_token_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[Optional[Any]] = mapped_column("metadata", JSONB, nullable=True)

    attempts: Mapped[list["LlmAttempt"]] = relationship(back_populates="client_request")


class LlmAttempt(Base):
    __tablename__ = "llm_attempts"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_llm_attempts_idempotency"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("private_api.client_requests.id"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deployment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    actual_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    api_base_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    market_value_microusd: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    charged_microcredits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_source: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    is_final_success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_platform_loss: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_class: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quality_failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    litellm_call_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_mode: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    credit_basis: Mapped[str] = mapped_column(String(32), default="market_value", nullable=False)
    metadata_json: Mapped[Optional[Any]] = mapped_column("metadata", JSONB, nullable=True)

    client_request: Mapped["ClientRequest"] = relationship(back_populates="attempts")


class PricingVersion(Base):
    __tablename__ = "pricing_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), default="*", nullable=False)
    model_pattern: Mapped[str] = mapped_column(String(128), nullable=False)
    # prices in microusd per 1M tokens
    input_price: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    output_price: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cached_input_price: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cached_write_price: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reasoning_price: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    billing_basis: Mapped[str] = mapped_column(String(32), default="market_value", nullable=False)
    credit_multiplier: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    minimum_microcredits: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    detail: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
