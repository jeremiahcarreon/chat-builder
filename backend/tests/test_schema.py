from app.connectors.schema import describe, incoming_references

CACHE = [
    {"name": "public.orders", "columns": [["id", "integer"], ["customer_id", "integer"], ["orddate", "text"]],
     "primary_key": ["id"], "foreign_keys": [["customer_id", "public.customers", "id"]], "rows": 2_500_000},
    {"name": "public.customers", "columns": [["id", "integer"], ["name", "text"]], "primary_key": ["id"],
     "foreign_keys": [], "rows": 40},
]


def test_incoming_references():
    assert incoming_references(CACHE) == {"public.customers": [("public.orders", "customer_id", "id")]}


def test_description_includes_keys_joins_size_and_notes():
    notes = {"note": "Order headers, one row per sales order.", "columns": {"orddate": "Order date as YYYYMMDD"}}
    text = describe("ERP", "PostgreSQL", CACHE[0], notes, [])
    assert "about 2.5 million rows" in text
    assert "About this table: Order headers" in text
    assert "Primary key: id" in text
    assert "- orddate (text): Order date as YYYYMMDD" in text
    assert "Joins to: customer_id = public.customers.id" in text


def test_description_lists_tables_that_reference_it():
    text = describe("ERP", "PostgreSQL", CACHE[1], {}, incoming_references(CACHE)["public.customers"])
    assert "Referenced by: public.orders.customer_id = public.customers.id" in text
    assert "(40 rows)" in text


def test_sampled_values_are_shown_to_the_model():
    from app.connectors.schema import describe

    entry = {
        "name": "dbo.iPies",
        "columns": [["PartNo", "varchar"], ["PAName", "varchar"]],
        "values": {
            "PAName": {"values": ["Core Thickness", "Core Row Quantity"], "complete": True},
            "PartNo": {"values": ["CC161", "EC797"], "complete": False},
        },
    }
    text = describe("Catalog", "SQL Server", entry, {}, [])
    assert "    values: 'Core Thickness', 'Core Row Quantity'" in text
    # Partial samples (like part numbers) are never shown: models copy them into queries.
    assert "CC161" not in text


def test_value_sampling_sql_per_dialect():
    from app.connectors.schema import _values_sql

    assert _values_sql("mssql", "dbo", "iPies", "PAName") == (
        "SELECT TOP 41 [PAName] FROM [dbo].[iPies] WHERE [PAName] IS NOT NULL GROUP BY [PAName] ORDER BY COUNT(*) DESC"
    )
    assert _values_sql("postgres", "public", "orders", "sku").endswith('GROUP BY "sku" ORDER BY COUNT(*) DESC LIMIT 41')
    assert _values_sql("mysql", "", "orders", "sku").startswith("SELECT `sku` FROM `orders`")
