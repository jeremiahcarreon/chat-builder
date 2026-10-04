"""per-app temperature and seed

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("temperature", sa.Float(), nullable=True))
    op.add_column("chat_apps", sa.Column("seed", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_apps", "seed")
    op.drop_column("chat_apps", "temperature")
