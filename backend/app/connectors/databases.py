"""Live database connections: SQL Server, MySQL, MariaDB, PostgreSQL and SQLite files.

Each source's schema is indexed (tables and columns) so the right tables can be
offered to the model; queries run live against the database, read-only.
"""

import asyncio
import sqlite3
import time
import uuid
from dataclasses import dataclass, field

from sqlglot import exp

from app.connectors.results import format_results
from app.connectors.sql_safety import check_select
from app.crypto import decrypt
from app.db import SessionLocal
from app.ingest import IngestError
from app.models import DataSource, SchemaDoc
from app.rag.embeddings import embed_documents

SERVER_KINDS = {"mssql", "mysql", "mariadb", "postgres"}
DATABASE_KINDS = SERVER_KINDS | {"sqlite"}
DEFAULT_PORTS = {"mssql": 1433, "mysql": 3306, "mariadb": 3306, "postgres": 5432}
KIND_LABELS = {
    "mssql": "SQL Server",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "postgres": "PostgreSQL",
    "sqlite": "SQLite",
}
CONNECT_TIMEOUT = 10
QUERY_TIMEOUT = 30
MAX_TABLES = 500
SYSTEM_SCHEMAS = {
    "information_schema", "pg_catalog", "pg_toast", "sys", "mysql", "performance_schema",
}


class ConnectionFailed(Exception):
    """A user-facing connection problem."""


@dataclass
class ConnectionSettings:
    kind: str
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = ""
    # SQL Server: "off", "request" or "require". Others: require TLS when true.
    encryption: str = "request"
    require_tls: bool = False
    # Optional list of schemas to include (empty means all).
    schemas: list[str] = field(default_factory=list)
    path: str = ""  # SQLite file


@dataclass
class TableInfo:
    schema: str
    name: str
    columns: list[tuple[str, str]]

    def qualified(self, kind: str) -> str:
        if kind in ("sqlite", "mysql", "mariadb") or not self.schema:
            return self.name
        return f"{self.schema}.{self.name}"


def secret_context(source_id: uuid.UUID) -> str:
    return f"data_source:{source_id}"


def settings_for(source: DataSource) -> ConnectionSettings:
    cfg = source.config
    password = decrypt(source.encrypted_secret, secret_context(source.id)) if source.encrypted_secret else ""
    return ConnectionSettings(
        kind=source.kind,
        host=cfg.get("host", ""),
        port=cfg.get("port"),
        database=cfg.get("database", ""),
        username=cfg.get("username", ""),
        password=password,
        encryption=cfg.get("encryption", "request"),
        require_tls=cfg.get("require_tls", False),
        schemas=cfg.get("schemas", []),
        path=cfg.get("path", ""),
    )


def connect(cs: ConnectionSettings):
    """Opens a connection configured for read-only use where the database supports it."""
    port = cs.port or DEFAULT_PORTS.get(cs.kind)
    try:
        if cs.kind == "mssql":
            import pymssql

            return pymssql.connect(
                server=cs.host,
                port=str(port),
                user=cs.username,
                password=cs.password,
                database=cs.database,
                login_timeout=CONNECT_TIMEOUT,
                timeout=QUERY_TIMEOUT,
                encryption=cs.encryption,
            )
        if cs.kind in ("mysql", "mariadb"):
            import pymysql

            conn = pymysql.connect(
                host=cs.host,
                port=port,
                user=cs.username,
                password=cs.password,
                database=cs.database or None,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=QUERY_TIMEOUT,
                ssl={} if cs.require_tls else None,
                charset="utf8mb4",
            )
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                for statement in (
                    f"SET SESSION max_execution_time = {QUERY_TIMEOUT * 1000}",  # MySQL
                    f"SET SESSION max_statement_time = {QUERY_TIMEOUT}",  # MariaDB
                ):
                    try:
                        cur.execute(statement)
                    except pymysql.MySQLError:
                        pass
            return conn
        if cs.kind == "postgres":
            import psycopg

            options = f"-c default_transaction_read_only=on -c statement_timeout={QUERY_TIMEOUT * 1000}"
            if cs.schemas:
                # Lets unqualified table names resolve within the included schemas.
                path = ",".join('"' + s.replace('"', '""').replace(" ", "\\ ") + '"' for s in cs.schemas)
                options += f" -c search_path={path},public"
            return psycopg.connect(
                host=cs.host,
                port=port,
                dbname=cs.database or None,
                user=cs.username or None,
                password=cs.password or None,
                connect_timeout=CONNECT_TIMEOUT,
                sslmode="require" if cs.require_tls else "prefer",
                options=options,
            )
        if cs.kind == "sqlite":
            conn = sqlite3.connect(f"file:{cs.path}?mode=ro", uri=True, timeout=CONNECT_TIMEOUT)
            deadline = time.monotonic() + QUERY_TIMEOUT
            # Abort long-running queries.
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            return conn
    except Exception as exc:  # noqa: BLE001 - every driver raises its own types
        raise ConnectionFailed(_friendly_error(exc)) from exc
    raise ConnectionFailed(f"Unsupported database type: {cs.kind}")


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip()
    if isinstance(exc.args, tuple) and exc.args and isinstance(exc.args[0], tuple):
        # pymssql packs (code, bytes message)
        code, raw = exc.args[0][:2]
        message = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    lowered = message.lower()
    if "login failed" in lowered or "access denied" in lowered or "password authentication failed" in lowered:
        return "Login failed: check the username and password."
    if any(s in lowered for s in ("timed out", "timeout", "unable to connect", "connection refused", "can't connect", "could not connect", "adaptive server is unavailable")):
        return (
            "Could not reach the database server. Check the host and port, and that the server "
            "accepts connections from this machine. From inside Docker, use the server's IP or "
            "host.docker.internal for a database on this computer, not localhost."
        )
    if "unable to open database" in lowered:
        return "Could not open the SQLite file."
    first = message.splitlines()[0] if message else exc.__class__.__name__
    return f"Connection failed: {first[:300]}"


