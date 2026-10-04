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


# Arbitrary key for a Postgres advisory lock. Concurrent GRANT/DROP ROLE statements
# on the same schema fail with "tuple concurrently updated", so role changes are
# serialized across workers.
ROLE_LOCK_KEY = 726_361_501


def source_role(source_id: uuid.UUID) -> str:
    """Each spreadsheet source's queries run as its own role, which can read only its tables."""
    return f"cb_sheet_{source_id.hex}"


def ensure_source_role(source_id: uuid.UUID, tables: list[str]) -> None:
    role = source_role(source_id)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ROLE_LOCK_KEY})
        conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        CREATE ROLE {role} NOLOGIN;
                    END IF;
                END
                $$
                """
            )
        )
        conn.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}"))
        for table in tables:
            conn.execute(text(f'GRANT SELECT ON {SCHEMA}."{table}" TO {role}'))
        conn.execute(text(f"GRANT {role} TO CURRENT_USER"))


def drop_sheet_tables(source: DataSource) -> None:
    if source.kind != "spreadsheet":
        return
    tables = [t["table"] for t in source.config.get("tables", [])]
    role = source_role(source.id)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ROLE_LOCK_KEY})
        for table in tables:
            conn.execute(text(f'DROP TABLE IF EXISTS {SCHEMA}."{table}"'))
        exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
        if exists:
            conn.execute(text(f"DROP OWNED BY {role}"))
            conn.execute(text(f"DROP ROLE {role}"))


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
        ensure_source_role(source.id, [table for _, table, _ in prepared])
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
        tables = [t["table"] for t in source.config.get("tables", [])]
    allowed = {t.lower() for t in tables}
    tree = check_select(sql, "spreadsheet")
    for schema, table in referenced_tables(tree):
        if schema not in ("", SCHEMA) or table not in allowed:
            name = f"{schema}.{table}" if schema else table
            raise PermissionError(f"Table {name} is not part of this spreadsheet. Use the tables listed.")

    role = source_role(source_id)
    with engine.connect() as conn:
        has_role = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    if not has_role:
        # Sources processed before per-source roles existed.
        ensure_source_role(source_id, tables)

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            # This role can read only this source's tables, whatever the SQL does.
            cur.execute(f"SET LOCAL ROLE {role}")
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
