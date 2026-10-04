import uuid

import pytest

from app.connectors.databases import _check_tables, _friendly_error
from app.connectors.query import _unwrap
from app.connectors.sql_safety import check_select
from app.models import DataSource


def _source(kind: str, database: str = "shop") -> DataSource:
    return DataSource(id=uuid.uuid4(), app_id=uuid.uuid4(), kind=kind, name="db", config={"database": database})


ALLOWED = {"sales.invoices", "sales.customers"}


@pytest.mark.parametrize(
    "sql",
    [
        "select * from sales.invoices",
        "select * from invoices",
        "with x as (select * from sales.customers) select * from x",
    ],
)
def test_allows_indexed_tables(sql):
    _check_tables(check_select(sql, "postgres"), _source("postgres"), ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from public.secret_payroll",
        "select * from secret_payroll",
        "select * from other.public.users",
    ],
)
def test_blocks_other_tables_and_databases(sql):
    with pytest.raises(PermissionError):
        _check_tables(check_select(sql, "postgres"), _source("postgres"), ALLOWED)


def test_mysql_blocks_other_databases():
    with pytest.raises(PermissionError):
        _check_tables(check_select("select * from mysql.user", "mysql"), _source("mysql"), {"orders"})
    _check_tables(check_select("select * from shop.orders", "mysql"), _source("mysql"), {"orders"})


def test_sql_server_three_part_names():
    allowed = {"dbo.orders"}
    _check_tables(check_select("select top 5 * from shop.dbo.orders", "mssql"), _source("mssql"), allowed)
    with pytest.raises(PermissionError):
        _check_tables(check_select("select * from master.dbo.sysdatabases", "mssql"), _source("mssql"), allowed)


def test_friendly_errors():
    assert "Login failed" in _friendly_error(Exception("password authentication failed for user x"))
    assert "Could not reach" in _friendly_error(Exception("connection refused"))


def test_unwraps_nested_tool_arguments():
    nested = {"object": {"sql": "select 1", "source_id": "x"}, "dialect": "PostgreSQL"}
    assert _unwrap(nested) == {"sql": "select 1", "source_id": "x"}
    assert _unwrap({"sql": "select 1"}) == {"sql": "select 1"}
