"""product knowledge notes per app

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("knowledge_notes", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("chat_apps", "knowledge_notes")
