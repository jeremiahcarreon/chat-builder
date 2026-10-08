"""What the assistant knows about a connected database's tables.

At indexing time this reads, for each table, its columns, primary key,
foreign keys (what it joins to) and an approximate row count from the
database's own catalog (readable by a read-only login). Owners add notes to
tables and columns, or hide tables entirely. Each visible table becomes one
embedded description the assistant can find; hidden tables can't be queried.

It also samples the values of short text columns, so the model knows that
specs are stored as PAName = 'Core Thickness' rather than in a CoreThickness
column, or that Make is written 'BMW'. Columns with few distinct values are
listed in full; others get their most common values as examples.

The full catalog is kept in the source's config ("schema_cache"); notes are in
"table_notes": {table: {"note": str, "hidden": bool, "columns": {column: note}}}.
"""

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class TableMeta:
    primary_key: list[str] = field(default_factory=list)
    # (column, referenced table, referenced column)
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)
    rows: int | None = None


def _qualified(kind: str, schema: str, name: str) -> str:
    if kind in ("sqlite", "mysql", "mariadb") or not schema:
        return name
    return f"{schema}.{name}"


def _rows(conn, sql: str) -> list[tuple]:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


POSTGRES_KEYS = """
SELECT cn.nspname, c.relname, con.contype, a.attname, rn.nspname, r.relname, ra.attname
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace cn ON cn.oid = c.relnamespace
JOIN LATERAL unnest(con.conkey, COALESCE(con.confkey, con.conkey)) WITH ORDINALITY AS k(col, refcol, n) ON true
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.col
LEFT JOIN pg_class r ON r.oid = con.confrelid
LEFT JOIN pg_namespace rn ON rn.oid = r.relnamespace
LEFT JOIN pg_attribute ra ON ra.attrelid = r.oid AND ra.attnum = k.refcol
WHERE con.contype IN ('p', 'f')
ORDER BY cn.nspname, c.relname, con.conname, k.n
"""
POSTGRES_ROWS = """
SELECT n.nspname, c.relname, c.reltuples::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
"""
MSSQL_PRIMARY = """
SELECT s.name, t.name, c.name
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE i.is_primary_key = 1
ORDER BY s.name, t.name, ic.key_ordinal
"""
MSSQL_FOREIGN = """
SELECT s.name, t.name, c.name, rs.name, rt.name, rc.name
FROM sys.foreign_key_columns f
JOIN sys.tables t ON t.object_id = f.parent_object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = f.parent_object_id AND c.column_id = f.parent_column_id
JOIN sys.tables rt ON rt.object_id = f.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
JOIN sys.columns rc ON rc.object_id = f.referenced_object_id AND rc.column_id = f.referenced_column_id
"""
MSSQL_ROWS = """
SELECT s.name, t.name, SUM(p.rows)
FROM sys.partitions p
JOIN sys.tables t ON t.object_id = p.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE p.index_id IN (0, 1)
GROUP BY s.name, t.name
"""
MYSQL_KEYS = """
SELECT table_schema, table_name, constraint_name, column_name, referenced_table_schema, referenced_table_name, referenced_column_name
FROM information_schema.key_column_usage
WHERE table_schema = DATABASE()
ORDER BY table_name, constraint_name, ordinal_position
"""
MYSQL_ROWS = "SELECT table_schema, table_name, table_rows FROM information_schema.tables WHERE table_schema = DATABASE()"


def read_metadata(conn, kind: str, tables: list) -> dict[str, TableMeta]:
    """Keys, relationships and row counts per qualified table name. Best effort: failures are logged."""
    meta: dict[str, TableMeta] = {t.qualified(kind): TableMeta() for t in tables}

    def entry(schema: str, name: str) -> TableMeta | None:
        return meta.get(_qualified(kind, schema or "", name))

    try:
        if kind == "postgres":
            for schema, table, contype, column, rschema, rtable, rcolumn in _rows(conn, POSTGRES_KEYS):
                if (m := entry(schema, table)) is None:
                    continue
                if contype == "p":
                    m.primary_key.append(column)
                elif rtable:
                    m.foreign_keys.append((column, _qualified(kind, rschema, rtable), rcolumn))
            for schema, table, rows in _rows(conn, POSTGRES_ROWS):
                if (m := entry(schema, table)) is not None and rows is not None and rows >= 0:
                    m.rows = int(rows)
        elif kind == "mssql":
            for schema, table, column in _rows(conn, MSSQL_PRIMARY):
                if (m := entry(schema, table)) is not None:
                    m.primary_key.append(column)
            for schema, table, column, rschema, rtable, rcolumn in _rows(conn, MSSQL_FOREIGN):
                if (m := entry(schema, table)) is not None:
                    m.foreign_keys.append((column, _qualified(kind, rschema, rtable), rcolumn))
            for schema, table, rows in _rows(conn, MSSQL_ROWS):
                if (m := entry(schema, table)) is not None and rows is not None:
                    m.rows = int(rows)
        elif kind in ("mysql", "mariadb"):
            for _schema, table, constraint, column, _rschema, rtable, rcolumn in _rows(conn, MYSQL_KEYS):
                if (m := entry("", table)) is None:
                    continue
                if constraint == "PRIMARY":
                    m.primary_key.append(column)
                elif rtable:
                    m.foreign_keys.append((column, rtable, rcolumn))
            for _schema, table, rows in _rows(conn, MYSQL_ROWS):
                if (m := entry("", table)) is not None and rows is not None:
                    m.rows = int(rows)
        elif kind == "sqlite":
            for table in tables:
                m = meta[table.qualified(kind)]
                quoted = table.name.replace('"', '""')
                for row in _rows(conn, f'PRAGMA table_info("{quoted}")'):
                    if row[5]:
                        m.primary_key.append(row[1])
                for row in _rows(conn, f'PRAGMA foreign_key_list("{quoted}")'):
                    m.foreign_keys.append((row[3], row[2], row[4] or ""))
                count = _rows(conn, f'SELECT COUNT(*) FROM "{quoted}"')
                m.rows = int(count[0][0]) if count else None
    except Exception as exc:  # noqa: BLE001 - metadata is a bonus; never fail indexing over it
        log.warning("Couldn't read keys or row counts: %s", exc)
    return meta


