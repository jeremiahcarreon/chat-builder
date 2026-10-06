"""automatic model choice

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("auto_route", sa.Boolean(), server_default="false", nullable=False))
    op.add_column(
        "chat_apps", sa.Column("route_tiers", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False)
    )
    op.add_column("chat_apps", sa.Column("router", sa.String(length=16), server_default="jev", nullable=False))
    op.add_column("conversations", sa.Column("route_floor", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("conversations", "route_floor")
    op.drop_column("chat_apps", "router")
    op.drop_column("chat_apps", "route_tiers")
    op.drop_column("chat_apps", "auto_route")
