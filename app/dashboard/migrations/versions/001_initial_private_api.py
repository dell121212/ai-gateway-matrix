"""initial private_api schema

Revision ID: 001
Revises:
Create Date: 2026-07-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS private_api")
    # Tables created via metadata.create_all at bootstrap as well;
    # this revision documents the schema boundary for ops.
    op.execute("SELECT 1")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS private_api CASCADE")
