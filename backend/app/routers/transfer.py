"""Export a chat app to a .zip file and import it into any Chat Builder install.

The file holds the app's settings, its data sources (uploaded files themselves,
and database connection details with table notes) and its test set. It never
holds conversations, feedback or the workspace's provider API keys. Database
passwords and the widget's signing secret are included only on request.

Format: app.json plus files/<n>/<filename>. Importers ignore settings they don't
know and default ones that are missing, so files move between versions.
"""

import io
import json
import secrets
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select

from app.config import get_settings
from app.connectors.databases import SERVER_KINDS
from app.connectors.databases import secret_context as db_secret_context
from app.crypto import decrypt, encrypt
from app.deps import AdminUser, DbSession
from app.evals.versions import current_version
from app.models import ChatApp, DataSource, TestCase
from app.routers.apps import AppIn, owned_app
from app.routers.embed import secret_context as embed_secret_context
from app.routers.sources import _safe_filename, queue_ingest, source_dir, tag_skus
from app.version import __version__

router = APIRouter(tags=["transfer"])

FORMAT = "chat-builder-app"
FORMAT_VERSION = 1
# Settings that describe a connection, copied as they are.
DATABASE_SETTINGS = (
    "host", "port", "database", "username", "encryption", "require_tls", "schemas", "query_timeout",
    "dirty_reads", "table_notes", "path",
)


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:60] or "chat-app"


@router.get("/api/apps/{app_id}/export")
def export_app(app_id: uuid.UUID, db: DbSession, user: AdminUser, include_secrets: bool = False) -> Response:
    app = owned_app(db, user, app_id)
    settings = {name: getattr(app, name) for name in AppIn.model_fields}
    embed = {
        "enabled": app.embed_enabled,
        "auth": app.embed_auth,
        "allowed_domains": app.allowed_domains,
        "color": app.widget_color,
    }
    if include_secrets and app.encrypted_embed_secret:
        embed["secret"] = decrypt(app.encrypted_embed_secret, embed_secret_context(app.id))

    buffer = io.BytesIO()
    sources = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, source in enumerate(
            db.scalars(select(DataSource).where(DataSource.app_id == app.id).order_by(DataSource.created_at))
        ):
            entry = {"kind": source.kind, "name": source.name}
            if source.kind in SERVER_KINDS or source.kind == "sqlite":
                entry["connection"] = {k: source.config[k] for k in DATABASE_SETTINGS if k in source.config and k != "path"}
                if include_secrets and source.encrypted_secret:
                    entry["password"] = decrypt(source.encrypted_secret, db_secret_context(source.id))
            if source.kind == "file":
                entry["skus"] = source.config.get("skus", [])
            path = source.config.get("path")
            if path and Path(path).is_file():
                entry["file"] = f"files/{index}/{source.config.get('filename') or Path(path).name}"
                archive.write(path, entry["file"])
            sources.append(entry)

        tests = [
            {k: getattr(t, k) for k in ("question", "expectation", "must_include", "reference_answer", "notes")}
            for t in db.scalars(select(TestCase).where(TestCase.app_id == app.id).order_by(TestCase.created_at))
        ]
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "chat_builder_version": __version__,
            "includes_secrets": include_secrets,
            "app": settings,
            "embed": embed,
            "sources": sources,
            "tests": tests,
        }
        archive.writestr("app.json", json.dumps(manifest, indent=2, default=str))

    filename = f"{_slug(app.name)}.chatbuilder.zip"
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _unique_name(db: DbSession, name: str) -> str:
    existing = set(db.scalars(select(ChatApp.name)))
    if name not in existing:
        return name
    n = 2
    while f"{name} ({n})" in existing:
        n += 1
    return f"{name} ({n})"


