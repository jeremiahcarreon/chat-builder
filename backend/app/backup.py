"""Full, password-encrypted backups for moving a whole install.

A backup holds the database (every app, conversation, person, vector and
setting), the uploaded files, and a manifest. It is encrypted with a
passphrase the admin chooses: scrypt turns the passphrase into a key, and the
data is sealed in 1 MB chunks with AES-256-GCM, so a wrong passphrase or any
tampering is detected before anything is restored.

Secrets in the database are encrypted with the install's MASTER_KEY. The old
key travels inside the (encrypted) backup, and restoring re-encrypts every
secret with the new install's key, so the new .env needs no changes.

    python -m app.backup create > backup.cbbackup     # passphrase from CB_BACKUP_PASSPHRASE or prompt
    python -m app.backup restore backup.cbbackup      # the app must be stopped
"""

import base64
import getpass
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"CBBACKUP1"
CHUNK = 1024 * 1024
SALT_BYTES = 16
NONCE_PREFIX_BYTES = 7  # + 4-byte counter + 1-byte final flag = 12-byte GCM nonce
MIN_PASSPHRASE = 12


class BackupError(Exception):
    """A user-facing problem creating or restoring a backup."""


# ------------------------------------------------------------------ crypto


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def _nonce(prefix: bytes, index: int, final: bool) -> bytes:
    return prefix + struct.pack(">I", index) + (b"\x01" if final else b"\x00")


def encrypt_stream(source: BinaryIO, target: BinaryIO, passphrase: str) -> None:
    """Writes MAGIC, salt, nonce prefix, then length-prefixed sealed chunks; the last is marked final."""
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    salt, prefix = os.urandom(SALT_BYTES), os.urandom(NONCE_PREFIX_BYTES)
    aes = AESGCM(_key(passphrase, salt))
    target.write(MAGIC + salt + prefix)
    index = 0
    chunk = source.read(CHUNK)
    while True:
        following = source.read(CHUNK)
        final = not following
        sealed = aes.encrypt(_nonce(prefix, index, final), chunk, MAGIC)
        target.write(struct.pack(">I", len(sealed)) + sealed)
        if final:
            return
        chunk, index = following, index + 1


def decrypt_stream(source: BinaryIO, target: BinaryIO, passphrase: str) -> None:
    header = source.read(len(MAGIC) + SALT_BYTES + NONCE_PREFIX_BYTES)
    if not header.startswith(MAGIC):
        raise BackupError("This isn't a Chat Builder backup.")
    salt = header[len(MAGIC) : len(MAGIC) + SALT_BYTES]
    prefix = header[len(MAGIC) + SALT_BYTES :]
    aes = AESGCM(_key(passphrase, salt))

    def read_chunk() -> bytes | None:
        size = source.read(4)
        if not size:
            return None
        if len(size) < 4:
            raise BackupError("The backup file is incomplete.")
        sealed = source.read(struct.unpack(">I", size)[0])
        return sealed

    index = 0
    current = read_chunk()
    if current is None:
        raise BackupError("The backup file is empty.")
    while current is not None:
        following = read_chunk()
        # The final flag is part of the nonce, so a truncated file can't pass as complete.
        try:
            target.write(aes.decrypt(_nonce(prefix, index, following is None), current, MAGIC))
        except InvalidTag:
            if index == 0:
                raise BackupError("Wrong passphrase, or the file was changed.") from None
            raise BackupError("The backup file is damaged or incomplete.") from None
        current, index = following, index + 1


# ---------------------------------------------------------------- database


def _libpq_url() -> str:
    from app.config import get_settings

    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _bundled_pg_bin() -> Path | None:
    """The Postgres tools of the pgserver package, when installed (development and test setups)."""
    spec = importlib.util.find_spec("pgserver")
    if spec is None or not spec.submodule_search_locations:
        return None
    folder = Path(next(iter(spec.submodule_search_locations))) / "pginstall" / "bin"
    return folder if folder.is_dir() else None


