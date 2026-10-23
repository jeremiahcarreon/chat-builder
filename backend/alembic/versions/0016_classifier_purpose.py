"""what the classifier decides: scope, or whether a reply is expected

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_apps", sa.Column("classifier_purpose", sa.String(16), server_default="scope", nullable=False))


def downgrade() -> None:
    op.drop_column("chat_apps", "classifier_purpose")
