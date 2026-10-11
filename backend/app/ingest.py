"""Processes a data source in the background: documents are chunked and embedded;
spreadsheets and databases are indexed by later steps."""

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete

from app.db import SessionLocal
from app.models import Chunk, DataSource, SchemaDoc
from app.rag.chunking import chunk_sections
from app.rag.embeddings import EmbeddingError, embed_documents
from app.rag.parsing import ParseError, extract

log = logging.getLogger(__name__)


class IngestError(Exception):
    """A user-facing reason a source couldn't be processed."""


def _set_status(source_id: uuid.UUID, status: str, error: str | None = None, **config) -> None:
    with SessionLocal() as db:
        source = db.get(DataSource, source_id)
        if source is None:
            return
        source.status = status
        source.error = error
        if config:
            source.config = {**source.config, **config}
        db.commit()


def _load(source_id: uuid.UUID) -> DataSource | None:
    with SessionLocal() as db:
        source = db.get(DataSource, source_id)
        if source is not None:
            db.expunge(source)
        return source


def source_file(source: DataSource) -> Path:
    return Path(source.config["path"])


async def ingest_document(source: DataSource) -> dict:
    sections = await asyncio.to_thread(extract, source_file(source))
    chunks = chunk_sections(sections)
    vectors = await embed_documents([c.text for c in chunks], title=source.name)

    def save() -> None:
        with SessionLocal() as db:
            db.execute(delete(Chunk).where(Chunk.source_id == source.id))
            db.add_all(
                Chunk(
                    app_id=source.app_id,
                    source_id=source.id,
                    ordinal=index,
                    content=chunk.text,
                    meta={"page": chunk.page} if chunk.page else {},
                    embedding=vector,
                )
                for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
            )
            db.commit()

    await asyncio.to_thread(save)
    pages = max((s.page or 0 for s in sections), default=0)
    return {"chunk_count": len(chunks), "page_count": pages or None}


async def ingest_source(source_id: uuid.UUID) -> None:
    source = _load(source_id)
    if source is None:
        log.info("Source %s was deleted before processing", source_id)
        return
    await asyncio.to_thread(_set_status, source_id, "processing")
    try:
        if source.kind == "file":
            stats = await ingest_document(source)
        elif source.kind == "spreadsheet":
            from app.connectors.spreadsheets import ingest_spreadsheet

            stats = await ingest_spreadsheet(source)
        elif source.kind == "mcp":
            from app.connectors.mcp import index_mcp

            stats = await index_mcp(source)
        else:
            from app.connectors.databases import DATABASE_KINDS, index_database

            if source.kind not in DATABASE_KINDS:
                raise IngestError(f"Unknown data source type: {source.kind}")
            stats = await index_database(source)
    except (ParseError, EmbeddingError, IngestError) as exc:
        log.warning("Processing %s failed: %s", source_id, exc)
        await asyncio.to_thread(_set_status, source_id, "failed", str(exc))
        return
    except Exception:
        log.exception("Processing %s failed", source_id)
        await asyncio.to_thread(
            _set_status, source_id, "failed", "Unexpected error while processing. See the worker logs."
        )
        return
    await asyncio.to_thread(_set_status, source_id, "ready", None, **stats)


def clear_index(source_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        db.execute(delete(Chunk).where(Chunk.source_id == source_id))
        db.execute(delete(SchemaDoc).where(SchemaDoc.source_id == source_id))
        db.commit()