def _pg_tool(name: str) -> str:
    """pg_dump or pg_restore: from PG_BIN, the PATH (the Docker image), or a bundled Postgres."""
    candidates = []
    if folder := os.environ.get("PG_BIN"):
        candidates.append(Path(folder) / name)
    if found := shutil.which(name):
        candidates.append(Path(found))
    if bundled := _bundled_pg_bin():
        candidates.append(bundled / name)
    for path in candidates:
        if path.is_file():
            return str(path)
    raise BackupError(f"{name} isn't available in this environment. Install the PostgreSQL client tools or set PG_BIN.")


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"{Path(args[0]).name} failed: {result.stderr.strip()[-500:]}")


# ------------------------------------------------------------------ create


def create(target: BinaryIO, passphrase: str) -> dict:
    """Writes an encrypted backup of this install to target. Returns the manifest (without secrets)."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import engine
    from app.version import __version__

    settings = get_settings()
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    manifest = {
        "format": "chat-builder-backup",
        "format_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "chat_builder_version": __version__,
        "alembic_revision": revision,
        "embedding_model": settings.embedding_model,
        "upload_dir": str(Path(settings.upload_dir).resolve()),
        # Lets the new install decrypt the stored secrets and re-encrypt them with its own key.
        "master_key": settings.master_key,
    }
    with tempfile.TemporaryDirectory() as work:
        dump = Path(work) / "database.dump"
        _run([_pg_tool("pg_dump"), "--format=custom", "--no-owner", "--no-acl", f"--file={dump}", _libpq_url()])
        bundle = Path(work) / "bundle.tar"
        with tarfile.open(bundle, "w") as tar:
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            tar.add(dump, arcname="database.dump")
            uploads = Path(settings.upload_dir)
            if uploads.is_dir():
                tar.add(uploads, arcname="uploads")
        with bundle.open("rb") as source:
            encrypt_stream(source, target, passphrase)
    return {k: v for k, v in manifest.items() if k != "master_key"}


# ----------------------------------------------------------------- restore


def source_secret_context(kind: str, source_id) -> str:
    """The context a data source's secret is encrypted with: an MCP token or a database password."""
    from app.connectors import databases, mcp

    if kind == mcp.MCP_KIND:
        return mcp.secret_context(source_id)
    return databases.secret_context(source_id)


def _re_encrypt(conn, old_key: bytes, new_key: bytes) -> int:
    """Decrypts every stored secret with the old install's key and re-encrypts it with this one's."""
    from sqlalchemy import text

    def swap(value: str, context: str) -> str:
        raw = base64.b64decode(value)
        plain = AESGCM(old_key).decrypt(raw[:12], raw[12:], context.encode())
        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(new_key).encrypt(nonce, plain, context.encode())).decode()

    # (table, secret column, select list, context the value was encrypted with)
    targets = [
        ("provider_keys", "encrypted_key", "id, provider, encrypted_key", lambda r: f"provider_key:{r['provider']}"),
        ("data_sources", "encrypted_secret", "id, kind, encrypted_secret", lambda r: source_secret_context(r["kind"], r["id"])),
        ("chat_apps", "encrypted_embed_secret", "id, encrypted_embed_secret", lambda r: f"embed_secret:{r['id']}"),
    ]
    count = 0
    for table, column, columns, context in targets:
        for row in conn.execute(text(f"SELECT {columns} FROM {table} WHERE {column} IS NOT NULL")).mappings().all():
            conn.execute(
                text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                {"value": swap(row[column], context(row)), "id": row["id"]},
            )
            count += 1
    return count


