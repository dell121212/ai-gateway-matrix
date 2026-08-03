"""Idempotent SQL used by both Alembic and bootstrap upgrades."""

ALTER_OBSERVABILITY_STATEMENTS = (
    "ALTER TABLE private_api.api_keys ALTER COLUMN credit_account_id DROP NOT NULL",
    "ALTER TABLE private_api.tasks ADD COLUMN IF NOT EXISTS prompt_tokens BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE private_api.tasks ADD COLUMN IF NOT EXISTS completion_tokens BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE private_api.tasks ADD COLUMN IF NOT EXISTS cost_microusd BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS cost_microusd BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS provider VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS actual_model VARCHAR(128) NOT NULL DEFAULT ''",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS service_tier VARCHAR(32) NOT NULL DEFAULT ''",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS ttft_ms INTEGER",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS route_strategy VARCHAR(32) NOT NULL DEFAULT 'brain-tier'",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS route_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE private_api.client_requests ALTER COLUMN route_strategy SET DEFAULT 'brain-tier'",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS request_summary JSONB",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS detail_artifact_path VARCHAR(512)",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS detail_artifact_size BIGINT",
    "ALTER TABLE private_api.client_requests ADD COLUMN IF NOT EXISTS detail_artifact_sha256 VARCHAR(64)",
    "ALTER TABLE private_api.llm_attempts ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE private_api.llm_attempts ADD COLUMN IF NOT EXISTS ttft_ms INTEGER",
    "ALTER TABLE private_api.llm_attempts ADD COLUMN IF NOT EXISTS service_tier VARCHAR(32) NOT NULL DEFAULT ''",
)
