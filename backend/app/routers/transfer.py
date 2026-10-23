"""Export a chat app to a .zip file and import it into any Chat Builder install,
or copy it within this install, choosing what comes along.

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
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, insert, literal, select

from app.config import get_settings
from app.connectors.databases import SERVER_KINDS
from app.connectors.databases import secret_context as db_secret_context
from app.connectors.mcp import secret_context as mcp_secret_context
from app.crypto import decrypt, encrypt
from app.deps import AdminUser, DbSession
from app.evals.versions import current_version
from app.models import ChatApp, Chunk, DataSource, SchemaDoc, TestCase
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
            if source.kind == "mcp":
                entry["mcp"] = {"url": source.config.get("url", ""), "enabled": source.config.get("enabled", [])}
                if include_secrets and source.encrypted_secret:
                    entry["token"] = decrypt(source.encrypted_secret, mcp_secret_context(source.id))
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
        elif kind == "mcp":
            settings = entry.get("mcp") or {}
            url = str(settings.get("url") or "")
            if not url.lower().startswith(("http://", "https://")):
                continue
            # The tool choices are kept; processing re-lists the server's tools.
            source.config = {"url": url, "imported_enabled": [str(n) for n in settings.get("enabled", [])]}
            if entry.get("token"):
                source.encrypted_secret = encrypt(str(entry["token"]), mcp_secret_context(source.id))
            to_ingest.append(source.id)
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


# ------------------------------------------------------------------ copy

# What a copy can take from the original, in groups a person can pick from. Settings in
# no group (provider and model) are always copied; the copy needs some model to run.
SETTING_GROUPS = [
    {
        "id": "personality",
        "label": "Personality and knowledge",
        "hint": "Description, welcome message, personality and knowledge notes.",
        "fields": ("description", "welcome_message", "personality", "knowledge_notes"),
    },
    {
        "id": "scope",
        "label": "Scope and guardrails",
        "hint": "What's in and out of scope, the refusal message, strict mode and the classifier settings.",
        "fields": (
            "in_scope", "out_of_scope", "refusal_message", "strict_mode", "classifier_enabled",
            "classifier_provider", "classifier_model", "classifier_strictness", "classifier_fail_open",
            "classifier_purpose",
        ),
    },
    {
        "id": "model",
        "label": "Model settings and automatic routing",
        "hint": "Temperature, seed, answer length and the model ladder. The provider and model always come along.",
        "fields": ("temperature", "seed", "max_answer_tokens", "auto_route", "route_tiers", "router"),
    },
    {
        "id": "limits",
        "label": "Usage limits",
        "hint": "Messages per minute and the daily token limit.",
        "fields": ("messages_per_minute", "daily_token_limit"),
    },
]
ALWAYS_COPIED = ("provider", "model")


def copied_settings(app: ChatApp, groups: set[str]) -> AppIn:
    """The copy's settings: the chosen groups from the original, defaults for the rest."""
    fields = set(ALWAYS_COPIED)
    for group in SETTING_GROUPS:
        if group["id"] in groups:
            fields.update(group["fields"])
    return AppIn(name=app.name, **{name: getattr(app, name) for name in fields})


class CopyOptions(BaseModel):
    name: str
    settings: list[dict]
    sources: list[dict]
    test_count: int
    widget: bool


class CopyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: list[str] = Field(default_factory=list)
    sources: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    tests: bool = False
    # The widget settings (sites, sign-in mode, colour); the copy always gets its own signing secret.
    widget: bool = False


@router.get("/api/apps/{app_id}/copy-options")
def copy_options(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> CopyOptions:
    app = owned_app(db, user, app_id)
    sources = db.scalars(select(DataSource).where(DataSource.app_id == app.id).order_by(DataSource.created_at))
    test_count = db.scalar(select(func.count()).select_from(TestCase).where(TestCase.app_id == app.id)) or 0
    return CopyOptions(
        name=_unique_name(db, f"{app.name} copy"),
        settings=[{k: g[k] for k in ("id", "label", "hint")} for g in SETTING_GROUPS],
        sources=[{"id": str(s.id), "kind": s.kind, "name": s.name, "status": s.status} for s in sources],
        test_count=test_count,
        widget=app.embed_enabled,
    )


def _copy_source(db: DbSession, source: DataSource, app: ChatApp) -> DataSource:
    """A source for the new app with the same settings, files and search index, so nothing is reprocessed."""
    copy = DataSource(
        id=uuid.uuid4(), app_id=app.id, kind=source.kind, name=source.name,
        config=dict(source.config), status=source.status, error=source.error,
    )
    path = source.config.get("path")
    if path and Path(path).is_file():
        directory = source_dir(copy.id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / Path(path).name
        shutil.copy2(path, target)
        copy.config["path"] = str(target)
    if source.encrypted_secret:
        # Secrets are bound to the source's id, so the copy needs them under its own.
        context = mcp_secret_context if source.kind == "mcp" else db_secret_context
        copy.encrypted_secret = encrypt(decrypt(source.encrypted_secret, context(source.id)), context(copy.id))
    db.add(copy)
    db.flush()
    for table in (Chunk, SchemaDoc):
        columns = [c for c in table.__table__.columns if c.name not in ("id", "app_id", "source_id")]
        db.execute(
            insert(table).from_select(
                ["app_id", "source_id", *[c.name for c in columns]],
                select(literal(app.id), literal(copy.id), *columns).where(table.source_id == source.id),
            )
        )
    return copy


@router.post("/api/apps/{app_id}/copy", status_code=status.HTTP_201_CREATED)
async def copy_app(app_id: uuid.UUID, body: CopyIn, db: DbSession, user: AdminUser) -> dict:
    original = owned_app(db, user, app_id)
    unknown = set(body.settings) - {g["id"] for g in SETTING_GROUPS}
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown setting group: {', '.join(sorted(unknown))}")
    settings = copied_settings(original, set(body.settings))
    app = ChatApp(owner_id=user.id, **settings.model_dump())
    app.name = _unique_name(db, body.name.strip())
    if body.widget:
        app.embed_enabled = original.embed_enabled
        app.embed_auth = original.embed_auth
        app.allowed_domains = list(original.allowed_domains)
        app.widget_color = original.widget_color
    db.add(app)
    db.flush()
    if app.embed_auth == "signed":
        app.encrypted_embed_secret = encrypt(secrets.token_urlsafe(32), embed_secret_context(app.id))
    current_version(db, app)

    wanted = set(body.sources)
    to_ingest: list[uuid.UUID] = []
    copied = 0
    if wanted:
        sources = db.scalars(select(DataSource).where(DataSource.app_id == original.id, DataSource.id.in_(wanted)))
        for source in sources:
            copy = _copy_source(db, source, app)
            copied += 1
            if source.status != "ready":
                # Never finished (or failed) on the original: try again for the copy.
                copy.status = "pending"
                copy.error = None
                to_ingest.append(copy.id)

    tests = 0
    if body.tests:
        for test in db.scalars(select(TestCase).where(TestCase.app_id == original.id).order_by(TestCase.created_at)):
            db.add(
                TestCase(
                    app_id=app.id, question=test.question, expectation=test.expectation,
                    must_include=test.must_include, reference_answer=test.reference_answer, notes=test.notes,
                )
            )
            tests += 1
    db.commit()
    for source_id in to_ingest:
        await queue_ingest(source_id)
    return {"app_id": str(app.id), "name": app.name, "sources": copied, "tests": tests}
