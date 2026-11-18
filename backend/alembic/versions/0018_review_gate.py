"""review gate: hold only the answers that need a person

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_apps",
        sa.Column("gate", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_apps", "gate")
