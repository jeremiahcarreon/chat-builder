"""Background job queue (Procrastinate, stored in Postgres).

The API queues jobs; the worker container runs them (see app/worker.py).
"""

import procrastinate

from app.config import get_settings


def _conninfo() -> str:
    # Procrastinate takes a plain libpq URL, not SQLAlchemy's "+psycopg" form.
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)


queue = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=_conninfo()),
    import_paths=["app.tasks"],
)