def _rows(conn, sql: str) -> list[tuple]:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def list_tables(conn, cs: ConnectionSettings) -> list[TableInfo]:
    if cs.kind == "sqlite":
        tables = []
        names = _rows(conn, "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
        for (name,) in names[:MAX_TABLES]:
            quoted = name.replace('"', '""')
            columns = [(row[1], row[2] or "") for row in _rows(conn, f'PRAGMA table_info("{quoted}")')]
            tables.append(TableInfo("", name, columns))
        return tables

    if cs.kind in ("mysql", "mariadb"):
        sql = (
            "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position"
        )
    else:
        sql = (
            "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
            "ORDER BY table_schema, table_name, ordinal_position"
        )
    wanted = {s.lower() for s in cs.schemas}
    tables: dict[tuple[str, str], TableInfo] = {}
    for schema, table, column, data_type in _rows(conn, sql):
        if (schema or "").lower() in SYSTEM_SCHEMAS:
            continue
        if wanted and (schema or "").lower() not in wanted:
            continue
        key = (schema, table)
        if key not in tables:
            if len(tables) >= MAX_TABLES:
                continue
            tables[key] = TableInfo(schema or "", table, [])
        tables[key].columns.append((column, data_type))
    return list(tables.values())


def test_connection(cs: ConnectionSettings) -> int:
    """Connects and returns the number of tables found. Raises ConnectionFailed."""
    conn = connect(cs)
    try:
        return len(list_tables(conn, cs))
    except Exception as exc:  # noqa: BLE001
        raise ConnectionFailed(_friendly_error(exc)) from exc
    finally:
        conn.close()


def _describe(source: DataSource, table: TableInfo) -> str:
    columns = "\n".join(f"- {name} ({data_type})" for name, data_type in table.columns)
    return (
        f"Table {table.qualified(source.kind)} in {KIND_LABELS[source.kind]} database "
        f'"{source.name}". Columns:\n{columns}'
    )


async def index_database(source: DataSource) -> dict:
    cs = settings_for(source)

    def read_schema() -> list[TableInfo]:
        conn = connect(cs)
        try:
            return list_tables(conn, cs)
        finally:
            conn.close()

    try:
        tables = await asyncio.to_thread(read_schema)
    except ConnectionFailed as exc:
        raise IngestError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise IngestError(_friendly_error(exc)) from exc
    if not tables:
        raise IngestError("Connected, but no tables were found that this login can see.")

    descriptions = [_describe(source, t) for t in tables]
    vectors = await embed_documents(descriptions, title=source.name)

    def save() -> None:
        with SessionLocal() as db:
            db.query(SchemaDoc).filter(SchemaDoc.source_id == source.id).delete()
            for table, description, vector in zip(tables, descriptions, vectors, strict=True):
                db.add(
                    SchemaDoc(
                        app_id=source.app_id,
                        source_id=source.id,
                        table_name=table.qualified(source.kind),
                        description=description,
                        columns=[{"name": n, "type": t} for n, t in table.columns],
                        embedding=vector,
                    )
                )
            db.commit()

    await asyncio.to_thread(save)
    return {"table_count": len(tables)}


def _check_tables(tree: exp.Expression, source: DataSource, allowed: set[str]) -> None:
    """Only tables that were indexed for this source may be queried (respects the schema filter)."""
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed_names = {a.rsplit(".", 1)[-1] for a in allowed}
    database = (source.config.get("database") or "").lower()
    for table in tree.find_all(exp.Table):
        name, schema, catalog = table.name.lower(), table.db.lower(), table.catalog.lower()
        if not schema and name in cte_names:
            continue
        if catalog and catalog != database:
            raise PermissionError("Queries can't reach other databases on the server.")
        if source.kind in ("mysql", "mariadb") and schema:
            # In MySQL the "schema" is the database itself.
            if schema != database:
                raise PermissionError("Queries can't reach other databases on the server.")
            schema = ""
        full = f"{schema}.{name}" if schema else name
        if full not in allowed and not (not schema and name in allowed_names):
            raise PermissionError(f"Table {full} is not available. Use the tables listed.")


def run_database_query(source_id: uuid.UUID, sql: str, max_rows: int) -> str:
    with SessionLocal() as db:
        source = db.get(DataSource, source_id)
        allowed = {
            name.lower()
            for (name,) in db.query(SchemaDoc.table_name).filter(SchemaDoc.source_id == source_id)
        }
        db.expunge(source)
    tree = check_select(sql, source.kind)
    _check_tables(tree, source, allowed)

    cs = settings_for(source)
    if source.kind == "postgres" and not cs.schemas:
        cs.schemas = sorted({name.rsplit(".", 1)[0] for name in allowed if "." in name})
    conn = connect(cs)
    try:
        cur = conn.cursor()
        # The validated SQL is sent as written, with no parameters, in the source's own dialect.
        cur.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
        cur.close()
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    return format_results(columns, [tuple(r) for r in rows[:max_rows]], len(rows) > max_rows)
