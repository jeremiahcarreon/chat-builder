import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
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
    "host", "port", "database", "username", "encrypt", "trust_server_certificate", "schemas",
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


def owned_source(db: DbSession, user: CurrentUser, source_id: uuid.UUID) -> DataSource:
    source = db.get(DataSource, source_id)
    if source is None or db.get(ChatApp, source.app_id).owner_id != user.id:
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
def list_all_sources(db: DbSession, user: CurrentUser) -> list[SourceOut]:
    rows = db.execute(
        select(DataSource, ChatApp.name)
        .join(ChatApp, ChatApp.id == DataSource.app_id)
        .where(ChatApp.owner_id == user.id)
        .order_by(ChatApp.name, DataSource.created_at)
    ).all()
    return [source_out(source, app_name) for source, app_name in rows]


@router.get("/apps/{app_id}/sources")
def list_sources(app_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[SourceOut]:
    owned_app(db, user, app_id)
    rows = db.scalars(
        select(DataSource).where(DataSource.app_id == app_id).order_by(DataSource.created_at)
    )
    return [source_out(s) for s in rows]


@router.post("/apps/{app_id}/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload_files(
    app_id: uuid.UUID, files: list[UploadFile], db: DbSession, user: CurrentUser
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
def rename_source(source_id: uuid.UUID, body: RenameSource, db: DbSession, user: CurrentUser) -> SourceOut:
    source = owned_source(db, user, source_id)
    source.name = body.name.strip()
    db.commit()
    return source_out(source)


@router.post("/sources/{source_id}/reprocess")
async def reprocess_source(source_id: uuid.UUID, db: DbSession, user: CurrentUser) -> SourceOut:
    source = owned_source(db, user, source_id)
    source.status = "pending"
    source.error = None
    db.commit()
    await queue_ingest(source.id)
    return source_out(source)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    source = owned_source(db, user, source_id)
    clear_index(source.id)
    from app.connectors.spreadsheets import drop_sheet_tables

    drop_sheet_tables(source)
    db.delete(source)
    db.commit()
    shutil.rmtree(source_dir(source_id), ignore_errors=True)
