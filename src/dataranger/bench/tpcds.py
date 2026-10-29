"""DQ-Repair instances built on TPC-DS store_sales instead of the synthetic generator.

The fact table is store_sales joined to its date and item dimensions; the reference table is
customer. One row is one ticket line, keyed by (ticket number, item). The segment that control
totals are split by is the store, the category is the item category, and the amount is net paid.
Rows without a sale date, customer or store (about 8 percent at scale factor 1) are dropped, because
the benchmark's invariants require every row to reference a customer.

Each instance is a window of consecutive days. TPC-DS volume is strongly seasonal (daily rows
double from July to August and rise again before the holidays), so windows that cross those
months produce volume alerts on correct data, which a repair system has to leave alone.

Requires the DuckDB tpcds extension: `pip install duckdb-extension-tpcds` or `INSTALL tpcds`.
"""
from __future__ import annotations

import glob
import os
from datetime import timedelta

import duckdb
import pandas as pd

from .tables import Instance

QUERY = """
select ss_ticket_number * 100000 + ss_item_sk as txn_id,
       ss_customer_sk as customer_id,
       d_date as txn_date,
       'store_' || ss_store_sk as channel,
       coalesce(i_category, 'Unknown') as category,
       cast(ss_net_paid as double) as amount
from store_sales
join date_dim on ss_sold_date_sk = d_date_sk
join item on ss_item_sk = i_item_sk
where ss_customer_sk is not null and ss_store_sk is not null and ss_net_paid is not null
"""


def _load_extension(con: duckdb.DuckDBPyConnection) -> None:
    try:
        import duckdb_extension_tpcds as pkg  # wheel that ships the extension binary
        path = glob.glob(os.path.join(os.path.dirname(pkg.__file__), "extensions", "**", "*.duckdb_extension"),
                         recursive=True)[0]
        con.execute(f"LOAD '{path}'")
    except (ImportError, IndexError):
        con.execute("INSTALL tpcds")
        con.execute("LOAD tpcds")


def load(sf: float = 1, db: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate (or reuse, if `db` already holds it) TPC-DS at scale factor `sf`; return facts and customers."""
    con = duckdb.connect(db or ":memory:")
    con.execute("SET enable_progress_bar = false")
    if not con.execute("select count(*) from information_schema.tables where table_name = 'store_sales'").fetchone()[0]:
        _load_extension(con)
        con.execute(f"CALL dsdgen(sf={sf})")
    facts = con.execute(QUERY + " order by txn_date, txn_id").df()
    customers = con.execute("select c_customer_sk as customer_id from customer order by 1").df()
    con.close()
    facts["txn_date"] = pd.to_datetime(facts["txn_date"]).dt.date
    facts["amount"] = facts["amount"].round(2)
    return facts, customers


def windows(facts: pd.DataFrame, customers: pd.DataFrame, n_days: int = 60) -> list[Instance]:
    """Consecutive, non-overlapping windows of `n_days` calendar days (business days only)."""
    first, last = min(facts["txn_date"]), max(facts["txn_date"])
    stores = sorted(facts["channel"].unique())
    present = set(facts["txn_date"])  # TPC-DS has no sales on a few dates (March 2 of four years): not business days
    out = []
    start = first
    k = 0
    while start + timedelta(days=n_days - 1) <= last:
        span = [start + timedelta(days=i) for i in range(n_days)]
        days = [d for d in span if d in present]
        w = facts[(facts["txn_date"] >= span[0]) & (facts["txn_date"] <= span[-1])].reset_index(drop=True)
        out.append(Instance(seed=k, size="tpcds", days=days, customers=customers, source_txn=w,
                            meta={"rows": len(w), "start": days[0].isoformat(), "scale_channels": stores,
                                  "big_ticket_category": "Electronics", "big_ticket_amount": 4000}))
        start += timedelta(days=n_days)
        k += 1
    return out