@router.post("/api/apps/import", status_code=status.HTTP_201_CREATED)
async def import_app(file: UploadFile, db: DbSession, user: AdminUser) -> dict:
    limit = get_settings().max_upload_mb * 10 * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is too large to import.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(archive.read("app.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This isn't a Chat Builder app export.") from None
    if manifest.get("format") != FORMAT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This isn't a Chat Builder app export.")
    if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This export is from a newer Chat Builder. Update this install first.")

    # Unknown settings are ignored and missing ones take their defaults.
    raw = manifest.get("app", {})
    try:
        settings = AppIn(**{k: v for k, v in raw.items() if k in AppIn.model_fields})
    except ValidationError as exc:
        problem = exc.errors()[0]
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid app setting {problem['loc'][0]}: {problem['msg']}") from None

    app = ChatApp(owner_id=user.id, **settings.model_dump())
    app.name = _unique_name(db, app.name)
    embed = manifest.get("embed", {})
    app.embed_enabled = bool(embed.get("enabled", False))
    app.embed_auth = embed.get("auth") if embed.get("auth") in ("login", "signed", "public") else "signed"
    app.allowed_domains = [d for d in embed.get("allowed_domains", []) if isinstance(d, str)]
    app.widget_color = embed.get("color") or "#0f172a"
    db.add(app)
    db.flush()
    new_secret = False
    if embed.get("secret"):
        app.encrypted_embed_secret = encrypt(str(embed["secret"]), embed_secret_context(app.id))
    elif app.embed_auth == "signed":
        # Sites signing for this app need the new secret (shown in the Deploy tab).
        app.encrypted_embed_secret = encrypt(secrets.token_urlsafe(32), embed_secret_context(app.id))
        new_secret = app.embed_enabled
    current_version(db, app)

    to_ingest: list[uuid.UUID] = []
    needs_password: list[str] = []
    names = set(archive.namelist())
    for entry in manifest.get("sources", []):
        kind = entry.get("kind")
        source = DataSource(id=uuid.uuid4(), app_id=app.id, kind=kind, name=str(entry.get("name") or kind)[:200], status="pending", config={})
        stored_file = entry.get("file")
        if kind in ("file", "spreadsheet", "sqlite"):
            if not stored_file or stored_file not in names:
                continue
            filename = _safe_filename(Path(stored_file).name)
            directory = source_dir(source.id)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            # Written by name we control; the archive's own paths are never used on disk.
            with archive.open(stored_file) as src, path.open("wb") as out:
                shutil.copyfileobj(src, out)
            source.config = {"path": str(path), "filename": filename, "size": path.stat().st_size}
            if kind == "sqlite":
                source.config.update({k: v for k, v in entry.get("connection", {}).items() if k in DATABASE_SETTINGS and k != "path"})
            if kind == "file":
                tag_skus(source, [str(s) for s in entry.get("skus", [])])
            to_ingest.append(source.id)
        elif kind in SERVER_KINDS:
            source.config = {k: v for k, v in entry.get("connection", {}).items() if k in DATABASE_SETTINGS and k != "path"}
            if entry.get("password"):
                source.encrypted_secret = encrypt(str(entry["password"]), db_secret_context(source.id))
                to_ingest.append(source.id)
            else:
                source.status = "failed"
                source.error = "Imported without its password. Edit the connection to enter it."
                needs_password.append(source.name)
        else:
            continue
        db.add(source)

    for test in manifest.get("tests", []):
        if not test.get("question"):
            continue
        db.add(
            TestCase(
                app_id=app.id,
                question=str(test["question"])[:8000],
                expectation="refuse" if test.get("expectation") == "refuse" else "answer",
                must_include=str(test.get("must_include") or ""),
                reference_answer=str(test.get("reference_answer") or ""),
                notes=str(test.get("notes") or ""),
            )
        )
    db.commit()
    for source_id in to_ingest:
        await queue_ingest(source_id)
    return {
        "app_id": str(app.id),
        "name": app.name,
        "sources": len(to_ingest) + len(needs_password),
        "needs_password": needs_password,
        # Embedding sites must switch to the new secret from the Deploy tab.
        "new_signing_secret": new_secret,
    }
