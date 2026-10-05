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
