"""Live database connections (build step 5)."""

import uuid

from app.ingest import IngestError
from app.models import DataSource


async def index_database(source: DataSource) -> dict:
    raise IngestError("Database connections arrive in build step 5.")


def run_database_query(source_id: uuid.UUID, sql: str, max_rows: int) -> str:
    raise PermissionError("Database queries arrive in build step 5.")
