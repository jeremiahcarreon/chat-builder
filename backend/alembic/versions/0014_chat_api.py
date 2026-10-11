"""chat app API keys and API conversation threads

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["chat_apps.id"], name="app_api_keys_app_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="app_api_keys_pkey"),
        sa.UniqueConstraint("key_hash", name="app_api_keys_key_hash_key"),
    )
    op.create_index("ix_app_api_keys_app_id", "app_api_keys", ["app_id"])
    op.add_column("conversations", sa.Column("external_id", sa.String(length=200), nullable=True))
    op.create_unique_constraint("conversations_app_id_external_id_key", "conversations", ["app_id", "external_id"])


def downgrade() -> None:
    op.drop_constraint("conversations_app_id_external_id_key", "conversations", type_="unique")
    op.drop_column("conversations", "external_id")
    op.drop_index("ix_app_api_keys_app_id", table_name="app_api_keys")
    op.drop_table("app_api_keys")
