"""classifier settings per chat app

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("classifier_provider", sa.String(length=32), nullable=True))
    op.add_column("chat_apps", sa.Column("classifier_model", sa.String(length=200), nullable=True))
    op.add_column(
        "chat_apps",
        sa.Column("classifier_strictness", sa.String(length=16), server_default="balanced", nullable=False),
    )
    op.add_column(
        "chat_apps",
        sa.Column("classifier_fail_open", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_apps", "classifier_fail_open")
    op.drop_column("chat_apps", "classifier_strictness")
    op.drop_column("chat_apps", "classifier_model")
    op.drop_column("chat_apps", "classifier_provider")
