"""Excel and CSV files loaded into queryable tables (build step 4)."""

from app.ingest import IngestError
from app.models import DataSource


async def ingest_spreadsheet(source: DataSource) -> dict:
    raise IngestError("Spreadsheet support arrives in build step 4.")


def drop_sheet_tables(source: DataSource) -> None:
    pass