TEXT_TYPES = ("char", "text", "string", "varchar", "nvarchar", "nchar", "character", "citext", "enum")
# A column with at most this many distinct values is listed in full.
COMPLETE_VALUES = 40
EXAMPLE_VALUES = 12
MAX_VALUE_CHARS = 60
TEXT_COLUMNS_PER_TABLE = 15
# Sampling stops after this long, and skips very large tables, so indexing stays quick.
VALUES_BUDGET_SECONDS = 120
VALUES_MAX_ROWS = 5_000_000


def _quote(kind: str, name: str) -> str:
    if kind == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    if kind in ("mysql", "mariadb"):
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _values_sql(kind: str, schema: str, table: str, column: str) -> str:
    source = (_quote(kind, schema) + "." if schema and kind not in ("sqlite", "mysql", "mariadb") else "") + _quote(kind, table)
    col = _quote(kind, column)
    limit = COMPLETE_VALUES + 1
    if kind == "mssql":
        return f"SELECT TOP {limit} {col} FROM {source} WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY COUNT(*) DESC"
    return f"SELECT {col} FROM {source} WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT {limit}"


def read_values(conn, kind: str, tables: list, meta: dict[str, TableMeta]) -> dict[str, dict]:
    """{table: {column: {"values": [...], "complete": bool}}} for short text columns. Best effort."""
    started = time.monotonic()
    result: dict[str, dict] = {}
    for table in tables:
        qualified = table.qualified(kind)
        rows = meta.get(qualified, TableMeta()).rows
        if rows is not None and rows > VALUES_MAX_ROWS:
            continue
        text_columns = [c for c, t in table.columns if any(word in (t or "").lower() for word in TEXT_TYPES)]
        for column in text_columns[:TEXT_COLUMNS_PER_TABLE]:
            if time.monotonic() - started > VALUES_BUDGET_SECONDS:
                log.info("Stopped sampling column values after %ss", VALUES_BUDGET_SECONDS)
                return result
            try:
                values = [str(r[0]).strip() for r in _rows(conn, _values_sql(kind, table.schema, table.name, column))]
            except Exception as exc:  # noqa: BLE001 - samples are a bonus
                log.info("Couldn't sample %s.%s: %s", qualified, column, exc)
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                continue
            short = [v for v in values if v and len(v) <= MAX_VALUE_CHARS]
            # Free text (notes, descriptions) isn't worth listing.
            if not short or len(short) < len([v for v in values if v]) / 2:
                continue
            complete = len(values) <= COMPLETE_VALUES and len(short) == len([v for v in values if v])
            result.setdefault(qualified, {})[column] = {
                "values": short if complete else short[:EXAMPLE_VALUES],
                "complete": complete,
            }
    return result


def _values_line(info: dict) -> str:
    listed = ", ".join("'" + v.replace("'", "''") + "'" for v in info["values"])
    return f"    values: {listed}" if info["complete"] else f"    most common: {listed}, ..."


def _rows_label(rows: int | None) -> str:
    if rows is None:
        return ""
    if rows >= 1_000_000:
        return f"about {rows / 1_000_000:.1f} million rows"
    if rows >= 10_000:
        return f"about {rows // 1000:,} thousand rows"
    return f"{rows:,} rows"


def describe(source_name: str, kind_label: str, entry: dict, notes: dict, incoming: list[tuple[str, str, str]]) -> str:
    """The description embedded for search and shown to the model for one table."""
    name = entry["name"]
    column_notes = notes.get("columns", {})
    lines = [f'Table {name} in {kind_label} database "{source_name}"' + (f" ({_rows_label(entry.get('rows'))})" if entry.get("rows") is not None else "") + "."]
    if notes.get("note"):
        lines.append(f"About this table: {notes['note']}")
    if entry.get("primary_key"):
        lines.append("Primary key: " + ", ".join(entry["primary_key"]))
    lines.append("Columns:")
    values = entry.get("values", {})
    for column, data_type in entry["columns"]:
        note = column_notes.get(column)
        lines.append(f"- {column} ({data_type})" + (f": {note}" if note else ""))
        if column in values:
            lines.append(_values_line(values[column]))
    if entry.get("foreign_keys"):
        lines.append("Joins to: " + "; ".join(f"{c} = {t}.{rc}" for c, t, rc in entry["foreign_keys"]))
    if incoming:
        lines.append("Referenced by: " + "; ".join(f"{t}.{c} = {name}.{rc}" for t, c, rc in incoming[:15]))
    return "\n".join(lines)


def incoming_references(cache: list[dict]) -> dict[str, list[tuple[str, str, str]]]:
    """For each table, the (table, column, referenced column) pairs that point at it."""
    incoming: dict[str, list[tuple[str, str, str]]] = {}
    for entry in cache:
        for column, ref_table, ref_column in entry.get("foreign_keys", []):
            incoming.setdefault(ref_table, []).append((entry["name"], column, ref_column))
    return incoming