def restore(source: BinaryIO, passphrase: str) -> dict:
    """Replaces this install's database and uploads with a backup. Run with the app stopped."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import engine

    settings = get_settings()
    with tempfile.TemporaryDirectory() as work:
        bundle = Path(work) / "bundle.tar"
        with bundle.open("wb") as out:
            decrypt_stream(source, out, passphrase)
        with tarfile.open(bundle) as tar:
            manifest = json.loads(tar.extractfile("manifest.json").read())
            if manifest.get("format") != "chat-builder-backup":
                raise BackupError("This isn't a Chat Builder backup.")
            # Only these two entries are used; the archive's own paths never decide where files go.
            members = [m for m in tar.getmembers() if m.name == "database.dump" or m.name.startswith("uploads/")]
            tar.extractall(Path(work) / "contents", members=members, filter="data")

        with engine.connect() as conn:
            # A database Chat Builder hasn't set up yet has no version table: nothing to compare.
            has_versions = conn.execute(text("SELECT to_regclass('public.alembic_version')")).scalar()
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar() if has_versions else None
        backup_revision = manifest.get("alembic_revision") or ""
        if current and backup_revision and backup_revision > current:
            raise BackupError(
                f"This backup is from a newer Chat Builder ({manifest.get('chat_builder_version')}). Update this install first."
            )

        # Start from an empty database: a newer install may have tables the backup
        # doesn't, and migrations run again on start from the backup's revision.
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS sheets CASCADE"))
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        _run([
            _pg_tool("pg_restore"), "--no-owner", "--no-acl", "--single-transaction", "--exit-on-error",
            f"--dbname={_libpq_url()}", str(Path(work) / "contents" / "database.dump"),
        ])

        # Uploaded files, with stored paths pointed at this install's upload folder.
        uploads = Path(settings.upload_dir)
        restored = Path(work) / "contents" / "uploads"
        if restored.is_dir():
            uploads.mkdir(parents=True, exist_ok=True)
            shutil.copytree(restored, uploads, dirs_exist_ok=True)
        old_dir, new_dir = manifest.get("upload_dir", ""), str(uploads.resolve())
        with engine.begin() as conn:
            if old_dir and old_dir != new_dir:
                conn.execute(
                    text(
                        "UPDATE data_sources SET config = jsonb_set(config, '{path}', "
                        "to_jsonb(:new || substr(config->>'path', length(:old) + 1))) "
                        "WHERE config->>'path' LIKE :prefix"
                    ),
                    {"new": new_dir, "old": old_dir, "prefix": old_dir + "%"},
                )
            secrets = 0
            old_key = base64.b64decode(manifest["master_key"])
            if old_key != settings.master_key_bytes:
                secrets = _re_encrypt(conn, old_key, settings.master_key_bytes)
            # Roles belong to the Postgres server, not the dump: a server Chat Builder hasn't run on
            # yet doesn't have the spreadsheet reader. Grants weren't restored (--no-acl);
            # per-spreadsheet access is re-granted on first use.
            conn.execute(text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbuilder_sheet_reader') "
                "THEN CREATE ROLE chatbuilder_sheet_reader NOLOGIN; END IF; END $$"
            ))
            conn.execute(text("GRANT chatbuilder_sheet_reader TO CURRENT_USER"))
            conn.execute(text("GRANT USAGE ON SCHEMA sheets TO chatbuilder_sheet_reader"))
        engine.dispose()

    warnings = []
    if manifest.get("embedding_model") != settings.embedding_model:
        warnings.append(
            f"The backup's documents were embedded with {manifest.get('embedding_model')}, but this install uses "
            f"{settings.embedding_model}. Reprocess documents (Data sources) for search to work."
        )
    return {
        "from_version": manifest.get("chat_builder_version"),
        "created_at": manifest.get("created_at"),
        "secrets_re_encrypted": secrets,
        "warnings": warnings,
    }


# --------------------------------------------------------------------- CLI


def _passphrase(confirm: bool) -> str:
    value = os.environ.get("CB_BACKUP_PASSPHRASE")
    if value:
        return value
    value = getpass.getpass("Backup passphrase: ")
    if confirm and getpass.getpass("Repeat the passphrase: ") != value:
        raise BackupError("The passphrases don't match.")
    return value


def main(argv: list[str]) -> int:
    if len(argv) < 1 or argv[0] not in ("create", "restore"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[0] == "create":
            out = sys.stdout.buffer if len(argv) < 2 or argv[1] == "-" else open(argv[1], "wb")
            info = create(out, _passphrase(confirm=True))
            print(f"Backup created (Chat Builder {info['chat_builder_version']}).", file=sys.stderr)
        else:
            if len(argv) < 2:
                print("Usage: python -m app.backup restore <file|->", file=sys.stderr)
                return 2
            source = sys.stdin.buffer if argv[1] == "-" else open(argv[1], "rb")
            result = restore(source, _passphrase(confirm=False))
            print(f"Restored a backup from Chat Builder {result['from_version']} ({result['created_at']}).", file=sys.stderr)
            print(f"Re-encrypted {result['secrets_re_encrypted']} saved secret(s) for this install.", file=sys.stderr)
            for warning in result["warnings"]:
                print(f"Warning: {warning}", file=sys.stderr)
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
