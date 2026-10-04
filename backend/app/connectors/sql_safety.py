"""Checks that model-written SQL is a single read-only query before it runs.

This is one of several layers: queries also run in read-only transactions where
the database supports it, with timeouts and row limits, and should use a
database login that only has read access.
"""

import logging

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# sqlglot dialect for each data source kind.
DIALECTS = {
    "spreadsheet": "postgres",
    "postgres": "postgres",
    "mssql": "tsql",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
}

FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Grant,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Use,
    exp.Pragma,
    exp.Copy,
    exp.LoadData,
)

# Functions that read files, sleep, reach other servers, change settings or
# sequences, or execute a SQL string (which would hide tables from the checks).
FORBIDDEN_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_sleep",
    "pg_sleep_for", "pg_sleep_until", "lo_import", "lo_export", "lo_get", "dblink",
    "dblink_exec", "query_to_xml", "query_to_xmlschema", "query_to_xml_and_xmlschema",
    "cursor_to_xml", "cursor_to_xmlschema", "table_to_xml", "table_to_xmlschema",
    "table_to_xml_and_xmlschema", "schema_to_xml", "schema_to_xmlschema",
    "schema_to_xml_and_xmlschema", "database_to_xml", "database_to_xmlschema",
    "database_to_xml_and_xmlschema", "ts_stat", "ts_debug", "set_config", "current_setting",
    "pg_advisory_lock", "pg_advisory_lock_shared", "pg_advisory_xact_lock",
    "pg_advisory_xact_lock_shared", "pg_try_advisory_lock", "pg_try_advisory_xact_lock",
    "nextval", "setval", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "load_file", "sleep", "benchmark", "get_lock", "openrowset", "opendatasource",
    "openquery", "openxml", "xp_cmdshell", "load_extension", "readfile", "writefile",
    "sys_exec", "sys_eval",
}


# sqlglot logs a warning for statements it can only partially parse; those are rejected anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class UnsafeQuery(Exception):
    pass


def check_select(sql: str, kind: str) -> exp.Expression:
    """Returns the parsed query, or raises UnsafeQuery with a reason the model can act on."""
    dialect = DIALECTS[kind]
    if "/*!" in sql:
        # MySQL runs the contents of "/*! ... */" comments; the parser treats them as comments.
        raise UnsafeQuery("Comments of the form /*! ... */ are not allowed.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        raise UnsafeQuery(f"Could not parse the SQL: {str(exc).splitlines()[0]}") from None
    if len(statements) != 1:
        raise UnsafeQuery("Send exactly one SQL statement.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeQuery("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise UnsafeQuery("Only read-only SELECT queries are allowed.")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise UnsafeQuery(f"The function {name} is not allowed.")
    return tree


def referenced_tables(tree: exp.Expression) -> set[tuple[str, str]]:
    """(schema, table) pairs the query reads, excluding names defined by CTEs."""
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not table.db and name in cte_names:
            continue
        tables.add((table.db.lower(), name))
    return tables
