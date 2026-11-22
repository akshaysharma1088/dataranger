"""Metadata completeness check: columns and tables with no business definition (#88)."""
import duckdb

from dataranger import metadata


def test_from_dbt_flags_undocumented_columns_and_table():
    fct = {
        "name": "fct_payments",
        "description": "Payments fact table",
        "meta": {"owner": "payments-team"},
        "columns": {
            "payment_id": {"description": "Primary key"},
            "amount": {"description": ""},  # present but blank: undocumented
            "channel": {},  # no description key at all: undocumented
        },
    }
    dim = {"name": "dim_customers", "description": "", "meta": {}, "columns": {"customer_id": {"description": "PK"}}}
    report = metadata.from_dbt([fct, dim])
    assert report.coverage_line() == "definitions: 2 of 4 columns"
    assert report.tables_without_description == ["dim_customers"]
    assert report.tables_without_owner == ["dim_customers"]
    fct_meta = next(t for t in report.tables if t.table == "fct_payments")
    assert fct_meta.owner == "payments-team"
    assert sorted(fct_meta.undocumented_columns) == ["amount", "channel"]
    rows = report.to_rows()
    assert rows[0]["table"] == "fct_payments"
    assert rows[0]["undocumented_columns"] == "amount; channel"
    d = report.to_dict()
    assert d["coverage"] == "definitions: 2 of 4 columns"
    assert d["tables_without_description"] == ["dim_customers"]


def test_from_dbt_with_no_columns_block_is_fully_undocumented():
    node = {"name": "stg_raw"}
    report = metadata.from_dbt([node])
    assert report.columns_total == 0
    assert report.tables_without_description == ["stg_raw"]
    assert report.tables_without_owner == ["stg_raw"]


def test_from_duckdb_reads_catalog_comments(tmp_path):
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create schema gold")
    con.execute("create table gold.payments (payment_id int, amount double, channel varchar)")
    con.execute("comment on table gold.payments is 'Payments fact table'")
    con.execute("comment on column gold.payments.payment_id is 'Primary key'")
    con.execute("create table gold.customers (customer_id int)")  # no comments at all
    con.close()

    report = metadata.from_duckdb(str(db), ["gold.payments", "gold.customers"])
    assert report.coverage_line() == "definitions: 1 of 4 columns"
    assert report.tables_without_description == ["gold.customers"]
    pay = next(t for t in report.tables if t.table == "gold.payments")
    assert pay.description == "Payments fact table"
    assert sorted(pay.undocumented_columns) == ["amount", "channel"]
    cust = next(t for t in report.tables if t.table == "gold.customers")
    assert cust.description is None
    assert cust.undocumented_columns == ["customer_id"]
