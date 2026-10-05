import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.deps import AdminUser, DbSession
from app.ingest import clear_index
from app.models import ChatApp, DataSource
from app.queue import queue
from app.rag.parsing import DOCUMENT_EXTENSIONS
from app.routers.apps import owned_app

router = APIRouter(prefix="/api", tags=["sources"])

SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm", ".csv"}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
UPLOAD_CHUNK = 1024 * 1024


class SourceOut(BaseModel):
    id: uuid.UUID
    app_id: uuid.UUID
    app_name: str | None = None
    kind: str
    name: str
    status: str
    error: str | None
    details: dict
    created_at: datetime
    updated_at: datetime


# Config keys that are safe and useful to show in the dashboard.
PUBLIC_CONFIG = {
    "filename", "size", "chunk_count", "page_count", "table_count", "row_count",
    "host", "port", "database", "username", "encryption", "require_tls", "schemas",
    "query_timeout", "dirty_reads", "hidden_count",
}


def source_out(source: DataSource, app_name: str | None = None) -> SourceOut:
    return SourceOut(
        id=source.id,
        app_id=source.app_id,
        app_name=app_name,
        kind=source.kind,
        name=source.name,
        status=source.status,
        error=source.error,
        details={k: v for k, v in source.config.items() if k in PUBLIC_CONFIG},
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def owned_source(db: DbSession, user: AdminUser, source_id: uuid.UUID) -> DataSource:
    source = db.get(DataSource, source_id)
    if source is None or not user.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found")
    return source


def source_dir(source_id: uuid.UUID) -> Path:
    return Path(get_settings().upload_dir) / "sources" / str(source_id)


def _safe_filename(name: str) -> str:
    name = Path(name or "upload").name
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "upload"
    return f"{stem[:120]}{suffix}"


async def queue_ingest(source_id: uuid.UUID) -> None:
    await queue.configure_task("ingest_source", queue="ingest").defer_async(source_id=str(source_id))


@router.get("/sources")
def list_all_sources(db: DbSession, user: AdminUser) -> list[SourceOut]:
    rows = db.execute(
        select(DataSource, ChatApp.name)
        .join(ChatApp, ChatApp.id == DataSource.app_id)
        
        .order_by(ChatApp.name, DataSource.created_at)
    ).all()
    return [source_out(source, app_name) for source, app_name in rows]


@router.get("/apps/{app_id}/sources")
def list_sources(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[SourceOut]:
    owned_app(db, user, app_id)
    rows = db.scalars(
        select(DataSource).where(DataSource.app_id == app_id).order_by(DataSource.created_at)
    )
    return [source_out(s) for s in rows]


@router.post("/apps/{app_id}/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload_files(
    app_id: uuid.UUID, files: list[UploadFile], db: DbSession, user: AdminUser
) -> list[SourceOut]:
    owned_app(db, user, app_id)
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    allowed = DOCUMENT_EXTENSIONS | SPREADSHEET_EXTENSIONS | SQLITE_EXTENSIONS
    created: list[DataSource] = []

    for upload in files:
        filename = _safe_filename(upload.filename or "")
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{filename}: unsupported file type. Supported: {', '.join(sorted(allowed))}",
            )
        kind = (
            "file" if suffix in DOCUMENT_EXTENSIONS
            else "spreadsheet" if suffix in SPREADSHEET_EXTENSIONS
            else "sqlite"
        )
        source = DataSource(id=uuid.uuid4(), app_id=app_id, kind=kind, name=filename, status="pending", config={})
        directory = source_dir(source.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        size = 0
        try:
            with path.open("wb") as out:
                while chunk := await upload.read(UPLOAD_CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(
                            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"{filename} is larger than {get_settings().max_upload_mb} MB",
                        )
                    out.write(chunk)
        except HTTPException:
            shutil.rmtree(directory, ignore_errors=True)
            for previous in created:
                shutil.rmtree(source_dir(previous.id), ignore_errors=True)
            raise
        source.config = {"path": str(path), "filename": filename, "size": size}
        created.append(source)

    db.add_all(created)
    db.commit()
    for source in created:
        await queue_ingest(source.id)
    return [source_out(s) for s in created]


class RenameSource(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.patch("/sources/{source_id}")
def rename_source(source_id: uuid.UUID, body: RenameSource, db: DbSession, user: AdminUser) -> SourceOut:
    source = owned_source(db, user, source_id)
    source.name = body.name.strip()
    db.commit()
    return source_out(source)


@router.post("/sources/{source_id}/reprocess")
async def reprocess_source(source_id: uuid.UUID, db: DbSession, user: AdminUser) -> SourceOut:
    source = owned_source(db, user, source_id)
    source.status = "pending"
    source.error = None
    db.commit()
    await queue_ingest(source.id)
    return source_out(source)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, db: DbSession, user: AdminUser) -> None:
    from app.connectors.spreadsheets import drop_sheet_tables

    source = owned_source(db, user, source_id)
    clear_index(source.id)
    drop_sheet_tables(source)
    db.delete(source)
    db.commit()
    shutil.rmtree(source_dir(source_id), ignore_errors=True)


class DatabaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(default="", max_length=255)
    username: str = Field(default="", max_length=255)
    # Leave empty when editing to keep the saved password.
    password: str = Field(default="", max_length=1000)
    encryption: str = "request"
    require_tls: bool = False
    schemas: list[str] = Field(default_factory=list, max_length=50)
    # Longest a single query may run (reports on large tables may need more).
    query_timeout: int = Field(default=30, ge=5, le=600)
    dirty_reads: bool = False

    def connection_settings(self, password: str | None = None):
        from app.connectors.databases import ConnectionSettings, SERVER_KINDS

        if self.kind not in SERVER_KINDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported database type: {self.kind}")
        if self.encryption not in ("off", "request", "require"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "encryption must be off, request or require")
        return ConnectionSettings(
            kind=self.kind,
            host=self.host.strip(),
            port=self.port,
            database=self.database.strip(),
            username=self.username.strip(),
            password=self.password if password is None else password,
            encryption=self.encryption,
            require_tls=self.require_tls,
            schemas=[s.strip() for s in self.schemas if s.strip()],
            query_timeout=self.query_timeout,
            dirty_reads=self.dirty_reads and self.kind == "mssql",
        )

    def public_config(self) -> dict:
        cs = self.connection_settings()
        return {
            "host": cs.host,
            "port": cs.port,
            "database": cs.database,
            "username": cs.username,
            "encryption": cs.encryption,
            "require_tls": cs.require_tls,
            "schemas": cs.schemas,
            "query_timeout": cs.query_timeout,
            "dirty_reads": cs.dirty_reads,
        }


class TestResult(BaseModel):
    ok: bool
    message: str


async def _test(cs) -> TestResult:
    import asyncio

    from app.connectors.databases import ConnectionFailed, test_connection

    try:
        count = await asyncio.to_thread(test_connection, cs)
    except ConnectionFailed as exc:
        return TestResult(ok=False, message=str(exc))
    return TestResult(ok=True, message=f"Connected. Found {count} table{'s' if count != 1 else ''}.")


@router.post("/apps/{app_id}/sources/database/test")
async def test_database(app_id: uuid.UUID, body: DatabaseIn, db: DbSession, user: AdminUser) -> TestResult:
    owned_app(db, user, app_id)
    return await _test(body.connection_settings())


@router.post("/sources/{source_id}/database/test")
async def test_saved_database(
    source_id: uuid.UUID, body: DatabaseIn, db: DbSession, user: AdminUser
) -> TestResult:
    """Tests edited settings, using the saved password if none was entered."""
    from app.connectors.databases import settings_for

    source = owned_source(db, user, source_id)
    password = body.password or settings_for(source).password
    return await _test(body.connection_settings(password))


@router.post("/apps/{app_id}/sources/database", status_code=status.HTTP_201_CREATED)
async def add_database(app_id: uuid.UUID, body: DatabaseIn, db: DbSession, user: AdminUser) -> SourceOut:
    from app.connectors.databases import secret_context
    from app.crypto import encrypt

    owned_app(db, user, app_id)
    source = DataSource(
        id=uuid.uuid4(), app_id=app_id, kind=body.kind, name=body.name.strip(), status="pending",
        config=body.public_config(),
    )
    source.encrypted_secret = encrypt(body.password, secret_context(source.id)) if body.password else None
    db.add(source)
    db.commit()
    await queue_ingest(source.id)
    return source_out(source)


@router.put("/sources/{source_id}/database")
async def update_database(
    source_id: uuid.UUID, body: DatabaseIn, db: DbSession, user: AdminUser
) -> SourceOut:
    from app.connectors.databases import SERVER_KINDS, secret_context
    from app.crypto import encrypt

    source = owned_source(db, user, source_id)
    if source.kind not in SERVER_KINDS or body.kind != source.kind:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The database type can't be changed")
    source.name = body.name.strip()
    source.config = {**source.config, **body.public_config()}
    if body.password:
        source.encrypted_secret = encrypt(body.password, secret_context(source.id))
    source.status = "pending"
    source.error = None
    db.commit()
    await queue_ingest(source.id)
    return source_out(source)


# ------------------------------------------------------------ table notes


class TableInfoOut(BaseModel):
    name: str
    columns: list[tuple[str, str]]
    primary_key: list[str]
    foreign_keys: list[tuple[str, str, str]]
    rows: int | None
    note: str
    hidden: bool
    column_notes: dict[str, str]


class TableNotesIn(BaseModel):
    note: str = Field(default="", max_length=4000)
    hidden: bool = False
    column_notes: dict[str, str] = Field(default_factory=dict)


def _database_source(db: DbSession, user: AdminUser, source_id: uuid.UUID) -> DataSource:
    source = owned_source(db, user, source_id)
    if source.kind in ("file", "spreadsheet"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Table notes are for connected databases.")
    return source


@router.get("/sources/{source_id}/tables")
def list_tables_for_notes(source_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[TableInfoOut]:
    source = _database_source(db, user, source_id)
    notes = source.config.get("table_notes", {})
    return [
        TableInfoOut(
            name=entry["name"],
            columns=[tuple(c) for c in entry["columns"]],
            primary_key=entry.get("primary_key", []),
            foreign_keys=[tuple(fk) for fk in entry.get("foreign_keys", [])],
            rows=entry.get("rows"),
            note=notes.get(entry["name"], {}).get("note", ""),
            hidden=bool(notes.get(entry["name"], {}).get("hidden")),
            column_notes=notes.get(entry["name"], {}).get("columns", {}),
        )
        for entry in source.config.get("schema_cache", [])
    ]


@router.put("/sources/{source_id}/tables/{table:path}")
async def update_table_notes(
    source_id: uuid.UUID, table: str, body: TableNotesIn, db: DbSession, user: AdminUser
) -> TableInfoOut:
    from app.connectors.databases import rebuild_schema_docs

    source = _database_source(db, user, source_id)
    cache = {entry["name"]: entry for entry in source.config.get("schema_cache", [])}
    if table not in cache:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such table. Re-read the schema if it's new.")
    known_columns = {c[0] for c in cache[table]["columns"]}
    column_notes = {c: n.strip()[:1000] for c, n in body.column_notes.items() if c in known_columns and n.strip()}
    notes = dict(source.config.get("table_notes", {}))
    entry = {"note": body.note.strip(), "hidden": body.hidden, "columns": column_notes}
    if any(entry.values()):
        notes[table] = entry
    else:
        notes.pop(table, None)
    source.config = {**source.config, "table_notes": notes}
    db.commit()
    # Re-describes just this table (no database connection needed).
    await rebuild_schema_docs(source.id, only={table})
    return TableInfoOut(
        name=table,
        columns=[tuple(c) for c in cache[table]["columns"]],
        primary_key=cache[table].get("primary_key", []),
        foreign_keys=[tuple(fk) for fk in cache[table].get("foreign_keys", [])],
        rows=cache[table].get("rows"),
        note=entry["note"],
        hidden=entry["hidden"],
        column_notes=column_notes,
    )
