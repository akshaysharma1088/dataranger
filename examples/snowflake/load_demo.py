"""Load DataRanger's demo data into Snowflake, so you can run a test patrol against a real account.

Creates the database DATARANGER_DEMO with three tables in its PUBLIC schema:
PAYMENTS (about 24,000 rows), CUSTOMERS and CONTROL_TOTALS. Remove it afterwards with:
    drop database DATARANGER_DEMO;

Uses the same SNOWFLAKE_* environment variables as `dataranger patrol --snowflake`.
"""
from datetime import date, timedelta

import pandas as pd
from snowflake.connector.pandas_tools import write_pandas

from dataranger.bench import generate, inject
from dataranger.connectors.snowflake import connect

DATABASE, SCHEMA = "DATARANGER_DEMO", "PUBLIC"


def main():
    case = inject(generate(7, "small", start=date.today() - timedelta(days=60)))
    tables = {"PAYMENTS": case.warehouse["transactions"], "CUSTOMERS": case.warehouse["customers"],
              "CONTROL_TOTALS": case.manifest}
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(f"create database if not exists {DATABASE}")
        cur.execute(f"use schema {DATABASE}.{SCHEMA}")
        for name, frame in tables.items():
            frame = frame.copy()
            if "txn_date" in frame:  # plain dates, so Snowflake stores a DATE column
                frame["txn_date"] = pd.to_datetime(frame["txn_date"]).dt.date
            frame.columns = [c.upper() for c in frame.columns]
            write_pandas(conn, frame, name, database=DATABASE, schema=SCHEMA, auto_create_table=True,
                         overwrite=True, quote_identifiers=False, use_logical_type=True)
            print(f"Loaded {len(frame):,} rows into {DATABASE}.{SCHEMA}.{name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
