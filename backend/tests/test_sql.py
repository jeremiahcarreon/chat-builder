import pytest

from app.connectors.results import format_results
from app.connectors.sql_safety import UnsafeQuery, check_select, referenced_tables

ALLOWED = [
    ("select * from sheets.t where name like '%a%'", "spreadsheet"),
    ("with x as (select 1 as a) select * from x", "spreadsheet"),
    ("select a from t union select b from u", "postgres"),
    ("select top 5 * from dbo.orders order by total desc", "mssql"),
    ("select region, count(*) from orders group by region limit 3", "mysql"),
    ("select * from t limit 3", "sqlite"),
]

BLOCKED = [
    ("delete from t", "spreadsheet"),
    ("update t set a = 1", "postgres"),
    ("insert into t values (1)", "mysql"),
    ("select 1; drop table t", "postgres"),
    ("select * into z from t", "mssql"),
    ("select * from t for update", "postgres"),
    ("select pg_sleep(10)", "postgres"),
    ("select pg_read_file('/etc/passwd')", "postgres"),
    ("select sleep(5)", "mysql"),
    ("select load_file('/etc/passwd')", "mysql"),
    ("exec xp_cmdshell 'dir'", "mssql"),
    ("exec sp_who", "mssql"),
    ("pragma table_info(t)", "sqlite"),
    ("attach database 'x' as y", "sqlite"),
    ("copy t to '/tmp/x'", "postgres"),
    ("set role postgres", "postgres"),
    ("create table x (a int)", "sqlite"),
    ("not sql at all (", "postgres"),
]


@pytest.mark.parametrize("sql,kind", ALLOWED)
def test_allows_read_only_queries(sql, kind):
    check_select(sql, kind)


@pytest.mark.parametrize("sql,kind", BLOCKED)
def test_blocks_everything_else(sql, kind):
    with pytest.raises(UnsafeQuery):
        check_select(sql, kind)


def test_referenced_tables_ignores_ctes():
    tree = check_select("with x as (select * from sheets.a) select * from x join b on true", "postgres")
    assert referenced_tables(tree) == {("sheets", "a"), ("", "b")}


def test_format_results_truncates():
    text = format_results(["a"], [(i,) for i in range(5)], more=True)
    assert "Showing the first 5 rows only" in text
    assert format_results(["a"], [], more=False).endswith("(no rows)")
    assert format_results(["a", "b"], [(1, None)], more=False) == "a | b\n1 | \n(1 row)"
