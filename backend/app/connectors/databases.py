"""Live database connections (build step 5)."""

from app.ingest import IngestError
from app.models import DataSource


async def index_database(source: DataSource) -> dict:
    raise IngestError("Database connections arrive in build step 5.")
