"""chat app descriptions, welcome messages, and visitor conversations

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("description", sa.Text(), server_default="", nullable=False))
    op.add_column("chat_apps", sa.Column("welcome_message", sa.Text(), server_default="", nullable=False))
    op.add_column("conversations", sa.Column("visitor_id", sa.String(length=64), nullable=True))
    op.create_index("ix_conversations_visitor_id", "conversations", ["visitor_id"])
    op.add_column(
        "conversations",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("conversations", "updated_at")
    op.drop_index("ix_conversations_visitor_id", table_name="conversations")
    op.drop_column("conversations", "visitor_id")
    op.drop_column("chat_apps", "welcome_message")
    op.drop_column("chat_apps", "description")
