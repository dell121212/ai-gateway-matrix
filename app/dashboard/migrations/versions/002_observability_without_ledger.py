"""durable usage observability without runtime credit ledger

Revision ID: 002
Revises: 001
Create Date: 2026-08-03
"""

from typing import Sequence, Union

from alembic import op

from dashboard.app.db.schema_upgrade import ALTER_OBSERVABILITY_STATEMENTS

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for statement in ALTER_OBSERVABILITY_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Non-destructive downgrade: legacy columns/tables contain call history.
    op.execute("SELECT 1")
