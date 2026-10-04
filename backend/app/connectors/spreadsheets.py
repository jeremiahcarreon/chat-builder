"""Excel and CSV files loaded into queryable tables in the sandboxed "sheets" schema."""

import asyncio
import re
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from app.connectors.results import format_results
from app.connectors.sql_safety import check_select, referenced_tables
from app.db import SessionLocal, engine
from app.ingest import IngestError, source_file
from app.models import DataSource, SchemaDoc
from app.rag.embeddings import embed_documents

SCHEMA = "sheets"
READER_ROLE = "chatbuilder_sheet_reader"
MAX_ROWS_PER_SHEET = 1_000_000
QUERY_TIMEOUT_MS = 10_000
SAMPLE_VALUES = 3


def _slug(value: str, limit: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return (slug or "x")[:limit]


def _clean_columns(columns) -> list[str]:
    names: list[str] = []
    for index, column in enumerate(columns):
        base = _slug(column, 50) if not str(column).startswith("Unnamed:") else f"column_{index + 1}"
        if base[0].isdigit():
            base = f"c_{base}"
        name, n = base, 2
        while name in names:
            name, n = f"{base}_{n}", n + 1
        names.append(name)
    return names


def _read(path: Path) -> dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return {path.stem: pd.read_csv(path, sep=None, engine="python", nrows=MAX_ROWS_PER_SHEET)}
        return pd.read_excel(path, sheet_name=None, engine="openpyxl", nrows=MAX_ROWS_PER_SHEET)
    except (ValueError, KeyError, OSError, pd.errors.ParserError) as exc:
        raise IngestError(f"Could not read the spreadsheet: {exc}") from exc


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = _clean_columns(df.columns)
    for column in df.columns:
        if df[column].dtype == object:
            # Mixed text and numbers: store as text so nothing is lost.
            df[column] = df[column].map(lambda v: None if pd.isna(v) else str(v))
    return df


def _pg_type(dtype) -> str:
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "timestamp"
    return "text"


def _describe(source: DataSource, sheet: str, table: str, df: pd.DataFrame, multi_sheet: bool) -> tuple[str, list]:
    columns = []
    lines = []
    for column in df.columns:
        values = df[column].dropna().astype(str).unique()[:SAMPLE_VALUES]
        kind = _pg_type(df[column].dtype)
        columns.append({"name": column, "type": kind})
        sample = ", ".join(v[:40] for v in values)
        lines.append(f"- {column} ({kind}){f': e.g. {sample}' if sample else ''}")
    where = f'sheet "{sheet}" of {source.name}' if multi_sheet else source.name
    description = (
        f"Table {SCHEMA}.{table} (from spreadsheet {where}), {len(df)} rows. Columns:\n" + "\n".join(lines)
    )
    return description, columns


def drop_sheet_tables(source: DataSource) -> None:
    tables = [t["table"] for t in source.config.get("tables", [])]
    if not tables:
        return
    with engine.begin() as conn:
        for table in tables:
            conn.execute(text(f'DROP TABLE IF EXISTS {SCHEMA}."{table}"'))


async def ingest_spreadsheet(source: DataSource) -> dict:
    sheets = await asyncio.to_thread(_read, source_file(source))
    prefix = f"s_{source.id.hex[:10]}_"
    prepared: list[tuple[str, str, pd.DataFrame]] = []
    used: set[str] = set()
    for sheet, df in sheets.items():
        df = _prepare(df)
        if df.empty:
            continue
        table = prefix + _slug(sheet, 63 - len(prefix) - 3)
        while table in used:
            table += "_x"
        used.add(table)
        prepared.append((str(sheet), table, df))
    if not prepared:
        raise IngestError("The spreadsheet has no data.")

    descriptions = [_describe(source, sheet, table, df, len(prepared) > 1) for sheet, table, df in prepared]
    vectors = await embed_documents([d for d, _ in descriptions], title=source.name)

    def load() -> None:
        drop_sheet_tables(source)
        for _, table, df in prepared:
            df.to_sql(table, engine, schema=SCHEMA, if_exists="replace", index=False, chunksize=5000)
        with SessionLocal() as db:
            db.query(SchemaDoc).filter(SchemaDoc.source_id == source.id).delete()
            for (sheet, table, _), (description, columns), vector in zip(prepared, descriptions, vectors, strict=True):
                db.add(
                    SchemaDoc(
                        app_id=source.app_id,
                        source_id=source.id,
                        table_name=f"{SCHEMA}.{table}",
                        description=description,
                        columns=columns,
                        embedding=vector,
                    )
                )
            src = db.get(DataSource, source.id)
            src.config = {
                **src.config,
                "tables": [{"sheet": sheet, "table": table, "rows": len(df)} for sheet, table, df in prepared],
            }
            db.commit()

    await asyncio.to_thread(load)
    return {"table_count": len(prepared), "row_count": sum(len(df) for _, _, df in prepared)}


def run_sheet_query(source_id: uuid.UUID, sql: str, max_rows: int) -> str:
    """Runs model-written SQL against this source's tables only, as the read-only role."""
    with SessionLocal() as db:
        source = db.get(DataSource, source_id)
        allowed = {t["table"].lower() for t in source.config.get("tables", [])}
    tree = check_select(sql, "spreadsheet")
    for schema, table in referenced_tables(tree):
        if schema not in ("", SCHEMA) or table not in allowed:
            name = f"{schema}.{table}" if schema else table
            raise PermissionError(f"Table {name} is not part of this spreadsheet. Use the tables listed.")

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL ROLE {READER_ROLE}")
            cur.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
            cur.execute(f"SET LOCAL search_path = {SCHEMA}")
            # No parameters are passed, so the SQL is sent exactly as written.
            cur.execute(tree.sql(dialect="postgres"))
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
    finally:
        raw.rollback()
        raw.close()
    return format_results(columns, [tuple(r) for r in rows[:max_rows]], len(rows) > max_rows)
