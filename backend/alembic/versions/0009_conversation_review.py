"""satisfaction review on conversations

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("satisfaction", sa.Float(), nullable=True))
    op.add_column("conversations", sa.Column("resolved", sa.Float(), nullable=True))
    op.add_column("conversations", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("reviewed_messages", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "reviewed_messages")
    op.drop_column("conversations", "reviewed_at")
    op.drop_column("conversations", "resolved")
    op.drop_column("conversations", "satisfaction")
