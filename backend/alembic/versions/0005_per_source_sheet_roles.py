"""spreadsheet queries run as a per-source role instead of one shared reader

The shared reader could SELECT every spreadsheet table, so SQL-executing
functions (such as ts_stat or query_to_xml) could read another source's data.
Each spreadsheet source now gets its own role, created when it's processed or
first queried.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHARED_ROLE = "chatbuilder_sheet_reader"


def upgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA sheets REVOKE SELECT ON TABLES FROM {SHARED_ROLE}")
    op.execute(f"REVOKE SELECT ON ALL TABLES IN SCHEMA sheets FROM {SHARED_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA sheets TO {SHARED_ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA sheets GRANT SELECT ON TABLES TO {SHARED_ROLE}")
