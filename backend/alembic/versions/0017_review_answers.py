"""hold answers to anonymous visitors until a person reviews them

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("review_answers", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("chat_apps", "review_answers")
