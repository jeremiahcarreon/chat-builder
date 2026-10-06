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
    op.add_column("usage_events", sa.Column("baseline_model", sa.String(length=250), nullable=True))
    op.add_column("test_runs", sa.Column("calibration_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("test_runs", sa.Column("tier", sa.Integer(), nullable=True))
    op.add_column("test_runs", sa.Column("model_label", sa.String(length=250), nullable=True))
    op.create_index("ix_test_runs_calibration_id", "test_runs", ["calibration_id"])


def downgrade() -> None:
    op.drop_index("ix_test_runs_calibration_id", table_name="test_runs")
    op.drop_column("test_runs", "model_label")
    op.drop_column("test_runs", "tier")
    op.drop_column("test_runs", "calibration_id")
    op.drop_column("usage_events", "baseline_model")
    op.drop_column("conversations", "route_floor")
    op.drop_column("chat_apps", "router")
    op.drop_column("chat_apps", "route_tiers")
    op.drop_column("chat_apps", "auto_route")
