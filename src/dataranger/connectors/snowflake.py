"""Patrol tables in Snowflake. Read-only: three SELECT statements, nothing else.

Needs the Snowflake connector: pip install "dataranger[snowflake]"

Credentials come from the environment or from a named connection in ~/.snowflake/connections.toml
(the file the Snowflake CLI and connector already use). Nothing is stored by DataRanger.
"""
from __future__ import annotations

import os

import pandas as pd

from . import sql

# Environment variable -> connector argument. The same names the Snowflake CLI and dbt examples use.
ENV = {
    "SNOWFLAKE_ACCOUNT": "account",
    "SNOWFLAKE_USER": "user",
    "SNOWFLAKE_PASSWORD": "password",
    "SNOWFLAKE_AUTHENTICATOR": "authenticator",
    "SNOWFLAKE_PASSCODE": "passcode",  # the current code from an authenticator app, with username_password_mfa
    "SNOWFLAKE_TOKEN": "token",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "private_key_file",
    "SNOWFLAKE_PRIVATE_KEY_FILE": "private_key_file",
    "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "private_key_file_pwd",
    "SNOWFLAKE_ROLE": "role",
    "SNOWFLAKE_WAREHOUSE": "warehouse",
    "SNOWFLAKE_DATABASE": "database",
    "SNOWFLAKE_SCHEMA": "schema",
}


def connection_params(connection: str | None = None, env=None) -> dict:
    """Connector arguments for a named connection, or for the SNOWFLAKE_* environment variables."""
    if connection:
        return {"connection_name": connection}
    env = os.environ if env is None else env
    params = {arg: env[var] for var, arg in ENV.items() if env.get(var)}
    if params and "account" not in params:
        raise ValueError("SNOWFLAKE_ACCOUNT is not set. Set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER plus a password, "
                         "key or authenticator, or name a connection from ~/.snowflake/connections.toml.")
    return params  # empty: the connector falls back to its default connection


def connect(connection: str | None = None, params: dict | None = None):
    """Open a Snowflake session tagged 'dataranger', so its queries are easy to find in query history."""
    try:
        import snowflake.connector
    except ImportError:
        raise SystemExit('The Snowflake source needs the Snowflake connector: pip install "dataranger[snowflake]"') from None
    params = dict(params if params is not None else connection_params(connection))
    params.setdefault("session_parameters", {"QUERY_TAG": "dataranger"})
    params.setdefault("application", "dataranger")
    return snowflake.connector.connect(**params)


def _frame(conn, query: str) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(query)
        if hasattr(cur, "fetch_pandas_all"):
            try:
                return cur.fetch_pandas_all()
            except Exception:  # connector installed without pyarrow: read the rows directly
                cur.execute(query)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    finally:
        cur.close()


def run_queries(queries, connection: str | None = None, params: dict | None = None, opener=None) -> tuple:
    """Open a session, run the read-only queries and close it. Snowflake errors come back as one readable line."""
    try:
        conn = (opener or connect)(connection, params)
    except SystemExit:
        raise
    except Exception as e:
        raise ValueError(f"could not connect to Snowflake: {_first_line(e)}") from None
    try:
        return tuple(_frame(conn, q) for q in queries)
    except Exception as e:
        raise ValueError(f"Snowflake query failed: {_first_line(e)}") from None
    finally:
        conn.close()


def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0]


def from_snowflake(table: str, ref_table: str, manifest_table: str, mapping, days: int | None = None,
                   connection: str | None = None, params: dict | None = None, opener=None):
    """Read facts, reference keys and control totals from Snowflake.

    `table`, `ref_table` and `manifest_table` may be qualified (DATABASE.SCHEMA.TABLE). Only the mapped
    columns are selected, and `days` limits both the facts and the control totals to the last N days.
    """
    queries = sql.build_queries(table, ref_table, manifest_table, mapping, days)  # refuses bad names before connecting
    return sql.normalize(*run_queries(queries, connection, params, opener), mapping)
